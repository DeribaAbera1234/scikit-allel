# -*- coding: utf-8 -*-
# cython: profile=True
# cython: linetrace=False
# cython: binding=False
from __future__ import absolute_import, print_function, division


import numpy as np
cimport numpy as np
cimport cython
from libc.math cimport sqrt, fabs


@cython.boundscheck(False)
@cython.wraparound(False)
@cython.cdivision(True)
cpdef inline np.float32_t gn_corrcoef_int8(np.int8_t[:] gn0,
                                           np.int8_t[:] gn1,
                                           np.int8_t[:] gn0_sq,
                                           np.int8_t[:] gn1_sq,
                                           np.float32_t fill) nogil:
    cdef:
        np.int8_t x, y, xsq, ysq
        Py_ssize_t i
        int n
        np.float32_t m0, m1, v0, v1, cov, r

    # initialise variables
    m0 = m1 = v0 = v1 = cov = n = 0

    # iterate over input vectors
    for i in range(gn0.shape[0]):
        x = gn0[i]
        y = gn1[i]
        # consider negative values as missing
        if x >= 0 and y >= 0:
            n += 1
            m0 += x
            m1 += y
            xsq = gn0_sq[i]
            ysq = gn1_sq[i]
            v0 += xsq
            v1 += ysq
            cov += x * y

    # compute mean, variance, covariance
    m0 /= n
    m1 /= n
    v0 /= n
    v1 /= n
    cov /= n
    cov -= m0 * m1
    v0 -= m0 * m0
    v1 -= m1 * m1

    # compute correlation coeficient
    if v0 == 0 or v1 == 0:
        r = fill
    else:
        r = cov / sqrt(v0 * v1)

    return r


@cython.boundscheck(False)
@cython.wraparound(False)
def gn_pairwise_corrcoef_int8(np.int8_t[:, :] gn not None,
                              np.float32_t fill=np.nan):
    cdef:
        Py_ssize_t i, j, k, n
        np.float32_t r
        # correlation matrix in condensed form
        np.float32_t[:] out
        np.int8_t[:, :] gn_sq
        np.int8_t[:] gn0, gn1, gn0_sq, gn1_sq

    # cache square calculation to improve performance
    gn_sq = np.power(gn, 2)

    # setup output array
    n = gn.shape[0]
    # number of distinct pairs
    n_pairs = n * (n - 1) // 2
    out = np.zeros(n_pairs, dtype=np.float32)

    # iterate over distinct pairs
    with nogil:
        k = 0
        for i in range(n):
            for j in range(i+1, n):
                gn0 = gn[i]
                gn1 = gn[j]
                gn0_sq = gn_sq[i]
                gn1_sq = gn_sq[j]
                r = gn_corrcoef_int8(gn0, gn1, gn0_sq, gn1_sq, fill)
                out[k] = r
                k += 1

    return np.asarray(out)


@cython.boundscheck(False)
@cython.wraparound(False)
def gn_pairwise2_corrcoef_int8(np.int8_t[:, :] gna not None,
                               np.int8_t[:, :] gnb not None,
                               np.float32_t fill=np.nan):
    cdef:
        Py_ssize_t i, j, k, m, n
        np.float32_t r
        # correlation matrix in condensed form
        np.float32_t[:, :] out
        np.int8_t[:, :] gna_sq, gnb_sq
        np.int8_t[:] gn0, gn1, gn0_sq, gn1_sq

    # cache square calculation to improve performance
    gna_sq = np.power(gna, 2)
    gnb_sq = np.power(gnb, 2)

    # setup output array
    m = gna.shape[0]
    n = gnb.shape[0]
    out = np.zeros((m, n), dtype=np.float32)

    # iterate over distinct pairs
    with nogil:
        for i in range(m):
            for j in range(n):
                gn0 = gna[i]
                gn1 = gnb[j]
                gn0_sq = gna_sq[i]
                gn1_sq = gnb_sq[j]
                r = gn_corrcoef_int8(gn0, gn1, gn0_sq, gn1_sq, fill)
                out[i, j] = r

    return np.asarray(out)


