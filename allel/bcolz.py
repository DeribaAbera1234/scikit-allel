# -*- coding: utf-8 -*-
"""
TODO doco

"""
from __future__ import absolute_import, print_function, division


import operator
from allel.compat import range, reduce, integer_types


import numpy as np
import bcolz


from allel.model import GenotypeArray, HaplotypeArray
from allel.constants import DIM_PLOIDY
from allel.util import asarray_ndim


__all__ = ['GenotypeCArray', 'HaplotypeCArray']


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


def _block_compress(condition, data, axis):

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
        out = bcolz.zeros((0,) + data.shape[1:],
                          dtype=data.dtype,
                          expectedlen=np.count_nonzero(condition))

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
        out = bcolz.zeros((0, np.count_nonzero(condition)) + data.shape[2:],
                          dtype=data.dtype,
                          expectedlen=data.shape[0])

        # build output
        bs = data.chunklen
        for i in range(0, data.shape[0], bs):
            block = data[i:i+bs]
            out.append(np.compress(condition, block, axis=1))

        return out


def _block_take(data, indices, axis):

    # check inputs
    indices = asarray_ndim(indices, 1)
    if axis not in {0, 1}:
        raise NotImplementedError('only axis 0 (variants) or 1 (samples) '
                                  'supported')

    if axis == 0:
        condition = np.zeros((data.shape[0],), dtype=bool)
        condition[indices] = True
        return _block_compress(condition, data, axis=0)

    elif axis == 1:
        condition = np.zeros((data.shape[1],), dtype=bool)
        condition[indices] = True
        return _block_compress(condition, data, axis=1)


def _block_subset(cls, data, sel0, sel1):

    # check inputs
    sel0 = asarray_ndim(sel0, 1, allow_none=True)
    sel1 = asarray_ndim(sel1, 1, allow_none=True)
    if sel0 is None and sel1 is None:
        raise ValueError('missing selection')

    # if either selection is None, use take/compress
    if sel1 is None:
        if sel0.size < data.shape[0]:
            return _block_take(data, sel0, axis=0)
        else:
            return _block_compress(sel0, data, axis=0)
    elif sel0 is None:
        if sel1.size < data.shape[1]:
            return _block_take(data, sel1, axis=1)
        else:
            return _block_compress(sel1, data, axis=1)

    # ensure boolean array for variants
    if sel0.size < data.shape[0]:
        tmp = np.zeros((data.shape[0],), dtype=bool)
        tmp[sel0] = True
        sel0 = tmp

    # ensure indices for samples/haplotypes
    if sel1.size == data.shape[1]:
        sel1 = np.nonzero(sel1)[0]

    # setup output
    out = bcolz.zeros((0, sel1.size) + data.shape[2:],
                      dtype=data.dtype,
                      expectedlen=np.count_nonzero(sel0))

    # build output
    bs = data.chunklen
    for i in range(0, data.shape[0], bs):
        block = data[i:i+bs]
        bsel0 = sel0[i:i+bs]
        x = cls(block, copy=False)
        out.append(x.subset(bsel0, sel1))

    return out


