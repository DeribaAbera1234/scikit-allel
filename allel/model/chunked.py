# -*- coding: utf-8 -*-
from __future__ import absolute_import, print_function, division


import numpy as np
import bcolz
import h5py
import tempfile
import operator
from allel.compat import reduce, string_types, copy_method_doc
from allel.util import check_dim0_aligned, asarray_ndim, check_same_ndim, \
    check_dim_aligned

from allel.model.ndarray import GenotypeArray, subset


def h5dmem(*args, **kwargs):
    """Create an in-memory HDF5 dataset, by default chunked and gzip
    compressed.

    All arguments are passed through to the h5py create_dataset() function.

    """

    # need a file name even tho nothing is ever written
    fn = tempfile.mktemp()

    # default file creation args (allow user to override)
    backing_store = kwargs.pop('backing_store', False)
    block_size = kwargs.pop('block_size', 2**16)

    # open HDF5 file
    h5f = h5py.File(fn, mode='w', driver='core', backing_store=backing_store,
                    block_size=block_size)

    # defaults for dataset creation
    kwargs.setdefault('chunks', True)
    if kwargs['chunks']:
        kwargs.setdefault('compression', 'gzip')
        kwargs.setdefault('shuffle', False)
    if len(args) == 0 and 'name' not in kwargs:
        # default dataset name
        args = ('data',)

    # create dataset
    h5d = h5f.create_dataset(*args, **kwargs)

    return h5d


# noinspection PyShadowingBuiltins
def h5dtmp(*args, **kwargs):
    """Create an HDF5 dataset backed by a temporary file, by default chunked
    and gzip compressed.

    All arguments are passed through to the h5py create_dataset() function.

    """

    # create temporary file name
    suffix = kwargs.pop('suffix', '')
    prefix = kwargs.pop('prefix', 'tmp')
    dir = kwargs.pop('dir', None)
    fn = tempfile.mktemp(suffix=suffix, prefix=prefix, dir=dir)

    # open HDF5 file
    h5f = h5py.File(fn, mode='w')

    # defaults for dataset creation
    kwargs.setdefault('chunks', True)
    if kwargs['chunks']:
        kwargs.setdefault('compression', 'gzip')
        kwargs.setdefault('shuffle', False)
    if len(args) == 0 and 'name' not in kwargs:
        # default dataset name
        args = ('data',)

    # create dataset
    h5d = h5f.create_dataset(*args, **kwargs)

    return h5d


def is_array_like(a):
    return hasattr(a, 'shape') and hasattr(a, 'dtype')


def check_array_like(a, ndim=None):
    if isinstance(a, tuple):
        for x in a:
            check_array_like(x)
    else:
        if not is_array_like(a):
            raise ValueError(
                'expected array-like with shape and dtype, found %r' % a
            )
        if ndim is not None and len(a.shape) != ndim:
            raise ValueError(
                'expected array-like with %s dimensions, found %s' %
                (ndim, len(a.shape))
            )


