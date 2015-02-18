# -*- coding: utf-8 -*-
"""
TODO doco

"""
from __future__ import absolute_import, print_function, division


import operator
from allel.compat import range


import numpy as np
import bcolz


from allel.model import GenotypeArray, HaplotypeArray, AlleleCountsArray
from allel.constants import DIM_PLOIDY
from allel.util import asarray_ndim


__all__ = ['GenotypeCArray', 'HaplotypeCArray', 'AlleleCountsCArray']


def _block_append(f, data, out, bs=None):
    if bs is None:
        bs = data.chunklen
    for i in range(0, data.shape[0], bs):
        block = data[i:i+bs]
        out.append(f(block))


def _block_sum(data, axis=None, f=None):
    bs = data.chunklen

    if axis is None:
        out = 0
        for i in range(0, data.shape[0], bs):
            block = data[i:i+bs]
            if f:
                block = f(block)
            out += np.sum(block)
        return out

    elif axis == 0:
        out = np.zeros((data.shape[1],), dtype=int)
        for i in range(0, data.shape[0], bs):
            block = data[i:i+bs]
            if f:
                block = f(block)
            out += np.sum(block, axis=0)
        return out

    elif axis == 1:
        out = np.zeros((data.shape[0],), dtype=int)
        for i in range(0, data.shape[0], bs):
            block = data[i:i+bs]
            if f:
                block = f(block)
            out[i:i+bs] += np.sum(block, axis=1)
        return out


def _block_max(data, axis=None):
    bs = data.chunklen
    out = None

    if axis is None:
        for i in range(0, data.shape[0], bs):
            block = data[i:i+bs]
            m = np.max(block)
            if out is None:
                out = m
            else:
                out = m if m > out else out
        return out

    elif axis == 0 or axis == (0, 2):
        for i in range(0, data.shape[0], bs):
            block = data[i:i+bs]
            m = np.max(block, axis=axis)
            if out is None:
                out = m
            else:
                out = np.where(m > out, m, out)
        return out

    elif axis == 1 or axis == (1, 2):
        out = np.zeros((data.shape[0],), dtype=int)
        for i in range(0, data.shape[0], bs):
            block = data[i:i+bs]
            out[i:i+bs] = np.max(block, axis=axis)
        return out

    else:
        raise NotImplementedError('axis not supported: %s' % axis)


def _block_min(data, axis=None):
    bs = data.chunklen
    out = None

    if axis is None:
        for i in range(0, data.shape[0], bs):
            block = data[i:i+bs]
            m = np.min(block)
            if out is None:
                out = m
            else:
                out = m if m < out else out
        return out

    elif axis == 0 or axis == (0, 2):
        for i in range(0, data.shape[0], bs):
            block = data[i:i+bs]
            m = np.min(block, axis=axis)
            if out is None:
                out = m
            else:
                out = np.where(m < out, m, out)
        return out

    elif axis == 1 or axis == (1, 2):
        out = np.zeros((data.shape[0],), dtype=int)
        for i in range(0, data.shape[0], bs):
            block = data[i:i+bs]
            out[i:i+bs] = np.min(block, axis=axis)
        return out

    else:
        raise NotImplementedError('axis not supported: %s' % axis)


def _block_compress(condition, data, axis, **kwargs):

    # check inputs
    condition = asarray_ndim(condition, 1)
    if axis not in {0, 1}:
        raise NotImplementedError('only axis 0 (variants) or 1 (samples) '
                                  'supported')

    if axis == 0:
        if condition.size != data.shape[0]:
            raise ValueError('length of condition must match length of '
                             'first dimension; expected %s, found %s' %
                             (data.shape[0], condition.size))

        # setup output
        kwargs.setdefault('dtype', data.dtype)
        kwargs.setdefault('expectedlen', np.count_nonzero(condition))
        out = bcolz.zeros((0,) + data.shape[1:], **kwargs)

        # build output
        bs = data.chunklen
        for i in range(0, data.shape[0], bs):
            block = data[i:i+bs]
            vcond = condition[i:i+bs]
            out.append(np.compress(vcond, block, axis=0))

        return out

    elif axis == 1:
        if condition.size != data.shape[1]:
            raise ValueError('length of condition must match length of '
                             'second dimension; expected %s, found %s' %
                             (data.shape[1], condition.size))

        # setup output
        kwargs.setdefault('dtype', data.dtype)
        kwargs.setdefault('expectedlen', data.shape[0])
        out = bcolz.zeros((0, np.count_nonzero(condition)) + data.shape[2:],
                          **kwargs)

        # build output
        bs = data.chunklen
        for i in range(0, data.shape[0], bs):
            block = data[i:i+bs]
            out.append(np.compress(condition, block, axis=1))

        return out


