# -*- coding: utf-8 -*-
"""
TODO:

* DONE Fix missing value in trailing item
* DONE Inintial implementation of vcf_to_npz
* DONE Initial implementation of vcf_to_hdf5
* DONE Add samples to output of read_vcf and store in vcf_to_... functions
* DONE Initial implementation of vcf_to_zarr
* DONE Parse FILTERS from header
* DONE Return filters as separate arrays in read_vcf
* DONE Store filters as separate arrays/datasets in vcf_to_... functions
* Parse INFO fields
* Parse other FORMAT fields
* Read from region via tabix
* Read from region via scanning
* Progress logging in vcf_to_... functions
* User-controlled dtypes
* User-controlled fill values
* User-controlled arities
* User-specified samples to parse
* Specialised parser for EFF
* Specialised parser for ANN
* Unit tests
* PY2 compatibility?

"""
from __future__ import absolute_import, print_function, division
import gzip
import sys
import itertools
import os
import re
from collections import namedtuple
import warnings


import numpy as np


from allel.opt.io_vcf_read import iter_vcf


def debug(*msg):
    print(*msg, file=sys.stderr)
    sys.stderr.flush()


DEFAULT_BUFFER_SIZE = 2**15
DEFAULT_CHUNK_LENGTH = 2**15
DEFAULT_CHUNK_WIDTH = 2**6
DEFAULT_TEMP_SIZE = 2**15


def read_vcf(path,
             fields=None,
             buffer_size=DEFAULT_BUFFER_SIZE,
             chunk_length=DEFAULT_CHUNK_LENGTH,
             temp_max_size=DEFAULT_TEMP_SIZE):
    """Read data from a VCF file into NumPy arrays.

    Parameters
    ----------
    path : str
        TODO
    fields : sequence of str
        TODO
    buffer_size : int
        TODO
    chunk_length : int
        TODO
    temp_max_size : int
        TODO

    Returns
    -------
    data : dict[str -> ndarray]
        A dictionary holding arrays.

    """

    # setup
    headers, chunks = read_vcf_chunks(path=path, fields=fields, buffer_size=buffer_size,
                                      chunk_length=chunk_length, temp_max_size=temp_max_size)

    # read all chunks into a list
    chunks = list(chunks)

    # setup output
    output = dict()
    # use binary string type for cross-platform compatibility
    output['samples'] = np.array(headers.samples).astype('S')

    if chunks:

        # find array keys
        keys = sorted(chunks[0].keys())

        # concatenate chunks
        for k in keys:
            output[k] = np.concatenate([chunk[k] for chunk in chunks], axis=0)

    return output


def vcf_to_npz(input_path, output_path,
               compressed=True,
               overwrite=False,
               fields=None,
               buffer_size=DEFAULT_BUFFER_SIZE,
               chunk_length=DEFAULT_CHUNK_LENGTH,
               temp_max_size=DEFAULT_TEMP_SIZE):
    """TODO"""

    # guard condition
    if not overwrite and os.path.exists(output_path):
        # TODO right exception class?
        raise ValueError('file exists at path %r; use overwrite=True to replace' % output_path)

    # read all data into memory
    data = read_vcf(path=input_path, fields=fields, buffer_size=buffer_size,
                    chunk_length=chunk_length, temp_max_size=temp_max_size)

    # setup save function
    if compressed:
        savez = np.savez_compressed
    else:
        savez = np.savez

    # save as npz
    savez(output_path, **data)


def vcf_to_hdf5(input_path, output_path,
                group='/',
                compression='gzip',
                compression_opts=1,
                shuffle=False,
                overwrite=False,
                fields=None,
                buffer_size=DEFAULT_BUFFER_SIZE,
                chunk_length=DEFAULT_CHUNK_LENGTH,
                chunk_width=DEFAULT_CHUNK_WIDTH,
                temp_max_size=DEFAULT_TEMP_SIZE):
    """TODO"""

    import h5py

    with h5py.File(output_path, mode='a') as h5f:

        # setup offset for loading
        offset = 0

        # obtain root group that data will be stored into
        root = h5f.require_group(group)

        # ensure sub-groups
        root.require_group('variants')
        root.require_group('calldata')

        # setup chunk iterator
        headers, chunks = read_vcf_chunks(input_path, fields=fields, buffer_size=buffer_size,
                                          chunk_length=chunk_length, temp_max_size=temp_max_size)
        # TODO this won't be necessary when using generators
        chunks = iter(chunks)

        # store samples
        name = 'samples'
        if name in root[group]:
            if overwrite:
                del root[group][name]
            else:
                # TODO right exception class?
                raise ValueError('dataset exists at path %r; use overwrite=True to replace' % name)
        root[group].create_dataset(name, data=np.array(headers.samples).astype('S'),
                                   chunks=None)

        # read first chunk
        chunk = next(chunks, None)

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
            root[group].create_dataset(name, shape=shape, maxshape=maxshape,
                                       chunks=chunk_shape, dtype=data.dtype,
                                       compression=compression,
                                       compression_opts=compression_opts, shuffle=shuffle)

        # reconstitute chunks iterator
        chunks = itertools.chain([chunk], chunks)

        # load chunks
        for chunk_index, chunk in enumerate(chunks):

            # compute length of current chunk
            current_chunk_length = chunk[keys[0]].shape[0]

            # new length of all arrays after loading this chunk
            new_length = offset + current_chunk_length

            # load arrays
            for k in keys:

                # data to be loaded
                data = chunk[k]

                # obtain dataset
                dataset = root[k]

                # ensure dataset is large enough
                if dataset.shape[0] < new_length:
                    dataset.resize(new_length, axis=0)

                # store the data
                dataset[offset:new_length, ...] = data

            # move offset
            offset = new_length