def get_chunklen(a):
    check_array_like(a)
    if hasattr(a, 'chunklen'):
        # bcolz carray
        return a.chunklen
    elif hasattr(a, 'chunks') and len(a.chunks) == len(a.shape):
        # h5py dataset
        return a.chunks[0]
    else:
        # do something vaguely sensible - ~64k blocks
        rowsize = a.dtype.itemsize * reduce(operator.mul, a.shape)
        return max(1, (2**16) // rowsize)


class Backend(object):

    def empty(self, shape, **kwargs):
        pass

    def create(self, data, *args, **kwargs):
        pass

    def store(self, source, sink, offset=0, blen=None):
        
        # check arguments
        check_array_like(source)
        check_array_like(sink)
        if sink.shape[0] < (offset + source.shape[0]):
            raise ValueError('sink is too short')

        # determine block size
        if blen is None:
            blen = get_chunklen(sink)

        # copy block-wise
        for i in range(0, source.shape[0], blen):
            j = min(i+blen, source.shape[0])
            sink[offset+i:offset+j] = source[i:j]
            
    def copy(self, charr, blen=None, **kwargs):
        check_array_like(charr)
        kwargs.setdefault('dtype', charr.dtype)
        sink = self.empty(charr.shape, **kwargs)
        self.store(charr, sink, blen=blen)
        return sink

    def reduce_axis(self, charr, reducer, block_reducer, mapper=None, 
                    axis=None, **kwargs):
        check_array_like(charr)
        print(axis, reducer, block_reducer, mapper)

        # determine block size for iteration
        blen = kwargs.pop('blen', get_chunklen(charr))

        # normalise axis argument
        if isinstance(axis, int):
            axis = (axis,)
            
        if axis is None or 0 in axis:
            out = None
            for i in range(0, charr.shape[0], blen):
                j = min(i+blen, charr.shape[0])
                block = charr[i:j]
                print(i, repr(block))
                if mapper:
                    block = mapper(block)
                r = reducer(block, axis=axis)
                print(i, repr(r))
                if i == 0:
                    out = r
                else:
                    out = block_reducer(out, r)
                print(i, repr(out))
            if np.isscalar(out):
                return out
            elif len(out.shape) == 0:
                # slightly weird case where array is returned
                return out[()]
            else:
                return self.create(out, **kwargs)

        else:

            # initialise output
            out = None

            # block iteration
            for i in range(0, charr.shape[0], blen):
                j = min(i+blen, charr.shape[0])
                block = charr[i:j]
                if mapper:
                    block = mapper(block)
                r = reducer(block, axis=axis)
                if i == 0:
                    outshape = (charr.shape[0],) + r.shape[1:]
                    kwargs.setdefault('dtype', r.dtype)
                    out = self.empty(outshape, **kwargs)
                out[i:j] = r
                # no need for block_reducer

            return out
        
    def amax(self, charr, axis=None, mapper=None, **kwargs):
        return self.reduce_axis(charr, axis=axis, reducer=np.amax, 
                                block_reducer=np.maximum, mapper=mapper, 
                                **kwargs)

    def amin(self, charr, axis=None, mapper=None, **kwargs):
        return self.reduce_axis(charr, axis=axis, reducer=np.amin, 
                                block_reducer=np.minimum, mapper=mapper,
                                **kwargs)
    
    def sum(self, charr, axis=None, mapper=None, **kwargs):
        return self.reduce_axis(charr, axis=axis, reducer=np.sum, 
                                block_reducer=np.add, mapper=mapper, **kwargs)

    def count_nonzero(self, charr, mapper=None, **kwargs):
        return self.reduce_axis(charr, axis=None, reducer=np.count_nonzero,
                                block_reducer=np.add, mapper=mapper, **kwargs)
    
    def map_blocks(self, domain, mapper, blen=None, **kwargs):
        """N.B., assumes mapper will preserve leading dimension."""
        
        # check inputs
        check_array_like(domain)
        if isinstance(domain, tuple):
            check_dim0_aligned(domain)
            length = domain[0].shape[0]
        else:
            length = domain.shape[0]
        
        # determine block size for iteration
        if blen is None:
            if isinstance(domain, tuple):
                blen = min(get_chunklen(a) for a in domain)
            else:
                blen = get_chunklen(domain)
                
        # block-wise iteration
        out = None
        for i in range(0, length, blen):        
            j = min(i+blen, length)
            
            # slice domain
            if isinstance(domain, tuple):
                blocks = [a[i:j] for a in domain]
            else:
                blocks = domain[i:j],
                
            # map
            res = mapper(*blocks)
            
            # create
            if i == 0:
                outshape = (length,) + res.shape[1:]
                kwargs.setdefault('dtype', res.dtype)
                out = self.empty(outshape, **kwargs)
    
            # store
            out[i:j] = res
            
        return out
            
    def dict_map_blocks(self, domain, mapper, blen=None, **kwargs):
        """N.B., assumes mapper will preserve leading dimension."""
        
        # check inputs
        check_array_like(domain)
        if isinstance(domain, tuple):
            check_dim0_aligned(domain)
            length = domain[0].shape[0]
        else:
            length = domain.shape[0]
        
        # determine block size for iteration
        if blen is None:
            if isinstance(domain, tuple):
                blen = min(get_chunklen(a) for a in domain)
            else:
                blen = get_chunklen(domain)
                
        # block-wise iteration
        out = None
        for i in range(0, length, blen):        
            j = min(i+blen, length)
            
            # slice domain
            if isinstance(domain, tuple):
                blocks = [a[i:j] for a in domain]
            else:
                blocks = domain[i:j],
                
            # map
            res = mapper(*blocks)
            
            # create
            if i == 0:
                out = dict()
                for k, v in res.items():
                    outshape = (length,) + v.shape[1:]
                    kwargs.setdefault('dtype', v.dtype)
                    out[k] = self.empty(outshape, **kwargs)
    
            # store
            for k, v in res.items():
                out[k][i:j] = v
            
        return out

    def compress(self, charr, condition, axis, **kwargs):

        # check inputs
        check_array_like(charr)
        length = charr.shape[0]
        if not is_array_like(condition):
            condition = np.asarray(condition)
        check_array_like(condition, 1)

        # determine block size for iteration
        blen = kwargs.pop('blen', get_chunklen(charr))

        # output defaults
        kwargs.setdefault('dtype', charr.dtype)

        if axis == 0:
            check_dim0_aligned(charr, condition)

            # setup output
            outshape = (self.count_nonzero(condition),) + charr.shape[1:]
            out = self.empty(outshape, **kwargs)
            offset = 0

            # block iteration
            for i in range(0, length, blen):
                j = min(i+blen, length)
                bcond = condition[i:j]
                # don't bother doing anything unless we have to
                n = np.count_nonzero(bcond)
                if n:
                    block = charr[i:j]
                    out[offset:offset+n] = np.compress(bcond, block, axis=0)
                    offset += n

            return out

        elif axis == 1:
            if condition.shape[0] != charr.shape[1]:
                raise ValueError('length of condition must match length of '
                                 'second dimension; expected %s, found %s' %
                                 (charr.shape[1], condition.size))

            # setup output
            outshape = (length, self.count_nonzero(condition)) + charr.shape[2:]
            out = self.empty(outshape, **kwargs)

            # block iteration
            for i in range(0, length, blen):
                j = min(i+blen, length)
                block = charr[i:j]
                out[i:j] = np.compress(condition, block, axis=1)

            return out

        else:
            raise NotImplementedError('axis not supported: %s' % axis)

    def take(self, charr, indices, axis, blen=None, **kwargs):

        # check inputs
        check_array_like(charr)
        length = charr.shape[0]
        indices = asarray_ndim(indices, 1)

        # determine block size for iteration
        blen = kwargs.pop('blen', get_chunklen(charr))

        # output defaults
        kwargs.setdefault('dtype', charr.dtype)

        if axis == 0:

            # check that indices are strictly increasing
            if np.any(indices[1:] <= indices[:-1]):
                raise NotImplementedError(
                    'indices must be strictly increasing'
                )
            # implement via compress()
            condition = np.zeros((length,), dtype=bool)
            condition[indices] = True
            return self.compress(charr, condition, axis=0, **kwargs)

        elif axis == 1:

            # setup output
            outshape = (length, len(indices)) + charr.shape[2:]
            out = self.empty(outshape, **kwargs)

            # block iteration
            for i in range(0, length, blen):
                j = min(i+blen, length)
                block = charr[i:j]
                out[i:j] = np.take(block, indices, axis=1)

            return out

        else:
            raise NotImplementedError('axis not supported: %s' % axis)

    def subset(self, charr, sel0, sel1, **kwargs):

        # check inputs
        check_array_like(charr)
        if len(charr.shape) < 2:
            raise ValueError('expected array-like with at least 2 dimensions')
        length = charr.shape[0]
        sel0 = asarray_ndim(sel0, 1, allow_none=True)
        sel1 = asarray_ndim(sel1, 1, allow_none=True)
        if sel0 is None and sel1 is None:
            raise ValueError('missing selection')

        # determine block size for iteration
        blen = kwargs.pop('blen', get_chunklen(charr))

        # ensure boolean array for dim 0
        if sel0.shape[0] < length:
            tmp = np.zeros(length, dtype=bool)
            tmp[sel0] = True
            sel0 = tmp

        # ensure indices for dim 1
        if sel1.shape[0] == charr.shape[1]:
            sel1 = np.nonzero(sel1)[0]

        # setup output
        kwargs.setdefault('dtype', charr.dtype)
        outshape = (self.count_nonzero(sel0), len(sel1)) + charr.shape[2:]
        out = self.empty(outshape, **kwargs)
        offset = 0

        # build output
        for i in range(0, length, blen):
            j = min(i+blen, length)
            bsel0= sel0[i:j]
            # don't bother doing anything unless we have to
            n = np.count_nonzero(bsel0)
            if n:
                block = charr[i:j]
                out[offset:offset+n] = subset(block, bsel0, sel1)
                offset += n

        return out

    def hstack(self, tup, **kwargs):

        # check inputs
        if not isinstance(tup, (tuple, list)):
            raise ValueError('expected tuple or list, found %r' % tup)
        if len(tup) < 2:
            raise ValueError('expected two or more arrays to stack')
        check_dim0_aligned(*tup)
        check_same_ndim(*tup)

        def mapper(*blocks):
            return np.hstack(blocks)

        return self.map_blocks(tup, mapper, **kwargs)

    def vstack(self, tup, blen=None, **kwargs):

        # check inputs
        if not isinstance(tup, (tuple, list)):
            raise ValueError('expected tuple or list, found %r' % tup)
        if len(tup) < 2:
            raise ValueError('expected two or more arrays to stack')
        check_same_ndim(*tup)
        for i in range(1, len(tup[0].shape)):
            check_dim_aligned(i, *tup)

        # set block size to use
        if blen is None:
            blen = min([get_chunklen(a) for a in tup])

        # setup output
        kwargs.setdefault('dtype', tup[0].dtype)
        outshape = (sum(a.shape[0] for a in tup),) + tup[0].shape[1:]
        out = self.empty(outshape, **kwargs)
        offset = 0

        # build output
        for a in tup:
            for i in range(0, a.shape[0], blen):
                j = min(i+blen, a.shape[0])
                block = a[i:j]
                out[offset:offset+block.shape[0]] = block
                offset += block.shape[0]

        return out

    def op_scalar(self, charr, op, other, **kwargs):

        # check inputs
        check_array_like(charr)
        if not np.isscalar(other):
            raise ValueError('expected scalar')

        def mapper(block):
            return op(block, other)

        return self.map_blocks(charr, mapper, **kwargs)


class NumpyBackend(Backend):

    def empty(self, shape, **kwargs):
        return np.empty(shape, **kwargs)

    def create(self, data, **kwargs):
        return np.asarray(data, **kwargs)


# singleton instance
numpy_backend = NumpyBackend()


class BColzBackend(Backend):
    
    def __init__(self, **kwargs):
        self.kwargs = kwargs

    def empty(self, shape, **kwargs):
        for k, v in self.kwargs.items():
            kwargs.setdefault(k, v)
        return bcolz.zeros(shape, **kwargs)

    def create(self, data, **kwargs):
        for k, v in self.kwargs.items():
            kwargs.setdefault(k, v)
        return bcolz.carray(data, **kwargs)


# singleton instance
bcolz_backend = BColzBackend()


class H5dtmpBackend(Backend):

    def __init__(self, **kwargs):
        self.kwargs = kwargs

    def empty(self, shape, **kwargs):
        for k, v in self.kwargs.items():
            kwargs.setdefault(k, v)
        return h5dtmp(shape=shape, **kwargs)

    def create(self, data, **kwargs):
        for k, v in self.kwargs.items():
            kwargs.setdefault(k, v)
        return h5dtmp(data=data, **kwargs)


# singleton instance
h5dtmp_backend = H5dtmpBackend()


class H5dmemBackend(Backend):

    def __init__(self, **kwargs):
        self.kwargs = kwargs

    def empty(self, shape, **kwargs):
        for k, v in self.kwargs.items():
            kwargs.setdefault(k, v)
        return h5dmem(shape=shape, **kwargs)

    def create(self, data, **kwargs):
        for k, v in self.kwargs.items():
            kwargs.setdefault(k, v)
        return h5dmem(data=data, **kwargs)


# singleton instance
h5dmem_backend = H5dmemBackend()


# set default
default_backend = bcolz_backend


def get_backend(backend=None):
    if backend is None:
        return default_backend
    elif isinstance(backend, string_types):
        # normalise backend
        backend = str(backend).lower()
        if backend in ['numpy', 'ndarray', 'np']:
            return numpy_backend
        elif backend in ['bcolz', 'carray']:
            return bcolz_backend
        elif backend in ['hdf5', 'h5py', 'h5dtmp']:
            return h5dtmp_backend
        elif backend in ['h5dmem']:
            return h5dmem_backend
        else:
            raise ValueError('unknown backend: %s' % backend)
    elif isinstance(backend, Backend):
        # custom backend
        return backend
    else:
        raise ValueError('expected None, string or Backend, found: %r'
                         % backend)


class ChunkedArray(object):

    def __init__(self, data):
        check_array_like(data)
        self.data = data

    def __getitem__(self, *args):
        return self.data.__getitem__(*args)

    def __setitem__(self, key, value):
        return self.data.__setitem__(key, value)

    def __getattr__(self, item):
        return getattr(self.data, item)

    def __array__(self):
        return self.data[:]

    def __repr__(self):
        return '<%s: shape %s, type %s, data %s>' % \
               (type(self), str(self.shape), str(self.dtype), type(self.data))
    
    def __str__(self):
        return str(self.data)

    def __len__(self):
        return len(self.data)

    @property
    def ndim(self):
        return len(self.shape)
    
    def store(self, sink, offset=0, **kwargs):
        backend = get_backend(kwargs.pop('backend', None))
        backend.store(self, sink, offset=offset, **kwargs)

    def copy(self, **kwargs):
        backend = get_backend(kwargs.pop('backend', None))
        out = backend.copy(self, **kwargs)
        return type(self)(out)
        
    def max(self, axis=None, **kwargs):
        backend = get_backend(kwargs.pop('backend', None))
        return backend.amax(self, axis=axis, **kwargs)
    
    def min(self, axis=None, **kwargs):
        backend = get_backend(kwargs.pop('backend', None))
        return backend.amin(self, axis=axis, **kwargs)
    
    def sum(self, axis=None, **kwargs):
        backend = get_backend(kwargs.pop('backend', None))
        return backend.sum(self, axis=axis, **kwargs)

    def op_scalar(self, op, other, **kwargs):
        backend = get_backend(kwargs.pop('backend', None))
        out = backend.op_scalar(self, op, other, **kwargs)
        return ChunkedArray(out)
        
    def __eq__(self, other, **kwargs):
        return self.op_scalar(operator.eq, other, **kwargs)

    def __ne__(self, other, **kwargs):
        return self.op_scalar(operator.ne, other, **kwargs)

    def __lt__(self, other, **kwargs):
        return self.op_scalar(operator.lt, other, **kwargs)

    def __gt__(self, other, **kwargs):
        return self.op_scalar(operator.gt, other, **kwargs)

    def __le__(self, other, **kwargs):
        return self.op_scalar(operator.le, other, **kwargs)

    def __ge__(self, other, **kwargs):
        return self.op_scalar(operator.ge, other, **kwargs)

    def __add__(self, other, **kwargs):
        return self.op_scalar(operator.add, other, **kwargs)

    def __floordiv__(self, other, **kwargs):
        return self.op_scalar(operator.floordiv, other, **kwargs)

    def __mod__(self, other, **kwargs):
        return self.op_scalar(operator.mod, other, **kwargs)

    def __mul__(self, other, **kwargs):
        return self.op_scalar(operator.mul, other, **kwargs)

    def __pow__(self, other, **kwargs):
        return self.op_scalar(operator.pow, other, **kwargs)

    def __sub__(self, other, **kwargs):
        return self.op_scalar(operator.sub, other, **kwargs)

    def __truediv__(self, other, **kwargs):
        return self.op_scalar(operator.truediv, other, **kwargs)

    def compress(self, condition, axis=0, **kwargs):
        backend = get_backend(kwargs.pop('backend', None))
        out = backend.compress(self.data, condition, axis=axis, **kwargs)
        return type(self)(out)

    def take(self, indices, axis=0, **kwargs):
        backend = get_backend(kwargs.pop('backend', None))
        out = backend.take(self.data, indices, axis=axis, **kwargs)
        return type(self)(out)

    def hstack(self, *others, **kwargs):
        backend = get_backend(kwargs.pop('backend', None))
        tup = (self,) + others
        out = backend.hstack(tup, **kwargs)
        return type(self)(out)

    def vstack(self, *others, **kwargs):
        backend = get_backend(kwargs.pop('backend', None))
        tup = (self,) + others
        out = backend.vstack(tup, **kwargs)
        return type(self)(out)


class GenotypeChunkedArray(ChunkedArray):
    """TODO

    """

    def __init__(self, data):
        self.check_input_data(data)
        super(GenotypeChunkedArray, self).__init__(data)

    @staticmethod
    def check_input_data(data):
        check_array_like(data)
        if len(data.shape) != 3:
            raise ValueError('expected array-like with 3 dimensions')

    def __getitem__(self, *args):
        out = self.data.__getitem__(*args)
        if is_array_like(out) \
                and len(self.shape) == len(out.shape) \
                and self.shape[2] == out.shape[2]:
            # dimensionality and ploidy preserved
            out = GenotypeArray(out)
            if self.mask is not None:
                # attempt to slice mask too
                m = self.mask.__getitem__(*args)
                out.mask = m
        return out

    def _repr_html_(self):
        return self[:6].to_html_str(caption=repr(self))

    @property
    def n_variants(self):
        return self.shape[0]

    @property
    def n_samples(self):
        return self.shape[1]

    @property
    def ploidy(self):
        return self.shape[2]

    @property
    def n_calls(self):
        """Total number of genotype calls (n_variants * n_samples)."""
        return self.shape[0] * self.shape[1]

    @property
    def n_allele_calls(self):
        """Total number of allele calls (n_variants * n_samples * ploidy)."""
        return self.shape[0] * self.shape[1] * self.shape[2]

    @property
    def mask(self):
        if hasattr(self, '_mask'):
            return self._mask
        else:
            return None

    @mask.setter
    def mask(self, mask):

        # check input
        if not is_array_like(mask):
            mask = np.asarray(mask)
        check_array_like(mask, 2)
        if mask.shape != self.shape[:2]:
            raise ValueError('mask has incorrect shape')

        # store
        self._mask = mask

    def fill_masked(self, value=-1, **kwargs):
        backend = get_backend(kwargs.pop('backend', None))
        
        def mapper(block):
            return block.fill_masked(value=value)
        
        out = backend.map_blocks(self, mapper, **kwargs)
        return GenotypeChunkedArray(out)
    
    def subset(self, variants=None, samples=None, **kwargs):
        backend = get_backend(kwargs.pop('backend', None))
        out = backend.subset(self, variants, samples, **kwargs)
        g = GenotypeChunkedArray(out)
        if self.mask is not None:
            mask = backend.subset(self.mask, variants, samples, **kwargs)
            g.mask = mask
        return g
    
    def is_called(self, **kwargs):
        backend = get_backend(kwargs.pop('backend', None))

        def mapper(block):
            return block.is_called()
        
        out = backend.map_blocks(self, mapper, **kwargs)
        return ChunkedArray(out)

    def is_missing(self, **kwargs):
        backend = get_backend(kwargs.pop('backend', None))

        def mapper(block):
            return block.is_missing()
        
        out = backend.map_blocks(self, mapper, **kwargs)
        return ChunkedArray(out)

    def is_hom(self, allele=None, **kwargs):
        backend = get_backend(kwargs.pop('backend', None))

        def mapper(block):
            return block.is_hom(allele=allele)
        
        out = backend.map_blocks(self, mapper, **kwargs)
        return ChunkedArray(out)

    def is_hom_ref(self, **kwargs):
        backend = get_backend(kwargs.pop('backend', None))

        def mapper(block):
            return block.is_hom_ref()
        
        out = backend.map_blocks(self, mapper, **kwargs)
        return ChunkedArray(out)

    def is_hom_alt(self, **kwargs):
        backend = get_backend(kwargs.pop('backend', None))

        def mapper(block):
            return block.is_hom_alt()
        
        out = backend.map_blocks(self, mapper, **kwargs)
        return ChunkedArray(out)

    def is_het(self, allele=None, **kwargs):
        backend = get_backend(kwargs.pop('backend', None))

        def mapper(block):
            return block.is_het(allele=allele)
        
        out = backend.map_blocks(self, mapper, **kwargs)
        return ChunkedArray(out)

    def is_call(self, call, **kwargs):
        backend = get_backend(kwargs.pop('backend', None))

        def mapper(block):
            return block.is_call(call)
        
        out = backend.map_blocks(self, mapper, **kwargs)
        return ChunkedArray(out)

    def count_called(self, axis=None, **kwargs):
        backend = get_backend(kwargs.pop('backend', None))

        def mapper(block):
            return block.is_called()
        
        out = backend.sum(self, axis=axis, mapper=mapper, **kwargs)
        return out
    
    def count_missing(self, axis=None, **kwargs):
        backend = get_backend(kwargs.pop('backend', None))

        def mapper(block):
            return block.is_missing()
        
        out = backend.sum(self, axis=axis, mapper=mapper, **kwargs)
        return out
    
    def count_hom(self, allele=None, axis=None, **kwargs):
        backend = get_backend(kwargs.pop('backend', None))

        def mapper(block):
            return block.is_hom(allele=allele)
        
        out = backend.sum(self, axis=axis, mapper=mapper, **kwargs)
        return out

    def count_hom_ref(self, axis=None, **kwargs):
        backend = get_backend(kwargs.pop('backend', None))

        def mapper(block):
            return block.is_hom_ref()
        
        out = backend.sum(self, axis=axis, mapper=mapper, **kwargs)
        return out
    
    def count_hom_alt(self, axis=None, **kwargs):
        backend = get_backend(kwargs.pop('backend', None))

        def mapper(block):
            return block.is_hom_alt()
        
        out = backend.sum(self, axis=axis, mapper=mapper, **kwargs)
        return out
    
    def count_het(self, axis=None, **kwargs):
        backend = get_backend(kwargs.pop('backend', None))

        def mapper(block):
            return block.is_het()
        
        out = backend.sum(self, axis=axis, mapper=mapper, **kwargs)
        return out
        
    def count_call(self, call, axis=None, **kwargs):
        backend = get_backend(kwargs.pop('backend', None))

        def mapper(block):
            return block.is_call(call)

        out = backend.sum(self, axis=axis, mapper=mapper, **kwargs)
        return out

    def to_haplotypes(self, **kwargs):
        backend = get_backend(kwargs.pop('backend', None))

        def mapper(block):
            return block.to_haplotypes()
        
        out = backend.map_blocks(self, mapper, **kwargs)
        # TODO wrap with HaplotypeChunkedArray
        return ChunkedArray(out)
        
    def to_n_ref(self, fill=0, dtype='i1', **kwargs):
        backend = get_backend(kwargs.pop('backend', None))

        def mapper(block):
            return block.to_n_ref(fill=fill, dtype=dtype)
        
        out = backend.map_blocks(self, mapper, dtype=dtype, **kwargs)
        return ChunkedArray(out)
        
    def to_n_alt(self, fill=0, dtype='i1', **kwargs):
        backend = get_backend(kwargs.pop('backend', None))

        def mapper(block):
            return block.to_n_alt(fill=fill, dtype=dtype)
        
        out = backend.map_blocks(self, mapper, dtype=dtype, **kwargs)
        return ChunkedArray(out)
        
    def to_allele_counts(self, alleles=None, **kwargs):
        backend = get_backend(kwargs.pop('backend', None))
    
        # determine alleles to count
        if alleles is None:
            m = self.max()
            alleles = list(range(m+1))
            
        def mapper(block):
            return block.to_allele_counts(alleles)
        
        out = backend.map_blocks(self, mapper, **kwargs)
        return ChunkedArray(out)
    
    def to_packed(self, boundscheck=True, **kwargs):
        backend = get_backend(kwargs.pop('backend', None))

        if self.ploidy != 2:
            raise ValueError('can only pack diploid calls')

        if boundscheck:
            amx = self.max()
            if amx > 14:
                raise ValueError('max allele for packing is 14, found %s'
                                 % amx)
            amn = self.min()
            if amn < -1:
                raise ValueError('min allele for packing is -1, found %s'
                                 % amn)

        def mapper(block):
            return block.to_packed(boundscheck=False)

        out = backend.map_blocks(self, mapper, **kwargs)
        return ChunkedArray(out)

    @staticmethod
    def from_packed(packed, **kwargs):
        backend = get_backend(kwargs.pop('backend', None))
        
        # check input
        check_array_like(packed)
        
        def mapper(block):
            return GenotypeArray.from_packed(block)
        
        out = backend.map_blocks(packed, mapper, **kwargs)
        return GenotypeChunkedArray(out)
        
    def count_alleles(self, max_allele=None, subpop=None, **kwargs):
        backend = get_backend(kwargs.pop('backend', None))
        
        # if max_allele not specified, count all alleles
        if max_allele is None:
            max_allele = self.max()

        def mapper(block):
            return block.count_alleles(max_allele=max_allele, subpop=subpop)
        
        out = backend.map_blocks(self, mapper, **kwargs)
        return AlleleCountsChunkedArray(out)
        
    def count_alleles_subpops(self, subpops, max_allele=None, **kwargs):
        backend = get_backend(kwargs.pop('backend', None))

        if max_allele is None:
            max_allele = self.max()

        def mapper(block):
            return block.count_alleles_subpops(subpops, max_allele=max_allele)
            
        out = backend.dict_map_blocks(self, mapper, **kwargs)
        for k, v in out.items():
            out[k] = AlleleCountsChunkedArray(v)
        return out

    def to_gt(self, phased=False, max_allele=None, **kwargs):
        backend = get_backend(kwargs.pop('backend', None))
        
        if max_allele is None:
            max_allele = self.max()

        def mapper(block):
            return block.to_gt(phased=phased, max_allele=max_allele)
            
        out = backend.map_blocks(self, mapper, **kwargs)
        return ChunkedArray(out)

    def map_alleles(self, mapping, **kwargs):
        backend = get_backend(kwargs.pop('backend', None))
        
        # check inputs
        check_array_like(mapping)
        check_dim0_aligned(self, mapping)
        
        # setup output
        kwargs.setdefault('dtype', self.dtype)

        # define mapping function
        def mapper(block, bmapping):
            return block.map_alleles(bmapping, copy=False)

        # execute map
        domain = (self, mapping)
        out = backend.map_blocks(domain, mapper, **kwargs)
        return GenotypeChunkedArray(out)
        
# copy docstrings
copy_method_doc(GenotypeChunkedArray.fill_masked, GenotypeArray.fill_masked)
copy_method_doc(GenotypeChunkedArray.subset, GenotypeArray.subset)
copy_method_doc(GenotypeChunkedArray.is_called, GenotypeArray.is_called)
copy_method_doc(GenotypeChunkedArray.is_missing, GenotypeArray.is_missing)
copy_method_doc(GenotypeChunkedArray.is_hom, GenotypeArray.is_hom)
copy_method_doc(GenotypeChunkedArray.is_hom_ref, GenotypeArray.is_hom_ref)
copy_method_doc(GenotypeChunkedArray.is_hom_alt, GenotypeArray.is_hom_alt)
copy_method_doc(GenotypeChunkedArray.is_het, GenotypeArray.is_het)
copy_method_doc(GenotypeChunkedArray.is_call, GenotypeArray.is_call)
copy_method_doc(GenotypeChunkedArray.to_haplotypes, GenotypeArray.to_haplotypes)
copy_method_doc(GenotypeChunkedArray.to_n_ref, GenotypeArray.to_n_ref)
copy_method_doc(GenotypeChunkedArray.to_n_alt, GenotypeArray.to_n_alt)
copy_method_doc(GenotypeChunkedArray.to_allele_counts,
                GenotypeArray.to_allele_counts)
copy_method_doc(GenotypeChunkedArray.to_packed, GenotypeArray.to_packed)
GenotypeChunkedArray.from_packed.__doc__ = GenotypeArray.from_packed.__doc__
copy_method_doc(GenotypeChunkedArray.count_alleles,
                GenotypeArray.count_alleles)
copy_method_doc(GenotypeChunkedArray.count_alleles_subpops,
                GenotypeArray.count_alleles_subpops)
copy_method_doc(GenotypeChunkedArray.to_gt, GenotypeArray.to_gt)
copy_method_doc(GenotypeChunkedArray.map_alleles, GenotypeArray.map_alleles)
copy_method_doc(GenotypeChunkedArray.hstack, GenotypeArray.hstack)
copy_method_doc(GenotypeChunkedArray.vstack, GenotypeArray.vstack)


class AlleleCountsChunkedArray(ChunkedArray):

    @property
    def n_variants(self):
        """Number of variants (length of first array dimension)."""
        return self.shape[0]

    @property
    def n_alleles(self):
        """Number of alleles (length of second array dimension)."""
        return self.shape[1]