def _block_take(data, indices, axis, **kwargs):

    # check inputs
    indices = asarray_ndim(indices, 1)
    if axis not in {0, 1}:
        raise NotImplementedError('only axis 0 (variants) or 1 (samples) '
                                  'supported')

    if axis == 0:
        condition = np.zeros((data.shape[0],), dtype=bool)
        condition[indices] = True
        return _block_compress(condition, data, axis=0, **kwargs)

    elif axis == 1:
        condition = np.zeros((data.shape[1],), dtype=bool)
        condition[indices] = True
        return _block_compress(condition, data, axis=1, **kwargs)


def _block_subset(cls, data, sel0, sel1, **kwargs):

    # check inputs
    sel0 = asarray_ndim(sel0, 1, allow_none=True)
    sel1 = asarray_ndim(sel1, 1, allow_none=True)
    if sel0 is None and sel1 is None:
        raise ValueError('missing selection')

    # if either selection is None, use take/compress
    if sel1 is None:
        if sel0.size < data.shape[0]:
            return _block_take(data, sel0, axis=0, **kwargs)
        else:
            return _block_compress(sel0, data, axis=0, **kwargs)
    elif sel0 is None:
        if sel1.size < data.shape[1]:
            return _block_take(data, sel1, axis=1, **kwargs)
        else:
            return _block_compress(sel1, data, axis=1, **kwargs)

    # ensure boolean array for variants
    if sel0.size < data.shape[0]:
        tmp = np.zeros((data.shape[0],), dtype=bool)
        tmp[sel0] = True
        sel0 = tmp

    # ensure indices for samples/haplotypes
    if sel1.size == data.shape[1]:
        sel1 = np.nonzero(sel1)[0]

    # setup output
    kwargs.setdefault('dtype', data.dtype)
    kwargs.setdefault('expectedlen', np.count_nonzero(sel0))
    out = bcolz.zeros((0, sel1.size) + data.shape[2:], **kwargs)

    # build output
    bs = data.chunklen
    for i in range(0, data.shape[0], bs):
        block = data[i:i+bs]
        bsel0 = sel0[i:i+bs]
        x = cls(block, copy=False)
        out.append(x.subset(bsel0, sel1))

    return out


