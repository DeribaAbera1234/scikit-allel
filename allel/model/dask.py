# -*- coding: utf-8 -*-
"""This module provides alternative implementations of array and table
classes defined in the :mod:`allel.model.ndarray` module, using
`dask.array <http://dask.pydata.org/en/latest/array.html>`_ as the
computational engine.

Dask uses blocked algorithms and task scheduling to break up work into
smaller pieces, allowing computation over very large datasets. It also uses
lazy evaluation, meaning that multiple operations can be chained together
into a task graph, reducing total memory requirements for intermediate
results, and only the tasks required to generate the requested
part of the final data set will be executed.

This module is experimental, if you find a bug please `raise an issue on GitHub
<https://github.com/cggh/scikit-allel/issues/new>`_.

Currently this module requires a specific branch of Dask to be installed::

    $ pip install git+https://github.com/mrocklin/dask.git@drop-new-axes

"""
from __future__ import absolute_import, print_function, division


import numpy as np
import dask.array as da


from allel.model.ndarray import GenotypeArray


def get_chunks(data, chunks=None):
    """Try to guess a reasonable chunk shape to use for block-wise
    algorithms operating over `data`."""

    if chunks is None:

        if hasattr(data, 'chunklen') and hasattr(data, 'shape'):
            # bcolz carray, chunk first dimension only
            return (data.chunklen,) + data.shape[1:]

        elif hasattr(data, 'chunks') and hasattr(data, 'shape') and \
                len(data.chunks) == len(data.shape):
            # h5py dataset
            return data.chunks

        else:
            # fall back to something simple, ~1Mb chunks of first dimension
            # print(repr(data))
            row = np.asarray(data[0])
            chunklen = max(1, (2**20) // row.nbytes)
            if row.shape:
                chunks = (chunklen,) + row.shape
            else:
                chunks = (chunklen,)
            return chunks

    else:

        return chunks


def ensure_array_like(data):
    if not hasattr(data, 'shape') or not hasattr(data, 'dtype'):
        a = np.asarray(data)
        if len(a.shape) == 0:
            raise ValueError('not array-like')
        return a
    else:
        return data


def ensure_dask_array(data, chunks=None):
    # print(repr(data))
    if isinstance(data, da.Array):
        return data
    elif isinstance(data, DaskArrayWrapper):
        return data.darr
    else:
        data = ensure_array_like(data)
        chunks = get_chunks(data, chunks)
        return da.from_array(data, chunks=chunks)


class DaskArrayWrapper(object):

    def __init__(self, data, chunks=None):
        self.darr = ensure_dask_array(data, chunks)

    def __getitem__(self, *args):
        return self.darr.__getitem__(*args)

    def __setitem__(self, key, value):
        return self.darr.__setitem__(key, value)

    def __getattr__(self, item):
        return getattr(self.darr, item)

    def __array__(self):
        return np.asarray(self.darr)

    def __repr__(self):
        return '%s%s' % (type(self).__name__, repr(self.darr[10:]))

    def __str__(self):
        return str(self.darr)

    def __len__(self):
        return len(self.darr)

    @property
    def ndim(self):
        return len(self.shape)

    def compress(self, condition, axis=0):
        if axis == 0:
            out = self.darr[condition]
        elif axis == 1:
            out = self.darr[:, condition]
        else:
            raise NotImplementedError('axis not implemented')
        return type(self)(out)

    def take(self, indices, axis=0):
        if axis == 0:
            out = self.darr[indices]
        elif axis == 1:
            out = self.darr[:, indices]
        else:
            raise NotImplementedError('axis not implemented')
        return type(self)(out)

    def subset(self, sel0, sel1):
        out = self.darr[sel0][:sel1]
        return type(self)(out)

    def hstack(self, *others, **kwargs):
        others = tuple(ensure_dask_array(d) for d in others)
        tup = (self.darr,) + others
        out = da.concatenate(tup, axis=1)
        return type(self)(out)

    def vstack(self, *others, **kwargs):
        others = tuple(ensure_dask_array(d) for d in others)
        tup = (self.darr,) + others
        out = da.concatenate(tup, axis=0)
        return type(self)(out)


class GenotypeDaskArray(DaskArrayWrapper):
    """TODO"""

    def __init__(self, data, chunks=None):
        super(GenotypeDaskArray, self).__init__(data, chunks=chunks)
        self._check_input_data(self.darr)
        self._mask = None

    @staticmethod
    def _check_input_data(data):
        if len(data.shape) != 3:
            raise ValueError('expected 3 dimensions')
        if data.dtype.kind not in 'ui':
            raise TypeError('expected integer dtype')

    def __getitem__(self, *args):
        out = super(GenotypeDaskArray, self).__getitem__(*args)
        if hasattr(out, 'shape') \
                and len(self.shape) == len(out.shape) \
                and self.shape[2] == out.shape[2]:
            # dimensionality and ploidy preserved
            out = GenotypeDaskArray(out)
            if self.mask is not None:
                # attempt to slice mask too
                m = self.mask.__getitem__(*args)
                out.mask = m
        return out

    def compute(self, **kwargs):
        a = self.darr.compute(**kwargs)
        g = GenotypeArray(a)
        if self.mask:
            m = self.mask.compute(**kwargs)
            g.mask = m
        return g

    def _repr_html_(self):
        return self[:6].compute().to_html_str(caption=repr(self))

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
        return self.shape[0] * self.shape[1]

    @property
    def n_allele_calls(self):
        return self.shape[0] * self.shape[1] * self.shape[2]

    @property
    def mask(self):
        return self._mask

    @mask.setter
    def mask(self, mask):

        # ensure dask array
        mask = ensure_dask_array(mask)

        # check shape
        if mask.shape != self.shape[:2]:
            raise ValueError('mask has incorrect shape')

        # store
        self._mask = mask

    def fill_masked(self, value=-1):
        def f(block, bmask):
            gb = GenotypeArray(block)
            gb.mask = bmask[:, :, 0]
            return gb.fill_masked(value=value)
        out = da.map_blocks(f, self.darr, self.mask[:, :, None],
                            chunks=self.darr.chunks)
        return GenotypeDaskArray(out)

    def is_called(self):
        def f(block):
            return GenotypeArray(block).is_called()
        chunks = (self.chunks[0], self.chunks[1])
        out = self.map_blocks(f, chunks=chunks, drop_dims=2)
        return out

    def is_missing(self):
        def f(block):
            return GenotypeArray(block).is_missing()
        chunks = (self.chunks[0], self.chunks[1])
        out = self.map_blocks(f, chunks=chunks, drop_dims=2)
        return out

    def is_hom(self, allele=None):
        def f(block):
            return GenotypeArray(block).is_hom(allele=allele)
        chunks = (self.chunks[0], self.chunks[1])
        out = self.map_blocks(f, chunks=chunks, drop_dims=2)
        return out

    def is_hom_ref(self):
        def f(block):
            return GenotypeArray(block).is_hom_ref()
        chunks = (self.chunks[0], self.chunks[1])
        out = self.map_blocks(f, chunks=chunks, drop_dims=2)
        return out

    def is_hom_alt(self):
        def f(block):
            return GenotypeArray(block).is_hom_alt()
        chunks = (self.chunks[0], self.chunks[1])
        out = self.map_blocks(f, chunks=chunks, drop_dims=2)
        return out

    def is_het(self, allele=None):
        def f(block):
            return GenotypeArray(block).is_het(allele=allele)
        chunks = (self.chunks[0], self.chunks[1])
        out = self.map_blocks(f, chunks=chunks, drop_dims=2)
        return out

    def is_call(self, call):
        def f(block):
            return GenotypeArray(block).is_call(call)
        chunks = (self.chunks[0], self.chunks[1])
        out = self.map_blocks(f, chunks=chunks, drop_dims=2)
        return out

    def count_called(self, axis=None):
        return self.is_called().sum(axis=axis)

    def count_missing(self, axis=None):
        return self.is_missing().sum(axis=axis)

    def count_hom(self, allele=None, axis=None):
        return self.is_hom(allele=allele).sum(axis=axis)

    def count_hom_ref(self, axis=None):
        return self.is_hom_ref().sum(axis=axis)

    def count_hom_alt(self, axis=None):
        return self.is_hom_alt().sum(axis=axis)

    def count_het(self, axis=None):
        return self.is_het().sum(axis=axis)

    def count_call(self, call, axis=None):
        return self.is_call(call).sum(axis=axis)

    def count_alleles(self, max_allele=None, subpop=None):

        # if max_allele not specified, count all alleles
        if max_allele is None:
            max_allele = self.max().compute()

        # deal with subpop
        if subpop:
            g = self.take(subpop, axis=1)
        else:
            g = self

        def f(block):
            block = GenotypeArray(block)
            # print(block.shape, block.ploidy)
            return block.count_alleles(max_allele=max_allele)[:, None, :]

        # determine output chunks - preserve dim0; change dim1, dim2
        chunks = (g.chunks[0], (1,)*len(g.chunks[1]), (max_allele+1,))

        # map blocks and reduce
        out = g.map_blocks(f, chunks=chunks).sum(axis=1)
        return AlleleCountsDaskArray(out)

    def count_alleles_subpops(self, subpops, max_allele=None):
        # TODO consider different implementation which requires only a
        # single pass over the data to compute allele counts for all subpops
        # (original intention of the method).

        # if max_allele not specified, count all alleles
        if max_allele is None:
            max_allele = self.max().compute()

        return {k: self.count_alleles(max_allele=max_allele, subpop=v)
                for k, v in subpops.items()}

    def to_packed(self, boundscheck=True):
        def f(block):
            return GenotypeArray(block).to_packed(boundscheck=boundscheck)
        chunks = (self.chunks[0], self.chunks[1])
        out = self.map_blocks(f, chunks=chunks, drop_dims=2)
        return out

    @staticmethod
    def from_packed(packed, chunks=None):
        def f(block):
            return GenotypeArray.from_packed(block)
        packed = ensure_dask_array(packed, chunks)
        chunks = (packed.chunks[0], packed.chunks[1], (2,))
        return da.map_blocks(f, packed, chunks=chunks, new_dims=2)

    def map_alleles(self, mapping, **kwargs):
        # TODO broken

        def f(block, bmapping):
            g = GenotypeArray(block)
            m = bmapping[:, 0, :]
            return g.map_alleles(m, copy=False)

        mapping = da.from_array(mapping, chunks=(self.chunks[0], None))
        print(self.shape, self.chunks)
        print(mapping.shape, mapping.chunks)
        out = da.map_blocks(f, self.darr, mapping[:, None, :])
        return GenotypeDaskArray(out)


class AlleleCountsDaskArray(DaskArrayWrapper):

    @property
    def n_variants(self):
        return self.shape[0]

    @property
    def n_alleles(self):
        return self.shape[1]

    # TODO