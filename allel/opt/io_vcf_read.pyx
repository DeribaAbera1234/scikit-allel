# cython: language_level=3
# cython: profile=False
# cython: linetrace=False
# cython: binding=False
"""
# cython: profile=True
# cython: linetrace=True
# cython: binding=True
# distutils: define_macros=CYTHON_TRACE=1
# distutils: define_macros=CYTHON_TRACE_NOGIL=1
"""


import sys
from cpython.bytes cimport PyBytes_AS_STRING, PyBytes_FromStringAndSize
from libc.stdlib cimport strtol, strtof
import numpy as np
cimport numpy as np
import cython
# noinspection PyUnresolvedReferences
cimport cython


cdef extern from "Python.h":
    char* PyByteArray_AS_STRING(object string)


cdef char TAB = b'\t'
cdef char NEWLINE = b'\n'
cdef char HASH = b'#'
cdef char COLON = b':'
cdef char PERIOD = b'.'
cdef char COMMA = b','
cdef char SLASH = b'/'
cdef char PIPE = b'|'


def iter_vcf(binary_file, buffer_size, block_size, n_samples, temp_max_size):
    cdef:
        ParserContext context
        StringParser chrom_parser
        Parser pos_parser
        StringParser id_parser
        StringParser ref_parser
        AltParser alt_parser
        Parser qual_parser
        FilterParser filter_parser
        InfoParser info_parser
        FormatParser format_parser
        CalldataParser calldata_parser

    # setup output
    blocks = []

    # setup reader
    reader = BufferedReader(binary_file, buffer_size=buffer_size)

    # setup context
    context = ParserContext(reader, temp_max_size=temp_max_size, n_samples=n_samples)

    # read in first character
    ParserContext_next(context)

    # TODO setup parsers
    chrom_parser = StringParser(field_name='CHROM', block_size=block_size, dtype='S12')
    pos_parser = PosInt32Parser(block_size=block_size)
    id_parser = StringParser(field_name='ID', block_size=block_size, dtype='S12')
    ref_parser = StringParser(field_name='REF', block_size=block_size, dtype='S1')
    alt_parser = AltParser(block_size=block_size, dtype='S1', arity=3)
    qual_parser = QualFloat32Parser(block_size=block_size, fill=-1)
    # TODO discover filters from header
    filter_parser = FilterParser(block_size=block_size, filters=[])
    # TODO discuver INFO fields from header
    info_parser = InfoParser(block_size=block_size)
    format_parser = FormatParser()
    # # TODO handle all FORMAT fields
    calldata_parser = CalldataParser(block_size=block_size,
                                     formats=[b'GT'],
                                     n_samples=context.n_samples,
                                     ploidy=2)

    while True:

        if context.c == 0:
            break

        elif context.state == ParserState.CHROM:
            chrom_parser.parse(context)
            context.state = ParserState.POS

        elif context.state == ParserState.POS:
            pos_parser.parse(context)
            context.state = ParserState.ID

        elif context.state == ParserState.ID:
            id_parser.parse(context)
            context.state = ParserState.REF

        elif context.state == ParserState.REF:
            ref_parser.parse(context)
            context.state = ParserState.ALT

        elif context.state == ParserState.ALT:
            alt_parser.parse(context)
            context.state = ParserState.QUAL

        elif context.state == ParserState.QUAL:
            qual_parser.parse(context)
            context.state = ParserState.FILTER

        elif context.state == ParserState.FILTER:
            filter_parser.parse(context)
            context.state = ParserState.INFO

        elif context.state == ParserState.INFO:
            info_parser.parse(context)
            context.state = ParserState.FORMAT

        elif context.state == ParserState.FORMAT:
            format_parser.parse(context)
            context.state = ParserState.CALLDATA

        elif context.state == ParserState.CALLDATA:
            calldata_parser.parse(context)
            context.state = ParserState.CHROM

            # setup next variant
            # debug('setup next variant')
            context.variant_index += 1
            if context.block_variant_index < block_size - 1:
                context.block_variant_index += 1
            else:
                # debug('start new block')
                block = {
                    'variants/CHROM': chrom_parser.values,
                    'variants/POS': pos_parser.values,
                    'variants/ID': id_parser.values,
                    'variants/REF': ref_parser.values,
                    'variants/ALT': alt_parser.values,
                    'variants/QUAL': qual_parser.values,
                    'variants/FILTER': filter_parser.values,
                    # TODO INFO
                    'calldata/GT': calldata_parser.get_parser(b'GT').values,
                    # TODO other calldata
                }
                # setup next block
                context.block_variant_index = 0
                blocks.append(block)
                chrom_parser.malloc()
                pos_parser.malloc()
                id_parser.malloc()
                ref_parser.malloc()
                alt_parser.malloc()
                qual_parser.malloc()
                filter_parser.malloc()
                info_parser.malloc()
                calldata_parser.malloc()

        else:
            raise Exception('unexpected parser state')

    # left-over block
    l = context.block_variant_index + 1
    block = {
        'variants/CHROM': chrom_parser.values[:l],
        'variants/POS': pos_parser.values[:l],
        'variants/ID': id_parser.values[:l],
        'variants/REF': ref_parser.values[:l],
        'variants/ALT': alt_parser.values[:l],
        'variants/QUAL': qual_parser.values[:l],
        'variants/FILTER': filter_parser.values[:l],
        # TODO INFO
        'calldata/GT': calldata_parser.get_parser(b'GT').values[:l],
        # TODO other calldata
    }
    blocks.append(block)

    # TODO yield
    return blocks