class GenotypeCArray(object):
    """TODO doco

    """

    @staticmethod
    def _check_input_data(obj):

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
        self._check_input_data(data)
        self.data = data

    def __getitem__(self, *args):
        out = self.data.__getitem__(*args)
        if hasattr(out, 'ndim') and out.ndim == 3:
            out = GenotypeArray(out, copy=False)
        return out

    def __array__(self):
        return self.data[:]

    @property
    def ndim(self):
        return self.data.ndim

    @property
    def shape(self):
        return self.data.shape

    @property
    def dtype(self):
        return self.data.dtype

    @property
    def nbytes(self):
        return self.data.nbytes

    @property
    def cbytes(self):
        return self.data.cbytes

    @property
    def chunklen(self):
        return self.data.chunklen

    @property
    def cparams(self):
        return self.data.cparams

    @property
    def n_variants(self):
        return self.data.shape[0]

    @property
    def n_samples(self):
        return self.data.shape[1]

    @property
    def ploidy(self):
        return self.data.shape[2]

    def __repr__(self):
        s = repr(self.data)
        s = 'GenotypeCArray' + s[6:]
        return s

    def compress(self, condition, axis):
        data = _block_compress(condition, self.data, axis)
        return GenotypeCArray(data, copy=False)

    def take(self, indices, axis):
        data = _block_take(self.data, indices, axis)
        return GenotypeCArray(data, copy=False)

    def subset(self, variants, samples):
        data = _block_subset(GenotypeArray, self.data, variants, samples)
        return GenotypeCArray(data, copy=False)

    def max(self, axis=None):
        return _block_max(self.data, axis=axis)

    def min(self, axis=None):
        return _block_min(self.data, axis=axis)

    def is_called(self):

        # setup output
        out = bcolz.zeros((0, self.n_samples),
                          dtype='u1',
                          expectedlen=self.n_variants)

        # build output
        f = lambda data: GenotypeArray(data, copy=False).is_called()
        _block_append(f, self.data, out)

        return out

    def is_missing(self):

        # setup output
        out = bcolz.zeros((0, self.n_samples),
                          dtype='u1',
                          expectedlen=self.n_variants)

        # build output
        f = lambda data: GenotypeArray(data, copy=False).is_missing()
        _block_append(f, self.data, out)

        return out

    def is_hom(self, allele=None):

        # setup output
        out = bcolz.zeros((0, self.n_samples),
                          dtype='u1',
                          expectedlen=self.n_variants)

        # build output
        f = lambda data: GenotypeArray(data, copy=False).is_hom(allele=allele)
        _block_append(f, self.data, out)

        return out

    def is_hom_ref(self):

        # setup output
        out = bcolz.zeros((0, self.n_samples),
                          dtype='u1',
                          expectedlen=self.n_variants)

        # build output
        f = lambda data: GenotypeArray(data, copy=False).is_hom_ref()
        _block_append(f, self.data, out)

        return out

    def is_hom_alt(self):

        # setup output
        out = bcolz.zeros((0, self.n_samples),
                          dtype='u1',
                          expectedlen=self.n_variants)

        # build output
        f = lambda data: GenotypeArray(data, copy=False).is_hom_alt()
        _block_append(f, self.data, out)

        return out

    def is_het(self):

        # setup output
        out = bcolz.zeros((0, self.n_samples),
                          dtype='u1',
                          expectedlen=self.n_variants)

        # build output
        f = lambda data: GenotypeArray(data, copy=False).is_het()
        _block_append(f, self.data, out)

        return out

    def is_call(self, call):

        # setup output
        out = bcolz.zeros((0, self.n_samples),
                          dtype='u1',
                          expectedlen=self.n_variants)

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

    def view_haplotypes(self):
        # Unfortunately this cannot be implemented as a lightweight view,
        # so we have to copy.

        # setup output
        out = bcolz.zeros((0, self.n_samples * self.ploidy),
                          dtype=self.data.dtype,
                          chunklen=self.data.chunklen)

        # build output
        f = lambda block: block.reshape((block.shape[0], -1))
        _block_append(f, self.data, out)

        h = HaplotypeCArray(out, copy=False)
        return h

    def to_n_alt(self, fill=0):

        # setup output
        out = bcolz.zeros((0, self.n_samples),
                          dtype='i1',
                          expectedlen=self.n_variants)

        # build output
        f = lambda data: GenotypeArray(data, copy=False).to_n_alt(fill)
        _block_append(f, self.data, out)

        return out

    def to_allele_counts(self, alleles=None):

        # determine alleles to count
        if alleles is None:
            m = self.max()
            alleles = list(range(m+1))

        # set up output
        out = bcolz.zeros((0, self.n_samples, len(alleles)),
                          dtype='u1',
                          expectedlen=self.n_variants)

        # build output
        def f(block):
            g = GenotypeArray(block, copy=False)
            return g.to_allele_counts(alleles)

        _block_append(f, self.data, out)

        return out

    def to_packed(self, boundscheck=True):

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
        out = bcolz.zeros((0, self.n_samples),
                          dtype='u1',
                          expectedlen=self.n_variants)

        # build output
        def f(block):
            g = GenotypeArray(block, copy=False)
            return g.to_packed(boundscheck=False)

        _block_append(f, self.data, out)

        return out

    @staticmethod
    def from_packed(packed):

        # check input
        if not isinstance(packed, (np.ndarray, bcolz.carray)):
            packed = np.asarray(packed)

        # set up output
        out = bcolz.zeros((0, packed.shape[1], 2),
                          dtype='i1',
                          expectedlen=packed.shape[0])
        bs = out.chunklen

        # build output
        def f(block):
            return GenotypeArray.from_packed(block)
        _block_append(f, packed, out, bs)

        return GenotypeCArray(out, copy=False)

    def allelism(self):
        out = bcolz.zeros((0,), dtype=int)

        def f(block):
            return GenotypeArray(block, copy=False).allelism()
        _block_append(f, self.data, out)
        return out

    def allele_number(self):
        out = bcolz.zeros((0,), dtype=int)

        def f(block):
            return GenotypeArray(block, copy=False).allele_number()
        _block_append(f, self.data, out)
        return out

    def allele_count(self, allele=1):
        out = bcolz.zeros((0,), dtype=int)

        def f(block):
            return GenotypeArray(block, copy=False).allele_count(allele=allele)
        _block_append(f, self.data, out)
        return out

    def allele_frequency(self, allele=1, fill=np.nan):
        out = bcolz.zeros((0,), dtype=float)

        def f(block):
            g = GenotypeArray(block, copy=False)
            af = g.allele_frequency(allele=allele, fill=fill)
            return af
        _block_append(f, self.data, out)

        return out

    def allele_counts(self, alleles=None):

        # if alleles not specified, count all alleles
        if alleles is None:
            m = self.max()
            alleles = list(range(m+1))

        # setup output
        out = bcolz.zeros((0, len(alleles)), dtype=int)

        def f(block):
            g = GenotypeArray(block, copy=False)
            return g.allele_counts(alleles=alleles)
        _block_append(f, self.data, out)

        return out

    def allele_frequencies(self, alleles=None, fill=np.nan):

        # if alleles not specified, count all alleles
        if alleles is None:
            m = self.max()
            alleles = list(range(m+1))

        # setup output
        out = bcolz.zeros((0, len(alleles)), dtype=float)

        def f(block):
            g = GenotypeArray(block, copy=False)
            af = g.allele_frequencies(alleles=alleles, fill=fill)
            return af
        _block_append(f, self.data, out)

        return out

    def is_variant(self):
        out = bcolz.zeros((0,), dtype=bool)

        def f(block):
            g = GenotypeArray(block, copy=False)
            return g.is_variant()
        _block_append(f, self.data, out)

        return out

    def is_non_variant(self):
        out = bcolz.zeros((0,), dtype=bool)

        def f(block):
            g = GenotypeArray(block, copy=False)
            return g.is_non_variant()
        _block_append(f, self.data, out)

        return out

    def is_segregating(self):
        out = bcolz.zeros((0,), dtype=bool)

        def f(block):
            g = GenotypeArray(block, copy=False)
            return g.is_segregating()
        _block_append(f, self.data, out)

        return out

    def is_non_segregating(self, allele=None):
        out = bcolz.zeros((0,), dtype=bool)

        def f(block):
            g = GenotypeArray(block, copy=False)
            return g.is_non_segregating(allele=allele)
        _block_append(f, self.data, out)

        return out

    def is_singleton(self, allele=1):
        out = bcolz.zeros((0,), dtype=bool)

        def f(block):
            g = GenotypeArray(block, copy=False)
            return g.is_singleton(allele=allele)
        _block_append(f, self.data, out)

        return out

    def is_doubleton(self, allele=1):
        out = bcolz.zeros((0,), dtype=bool)

        def f(block):
            g = GenotypeArray(block, copy=False)
            return g.is_doubleton(allele=allele)
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

    @staticmethod
    def from_hdf5(*args, **kwargs):
        import h5py

        h5f = None

        if len(args) == 1:
            dataset = args[0]
            if not isinstance(dataset, h5py.Dataset):
                raise ValueError('bad argument: expected dataset or '
                                 '(file_path, node_path), found %s' %
                                 repr(dataset))

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
            GenotypeCArray._check_input_data(dataset)
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

            return GenotypeCArray(data, copy=False)

        finally:
            if h5f is not None:
                h5f.close()