def vcf_to_zarr(input_path, output_path,
                group='/',
                compressor='default',
                fill_value=0,
                order='C',
                overwrite=False,
                fields=None,
                buffer_size=DEFAULT_BUFFER_SIZE,
                chunk_length=DEFAULT_CHUNK_LENGTH,
                chunk_width=DEFAULT_CHUNK_WIDTH,
                temp_max_size=DEFAULT_TEMP_SIZE):
    """TODO"""

    import zarr

    # open root group
    root = zarr.open_group(output_path, mode='a', path=group)

    # ensure sub-groups
    root.require_group('variants')
    root.require_group('calldata')

    # setup chunk iterator
    headers, chunks = read_vcf_chunks(input_path, fields=fields, buffer_size=buffer_size,
                                      chunk_length=chunk_length, temp_max_size=temp_max_size)
    # TODO this won't be necessary when using generators
    chunks = iter(chunks)

    # store samples
    root[group].create_dataset('samples', data=np.array(headers.samples).astype('S'),
                               compressor=None, overwrite=overwrite)

    # read first chunk
    chunk = next(chunks, None)

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
        shape = (0,) + data.shape[1:]
        root.create_dataset(k, shape=shape, chunks=chunk_shape, dtype=data.dtype,
                            compressor=compressor, overwrite=overwrite, fill_value=fill_value,
                            order=order)

    # reconstitute chunks iterator
    chunks = itertools.chain([chunk], chunks)

    # load chunks
    for chunk_index, chunk in enumerate(chunks):

        # load arrays
        for k in keys:

            # append data
            root[k].append(chunk[k], axis=0)


def read_vcf_chunks(path,
                    fields=None,
                    buffer_size=DEFAULT_BUFFER_SIZE,
                    chunk_length=DEFAULT_CHUNK_LENGTH,
                    temp_max_size=DEFAULT_TEMP_SIZE):
    """TODO"""

    if isinstance(path, str) and path.endswith('gz'):
        # assume gzip-compatible compression
        with gzip.open(path, mode='rb') as binary_file:
            return _read_vcf(binary_file, fields=fields, buffer_size=buffer_size,
                             chunk_length=chunk_length, temp_max_size=temp_max_size)

    elif isinstance(path, str):
        # assume no compression
        with open(path, mode='rb', buffering=0) as binary_file:
            return _read_vcf(binary_file, fields=fields, buffer_size=buffer_size,
                             chunk_length=chunk_length, temp_max_size=temp_max_size)

    else:
        # assume some other binary file-like object
        binary_file = path
        return _read_vcf(binary_file, fields=fields, buffer_size=buffer_size,
                         chunk_length=chunk_length, temp_max_size=temp_max_size)


FIXED_VARIANTS_FIELDS = (
    'CHROM',
    'POS',
    'ID',
    'REF',
    'ALT',
    'QUAL',
)


def normalize_field_prefix(field, headers):
    """TODO"""

    # already contains prefix?
    if field.startswith('variants/') or field.startswith('calldata/'):
        return field

    # try to find in fixed fields first
    elif field in FIXED_VARIANTS_FIELDS:
        return 'variants/' + field

    # try to find in FILTER next
    elif field.startswith('FILTER_'):
        return 'variants/' + field

    # try to find in FILTER next
    elif field in headers.filters:
        return 'variants/FILTER_' + field

    # try to find in INFO next
    elif field in headers.infos:
        return 'variants/' + field

    # try to find in FORMAT next
    elif field in headers.formats:
        return 'calldata/' + field

    else:
        # assume anything else in variants, even if not declared in header
        return 'variants/' + field


def check_field(field, headers):
    """TODO"""

    # assume field is already normalized for prefix
    group, name = field.split('/')

    if group == 'variants':

        if name in FIXED_VARIANTS_FIELDS:
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


def add_all_fields(fields, headers):
    add_all_variants_fields(fields, headers)
    add_all_calldata_fields(fields, headers)


def add_all_variants_fields(fields, headers):
    add_all_fixed_variants_fields(fields)
    add_all_info_fields(fields, headers)
    add_all_filter_fields(fields, headers)