class _CArrayWrapper(object):

    def __setitem__(self, key, value):
        self.data[key] = value

    def __getattr__(self, item):
        return getattr(self.data, item)

    def __setattr__(self, key, value):
        setattr(self.data, key, value)

    def __array__(self):
        return self.data[:]

    def __repr__(self):
        s = repr(self.data)
        s = type(self).__name__ + s[6:]
        return s

    def max(self, axis=None):
        return _block_max(self.data, axis=axis)

    def min(self, axis=None):
        return _block_min(self.data, axis=axis)

    def compare_scalar(self, op, other, **kwargs):
        if not np.isscalar(other):
            raise NotImplementedError('only supported for scalars')

        # setup output
        kwargs.setdefault('dtype', bool)
        kwargs.setdefault('expectedlen', self.shape[0])
        out = bcolz.zeros((0,) + self.shape[1:], **kwargs)

        # build output
        f = lambda data: op(data, other)
        _block_append(f, self.data, out)

        return out

    def __eq__(self, other):
        return self.compare_scalar(operator.eq, other)

    def __ne__(self, other):
        return self.compare_scalar(operator.ne, other)

    def __lt__(self, other):
        return self.compare_scalar(operator.lt, other)

    def __gt__(self, other):
        return self.compare_scalar(operator.gt, other)

    def __le__(self, other):
        return self.compare_scalar(operator.le, other)

    def __ge__(self, other):
        return self.compare_scalar(operator.ge, other)

    @classmethod
    def from_hdf5(cls, *args, **kwargs):
        import h5py

        h5f = None

        if len(args) == 1:
            dataset = args[0]

        elif len(args) == 2:
            file_path, node_path = args
            h5f = h5py.File(file_path, mode='r')
            try:
                dataset = h5f[node_path]
            except:
                h5f.close()
                raise

        else:
            raise ValueError('bad arguments; expected dataset or (file_path, '
                             'node_path), found %s' % repr(args))

        try:

            # check input dataset
            cls.check_input_data(dataset)
            start = kwargs.pop('start', 0)
            stop = kwargs.pop('stop', dataset.shape[0])
            step = kwargs.pop('step', 1)

            # setup output data
            kwargs.setdefault('expectedlen', dataset.shape[0])
            kwargs.setdefault('dtype', dataset.dtype)
            data = bcolz.zeros((0,) + dataset.shape[1:], **kwargs)

            # load block-wise
            bs = dataset.chunks[0]
            for i in range(start, stop, bs):
                j = min(i + bs, stop)
                data.append(dataset[i:j:step])

            return cls(data, copy=False)

        finally:
            if h5f is not None:
                h5f.close()