@cython.boundscheck(False)
@cython.wraparound(False)
def gn_locate_unlinked_int8(np.int8_t[:, :] gn not None,
                            np.uint8_t[:] loc not None,
                            Py_ssize_t size, Py_ssize_t step,
                            np.float32_t threshold):
    cdef:
        Py_ssize_t window_start, window_stop, i, j, n_variants
        np.float32_t r_squared
        np.int8_t[:, :] gn_sq
        np.int8_t[:] gn0, gn1, gn0_sq, gn1_sq
        int overlap = size - step
        bint last
        np.float32_t fill = np.nan

    # cache square calculation to improve performance
    gn_sq = np.power(gn, 2)

    # setup
    n_variants = gn.shape[0]
    last = False

    for window_start in range(0, n_variants, step):
        with nogil:

            # determine end of current window
            window_stop = window_start + size
            if window_stop > n_variants:
                window_stop = n_variants
                last = True

            if window_start == 0:
                # first window
                for i in range(window_start, window_stop):
                    # only go further if still unlinked
                    if loc[i]:
                        for j in range(i+1, window_stop):
                            # only go further if still unlinked
                            if loc[j]:
                                gn0 = gn[i]
                                gn1 = gn[j]
                                gn0_sq = gn_sq[i]
                                gn1_sq = gn_sq[j]
                                r_squared = gn_corrcoef_int8(gn0, gn1, gn0_sq,
                                                             gn1_sq, fill) ** 2
                                if r_squared > threshold:
                                    loc[j] = 0

            else:
                # subsequent windows
                for i in range(window_start, window_stop):
                    # only go further if still unlinked
                    if loc[i]:
                        # don't recalculate anything from overlap with previous
                        # window
                        ii = max(i+1, window_start+overlap)
                        if ii < window_stop:
                            for j in range(ii, window_stop):
                                # only go further if still unlinked
                                if loc[j]:
                                    gn0 = gn[i]
                                    gn1 = gn[j]
                                    gn0_sq = gn_sq[i]
                                    gn1_sq = gn_sq[j]
                                    r_squared = gn_corrcoef_int8(gn0, gn1,
                                                                 gn0_sq,
                                                                 gn1_sq,
                                                                 fill) ** 2
                                    if r_squared > threshold:
                                        loc[j] = 0

            if last:
                break


@cython.boundscheck(False)
@cython.wraparound(False)
cpdef Py_ssize_t shared_prefix_length_int8(np.int8_t[:] a,
                                           np.int8_t[:] b) nogil:
    """Compute the length of the shared prefix between two arrays."""

    cdef:
        Py_ssize_t i, n

    # count up to the length of the shortest array
    n = min(a.shape[0], b.shape[0])

    # iterate until we find a difference
    for i in range(n):
        if a[i] != b[i]:
            return i

    # arrays are equal up to shared length
    return n


@cython.boundscheck(False)
@cython.wraparound(False)
cpdef pairwise_shared_prefix_lengths_int8(np.int8_t[:, :] h):
    """Compute the length of the shared prefix between all pairs of
    columns in a 2-dimensional array."""

    cdef:
        Py_ssize_t i, j, k, n, n_pairs
        np.int32_t[:] lengths

    # initialise variables
    n = h.shape[1]
    n_pairs = (n * (n - 1)) // 2
    lengths = np.empty(n_pairs, dtype='i4')
    k = 0

    # iterate over pairs
    with nogil:
        for i in range(n):
            for j in range(i+1, n):
                lengths[k] = shared_prefix_length_int8(h[:, i], h[:, j])
                k += 1

    return np.asarray(lengths)