def add_all_fixed_variants_fields(fields):
    for f in FIXED_VARIANTS_FIELDS:
        fields.add('variants/' + f)


def add_all_info_fields(fields, headers):
    for f in headers.infos:
        fields.add('variants/' + f)


def add_all_filter_fields(fields, headers):
    fields.add('variants/FILTER_PASS')
    for f in headers.filters:
        fields.add('variants/FILTER_' + f)


def add_all_calldata_fields(fields, headers):
    for f in headers.formats:
        fields.add('calldata/' + f)


def normalize_fields(fields, headers):

    # setup normalized fields
    normed_fields = set()

    # special case, single field specification
    if isinstance(fields, str):
        fields = [fields]

    for f in fields:

        # special cases: be lenient about how to specify

        if f == '*':
            add_all_fields(normed_fields, headers)

        elif f in ['variants', 'variants*', 'variants/*']:
            add_all_variants_fields(normed_fields, headers)

        elif f in ['calldata', 'calldata*', 'calldata/*']:
            add_all_calldata_fields(normed_fields, headers)

        elif f in ['INFO', 'INFO*', 'INFO/*', 'variants/INFO', 'variants/INFO*', 'variants/INFO/*']:
            add_all_info_fields(normed_fields, headers)

        elif f in ['FILTER', 'FILTER*', 'FILTER_*', 'variants/FILTER', 'variants/FILTER*',
                   'variants/FILTER/*']:
            add_all_filter_fields(normed_fields, headers)

        # exact field specification

        else:

            # normalize field specification
            f = normalize_field_prefix(f, headers)
            check_field(f, headers)
            normed_fields.add(f)

    return normed_fields


def _read_vcf(fileobj, fields, buffer_size, chunk_length, temp_max_size):

    # read VCF headers
    headers = read_vcf_headers(fileobj)

    # setup fields to read
    if fields is None:

        # choose default fields
        fields = set()
        add_all_fixed_variants_fields(fields)
        fields.add('variants/FILTER_PASS')
        fields.add('calldata/GT')

    else:
        fields = normalize_fields(fields, headers)

    # setup chunks iterator
    chunks = iter_vcf(fileobj, buffer_size=buffer_size, chunk_length=chunk_length,
                      temp_max_size=temp_max_size, headers=headers, fields=fields)

    return headers, chunks


def _binary_readline(binary_file):
    # N.B., cannot do this with standard library text I/O because we don't want to advance the
    # underlying stream beyond exactly the number of bytes read for the header
    line = []
    c = binary_file.read(1)
    while c and c != b'\n':
        line.append(c)
        c = binary_file.read(1)
    line = b''.join(line)
    line = str(line, 'ascii')
    return line


# pre-compile some regular expressions
re_filter_header = \
    re.compile('##FILTER=<ID=([^,]+),Description="([^"]+)">')
re_info_header = \
    re.compile('##INFO=<ID=([^,]+),Number=([^,]+),Type=([^,]+),Description="([^"]+)">')
re_format_header = \
    re.compile('##FORMAT=<ID=([^,]+),Number=([^,]+),Type=([^,]+),Description="([^"]+)">')


VCFHeaders = namedtuple('VCFHeaders', ['headers', 'filters', 'infos', 'formats', 'samples'])


def read_vcf_headers(binary_file):

    # setup
    headers = []
    samples = None
    filters = dict()
    infos = dict()
    formats = dict()

    # read first header line
    header = _binary_readline(binary_file)

    while header and header[0] == '#':
        # debug('found header', header)

        headers.append(header)

        if header.startswith('##FILTER'):

            match = re_filter_header.match(header)
            if match is None:
                warnings.warn('invalid FILTER header: %r' % header)
            else:
                k, d = match.groups()
                filters[k] = {
                    'ID': k,
                    'Description': d
                }

        elif header.startswith('##INFO'):

            match = re_info_header.match(header)
            if match is None:
                warnings.warn('invalid INFO header: %r' % header)
            else:
                k, n, t, d = match.groups()
                infos[k] = {
                    'ID': k,
                    'Number': n,
                    'Type': t,
                    'Description': d
                }

        elif header.startswith('##FORMAT'):

            match = re_format_header.match(header)
            if match is None:
                warnings.warn('invalid FORMAT header: %r' % header)
            else:
                k, n, t, d = match.groups()
                formats[k] = {
                    'ID': k,
                    'Number': n,
                    'Type': t,
                    'Description': d
                }

        elif header.startswith('#CHROM'):

            # parse out samples
            samples = header.split('\t')[9:]
            break

        # read next header line
        header = _binary_readline(binary_file)

    # check if we saw the mandatory header line or not
    if samples is None:
        # can't warn about this, it's fatal
        raise RuntimeError('VCF file is missing mandatory header line ("#CHROM...")')

    return VCFHeaders(headers, filters, infos, formats, samples)