class GenotypeCArray(_CArrayWrapper):
    """TODO doco

    """

    @staticmethod
    def check_input_data(obj):

        # check dtype
        if obj.dtype.kind not in 'ui':
            raise TypeError('integer dtype required')

        # check dimensionality
        if hasattr(obj, 'ndim'):
            ndim = obj.ndim
        else:
            ndim = len(obj.shape)
        if ndim != 3:
            raise TypeError('array with 3 dimensions required')

        # check length of ploidy dimension
        if obj.shape[DIM_PLOIDY] == 1:
            raise ValueError('use HaplotypeCArray for haploid calls')

    def __init__(self, data, copy=True, **kwargs):
        if copy or not isinstance(data, bcolz.carray):
            data = bcolz.carray(data, **kwargs)
        # check late to avoid creating an intermediate numpy array
        self.check_input_data(data)
        object.__setattr__(self, 'data', data)

    def __getitem__(self, *args):
        out = self.data.__getitem__(*args)
        if hasattr(out, 'ndim') and out.ndim == 3:
            out = GenotypeArray(out, copy=False)
        return out

    @property
    def n_variants(self):
        return self.data.shape[0]

    @property
    def n_samples(self):
        return self.data.shape[1]

    @property
    def ploidy(self):
        return self.data.shape[2]

    def compress(self, condition, axis, **kwargs):
        data = _block_compress(condition, self.data, axis, **kwargs)
        return GenotypeCArray(data, copy=False)

    def take(self, indices, axis, **kwargs):
        data = _block_take(self.data, indices, axis, **kwargs)
        return GenotypeCArray(data, copy=False)

    def subset(self, variants, samples, **kwargs):
        data = _block_subset(GenotypeArray, self.data, variants, samples,
                             **kwargs)
        return GenotypeCArray(data, copy=False)

    def is_called(self, **kwargs):

        # setup output
        kwargs.setdefault('dtype', bool)
        kwargs.setdefault('expectedlen', self.n_variants)
        out = bcolz.zeros((0, self.n_samples), **kwargs)

        # build output
        f = lambda data: GenotypeArray(data, copy=False).is_called()
        _block_append(f, self.data, out)

        return out

    def is_missing(self, **kwargs):

        # setup output
        kwargs.setdefault('dtype', bool)
        kwargs.setdefault('expectedlen', self.n_variants)
        out = bcolz.zeros((0, self.n_samples), **kwargs)

        # build output
        f = lambda data: GenotypeArray(data, copy=False).is_missing()
        _block_append(f, self.data, out)

        return out

    def is_hom(self, allele=None, **kwargs):

        # setup output
        kwargs.setdefault('dtype', bool)
        kwargs.setdefault('expectedlen', self.n_variants)
        out = bcolz.zeros((0, self.n_samples), **kwargs)

        # build output
        f = lambda data: GenotypeArray(data, copy=False).is_hom(allele=allele)
        _block_append(f, self.data, out)

        return out

    def is_hom_ref(self, **kwargs):

        # setup output
        kwargs.setdefault('dtype', bool)
        kwargs.setdefault('expectedlen', self.n_variants)
        out = bcolz.zeros((0, self.n_samples), **kwargs)

        # build output
        f = lambda data: GenotypeArray(data, copy=False).is_hom_ref()
        _block_append(f, self.data, out)

        return out

    def is_hom_alt(self, **kwargs):

        # setup output
        kwargs.setdefault('dtype', bool)
        kwargs.setdefault('expectedlen', self.n_variants)
        out = bcolz.zeros((0, self.n_samples), **kwargs)

        # build output
        f = lambda data: GenotypeArray(data, copy=False).is_hom_alt()
        _block_append(f, self.data, out)

        return out

    def is_het(self, **kwargs):

        # setup output
        kwargs.setdefault('dtype', bool)
        kwargs.setdefault('expectedlen', self.n_variants)
        out = bcolz.zeros((0, self.n_samples), **kwargs)

        # build output
        f = lambda data: GenotypeArray(data, copy=False).is_het()
        _block_append(f, self.data, out)

        return out

    def is_call(self, call, **kwargs):

        # setup output
        kwargs.setdefault('dtype', bool)
        kwargs.setdefault('expectedlen', self.n_variants)
        out = bcolz.zeros((0, self.n_samples), **kwargs)

        # build output
        f = lambda data: GenotypeArray(data, copy=False).is_call(call)
        _block_append(f, self.data, out)

        return out

    def count_called(self, axis=None):
        f = lambda block: GenotypeArray(block, copy=False).is_called()
        return _block_sum(self.data, axis=axis, f=f)

    def count_missing(self, axis=None):
        f = lambda block: GenotypeArray(block, copy=False).is_missing()
        return _block_sum(self.data, axis=axis, f=f)

    def count_hom(self, allele=None, axis=None):
        def f(block):
            g = GenotypeArray(block, copy=False)
            return g.is_hom(allele=allele)
        return _block_sum(self.data, axis=axis, f=f)

    def count_hom_ref(self, axis=None):
        f = lambda block: GenotypeArray(block, copy=False).is_hom_ref()
        return _block_sum(self.data, axis=axis, f=f)

    def count_hom_alt(self, axis=None):
        f = lambda block: GenotypeArray(block, copy=False).is_hom_alt()
        return _block_sum(self.data, axis=axis, f=f)

    def count_het(self, axis=None):
        f = lambda block: GenotypeArray(block, copy=False).is_het()
        return _block_sum(self.data, axis=axis, f=f)

    def count_call(self, call, axis=None):
        def f(block):
            g = GenotypeArray(block, copy=False)
            return g.is_call(call=call)
        return _block_sum(self.data, axis=axis, f=f)

    def to_haplotypes(self, **kwargs):
        # Unfortunately this cannot be implemented as a lightweight view,
        # so we have to copy.

        # setup output
        kwargs.setdefault('dtype', self.data.dtype)
        kwargs.setdefault('expectedlen', self.n_variants)
        out = bcolz.zeros((0, self.n_samples * self.ploidy), **kwargs)

        # build output
        f = lambda block: block.reshape((block.shape[0], -1))
        _block_append(f, self.data, out)

        h = HaplotypeCArray(out, copy=False)
        return h

    def to_n_alt(self, fill=0, **kwargs):

        # setup output
        kwargs.setdefault('dtype', 'i1')
        kwargs.setdefault('expectedlen', self.n_variants)
        out = bcolz.zeros((0, self.n_samples), **kwargs)

        # build output
        f = lambda data: GenotypeArray(data, copy=False).to_n_alt(fill)
        _block_append(f, self.data, out)

        return out

    def to_allele_counts(self, alleles=None, **kwargs):

        # determine alleles to count
        if alleles is None:
            m = self.max()
            alleles = list(range(m+1))

        # set up output
        kwargs.setdefault('dtype', 'u1')
        kwargs.setdefault('expectedlen', self.n_variants)
        out = bcolz.zeros((0, self.n_samples, len(alleles)), **kwargs)

        # build output
        def f(block):
            g = GenotypeArray(block, copy=False)
            return g.to_allele_counts(alleles)

        _block_append(f, self.data, out)

        return out

    def to_packed(self, boundscheck=True, **kwargs):

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

        # set up output
        kwargs['dtype'] = 'u1'  # force dtype
        kwargs.setdefault('expectedlen', self.n_variants)
        out = bcolz.zeros((0, self.n_samples), **kwargs)

        # build output
        def f(block):
            g = GenotypeArray(block, copy=False)
            return g.to_packed(boundscheck=False)

        _block_append(f, self.data, out)

        return out

    @staticmethod
    def from_packed(packed, **kwargs):

        # check input
        if not isinstance(packed, (np.ndarray, bcolz.carray)):
            packed = np.asarray(packed)

        # set up output
        kwargs.setdefault('dtype', 'i1')
        kwargs.setdefault('expectedlen', packed.shape[0])
        out = bcolz.zeros((0, packed.shape[1], 2), **kwargs)
        bs = out.chunklen

        # build output
        def f(block):
            return GenotypeArray.from_packed(block)
        _block_append(f, packed, out, bs)

        return GenotypeCArray(out, copy=False)

    def count_alleles(self, max_allele=None, **kwargs):

        # if max_allele not specified, count all alleles
        if max_allele is None:
            max_allele = self.max()

        # setup output
        kwargs.setdefault('expectedlen', self.n_variants)
        kwargs.setdefault('dtype', int)
        out = bcolz.zeros((0, max_allele + 1), **kwargs)

        def f(block):
            g = GenotypeArray(block, copy=False)
            return g.count_alleles(max_allele=max_allele)
        _block_append(f, self.data, out)

        return AlleleCountsCArray(out, copy=False)