class HaplotypeCArray(object):
    """TODO doco

    """

    @staticmethod
    def _check_input_data(obj):

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
        self._check_input_data(data)
        self.data = data

    def __getitem__(self, *args):
        out = self.data.__getitem__(*args)
        if hasattr(out, 'ndim') and out.ndim == 2:
            out = HaplotypeArray(out, copy=False)
        return out

    def __array__(self):
        return self.data[:]

    @property
    def ndim(self):
        return self.data.ndim

    @property
    def shape(self):
        return self.data.shape

    @property
    def dtype(self):
        return self.data.dtype

    @property
    def nbytes(self):
        return self.data.nbytes

    @property
    def cbytes(self):
        return self.data.cbytes

    @property
    def chunklen(self):
        return self.data.chunklen

    @property
    def cparams(self):
        return self.data.cparams

    @property
    def n_variants(self):
        """Number of variants (length of first array dimension)."""
        return self.data.shape[0]

    @property
    def n_haplotypes(self):
        """Number of haplotypes (length of second array dimension)."""
        return self.data.shape[1]

    def __repr__(self):
        s = repr(self.data)
        s = 'HaplotypeCArray' + s[6:]
        return s

    def compress(self, condition, axis):
        data = _block_compress(condition, self.data, axis)
        return HaplotypeCArray(data, copy=False)

    def take(self, indices, axis):
        data = _block_take(self.data, indices, axis)
        return HaplotypeCArray(data, copy=False)

    def subset(self, variants, haplotypes):
        data = _block_subset(HaplotypeArray, self.data, variants, haplotypes)
        return HaplotypeCArray(data, copy=False)

    def max(self, axis=None):
        return _block_max(self.data, axis=axis)

    def min(self, axis=None):
        return _block_min(self.data, axis=axis)

    def view_genotypes(self, ploidy):
        # Unfortunately this cannot be implemented as a lightweight view,
        # so we have to copy.

        # check ploidy is compatible
        if (self.n_haplotypes % ploidy) > 0:
            raise ValueError('incompatible ploidy')

        # setup output
        n_samples = self.n_haplotypes / ploidy
        out = bcolz.zeros((0, n_samples, ploidy),
                          dtype=self.data.dtype,
                          chunklen=self.data.chunklen)

        # build output
        f = lambda block: block.reshape((block.shape[0], -1, ploidy))
        _block_append(f, self.data, out)

        g = GenotypeCArray(out, copy=False)
        return g

    def is_called(self):
        return self >= 0

    def is_missing(self):
        return self < 0

    def is_ref(self):
        return self == 0

    def is_alt(self, allele=None):
        if allele is None:
            return self > 0
        else:
            return self == allele

    def is_call(self, allele):
        return self == allele

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

    def _op(self, op, other):
        if not isinstance(other, integer_types):
            raise NotImplementedError('only supported for scalars')

        # setup output
        out = bcolz.zeros((0, self.n_haplotypes),
                          dtype='u1',
                          expectedlen=self.n_variants)

        # build output
        f = lambda data: op(data, other)
        _block_append(f, self.data, out)

        return out

    def __eq__(self, other):
        return self._op(operator.eq, other)

    def __ne__(self, other):
        return self._op(operator.ne, other)

    def __lt__(self, other):
        return self._op(operator.lt, other)

    def __gt__(self, other):
        return self._op(operator.gt, other)

    def __le__(self, other):
        return self._op(operator.le, other)

    def __ge__(self, other):
        return self._op(operator.ge, other)

    def allelism(self):
        out = bcolz.zeros((0,), dtype=int)

        def f(data):
            return HaplotypeArray(data, copy=False).allelism()
        _block_append(f, self.data, out)
        return out

    def allele_number(self):
        out = bcolz.zeros((0,), dtype=int)

        def f(data):
            return HaplotypeArray(data, copy=False).allele_number()
        _block_append(f, self.data, out)
        return out

    def allele_count(self, allele=1):
        out = bcolz.zeros((0,), dtype=int)

        def f(data):
            return HaplotypeArray(data, copy=False).allele_count(allele=allele)
        _block_append(f, self.data, out)
        return out

    def allele_frequency(self, allele=1, fill=np.nan):
        out = bcolz.zeros((0,), dtype=float)

        def f(data):
            g = HaplotypeArray(data, copy=False)
            af = g.allele_frequency(allele=allele, fill=fill)
            return af
        _block_append(f, self.data, out)

        return out

    def allele_counts(self, alleles=None):
        # if alleles not specified, count all alleles
        if alleles is None:
            m = self.max()
            alleles = list(range(m+1))

        # setup output
        out = bcolz.zeros((0, len(alleles)), dtype=int)

        def f(data):
            g = HaplotypeArray(data, copy=False)
            return g.allele_counts(alleles=alleles)
        _block_append(f, self.data, out)

        return out

    def allele_frequencies(self, alleles=None, fill=np.nan):

        # if alleles not specified, count all alleles
        if alleles is None:
            m = self.max()
            alleles = list(range(m+1))

        # setup output
        out = bcolz.zeros((0, len(alleles)), dtype=float)

        def f(data):
            g = HaplotypeArray(data, copy=False)
            af = g.allele_frequencies(alleles=alleles, fill=fill)
            return af
        _block_append(f, self.data, out)

        return out

    def is_variant(self):
        out = bcolz.zeros((0,), dtype=bool)

        def f(data):
            g = HaplotypeArray(data, copy=False)
            return g.is_variant()
        _block_append(f, self.data, out)

        return out

    def is_non_variant(self):
        out = bcolz.zeros((0,), dtype=bool)

        def f(data):
            g = HaplotypeArray(data, copy=False)
            return g.is_non_variant()
        _block_append(f, self.data, out)

        return out

    def is_segregating(self):
        out = bcolz.zeros((0,), dtype=bool)

        def f(data):
            g = HaplotypeArray(data, copy=False)
            return g.is_segregating()
        _block_append(f, self.data, out)

        return out

    def is_non_segregating(self, allele=None):
        out = bcolz.zeros((0,), dtype=bool)

        def f(data):
            g = HaplotypeArray(data, copy=False)
            return g.is_non_segregating(allele=allele)
        _block_append(f, self.data, out)

        return out

    def is_singleton(self, allele=1):
        out = bcolz.zeros((0,), dtype=bool)

        def f(data):
            g = HaplotypeArray(data, copy=False)
            return g.is_singleton(allele=allele)
        _block_append(f, self.data, out)

        return out

    def is_doubleton(self, allele=1):
        out = bcolz.zeros((0,), dtype=bool)

        def f(data):
            g = HaplotypeArray(data, copy=False)
            return g.is_doubleton(allele=allele)
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

    @staticmethod
    def from_hdf5(*args, **kwargs):
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
            HaplotypeCArray._check_input_data(dataset)
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

            return HaplotypeCArray(data, copy=False)

        finally:
            if h5f is not None:
                h5f.close()