def debug(*msg):
    print(*msg, file=sys.stderr)
    sys.stderr.flush()


cdef class BufferedReader(object):

    cdef object binary_file
    cdef int buffer_size
    cdef bytearray buffer
    cdef char* stream
    cdef char* stream_end

    def __cinit__(self, binary_file, buffer_size):
        self.binary_file = binary_file
        self.buffer_size = buffer_size
        self.buffer = bytearray(buffer_size)
        BufferedReader_fill_buffer(self)


# break out method as function for profiling
cdef inline void BufferedReader_fill_buffer(BufferedReader self):
    cdef:
        int l
    l = self.binary_file.readinto(self.buffer)
    # debug(l, self.buffer)
    if l > 0:
        self.stream = PyByteArray_AS_STRING(self.buffer)
        self.stream_end = self.stream + l
    else:
        self.stream = NULL


# break out method as function for profiling
cdef inline char BufferedReader_read(BufferedReader self):
    cdef:
        char c
    if self.stream == self.stream_end:
        BufferedReader_fill_buffer(self)
    if self.stream == NULL:
        return 0
    c = self.stream[0]
    self.stream += 1
    return c


cdef enum ParserState:
    CHROM,
    POS,
    ID,
    REF,
    ALT,
    QUAL,
    FILTER,
    INFO,
    FORMAT,
    CALLDATA


cdef class ParserContext(object):

    cdef int state
    cdef bytes temp_bytes
    cdef char* temp
    cdef int temp_max_size
    cdef int temp_size
    cdef char c
    cdef BufferedReader reader
    cdef int n_samples
    cdef int variant_index
    cdef int block_variant_index
    cdef int sample_index
    cdef list formats
    cdef int format_index

    def __cinit__(self, reader, temp_max_size, n_samples):
        self.reader = reader
        self.state = ParserState.CHROM
        self.temp_bytes = PyBytes_FromStringAndSize(NULL, temp_max_size)
        self.temp = PyBytes_AS_STRING(self.temp_bytes)
        self.temp_max_size = temp_max_size
        self.temp_size = 0
        self.n_samples = n_samples
        self.variant_index = 0
        self.block_variant_index = 0
        self.sample_index = 0
        self.format_index = 0


@cython.nonecheck(False)
@cython.initializedcheck(False)
cdef inline void ParserContext_next(ParserContext self):
    cdef:
        BufferedReader reader

    reader = self.reader
    self.c = BufferedReader_read(reader)


cdef class Parser(object):
    """Abstract base class."""

    cdef object values
    cdef int block_size

    cdef parse(self, ParserContext context):
        pass

    cdef malloc(self):
        pass


def check_string_dtype(dtype):
    dtype = np.dtype(dtype)
    if dtype.kind != 'S':
        raise ValueError('expected byte string ("S") dtype, found: %r' % dtype)
    return dtype