class HaplotypeCArray(_CArrayWrapper):
    """TODO doco

    """

    @staticmethod
    def check_input_data(obj):

        # check dtype
        if obj.dtype.kind not in 'ui':
            raise TypeError('integer dtype required')

        # check dimensionality
        if hasattr(obj, 'ndim'):
            ndim = obj.ndim
        else:
            ndim = len(obj.shape)
        if ndim != 2:
            raise TypeError('array with 2 dimensions required')

    def __init__(self, data, copy=True, **kwargs):
        if copy or not isinstance(data, bcolz.carray):
            data = bcolz.carray(data, **kwargs)
        # check late to avoid creating an intermediate numpy array
        self.check_input_data(data)
        object.__setattr__(self, 'data', data)

    def __getitem__(self, *args):
        out = self.data.__getitem__(*args)
        if hasattr(out, 'ndim') and out.ndim == 2:
            out = HaplotypeArray(out, copy=False)
        return out

    @property
    def n_variants(self):
        """Number of variants (length of first array dimension)."""
        return self.data.shape[0]

    @property
    def n_haplotypes(self):
        """Number of haplotypes (length of second array dimension)."""
        return self.data.shape[1]

    def compress(self, condition, axis, **kwargs):
        data = _block_compress(condition, self.data, axis, **kwargs)
        return HaplotypeCArray(data, copy=False)

    def take(self, indices, axis, **kwargs):
        data = _block_take(self.data, indices, axis, **kwargs)
        return HaplotypeCArray(data, copy=False)

    def subset(self, variants, haplotypes, **kwargs):
        data = _block_subset(HaplotypeArray, self.data, variants, haplotypes,
                             **kwargs)
        return HaplotypeCArray(data, copy=False)

    def to_genotypes(self, ploidy, **kwargs):
        # Unfortunately this cannot be implemented as a lightweight view,
        # so we have to copy.

        # check ploidy is compatible
        if (self.n_haplotypes % ploidy) > 0:
            raise ValueError('incompatible ploidy')

        # setup output
        n_samples = self.n_haplotypes / ploidy
        kwargs.setdefault('dtype', self.data.dtype)
        kwargs.setdefault('expectedlen', self.n_variants)
        out = bcolz.zeros((0, n_samples, ploidy), **kwargs)

        # build output
        f = lambda block: block.reshape((block.shape[0], -1, ploidy))
        _block_append(f, self.data, out)

        g = GenotypeCArray(out, copy=False)
        return g

    def is_called(self, **kwargs):
        return self.compare_scalar(operator.ge, 0, **kwargs)

    def is_missing(self, **kwargs):
        return self.compare_scalar(operator.lt, 0, **kwargs)

    def is_ref(self, **kwargs):
        return self.compare_scalar(operator.eq, 0, **kwargs)

    def is_alt(self, allele=None, **kwargs):
        if allele is None:
            return self.compare_scalar(operator.gt, 0, **kwargs)
        else:
            return self.compare_scalar(operator.eq, allele, **kwargs)

    def is_call(self, allele, **kwargs):
        return self.compare_scalar(operator.eq, allele, **kwargs)

    def count_called(self, axis=None):
        f = lambda block: HaplotypeArray(block, copy=False).is_called()
        return _block_sum(self.data, axis=axis, f=f)

    def count_missing(self, axis=None):
        f = lambda block: HaplotypeArray(block, copy=False).is_missing()
        return _block_sum(self.data, axis=axis, f=f)

    def count_ref(self, axis=None):
        f = lambda block: HaplotypeArray(block, copy=False).is_ref()
        return _block_sum(self.data, axis=axis, f=f)

    def count_alt(self, axis=None):
        f = lambda block: HaplotypeArray(block, copy=False).is_alt()
        return _block_sum(self.data, axis=axis, f=f)

    def count_call(self, allele, axis=None):
        def f(block):
            h = HaplotypeArray(block, copy=False)
            return h.is_call(allele=allele)
        return _block_sum(self.data, axis=axis, f=f)

    def count_alleles(self, max_allele=None, **kwargs):

        # if max_allele not specified, count all alleles
        if max_allele is None:
            max_allele = self.max()

        # setup output
        kwargs.setdefault('expectedlen', self.n_variants)
        kwargs.setdefault('dtype', int)
        out = bcolz.zeros((0, max_allele + 1), **kwargs)

        def f(block):
            h = HaplotypeArray(block, copy=False)
            return h.count_alleles(max_allele=max_allele)
        _block_append(f, self.data, out)

        return AlleleCountsCArray(out, copy=False)