@cython.boundscheck(False)
@cython.wraparound(False)
cpdef neighbour_shared_prefix_lengths_int8(np.int8_t[:, :] h):
    """Compute the length of the shared prefix between neighbouring
    columns in a 2-dimensional array."""

    cdef:
        Py_ssize_t i, n
        np.int32_t[:] lengths

    # initialise variables
    n = h.shape[1]
    lengths = np.empty(n-1, dtype='i4')

    # iterate over columns
    with nogil:
        for i in range(n-1):
            lengths[i] = shared_prefix_length_int8(h[:, i], h[:, i+1])

    return np.asarray(lengths)


@cython.boundscheck(False)
@cython.wraparound(False)
cpdef neighbour_shared_prefix_lengths_unsorted_int8(np.int8_t[:, :] h,
                                                    np.int64_t[:] indices):
    """Compute the length of the shared prefix between neighbouring
    columns in a 2-dimensional array."""

    cdef:
        Py_ssize_t i, n, ix, jx
        np.int32_t[:] lengths

    # initialise variables
    n = h.shape[1]
    lengths = np.empty(n-1, dtype='i4')

    # iterate over columns
    with nogil:
        for i in range(n-1):
            ix = indices[i]
            jx = indices[i+1]
            lengths[i] = shared_prefix_length_int8(h[:, ix], h[:, jx])

    return np.asarray(lengths)


@cython.boundscheck(False)
@cython.wraparound(False)
cpdef inline Py_ssize_t bisect_left_int8(np.int8_t[:] s, int x) nogil:
    """Optimized implementation of bisect_left."""
    cdef:
        Py_ssize_t l, u, m, v

    # initialise
    l = 0  # lower index
    u = s.shape[0]  # upper index

    # bisect
    while (u - l) > 1:
        m = (u + l) // 2
        v = s[m]
        if v >= x:
            u = m
        else:
            l = m

    # check boundary condition
    if s[l] >= x:
        return l

    return u


@cython.boundscheck(False)
@cython.wraparound(False)
def paint_shared_prefixes_int8(np.int8_t[:, :] h not None):
    """Paint each shared prefix with a different number. N.B., `h` must be
    already sorted by prefix.

    """

    cdef:
        Py_ssize_t n_variants, n_haplotypes, pp_start, pp_stop, pp_size, n0, n1
        np.int32_t pp_color, next_color
        np.int32_t[:, :] painting
        np.int8_t[:] s

    # initialise variables
    n_variants = h.shape[0]
    n_haplotypes = h.shape[1]
    prefixes = [(0, n_haplotypes, 1)]
    next_color = 2
    painting = np.zeros((n_variants, n_haplotypes), dtype='i4')

    # iterate over variants
    for i in range(n_variants):

        # setup for this iteration
        parent_prefixes = prefixes
        prefixes = list()

        if not parent_prefixes:
            # no more shared prefixes
            break

        # iterate over parent prefixes
        for pp_start, pp_stop, pp_color in parent_prefixes:
            pp_size = pp_stop - pp_start

            # find the split point
            s = h[i, pp_start:pp_stop]
            # number of reference alleles
            n0 = bisect_left_int8(s, 1)
            # number of alternate alleles
            n1 = pp_size - n0

            if n0 == 0 or n1 == 0:
                # no split, continue parent prefix
                painting[i, pp_start:pp_stop] = pp_color
                prefixes.append((pp_start, pp_stop, pp_color))

            elif n0 > n1:
                # ref is major, alt is minor
                painting[i, pp_start:pp_start+n0] = pp_color
                prefixes.append((pp_start, pp_start+n0, pp_color))
                if n1 > 1:
                    painting[i, pp_start+n0:pp_stop] = next_color
                    prefixes.append((pp_start+n0, pp_stop, next_color))
                    next_color += 1

            elif n1 > n0:
                # ref is minor, alt is major
                if n0 > 1:
                    painting[i, pp_start:pp_start+n0] = next_color
                    prefixes.append((pp_start, pp_start+n0, next_color))
                    next_color += 1
                painting[i, pp_start+n0:pp_stop] = pp_color
                prefixes.append((pp_start+n0, pp_stop, pp_color))

            elif n0 == n1 and n0 > 1:
                # same number of ref and alt alleles, arbitrarily pick ref as major
                painting[i, pp_start:pp_start+n0] = pp_color
                prefixes.append((pp_start, pp_start+n0, pp_color))
                painting[i, pp_start+n0:pp_stop] = next_color
                prefixes.append((pp_start+n0, pp_stop, next_color))
                next_color += 1

    return np.asarray(painting)


