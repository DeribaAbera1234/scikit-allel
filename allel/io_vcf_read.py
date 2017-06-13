# -*- coding: utf-8 -*-
"""
Functions for extracting data from Variant Call Format (VCF) files and loading
into NumPy arrays, NumPy files, HDF5 files or Zarr array stores.

TODO:

* Port any relevant tests from vcfnp
* PY2 compatibility

WONTFIX:

* is_snp or SVTYPE (SNP|INS|DEL|COMPLEX) computed field
* is_phased computed field
* Feature to rename fields, e.g., calldata/GT -> calldata/genotype. Could be implemented via transformer.
* Specialised parser for EFF - obsolete.

"""
from __future__ import absolute_import, print_function, division
import gzip
import sys
import os
import re
from collections import namedtuple
import warnings


import numpy as np

# noinspection PyUnresolvedReferences
from allel.opt.io_vcf_read import VCFChunkIterator, FileInputStream, \
    VCFParallelChunkIterator, ANNTransformer


def debug(*msg):
    print(*msg, file=sys.stderr)
    sys.stderr.flush()


DEFAULT_BUFFER_SIZE = 2**14
DEFAULT_CHUNK_LENGTH = 2**16
DEFAULT_BLOCK_LENGTH = 2**11
DEFAULT_CHUNK_WIDTH = 2**6


def _prep_fields_param(fields):
    """Prepare the `fields` parameter, and determine whether or not to store samples."""

    store_samples = False

    if fields is None:
        # add samples by default
        return True, None

    if isinstance(fields, str):
        fields = [fields]
    else:
        fields = list(fields)

    if 'samples' in fields:
        fields.remove('samples')
        store_samples = True
    elif '*' in fields:
        store_samples = True

    return store_samples, fields


import time