cdef class StringParser(Parser):
    """Generic string field parser, used for CHROM, ID, REF."""

    cdef object dtype
    cdef int itemsize
    cdef np.uint8_t[:] memory
    cdef object field_name

    def __cinit__(self, field_name, block_size, dtype):
        self.field_name = field_name
        self.block_size = block_size
        self.dtype = check_string_dtype(dtype)
        self.itemsize = self.dtype.itemsize
        self.malloc()

    cdef malloc(self):
        self.values = np.zeros(self.block_size, dtype=self.dtype)
        self.memory = self.values.view('u1')

    cdef parse(self, ParserContext context):
        StringParser_parse(self, context)


# break out method as function for profiling
@cython.nonecheck(False)
@cython.initializedcheck(False)
@cython.boundscheck(False)
@cython.wraparound(False)
cdef inline void StringParser_parse(StringParser self, ParserContext context):
    cdef:
        # index into memory view
        int memory_index
        # number of characters read into current value
        int chars_stored = 0

    # debug('StringParser_parse', self.field_name)

    # initialise memory index
    memory_index = context.block_variant_index * self.itemsize

    # read characters until tab
    while context.c != TAB:
        if chars_stored < self.itemsize:
            # store value
            self.memory[memory_index] = context.c
            # advance memory index
            memory_index += 1
            # advance number of characters stored
            chars_stored += 1
        # advance input stream
        ParserContext_next(context)

    # advance input stream beyond tab
    ParserContext_next(context)

    # debug(context.variant_index, self.field_name, self.values[context.block_variant_index],
    #       chars_stored)


cdef class PosInt32Parser(Parser):
    """Parser for POS field."""

    cdef np.int32_t[:] memory

    def __cinit__(self, block_size):
        self.block_size = block_size
        self.malloc()

    cdef malloc(self):
        self.values = np.zeros(self.block_size, dtype='i4')
        self.memory = self.values

    cdef parse(self, ParserContext context):
        PosInt32Parser_parse(self, context)


# break out method as function for profiling
@cython.nonecheck(False)
@cython.initializedcheck(False)
@cython.boundscheck(False)
@cython.wraparound(False)
cdef inline void PosInt32Parser_parse(PosInt32Parser self, ParserContext context):
    cdef:
        long value
        char* str_end

    # reset temporary buffer
    context.temp_size = 0

    # read into temporary buffer until tab
    while context.c != TAB and context.temp_size < context.temp_max_size:
        context.temp[context.temp_size] = context.c
        context.temp_size += 1
        ParserContext_next(context)

    # parse string as integer
    context.temp[context.temp_size] = 0
    value = strtol(context.temp, &str_end, 10)
    # debug((<bytes>context.temp)[:context.temp_size])
    # debug(context.variant_index, 'POS', value)

    # check success
    if str_end > context.temp:

        # store value
        self.memory[context.block_variant_index] = value

        # advance input stream
        ParserContext_next(context)

    else:
        raise RuntimeError('error %s parsing POS at variant index %s' %
                           (value, context.variant_index))


cdef class AltParser(Parser):
    """Parser for ALT field."""

    cdef object dtype
    cdef int itemsize
    cdef int arity
    cdef np.uint8_t[:] memory

    def __cinit__(self, block_size, dtype, arity):
        self.block_size = block_size
        self.dtype = check_string_dtype(dtype)
        self.itemsize = self.dtype.itemsize
        self.arity = arity
        self.malloc()

    cdef malloc(self):
        self.values = np.zeros((self.block_size, self.arity), dtype=self.dtype, order='C')
        self.memory = self.values.reshape(-1).view('u1')

    cdef parse(self, ParserContext context):
        AltParser_parse(self, context)