@cython.boundscheck(False)
@cython.wraparound(False)
@cython.cdivision(True)
def ssl2ihh(np.int32_t[:] ssl,
            Py_ssize_t vidx,
            np.int32_t[:] pos,
            np.float64_t min_ehh=0,
            bint include_edges=False):
    """Compute integrated haplotype homozygosity from shared suffix lengths.

    Parameters
    ----------
    ssl : ndarray, int32, shape (n_pairs,)
        Shared suffix lengths between all haplotype pairs.
    vidx : int
        Current variant index.
    min_ehh : float
        Minimum EHH below which IHH computation will be truncated.
    include_edges : bool
        If True, report results for variants where EHH does not fall below the
        specified minimum before reaching the contig end.

    Returns
    -------
    ihh : float
        Integrated haplotype homozygosity.

    """

    cdef:
        Py_ssize_t i, ix, l_max
        np.float64_t ehh_prv, ehh_cur, ihh, ret, n_pairs, n_pairs_ident, g
        np.int64_t[:] b
        bint edge

    # guard condition
    assert min_ehh >= 0

    # initialize
    n_pairs = ssl.shape[0]
    ret = np.nan
    edge = True

    # only compute if at least 1 pair
    if n_pairs > 0:

        # e.g., ssl = [0, 1, 3]
        b = np.bincount(ssl)
        # e.g., b = [1, 1, 0, 1]

        with nogil:

            l_max = b.shape[0]
            # e.g., l_max = 3

            # initialise
            n_pairs_ident = n_pairs - b[0]
            ihh = 0
            ehh_prv = n_pairs_ident / n_pairs

            # iterate backwards over variants
            for i in range(1, vidx+1):

                # compute current EHH
                n_pairs_ident -= b[i]
                ehh_cur = n_pairs_ident / n_pairs

                # check if we've reached minimum EHH
                if ehh_cur <= min_ehh:
                    edge = False
                    break

                # accumulate IHH
                ix = vidx - i
                g = fabs(pos[ix] - pos[ix + 1])
                ihh += g * (ehh_cur + ehh_prv) / 2

                # move on
                ehh_prv = ehh_cur

        if ihh > 0 and (not edge or include_edges):
            ret = ihh

    return ret


@cython.boundscheck(False)
@cython.wraparound(False)
def ihh_scan_int8(np.int8_t[:, :] h,
                  np.int32_t[:] pos,
                  np.float64_t min_ehh=0,
                  bint include_edges=False):
    """Scan forwards over haplotypes, computing the integrated haplotype
    homozygosity backwards for each variant."""

    cdef:
        Py_ssize_t n_variants, n_haplotypes, n_pairs, i, j, k, u, s
        np.int32_t[:] ssl
        np.int8_t a1, a2
        np.float64_t[:] vihh
        np.float64_t ihh

    n_variants = h.shape[0]
    # initialise
    n_haplotypes = h.shape[1]
    n_pairs = (n_haplotypes * (n_haplotypes - 1)) // 2

    # shared suffix lengths between all pairs of haplotypes
    ssl = np.zeros(n_pairs, dtype='i4')

    # integrated haplotype homozygosity values for each variant
    vihh = np.empty(n_variants, dtype='f8')

    # iterate forward over variants
    for i in range(n_variants):

        # pairwise comparison of alleles between haplotypes to determine
        # shared suffix lengths
        with nogil:
            u = 0  # pair index
            for j in range(n_haplotypes):
                a1 = h[i, j]  # allele on first haplotype in pair
                for k in range(j+1, n_haplotypes):
                    a2 = h[i, k]  # allele on second haplotype in pair
                    # test for non-equal and non-missing alleles
                    if (a1 != a2) and (a1 >= 0) and (a2 >= 0):
                        # break shared suffix, reset length to zero
                        ssl[u] = 0
                    else:
                        # extend shared suffix
                        ssl[u] += 1
                    # increment pair index
                    u += 1

        # compute IHH from shared suffix lengths
        ihh = ssl2ihh(ssl, i, pos, min_ehh=min_ehh, include_edges=include_edges)
        vihh[i] = ihh

    return np.asarray(vihh)