def _chunk_iter_progress(it, log, prefix):
    """Wrap a chunk iterator for progress logging."""
    n_variants = 0
    before_all = time.time()
    before_chunk = before_all
    for chunk, chunk_length, chrom, pos in it:
        after_chunk = time.time()
        elapsed_chunk = after_chunk - before_chunk
        elapsed = after_chunk - before_all
        n_variants += chunk_length
        print('%s %s rows in %.2fs; chunk in %.2fs (%s rows/s); %s:%s' %
              (prefix, n_variants, elapsed, elapsed_chunk,
               int(chunk_length//elapsed_chunk), str(chrom, 'ascii'), pos),
              file=log)
        log.flush()
        yield chunk, chunk_length, chrom, pos
        before_chunk = after_chunk
    after_all = time.time()
    elapsed = after_all - before_all
    print('%s all done (%s rows/s)' %
          (prefix, int(n_variants//elapsed)), file=log)
    log.flush()


def _chunk_iter_transform(it, transformers):
    for chunk, chunk_length, chrom, pos in it:
        for transformer in transformers:
            transformer.transform(chunk)
        yield chunk, chunk_length, chrom, pos


_doc_param_input_path = \
    """Path to VCF file on the local file system. May be uncompressed or gzip-compatible
        compressed file. May also be a file-like object (e.g., `io.BytesIO`)."""

_doc_param_fields = \
    """Fields to extract data for. Should be a list of strings, e.g., `['variants/CHROM',
        'variants/POS', 'variants/DP', 'calldata/GT']`. If you are feeling lazy, you can drop
        the 'variants/' and 'calldata/' prefixes, in which case the fields will be matched against
        fields declared in the VCF header, with variants taking priority over calldata if a field
        with the same ID exists both in INFO and FORMAT headers. I.e., `['CHROM', 'POS', 'DP', 'GT']`
        will also work as well, although watch out for fields like 'DP' which can be both INFO and
        FORMAT. For convenience, some special string values are also recognized. To extract all fields,
        provide just the string '*'. To extract all variants fields (including all INFO fields) provide
        'variants/*'. To extract all calldata fields (i.e., defined in FORMAT headers) provide 'calldata/*'."""

_doc_param_types = \
    """Overide data types. Should be a dictionary mapping field names to NumPy data types.
        E.g., providing the dictionary `{'variants/DP': 'i8', 'calldata/GQ': 'i2'}` will mean
        the 'variants/DP' field is stored in a 64-bit integer array, and the `calldata/GQ` field
        is stored in a 16-bit integer array."""

_doc_param_numbers = \
    """Override the expected number of values. Should be a dictionary mapping field names to
        integers. E.g., providing the dictionary `{'variants/ALT': 3, 'variants/AC': 3,
        'calldata/HQ': 2}` will mean that, for each variant, 3 values are stored for the
        'variants/ALT' field, 3 values are stored for the 'variants/AC' field, and for each
        sample, 2 values are stored for the 'calldata/HQ' field."""

_doc_param_fills = \
    """Override the fill value used for empty values. Should be a dictionary mapping field names
        to fill values."""

_doc_param_region = \
    """Genomic region to extract variants for. If provided, should be a tabix-style region string,
        which can be either just a chromosome name (e.g., '2L'), or a chromosome name followed by
        1-based beginning and end coordinates (e.g., '2L:100000-200000')."""

_doc_param_tabix = \
    """Name or path to tabix executable. Only required if `region` is given. Setting `tabix` to
        `None` will cause a fall-back to scanning through the VCF file from the beginning, which
        may be much slower than tabix but the only option if tabix is not available on your system
        and/or the VCF file has not been tabix-indexed."""

_doc_param_samples = \
    """Selection of samples to extract calldata for. If provided, should be a list of strings
        giving sample identifiers. May also be a list of integers giving indices of selected
        samples."""

_doc_param_transformers = \
    """Transformers for post-processing data. If provided, should be a list of Transformer
        objects, each of which must implement a "transform()" method that accepts a dict
        containing the chunk of data to be transformed. See also the :class:`ANNTransformer`
        class which implements post-processing of data from SNPEFF."""

_doc_param_buffer_size = \
    """Size in bytes of the I/O buffer used when reading data from the underlying file or
        tabix stream."""

_doc_param_chunk_length = \
    """Length (number of variants) of chunks in which data are processed."""

_doc_param_n_threads = \
    """Experimental: number of additional threads to launch to parse in parallel.
        E.g., a value of 1 will launch 1 parsing thread, in addition to the main
        program thread. If you are feeling adventurous and/or impatient, try a value of 1
        or 2. May increase or decrease speed of parsing relative to single-threaded
        behaviour, depending on the data, your computer, the weather, and how the stars
        are aligned. A value of None (default) means single-threaded  parsing."""

_doc_param_block_length = \
    """Only applies if n_threads is not None (multi-threaded parsing). Size of block
        (number of rows) that will be handed off to be parsed in parallel."""

_doc_param_log = \
    """A file-like object (e.g., `sys.stderr`) to print progress information."""


def read_vcf(input_path,
             fields=None,
             types=None,
             numbers=None,
             fills=None,
             region=None,
             tabix='tabix',
             samples=None,
             transformers=None,
             buffer_size=DEFAULT_BUFFER_SIZE,
             chunk_length=DEFAULT_CHUNK_LENGTH,
             n_threads=None,
             block_length=DEFAULT_BLOCK_LENGTH,
             log=None):
    """Read data from a VCF file into NumPy arrays.

    Parameters
    ----------
    input_path : string
        {input_path}
    fields : list of strings, optional
        {fields}
    types : dict, optional
        {types}
    numbers : dict, optional
        {numbers}
    fills : dict, optional
        {fills}
    region : string, optional
        {region}
    tabix : string, optional
        {tabix}
    samples : list of strings
        {samples}
    transformers : list of transformer objects, optional
        {transformers}
    buffer_size : int, optional
        {buffer_size}
    chunk_length : int, optional
        {chunk_length}
    n_threads : int, optional
        {n_threads}
    block_length : int, optional
        {block_length}
    log : file-like, optional
        {log}

    Returns
    -------
    data : dict[str -> ndarray]
        A dictionary holding arrays.

    """

    # samples requested?
    # noinspection PyTypeChecker
    store_samples, fields = _prep_fields_param(fields)

    # setup
    samples, _, it = iter_vcf_chunks(
        input_path=input_path, fields=fields, types=types, numbers=numbers,buffer_size=buffer_size,
        chunk_length=chunk_length, block_length=block_length, n_threads=n_threads, fills=fills, region=region,
        tabix=tabix, samples=samples, transformers=transformers
    )

    # setup progress logging
    if log is not None:
        it = _chunk_iter_progress(it, log, prefix='[read_vcf]')

    # read all chunks into a list
    chunks = [chunk for chunk, _, _, _ in it]

    # setup output
    output = dict()

    if samples and store_samples:
        # use binary string type
        output['samples'] = np.array(samples).astype('S')

    if chunks:

        # find array keys
        keys = sorted(chunks[0].keys())

        # concatenate chunks
        for k in keys:
            output[k] = np.concatenate([chunk[k] for chunk in chunks], axis=0)

    return output


read_vcf.__doc__ = read_vcf.__doc__.format(
    input_path=_doc_param_input_path,
    fields=_doc_param_fields,
    types=_doc_param_types,
    numbers=_doc_param_numbers,
    fills=_doc_param_fills,
    region=_doc_param_region,
    tabix=_doc_param_tabix,
    samples=_doc_param_samples,
    transformers=_doc_param_transformers,
    buffer_size=_doc_param_buffer_size,
    chunk_length=_doc_param_chunk_length,
    n_threads=_doc_param_n_threads,
    block_length=_doc_param_block_length,
    log=_doc_param_log,
)


_doc_param_output_path = \
    """File-system path to write output to."""

_doc_param_overwrite = \
    """If False (default), do not overwrite an existing file."""


def vcf_to_npz(input_path, output_path,
               compressed=True,
               overwrite=False,
               fields=None,
               types=None,
               numbers=None,
               fills=None,
               region=None,
               tabix=True,
               samples=None,
               transformers=None,
               buffer_size=DEFAULT_BUFFER_SIZE,
               chunk_length=DEFAULT_CHUNK_LENGTH,
               n_threads=None,
               block_length=DEFAULT_BLOCK_LENGTH,
               log=None):
    """Read data from a VCF file into NumPy arrays and save as a .npz file.

    Parameters
    ----------
    input_path : string
        {input_path}
    output_path : string
        {output_path}
    compressed : bool, optional
        If True (default), save with compression.
    overwrite : bool, optional
        {overwrite}
    fields : list of strings, optional
        {fields}
    types : dict, optional
        {types}
    numbers : dict, optional
        {numbers}
    fills : dict, optional
        {fills}
    region : string, optional
        {region}
    tabix : string, optional
        {tabix}
    samples : list of strings
        {samples}
    transformers : list of transformer objects, optional
        {transformers}
    buffer_size : int, optional
        {buffer_size}
    chunk_length : int, optional
        {chunk_length}
    n_threads : int, optional
        {n_threads}
    block_length : int, optional
        {block_length}
    log : file-like, optional
        {log}

    """

    # guard condition
    if not overwrite and os.path.exists(output_path):
        raise ValueError('file exists at path %r; use overwrite=True to replace' % output_path)

    # read all data into memory
    data = read_vcf(
        input_path=input_path, fields=fields, types=types, numbers=numbers, buffer_size=buffer_size,
        chunk_length=chunk_length, block_length=block_length, n_threads=n_threads, log=log, fills=fills,
        region=region, tabix=tabix, samples=samples, transformers=transformers
    )

    # setup save function
    if compressed:
        savez = np.savez_compressed
    else:
        savez = np.savez

    # save as npz
    savez(output_path, **data)


vcf_to_npz.__doc__ = vcf_to_npz.__doc__.format(
    input_path=_doc_param_input_path,
    output_path=_doc_param_output_path,
    overwrite=_doc_param_overwrite,
    fields=_doc_param_fields,
    types=_doc_param_types,
    numbers=_doc_param_numbers,
    fills=_doc_param_fills,
    region=_doc_param_region,
    tabix=_doc_param_tabix,
    samples=_doc_param_samples,
    transformers=_doc_param_transformers,
    buffer_size=_doc_param_buffer_size,
    chunk_length=_doc_param_chunk_length,
    n_threads=_doc_param_n_threads,
    block_length=_doc_param_block_length,
    log=_doc_param_log,
)


def _hdf5_setup_datasets(chunk, root, chunk_length, chunk_width, compression, compression_opts, shuffle, overwrite,
                         headers):

    # handle no input
    if chunk is None:
        raise RuntimeError('input file has no data?')

    # setup datasets
    keys = sorted(chunk.keys())
    for k in keys:

        # obtain initial data
        data = chunk[k]

        # determine chunk shape
        if data.ndim == 1:
            chunk_shape = (chunk_length,)
        else:
            chunk_shape = (chunk_length, min(chunk_width, data.shape[1])) + data.shape[2:]

        # create dataset
        group, name = k.split('/')
        if name in root[group]:
            if overwrite:
                del root[group][name]
            else:
                # TODO right exception class?
                raise ValueError('dataset exists at path %r; use overwrite=True to replace' % k)

        shape = (0,) + data.shape[1:]
        maxshape = (None,) + data.shape[1:]
        ds = root[group].create_dataset(
            name, shape=shape, maxshape=maxshape, chunks=chunk_shape, dtype=data.dtype, compression=compression,
            compression_opts=compression_opts, shuffle=shuffle
        )

        # copy metadata from VCF headers
        meta = None
        if group == 'variants' and name in headers.infos:
            meta = headers.infos[name]
        elif group == 'calldata' and name in headers.formats:
            meta = headers.formats[name]
        if meta is not None:
            ds.attrs['ID'] = meta['ID']
            ds.attrs['Number'] = meta['Number']
            ds.attrs['Type'] = meta['Type']
            ds.attrs['Description'] = meta['Description']

    return keys


def _hdf5_store_chunk(root, keys, chunk):

    # compute length of current chunk
    current_chunk_length = chunk[keys[0]].shape[0]

    # find current length of datasets
    old_length = root[keys[0]].shape[0]

    # new length of all arrays after loading this chunk
    new_length = old_length + current_chunk_length

    # load arrays
    for k in keys:

        # data to be loaded
        data = chunk[k]

        # obtain dataset
        dataset = root[k]

        # ensure dataset is large enough
        dataset.resize(new_length, axis=0)

        # store the data
        dataset[old_length:new_length, ...] = data


_doc_param_chunk_width = \
    """Width (number of samples) to use when storing chunks in output."""


def vcf_to_hdf5(input_path, output_path,
                group='/',
                compression='gzip',
                compression_opts=1,
                shuffle=False,
                overwrite=False,
                fields=None,
                types=None,
                numbers=None,
                fills=None,
                region=None,
                tabix='tabix',
                samples=None,
                transformers=None,
                buffer_size=DEFAULT_BUFFER_SIZE,
                chunk_length=DEFAULT_CHUNK_LENGTH,
                chunk_width=DEFAULT_CHUNK_WIDTH,
                n_threads=None,
                block_length=DEFAULT_BLOCK_LENGTH,
                log=None):
    """Read data from a VCF file and load into an HDF5 file.

    Parameters
    ----------
    input_path : string
        {input_path}
    output_path : string
        {output_path}
    group : string
        Group within destination HDF5 file to store data in.
    compression : string
        Compression algorithm, e.g., 'gzip' (default).
    compression_opts : int
        Compression level, e.g., 1 (default).
    shuffle : bool
        Use byte shuffling, which may improve compression (default is False).
    overwrite : bool
        {overwrite}
    fields : list of strings, optional
        {fields}
    types : dict, optional
        {types}
    numbers : dict, optional
        {numbers}
    fills : dict, optional
        {fills}
    region : string, optional
        {region}
    tabix : string, optional
        {tabix}
    samples : list of strings
        {samples}
    transformers : list of transformer objects, optional
        {transformers}
    buffer_size : int, optional
        {buffer_size}
    chunk_length : int, optional
        {chunk_length}
    chunk_width : int, optional
        {chunk_width}
    n_threads : int, optional
        {n_threads}
    block_length : int, optional
        {block_length}
    log : file-like, optional
        {log}

    """

    import h5py

    # samples requested?
    # noinspection PyTypeChecker
    store_samples, fields = _prep_fields_param(fields)

    with h5py.File(output_path, mode='a') as h5f:

        # obtain root group that data will be stored into
        root = h5f.require_group(group)

        # ensure sub-groups
        root.require_group('variants')
        root.require_group('calldata')

        # setup chunk iterator
        samples, headers, it = iter_vcf_chunks(
            input_path, fields=fields, types=types, numbers=numbers, buffer_size=buffer_size,
            chunk_length=chunk_length, block_length=block_length, n_threads=n_threads,
            fills=fills, region=region, tabix=tabix, samples=samples, transformers=transformers
        )

        # setup progress logging
        if log is not None:
            it = _chunk_iter_progress(it, log, prefix='[vcf_to_hdf5]')

        if samples and store_samples:
            # store samples
            name = 'samples'
            if name in root[group]:
                if overwrite:
                    del root[group][name]
                else:
                    # TODO right exception class?
                    raise ValueError('dataset exists at path %r; use overwrite=True to replace' % name)
            root[group].create_dataset(name, data=np.array(samples).astype('S'),
                                       chunks=None)

        # read first chunk
        chunk, _, _, _ = next(it)

        # setup datasets
        # noinspection PyTypeChecker
        keys = _hdf5_setup_datasets(
            chunk=chunk, root=root, chunk_length=chunk_length, chunk_width=chunk_width, compression=compression,
            compression_opts=compression_opts, shuffle=shuffle, overwrite=overwrite, headers=headers
        )

        # store first chunk
        _hdf5_store_chunk(root, keys, chunk)

        # store remaining chunks
        for chunk, _, _, _ in it:

            _hdf5_store_chunk(root, keys, chunk)


vcf_to_hdf5.__doc__ = vcf_to_hdf5.__doc__.format(
    input_path=_doc_param_input_path,
    output_path=_doc_param_output_path,
    overwrite=_doc_param_overwrite,
    fields=_doc_param_fields,
    types=_doc_param_types,
    numbers=_doc_param_numbers,
    fills=_doc_param_fills,
    region=_doc_param_region,
    tabix=_doc_param_tabix,
    samples=_doc_param_samples,
    transformers=_doc_param_transformers,
    buffer_size=_doc_param_buffer_size,
    chunk_length=_doc_param_chunk_length,
    chunk_width=_doc_param_chunk_width,
    n_threads=_doc_param_n_threads,
    block_length=_doc_param_block_length,
    log=_doc_param_log,
)


def _zarr_setup_datasets(chunk, root, chunk_length, chunk_width, compressor, overwrite, headers):

    # handle no input
    if chunk is None:
        raise RuntimeError('input file has no data?')

    # setup datasets
    keys = sorted(chunk.keys())
    for k in keys:

        # obtain initial data
        data = chunk[k]

        # determine chunk shape
        if data.ndim == 1:
            chunk_shape = (chunk_length,)
        else:
            chunk_shape = (chunk_length, min(chunk_width, data.shape[1])) + data.shape[2:]

        # debug(k, data.ndim, data.shape, chunk_shape)

        # create dataset
        shape = (0,) + data.shape[1:]
        ds = root.create_dataset(k, shape=shape, chunks=chunk_shape, dtype=data.dtype, compressor=compressor,
                            overwrite=overwrite)

        # copy metadata from VCF headers
        group, name = k.split('/')
        meta = None
        if group == 'variants' and name in headers.infos:
            meta = headers.infos[name]
        elif group == 'calldata' and name in headers.formats:
            meta = headers.formats[name]
        if meta is not None:
            ds.attrs['ID'] = meta['ID']
            ds.attrs['Number'] = meta['Number']
            ds.attrs['Type'] = meta['Type']
            ds.attrs['Description'] = meta['Description']

    return keys


def _zarr_store_chunk(root, keys, chunk):

    # load arrays
    for k in keys:

        # append data
        root[k].append(chunk[k], axis=0)


def vcf_to_zarr(input_path, output_path,
                group='/',
                compressor='default',
                overwrite=False,
                fields=None,
                types=None,
                numbers=None,
                fills=None,
                region=None,
                tabix='tabix',
                samples=None,
                transformers=None,
                buffer_size=DEFAULT_BUFFER_SIZE,
                chunk_length=DEFAULT_CHUNK_LENGTH,
                chunk_width=DEFAULT_CHUNK_WIDTH,
                n_threads=None,
                block_length=DEFAULT_BLOCK_LENGTH,
                log=None):
    """Read data from a VCF file and load into a Zarr on-disk store.

    Parameters
    ----------
    input_path : string
        {input_path}
    output_path : string
        {output_path}
    group : string
        Group within destination Zarr hierarchy to store data in.
    compressor : compressor
        Compression algorithm, e.g., zarr.Blosc(cname='zstd', clevel=1, shuffle=1).
    overwrite : bool
        {overwrite}
    fields : list of strings, optional
        {fields}
    types : dict, optional
        {types}
    numbers : dict, optional
        {numbers}
    fills : dict, optional
        {fills}
    region : string, optional
        {region}
    tabix : string, optional
        {tabix}
    samples : list of strings
        {samples}
    transformers : list of transformer objects, optional
        {transformers}
    buffer_size : int, optional
        {buffer_size}
    chunk_length : int, optional
        {chunk_length}
    chunk_width : int, optional
        {chunk_width}
    n_threads : int, optional
        {n_threads}
    block_length : int, optional
        {block_length}
    log : file-like, optional
        {log}

    """

    import zarr

    # samples requested?
    # noinspection PyTypeChecker
    store_samples, fields = _prep_fields_param(fields)

    # open root group
    root = zarr.open_group(output_path, mode='a', path=group)

    # ensure sub-groups
    root.require_group('variants')
    root.require_group('calldata')

    # setup chunk iterator
    samples, headers, it = iter_vcf_chunks(
        input_path, fields=fields, types=types, numbers=numbers, buffer_size=buffer_size, chunk_length=chunk_length,
        fills=fills, block_length=block_length, n_threads=n_threads, region=region, tabix=tabix, samples=samples,
        transformers=transformers
    )

    # setup progress logging
    if log is not None:
        it = _chunk_iter_progress(it, log, prefix='[vcf_to_zarr]')

    if samples and store_samples:
        # store samples
        root[group].create_dataset('samples', data=np.array(samples).astype('S'), compressor=None, overwrite=overwrite)

    # read first chunk
    chunk, _, _, _ = next(it)

    # setup datasets
    # noinspection PyTypeChecker
    keys = _zarr_setup_datasets(
        chunk, root=root, chunk_length=chunk_length, chunk_width=chunk_width, compressor=compressor,
        overwrite=overwrite, headers=headers
    )

    # store first chunk
    _zarr_store_chunk(root, keys, chunk)

    # store remaining chunks
    for chunk, _, _, _ in it:

        _zarr_store_chunk(root, keys, chunk)


vcf_to_zarr.__doc__ = vcf_to_zarr.__doc__.format(
    input_path=_doc_param_input_path,
    output_path=_doc_param_output_path,
    overwrite=_doc_param_overwrite,
    fields=_doc_param_fields,
    types=_doc_param_types,
    numbers=_doc_param_numbers,
    fills=_doc_param_fills,
    region=_doc_param_region,
    tabix=_doc_param_tabix,
    samples=_doc_param_samples,
    transformers=_doc_param_transformers,
    buffer_size=_doc_param_buffer_size,
    chunk_length=_doc_param_chunk_length,
    chunk_width=_doc_param_chunk_width,
    n_threads=_doc_param_n_threads,
    block_length=_doc_param_block_length,
    log=_doc_param_log,
)


import subprocess


def iter_vcf_chunks(input_path,
                    fields=None,
                    types=None,
                    numbers=None,
                    fills=None,
                    region=None,
                    tabix='tabix',
                    samples=None,
                    transformers=None,
                    buffer_size=DEFAULT_BUFFER_SIZE,
                    chunk_length=DEFAULT_CHUNK_LENGTH,
                    n_threads=None,
                    block_length=DEFAULT_BLOCK_LENGTH):
    """Iterate over chunks of data from a VCF file as NumPy arrays.

    Parameters
    ----------
    input_path : string
        {input_path}
    fields : list of strings, optional
        {fields}
    types : dict, optional
        {types}
    numbers : dict, optional
        {numbers}
    fills : dict, optional
        {fills}
    region : string, optional
        {region}
    tabix : string, optional
        {tabix}
    samples : list of strings
        {samples}
    transformers : list of transformer objects, optional
        {transformers}
    buffer_size : int, optional
        {buffer_size}
    chunk_length : int, optional
        {chunk_length}
    n_threads : int, optional
        {n_threads}
    block_length : int, optional
        {block_length}

    Returns
    -------
    samples : list of strings
        Samples for which data will be extracted.
    headers : tuple
        Tuple of metadata extracted from VCF headers.
    it : iterator
        Chunk iterator.

    """

    # guard condition
    if n_threads is not None and region:
        warnings.warn('cannot use multiple threads if region is given, falling back to '
                      'single-threaded implementation')
        n_threads = None

    # setup commmon keyword args
    kwds = dict(fields=fields, types=types, numbers=numbers,
                chunk_length=chunk_length, block_length=block_length,
                n_threads=n_threads, fills=fills, samples=samples)

    # obtain a file-like object
    if isinstance(input_path, str) and input_path.endswith('gz'):

        if region and tabix and os.name != 'nt':

            try:
                # try tabix
                p = subprocess.Popen([tabix, '-h', input_path, region],
                                     stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE,
                                     bufsize=0)

                # check if tabix exited early, look for tabix error
                time.sleep(.5)
                poll = p.poll()
                if poll is not None and poll > 0:
                    err = p.stderr.read(-1)
                    raise Exception(str(err, 'ascii').strip())
                fileobj = p.stdout
                region = None

            except FileNotFoundError:
                # no tabix, fall back to scanning
                warnings.warn('tabix not found, falling back to scanning to region')
                fileobj = gzip.open(input_path, mode='rb')

            except Exception as e:
                warnings.warn('error occurred attempting tabix (%s); falling back to '
                              'scanning to region' % e)
                fileobj = gzip.open(input_path, mode='rb')

        else:
            fileobj = gzip.open(input_path, mode='rb')

    elif isinstance(input_path, str):
        # assume no compression
        fileobj = open(input_path, mode='rb', buffering=0)

    elif hasattr(input_path, 'readinto'):
        fileobj = input_path

    else:
        raise ValueError('path must be string or file-like, found %r' % input_path)

    # setup input stream
    stream = FileInputStream(fileobj, buffer_size=buffer_size)

    # deal with region
    kwds['region'] = region

    # setup iterator
    samples, headers, it = _read_vcf(stream, **kwds)

    # setup transformers
    if transformers is not None:
        it = _chunk_iter_transform(it, transformers)

    return samples, headers, it


iter_vcf_chunks.__doc__ = iter_vcf_chunks.__doc__.format(
    input_path=_doc_param_input_path,
    fields=_doc_param_fields,
    types=_doc_param_types,
    numbers=_doc_param_numbers,
    fills=_doc_param_fills,
    region=_doc_param_region,
    tabix=_doc_param_tabix,
    samples=_doc_param_samples,
    transformers=_doc_param_transformers,
    buffer_size=_doc_param_buffer_size,
    chunk_length=_doc_param_chunk_length,
    n_threads=_doc_param_n_threads,
    block_length=_doc_param_block_length,
    log=_doc_param_log,
)


FIXED_VARIANTS_FIELDS = (
    'CHROM',
    'POS',
    'ID',
    'REF',
    'ALT',
    'QUAL',
)


def _normalize_field_prefix(field, headers):

    # already contains prefix?
    if field.startswith('variants/') or field.startswith('calldata/'):
        return field

    # try to find in fixed fields
    elif field in FIXED_VARIANTS_FIELDS:
        return 'variants/' + field

    # try to find in FILTER
    elif field.startswith('FILTER_'):
        return 'variants/' + field

    # try to find in FILTER
    elif field in headers.filters:
        return 'variants/FILTER_' + field

    # try to find in INFO
    elif field in headers.infos:
        return 'variants/' + field

    # try to find in FORMAT
    elif field in headers.formats:
        return 'calldata/' + field

    else:
        # assume anything else in variants, even if not declared in header
        return 'variants/' + field


def _check_field(field, headers):

    # assume field is already normalized for prefix
    group, name = field.split('/')

    if group == 'variants':

        if name in FIXED_VARIANTS_FIELDS:
            return

        elif name in ['numalt', 'svlen']:
            # computed fields
            return

        elif name.startswith('FILTER_'):
            filter_name = name[7:]
            if filter_name in headers.filters:
                return
            else:
                warnings.warn('FILTER not declared in header: %r' % filter_name)

        elif name in headers.infos:
            return

        else:
            warnings.warn('INFO not declared in header: %r' % name)

    elif group == 'calldata':

        if name in headers.formats:
            return

        else:
            warnings.warn('FORMAT not declared in header: %r' % name)

    else:
        # should never be reached
        raise ValueError('invalid field specification: %r' % field)


def _add_all_fields(fields, headers, samples):
    _add_all_variants_fields(fields, headers)
    if samples:
        _add_all_calldata_fields(fields, headers)


def _add_all_variants_fields(fields, headers):
    _add_all_fixed_variants_fields(fields)
    _add_all_info_fields(fields, headers)
    _add_all_filter_fields(fields, headers)
    # add in special computed fields
    fields.add('variants/numalt')
    fields.add('variants/svlen')


def _add_all_fixed_variants_fields(fields):
    for f in FIXED_VARIANTS_FIELDS:
        fields.add('variants/' + f)


def _add_all_info_fields(fields, headers):
    for f in headers.infos:
        fields.add('variants/' + f)


def _add_all_filter_fields(fields, headers):
    fields.add('variants/FILTER_PASS')
    for f in headers.filters:
        fields.add('variants/FILTER_' + f)


def _add_all_calldata_fields(fields, headers):
    # only add calldata fields if there are samples
    if headers.samples:
        for f in headers.formats:
            fields.add('calldata/' + f)


def _normalize_fields(fields, headers, samples):

    # setup normalized fields
    normed_fields = set()

    # special case, single field specification
    if isinstance(fields, str):
        fields = [fields]

    for f in fields:

        # special cases: be lenient about how to specify

        if f in ['*', 'kitchen sink']:
            _add_all_fields(normed_fields, headers, samples)

        elif f in ['variants', 'variants*', 'variants/*']:
            _add_all_variants_fields(normed_fields, headers)

        elif f in ['calldata', 'calldata*', 'calldata/*'] and samples:
            _add_all_calldata_fields(normed_fields, headers)

        elif f in ['INFO', 'INFO*', 'INFO/*', 'variants/INFO', 'variants/INFO*', 'variants/INFO/*']:
            _add_all_info_fields(normed_fields, headers)

        elif f in ['FILTER', 'FILTER*', 'FILTER/*', 'FILTER_*', 'variants/FILTER',
                   'variants/FILTER*', 'variants/FILTER/*', 'variants/FILTER_*']:
            _add_all_filter_fields(normed_fields, headers)

        # exact field specification

        else:

            # normalize field specification
            f = _normalize_field_prefix(f, headers)
            _check_field(f, headers)
            if f.startswith('calldata/') and not samples:
                # only add calldata fields if there are samples
                pass
            else:
                normed_fields.add(f)

    return normed_fields


default_integer_dtype = 'i4'
default_float_dtype = 'f4'
default_string_dtype = 'S12'


def _normalize_type(t):
    if t == 'Integer':
        return np.dtype(default_integer_dtype)
    elif t == 'Float':
        return np.dtype(default_float_dtype)
    elif t == 'String':
        return np.dtype(default_string_dtype)
    elif t == 'Flag':
        return np.dtype(bool)
    elif isinstance(t, str) and t.startswith('genotype/'):
        # custom genotype dtype
        return t
    elif isinstance(t, str) and t.startswith('genotype_ac/'):
        # custom genotype allele counts dtype
        return t
    else:
        return np.dtype(t)


default_types = {
    'variants/CHROM': 'S12',
    'variants/POS': 'i4',
    'variants/ID': 'S12',
    'variants/REF': 'S1',
    'variants/ALT': 'S1',
    'variants/QUAL': 'f4',
    'variants/DP': 'i4',
    'variants/AN': 'i4',
    'variants/AC': 'i4',
    'variants/AF': 'f4',
    'variants/MQ': 'f4',
    'variants/ANN': 'S400',
    'calldata/GT': 'genotype/i1',
    'calldata/GQ': 'i1',
    'calldata/HQ': 'i1',
    'calldata/DP': 'i2',
    'calldata/AD': 'i2',
    'calldata/MQ0': 'i2',
    'calldata/MQ': 'f2',
}


def _normalize_types(types, fields, headers):

    # normalize user-provided types
    if types is None:
        types = dict()
    types = {_normalize_field_prefix(f, headers): _normalize_type(t)
             for f, t in types.items()}

    # setup output
    normed_types = dict()

    for f in fields:

        group, name = f.split('/')

        if f in types:
            normed_types[f] = types[f]

        elif f in default_types:
            normed_types[f] = _normalize_type(default_types[f])

        elif group == 'variants':

            if name in ['numalt', 'svlen']:
                # computed fields, special case
                continue

            elif name.startswith('FILTER_'):
                normed_types[f] = np.dtype(bool)

            elif name in headers.infos:
                normed_types[f] = _normalize_type(headers.infos[name]['Type'])

            else:
                # fall back to string
                normed_types[f] = _normalize_type('String')
                warnings.warn('could not determine type for field %r, falling back to %s' %
                              (f, normed_types[f]))

        elif group == 'calldata':

            if name in headers.formats:
                normed_types[f] = _normalize_type(headers.formats[name]['Type'])

            else:
                # fall back to string
                normed_types[f] = _normalize_type('String')
                warnings.warn('could not determine type for field %r, falling back to %s' %
                              (f, normed_types[f]))

        else:
            raise RuntimeError('unpected field: %r' % f)

    return normed_types


default_numbers = {
    'variants/CHROM': 1,
    'variants/POS': 1,
    'variants/ID': 1,
    'variants/REF': 1,
    'variants/ALT': 3,
    'variants/QUAL': 1,
    'variants/DP': 1,
    'variants/AN': 1,
    'variants/AC': 3,
    'variants/AF': 3,
    'variants/MQ': 1,
    'calldata/DP': 1,
    'calldata/GT': 2,
    'calldata/GQ': 1,
    'calldata/HQ': 2,
    'calldata/AD': 4,
    'calldata/MQ0': 1,
    'calldata/MQ': 1,
}


def _normalize_number(field, n):
    if n == '.':
        return 1
    elif n == 'A':
        return 3
    elif n == 'R':
        return 4
    elif n == 'G':
        return 3
    else:
        try:
            return int(n)
        except ValueError:
            warnings.warn('error parsing %r as number for field %r' % (n, field))
        return 1


def _normalize_numbers(numbers, fields, headers):

    # normalize user-provided numbers
    if numbers is None:
        numbers = dict()
    numbers = {_normalize_field_prefix(f, headers): _normalize_number(f, n)
               for f, n in numbers.items()}

    # setup output
    normed_numbers = dict()

    for f in fields:

        group, name = f.split('/')

        if f in numbers:
            normed_numbers[f] = numbers[f]

        elif f in default_numbers:
            normed_numbers[f] = default_numbers[f]

        elif group == 'variants':

            if name in ['numalt', 'svlen']:
                # computed fields, special case - number depends on ALT
                continue

            elif name.startswith('FILTER_'):
                normed_numbers[f] = 0

            elif name in headers.infos:
                normed_numbers[f] = _normalize_number(f, headers.infos[name]['Number'])

            else:
                # fall back to 1
                normed_numbers[f] = 1
                warnings.warn('could not determine number for field %r, falling back to 1' % f)

        elif group == 'calldata':

            if name in headers.formats:
                normed_numbers[f] = _normalize_number(f, headers.formats[name]['Number'])

            else:
                # fall back to 1
                normed_numbers[f] = 1
                warnings.warn('could not determine number for field %r, falling back to 1' % f)

        else:
            raise RuntimeError('unpected field: %r' % f)

    return normed_numbers


def _normalize_fills(fills, fields, headers):

    if fills is None:
        fills = dict()
    fills = {_normalize_field_prefix(f, headers): v
             for f, v in fills.items()}

    # setup output
    normed_fills = dict()

    for f in fields:

        if f in fills:
            normed_fills[f] = fills[f]

    return normed_fills


def _normalize_samples(samples, headers):
    loc_samples = np.zeros(len(headers.samples), dtype='u1')

    if samples is None:
        normed_samples = headers.samples
        loc_samples.fill(1)

    else:
        samples = set(samples)
        normed_samples = []
        for i, s in enumerate(headers.samples):
            if i in samples:
                normed_samples.append(s)
                samples.remove(i)
                loc_samples[i] = 1
            elif s in samples:
                normed_samples.append(s)
                samples.remove(s)
                loc_samples[i] = 1
        if samples:
            warnings.warn('samples not found, will be ignored: ' + ', '.join(map(repr, sorted(samples))))

    return normed_samples, loc_samples


def _read_vcf(stream, fields, types, numbers, chunk_length, block_length, n_threads,
              fills, region, samples):

    # read VCF headers
    headers = _read_vcf_headers(stream)

    # setup samples
    samples, loc_samples = _normalize_samples(samples, headers)

    # setup fields to read
    if fields is None:

        # choose default fields
        fields = set()
        _add_all_fixed_variants_fields(fields)
        fields.add('variants/FILTER_PASS')
        if samples and 'GT' in headers.formats:
            fields.add('calldata/GT')

    else:
        fields = _normalize_fields(fields, headers, samples)

    # setup data types
    types = _normalize_types(types, fields, headers)

    # setup numbers (a.k.a., arity)
    numbers = _normalize_numbers(numbers, fields, headers)

    # setup fills
    fills = _normalize_fills(fills, fields, headers)

    # setup chunks iterator
    if n_threads is None:
        chunks = VCFChunkIterator(
            stream, chunk_length=chunk_length, headers=headers, fields=fields, types=types, numbers=numbers,
            fills=fills, region=region, samples=loc_samples
        )
    else:
        # noinspection PyArgumentList
        chunks = VCFParallelChunkIterator(
            stream, chunk_length=chunk_length, block_length=block_length, n_threads=n_threads, headers=headers,
            fields=fields, types=types, numbers=numbers, fills=fills, region=region, samples=loc_samples
        )

    return samples, headers, chunks


# pre-compile some regular expressions
_re_filter_header = \
    re.compile('##FILTER=<ID=([^,]+),Description="([^"]+)">')
_re_info_header = \
    re.compile('##INFO=<ID=([^,]+),Number=([^,]+),Type=([^,]+),Description="([^"]+)">')
_re_format_header = \
    re.compile('##FORMAT=<ID=([^,]+),Number=([^,]+),Type=([^,]+),Description="([^"]+)">')


_VCFHeaders = namedtuple('VCFHeaders', ['headers', 'filters', 'infos', 'formats', 'samples'])


def _read_vcf_headers(stream):

    # setup
    headers = []
    samples = None
    filters = dict()
    infos = dict()
    formats = dict()

    # read first header line
    header = str(stream.readline(), 'ascii')

    while header and header[0] == '#':

        headers.append(header)

        if header.startswith('##FILTER'):

            match = _re_filter_header.match(header)
            if match is None:
                warnings.warn('invalid FILTER header: %r' % header)
            else:
                k, d = match.groups()
                filters[k] = {'ID': k, 'Description': d}

        elif header.startswith('##INFO'):

            match = _re_info_header.match(header)
            if match is None:
                warnings.warn('invalid INFO header: %r' % header)
            else:
                k, n, t, d = match.groups()
                infos[k] = {'ID': k, 'Number': n, 'Type': t, 'Description': d}

        elif header.startswith('##FORMAT'):

            match = _re_format_header.match(header)
            if match is None:
                warnings.warn('invalid FORMAT header: %r' % header)
            else:
                k, n, t, d = match.groups()
                formats[k] = {'ID': k, 'Number': n, 'Type': t, 'Description': d}

        elif header.startswith('#CHROM'):

            # parse out samples
            samples = header.strip().split('\t')[9:]
            break

        # read next header line
        header = str(stream.readline(), 'ascii')

    # check if we saw the mandatory header line or not
    if samples is None:
        # can't warn about this, it's fatal
        raise RuntimeError('VCF file is missing mandatory header line ("#CHROM...")')

    return _VCFHeaders(headers, filters, infos, formats, samples)