# break out method as function for profiling
@cython.nonecheck(False)
@cython.initializedcheck(False)
@cython.boundscheck(False)
@cython.wraparound(False)
cdef inline void AltParser_parse(AltParser self, ParserContext context):
    cdef:
        # index of alt values
        int alt_index = 0
        # index into memory view
        int memory_offset, memory_index
        # number of characters read into current value
        int chars_stored = 0

    # initialise memory offset and index
    memory_offset = context.block_variant_index * self.itemsize * self.arity
    memory_index = memory_offset

    # read characters until tab
    while True:
        if context.c == TAB:
            ParserContext_next(context)
            break
        elif context.c == COMMA:
            # advance value index
            alt_index += 1
            # set memory index to beginning of next item
            memory_index = memory_offset + (alt_index * self.itemsize)
            # reset chars stored
            chars_stored = 0
        elif chars_stored < self.itemsize:
            # store value
            self.memory[memory_index] = context.c
            # advance memory index
            memory_index += 1
            # advance number of characters stored
            chars_stored += 1
        # advance input stream
        ParserContext_next(context)

    # debug(context.variant_index, 'ALT', self.values[context.block_variant_index])


cdef class QualFloat32Parser(Parser):

    cdef np.float32_t fill
    cdef np.float32_t[:] memory

    def __cinit__(self, block_size, fill):
        # debug('QualFloat32Parser __cinit__()')
        self.block_size = block_size
        self.fill = fill
        self.malloc()

    cdef malloc(self):
        # debug('QualFloat32Parser malloc()')
        self.values = np.empty(self.block_size, dtype='f4')
        self.memory = self.values
        self.memory[:] = self.fill

    cdef parse(self, ParserContext context):
        QualFloat32Parser_parse(self, context)


# break out method as function for profiling
@cython.nonecheck(False)
@cython.initializedcheck(False)
@cython.boundscheck(False)
@cython.wraparound(False)
cdef inline void QualFloat32Parser_parse(QualFloat32Parser self, ParserContext context):
    cdef:
        float value
        char* str_end

    # reset temporary buffer
    context.temp_size = 0

    # read into temporary buffer until tab
    while context.c != TAB and context.temp_size < context.temp_max_size:
        context.temp[context.temp_size] = context.c
        context.temp_size += 1
        ParserContext_next(context)

    if context.temp_size == 0:
        # completely missing value - not strictly kosher
        pass

    elif context.temp_size == 1 and context.temp[0] == PERIOD:
        # missing value
        pass

    else:

        # parse string as float
        context.temp[context.temp_size] = 0
        value = strtof(context.temp, &str_end)
        # debug((<bytes>context.temp)[:context.temp_size])
        # debug(context.variant_index, 'QUAL', value)

        # check success
        if str_end > context.temp:

            # store value
            self.memory[context.block_variant_index] = value

            # advance input stream
            ParserContext_next(context)

        else:
            raise RuntimeError('error %s parsing QUAL at variant index %s' %
                               (value, context.variant_index))


cdef class FilterParser(Parser):

    cdef tuple filters
    cdef dict filter_position
    cdef np.uint8_t[:, :] memory

    def __cinit__(self, block_size, filters):
        self.block_size = block_size
        self.filters = tuple(filters)
        # PASS comes first
        self.filter_position = {f: i + 1 for i, f in enumerate(self.filters)}
        self.filter_position[b'PASS'] = 0
        self.malloc()

    cdef malloc(self):
        self.values = np.zeros((self.block_size, len(self.filters) + 1), dtype=bool)
        self.memory = self.values.view('u1')

    cdef parse(self, ParserContext context):
        FilterParser_parse(self, context)

    cdef store(self, ParserContext context):
        FilterParser_store(self, context)


# break out method as function for profiling
@cython.nonecheck(False)
@cython.initializedcheck(False)
@cython.boundscheck(False)
@cython.wraparound(False)
cdef inline void FilterParser_parse(FilterParser self, ParserContext context):
    cdef:
        int filter_index

    # reset temporary buffer
    context.temp_size = 0

    while context.temp_size < context.temp_max_size:

        if context.c == TAB:
            if context.temp_size > 0:
                FilterParser_store(self, context)
            break

        elif context.c == COMMA:  # TODO semi-colon, colon?
            if context.temp_size > 0:
                FilterParser_store(self, context)
                context.temp_size = 0
            else:
                raise RuntimeError('found bad FILTER at variant index %s' %
                                   context.variant_index)

        elif context.c == PERIOD:
            # TODO review safety here
            pass

        else:
            context.temp[context.temp_size] = context.c
            context.temp_size += 1

        # advance to next character
        ParserContext_next(context)

    # advance to next field
    ParserContext_next(context)

    # debug(context.variant_index, 'FILTER', self.values[context.block_variant_index])