class AlleleCountsCArray(_CArrayWrapper):
    """TODO doco

    """

    @staticmethod
    def check_input_data(obj):

        # check dtype
        if obj.dtype.kind not in 'ui':
            raise TypeError('integer dtype required')

        # check dimensionality
        if hasattr(obj, 'ndim'):
            ndim = obj.ndim
        else:
            ndim = len(obj.shape)
        if ndim != 2:
            raise TypeError('array with 2 dimensions required')

    def __init__(self, data, copy=True, **kwargs):
        if copy or not isinstance(data, bcolz.carray):
            data = bcolz.carray(data, **kwargs)
        # check late to avoid creating an intermediate numpy array
        self.check_input_data(data)
        object.__setattr__(self, 'data', data)

    def __getitem__(self, *args):
        out = self.data.__getitem__(*args)
        if hasattr(out, 'ndim') \
                and out.ndim == 2 \
                and out.shape[1] == self.n_alleles:
            # wrap only if number of alleles is preserved
            out = AlleleCountsArray(out, copy=False)
        return out

    @property
    def n_variants(self):
        """Number of variants (length of first array dimension)."""
        return self.data.shape[0]

    @property
    def n_alleles(self):
        """Number of alleles (length of second array dimension)."""
        return self.data.shape[1]

    def compress(self, condition, axis, **kwargs):
        data = _block_compress(condition, self.data, axis, **kwargs)
        if data.shape[1] == self.shape[1]:
            return AlleleCountsCArray(data, copy=False)
        else:
            return data

    def take(self, indices, axis, **kwargs):
        data = _block_take(self.data, indices, axis, **kwargs)
        if data.shape[1] == self.shape[1]:
            return AlleleCountsCArray(data, copy=False)
        else:
            return data

    def to_frequencies(self, fill=np.nan, **kwargs):
        kwargs.setdefault('expectedlen', self.n_variants)
        kwargs.setdefault('dtype', float)
        out = bcolz.zeros((0, self.n_alleles), **kwargs)

        def f(block):
            ac = AlleleCountsArray(block, copy=False)
            return ac.to_frequencies(fill=fill)
        _block_append(f, self.data, out)

        return out

    def allelism(self, **kwargs):
        kwargs.setdefault('expectedlen', self.n_variants)
        kwargs.setdefault('dtype', int)
        out = bcolz.zeros((0,), **kwargs)

        def f(block):
            return AlleleCountsArray(block, copy=False).allelism()
        _block_append(f, self.data, out)
        return out

    def is_variant(self, **kwargs):
        kwargs.setdefault('expectedlen', self.n_variants)
        kwargs.setdefault('dtype', bool)
        out = bcolz.zeros((0,), **kwargs)

        def f(block):
            ac = AlleleCountsArray(block, copy=False)
            return ac.is_variant()
        _block_append(f, self.data, out)

        return out

    def is_non_variant(self, **kwargs):
        kwargs.setdefault('expectedlen', self.n_variants)
        kwargs.setdefault('dtype', bool)
        out = bcolz.zeros((0,), **kwargs)

        def f(block):
            ac = AlleleCountsArray(block, copy=False)
            return ac.is_non_variant()
        _block_append(f, self.data, out)

        return out

    def is_segregating(self, **kwargs):
        kwargs.setdefault('expectedlen', self.n_variants)
        kwargs.setdefault('dtype', bool)
        out = bcolz.zeros((0,), **kwargs)

        def f(block):
            ac = AlleleCountsArray(block, copy=False)
            return ac.is_segregating()
        _block_append(f, self.data, out)

        return out

    def is_non_segregating(self, allele=None, **kwargs):
        kwargs.setdefault('expectedlen', self.n_variants)
        kwargs.setdefault('dtype', bool)
        out = bcolz.zeros((0,), **kwargs)

        def f(block):
            ac = AlleleCountsArray(block, copy=False)
            return ac.is_non_segregating(allele=allele)
        _block_append(f, self.data, out)

        return out

    def is_singleton(self, allele=1, **kwargs):
        kwargs.setdefault('expectedlen', self.n_variants)
        kwargs.setdefault('dtype', bool)
        out = bcolz.zeros((0,), **kwargs)

        def f(block):
            ac = AlleleCountsArray(block, copy=False)
            return ac.is_singleton(allele=allele)
        _block_append(f, self.data, out)

        return out

    def is_doubleton(self, allele=1, **kwargs):
        kwargs.setdefault('expectedlen', self.n_variants)
        kwargs.setdefault('dtype', bool)
        out = bcolz.zeros((0,), **kwargs)

        def f(block):
            ac = AlleleCountsArray(block, copy=False)
            return ac.is_doubleton(allele=allele)
        _block_append(f, self.data, out)

        return out

    def count_variant(self):
        return _block_sum(self.is_variant())

    def count_non_variant(self):
        return _block_sum(self.is_non_variant())

    def count_segregating(self):
        return _block_sum(self.is_segregating())

    def count_non_segregating(self, allele=None):
        return _block_sum(self.is_non_segregating(allele=allele))

    def count_singleton(self, allele=1):
        return _block_sum(self.is_singleton(allele=allele))

    def count_doubleton(self, allele=1):
        return _block_sum(self.is_doubleton(allele=allele))