@cython.boundscheck(False)
@cython.wraparound(False)
def ssl01_scan_int8(np.int8_t[:, :] h, stat, **kwargs):
    """Scan forwards over haplotypes, computing a summary statistic derived
    from the pairwise shared suffix lengths for each variant, for the
    reference (0) and alternate (1) alleles separately."""

    cdef:
        Py_ssize_t n_variants, n_haplotypes, n_pairs, i, j, k, u, u00, u11
        np.int32_t l
        np.int32_t[:] ssl, ssl00, ssl11
        np.int8_t a1, a2
        np.float64_t[:] vstat0, vstat1

    # initialise
    n_variants = h.shape[0]
    n_haplotypes = h.shape[1]

    # shared suffix lengths between all pairs of haplotypes
    n_pairs = (n_haplotypes * (n_haplotypes - 1)) // 2
    ssl = np.zeros(n_pairs, dtype='i4')
    ssl00 = np.zeros(n_pairs, dtype='i4')
    ssl11 = np.zeros(n_pairs, dtype='i4')

    # statistic values for each variant
    vstat0 = np.empty(n_variants, dtype='f8')
    vstat1 = np.empty(n_variants, dtype='f8')

    # iterate forward over variants
    for i in range(n_variants):

        # pairwise comparison of alleles between haplotypes to determine
        # shared suffix lengths
        with nogil:
            u = u00 = u11 = 0
            for j in range(n_haplotypes):
                a1 = h[i, j]
                for k in range(j+1, n_haplotypes):
                    a2 = h[i, k]
                    if a1 < 0 or a2 < 0:
                        # missing allele, assume sharing continues
                        l = ssl[u] + 1
                        ssl[u] = l
                    elif a1 == a2 == 0:
                        l = ssl[u] + 1
                        ssl[u] = l
                        ssl00[u00] = l
                        u00 += 1
                    elif a1 == a2 == 1:
                        l = ssl[u] + 1
                        ssl[u] = l
                        ssl11[u11] = l
                        u11 += 1
                    else:
                        # break shared suffix, reset to zero
                        ssl[u] = 0
                    u += 1

        # compute statistic from shared suffix lengths
        stat00 = stat(np.asarray(ssl00[:u00]), i, **kwargs)
        stat11 = stat(np.asarray(ssl11[:u11]), i, **kwargs)
        vstat0[i] = stat00
        vstat1[i] = stat11

    return np.asarray(vstat0), np.asarray(vstat1)


def ihh01_scan_int8(np.int8_t[:, :] h, pos, min_ehh=0, include_edges=False):
    """Scan forwards over haplotypes, computing the integrated haplotype
    homozygosity backwards for each variant for the reference (0) and
    alternate (1) alleles separately."""

    return ssl01_scan_int8(h, ssl2ihh,
                           pos=pos,
                           min_ehh=min_ehh,
                           include_edges=include_edges)


def ssl2nsl(ssl, *args, **kwargs):
    """Compute number segregating by length from shared suffix lengths."""

    n_pairs = ssl.shape[0]
    if n_pairs > 0:

        # compute NSL
        nsl = np.mean(ssl)

    else:

        # cannot be computed- as anc/der is singleton
        # the result should never be 0- as the current snp counts
        nsl = np.nan

    return nsl


def nsl01_scan_int8(np.int8_t[:, :] h):
    """Scan forwards over haplotypes, computing the number of segregating
    sites by length backwards for each variant for the reference (0) and
    alternate (1) alleles separately."""

    return ssl01_scan_int8(h, ssl2nsl)