cdef inline void FilterParser_store(FilterParser self, ParserContext context):
    # TODO needs optimising?
    cdef:
        bytes f
        int filter_index

    # read filter into byte string
    f = PyBytes_FromStringAndSize(context.temp, context.temp_size)

    # find filter position
    filter_index = self.filter_position.get(f, -1)

    # store value
    if filter_index >= 0:
        self.memory[context.block_variant_index, filter_index] = 1

    # debug(context.variant_index, 'FILTER', f)


cdef class InfoParser(Parser):

    def __cinit__(self, block_size):
        pass

    cdef malloc(self):
        pass

    cdef parse(self, ParserContext context):
        InfoParser_parse(self, context)


# break out method as function for profiling
@cython.nonecheck(False)
@cython.initializedcheck(False)
@cython.boundscheck(False)
@cython.wraparound(False)
cdef inline void InfoParser_parse(InfoParser self, ParserContext context):
    # TODO
    # debug('InfoParser_parse()')

    while context.c != TAB:
        # debug('INFO', context.c)
        ParserContext_next(context)

    ParserContext_next(context)

    # debug(context.variant_index, 'INFO')


cdef class FormatParser(Parser):

    def __cinit__(self):
        pass

    cdef malloc(self):
        pass

    cdef parse(self, ParserContext context):
        FormatParser_parse(self, context)


# break out method as function for profiling
@cython.nonecheck(False)
@cython.initializedcheck(False)
@cython.boundscheck(False)
@cython.wraparound(False)
cdef inline void FormatParser_parse(FormatParser self, ParserContext context):
    cdef:
        char* format
        bytes f
        list formats = []

    # debug('FormatParser_parse()')

    # reset temporary buffer
    context.temp_size = 0

    while context.temp_size < context.temp_max_size:
        # debug(context.c)

        if context.c == TAB:
            # debug('tab')

            # add last format
            if context.temp_size > 0:
                # debug('add last format')
                f = PyBytes_FromStringAndSize(context.temp, context.temp_size)
                formats.append(f)

            # set context
            context.formats = formats

            # we're done here
            break

        elif context.c == COLON:
            # debug('colon')

            # add format
            if context.temp_size > 0:
                # debug('add format')
                f = PyBytes_FromStringAndSize(context.temp, context.temp_size)
                formats.append(f)
                context.temp_size = 0

        else:
            # debug('other')

            context.temp[context.temp_size] = context.c
            context.temp_size += 1

        # advance to next character
        ParserContext_next(context)

    # advance to next field
    ParserContext_next(context)

    # debug(context.variant_index, 'FORMAT', formats)

# noinspection PyShadowingBuiltins
cdef class CalldataParser(Parser):

    cdef dict parsers
    cdef Parser dummy_parser

    def __cinit__(self, block_size, formats, n_samples, ploidy):
        self.parsers = dict()
        for f in formats:
            if f == b'GT':
                self.parsers[f] = GenotypeInt8Parser(block_size=block_size,
                                                     n_samples=n_samples,
                                                     ploidy=ploidy,
                                                     fill=-1)
            # TODO initialise parsers for all fields
        self.dummy_parser = DummyCalldataParser()

    cdef malloc(self):
        cdef Parser parser
        for parser in self.parsers.values():
            parser.malloc()

    cdef parse(self, ParserContext context):
        CalldataParser_parse(self, context)

    cdef Parser get_parser(self, bytes format):
        return <Parser> self.parsers.get(format, self.dummy_parser)


# break out method as function for profiling
@cython.nonecheck(False)
@cython.initializedcheck(False)
@cython.boundscheck(False)
@cython.wraparound(False)
cdef inline void CalldataParser_parse(CalldataParser self, ParserContext context):
    cdef:
        list parsers
        Parser parser

    # initialise context
    context.sample_index = 0
    context.format_index = 0

    # initialise format parsers in correct order for this variant
    parsers = [self.parsers.get(f, self.dummy_parser) for f in context.formats]
    parser = <Parser> parsers[0]

    while True:

        if context.c == 0 or context.c == NEWLINE:
            ParserContext_next(context)
            break

        elif context.c == TAB:

            context.sample_index += 1
            context.format_index = 0
            parser = <Parser> parsers[0]
            ParserContext_next(context)

        elif context.c == COLON:

            context.format_index += 1
            parser = <Parser> parsers[context.format_index]
            ParserContext_next(context)

        else:

            # debug('format parse', context.format_index,
            #       context.formats[context.format_index],
            #       repr(parser))
            parser.parse(context)


cdef class GenotypeInt8Parser(Parser):

    cdef int n_samples
    cdef int ploidy
    cdef np.int8_t[:, :, :] memory
    cdef np.int8_t fill

    def __cinit__(self, block_size, n_samples, ploidy, fill):
        self.block_size = block_size
        self.n_samples = n_samples
        self.ploidy = ploidy
        self.fill = fill
        self.malloc()

    cdef malloc(self):
        self.values = np.empty((self.block_size, self.n_samples, self.ploidy), dtype='i1')
        self.memory = self.values
        self.memory[:] = self.fill

    cdef parse(self, ParserContext context):
        GenotypeInt8Parser_parse(self, context)

    cdef store(self, ParserContext context, int allele_index):
        GenotypeInt8Parser_store(self, context, allele_index)


@cython.boundscheck(False)
@cython.wraparound(False)
@cython.initializedcheck(False)
@cython.nonecheck(False)
cdef inline void GenotypeInt8Parser_parse(GenotypeInt8Parser self, ParserContext context):
    cdef:
        int allele_index = 0
        long allele
        char* str_end
    # debug('GenotypeInt8Parser_parse')

    # reset temporary buffer
    context.temp_size = 0

    while context.temp_size < context.temp_max_size:

        if context.c == PERIOD:
            pass

        elif context.c == SLASH or context.c == PIPE:

            GenotypeInt8Parser_store(self, context, allele_index)
            allele_index += 1

        elif context.c == COLON or context.c == TAB or context.c == NEWLINE:

            GenotypeInt8Parser_store(self, context, allele_index)
            break

        else:

            context.temp[context.temp_size] = context.c
            context.temp_size += 1

        ParserContext_next(context)

    # debug(context.variant_index, context.sample_index, 'GT',
    #       self.values[context.block_variant_index, context.sample_index])


@cython.boundscheck(False)
@cython.wraparound(False)
@cython.initializedcheck(False)
@cython.nonecheck(False)
cdef inline void GenotypeInt8Parser_store(GenotypeInt8Parser self, ParserContext context,
                                          int allele_index):
    cdef:
        long allele
        char* str_end

    if allele_index >= self.ploidy:
        # more alleles than we've made room for, ignore
        pass

    elif context.temp_size == 0:
        # empty allele - note strictly kosher
        pass

        # raise RuntimeError('empty genotype allele at variant index %s, sample index %s, '
        #                    'allele index %s' % (context.variant_index, context.sample_index,
        #                                         allele_index))

    else:

        # attempt to parse allele
        context.temp[context.temp_size] = 0
        allele = strtol(context.temp, &str_end, 10)
        # debug('storing allele', context.variant_index, context.sample_index, allele_index, allele)

        # reset temporary buffer
        context.temp_size = 0

        # store value
        if str_end > context.temp:
            self.memory[context.block_variant_index, context.sample_index, allele_index] = allele

        else:
            raise RuntimeError('error %s parsing genotype at variant index %s, sample index '
                               '%s' % (allele, context.variant_index, context.sample_index))


cdef class DummyCalldataParser(Parser):

    def __cinit__(self):
        pass

    cdef malloc(self):
        pass

    cdef parse(self, ParserContext context):
        DummyCalldataParser_parse(self, context)


# break out method as function for profiling
@cython.nonecheck(False)
@cython.initializedcheck(False)
@cython.boundscheck(False)
@cython.wraparound(False)
cdef inline void DummyCalldataParser_parse(DummyCalldataParser self, ParserContext context):
    # debug('DummyCalldataParser_parse')
    while True:

        if context.c == COLON or context.c == TAB or context.c == NEWLINE or context.c == 0:
            break

        ParserContext_next(context)
