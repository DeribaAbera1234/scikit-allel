# -*- coding: utf-8 -*-
from __future__ import absolute_import, print_function, division
import unittest


import numpy as np
from numpy.testing import assert_array_equal


from allel.stats import mendel_errors


class TestMendelianError(unittest.TestCase):

    def _test(self, genotypes, expect):
        parent_genotypes = genotypes[:, 0:2]
        progeny_genotypes = genotypes[:, 2:]

        # run test
        actual = mendel_errors(parent_genotypes, progeny_genotypes)
        assert_array_equal(expect, actual)

        # swap parents, should have no affect
        actual = mendel_errors(parent_genotypes, progeny_genotypes)
        assert_array_equal(expect, actual)

        # swap alleles, should have no effect
        parent_genotypes = parent_genotypes[:, :, ::-1]
        progeny_genotypes = progeny_genotypes[:, :, ::-1]
        actual = mendel_errors(parent_genotypes, progeny_genotypes)
        assert_array_equal(expect, actual)

    def test_consistent(self):
        genotypes = np.array([
            # aa x aa -> aa
            [[0, 0], [0, 0], [0, 0], [-1, -1], [-1, -1], [-1, -1]],
            [[1, 1], [1, 1], [1, 1], [-1, -1], [-1, -1], [-1, -1]],
            [[2, 2], [2, 2], [2, 2], [-1, -1], [-1, -1], [-1, -1]],
            # aa x ab -> aa or ab
            [[0, 0], [0, 1], [0, 0], [0, 1], [-1, -1], [-1, -1]],
            [[0, 0], [0, 2], [0, 0], [0, 2], [-1, -1], [-1, -1]],
            [[1, 1], [0, 1], [1, 1], [0, 1], [-1, -1], [-1, -1]],
            # aa x bb -> ab
            [[0, 0], [1, 1], [0, 1], [-1, -1], [-1, -1], [-1, -1]],
            [[0, 0], [2, 2], [0, 2], [-1, -1], [-1, -1], [-1, -1]],
            [[1, 1], [2, 2], [1, 2], [-1, -1], [-1, -1], [-1, -1]],
            # aa x bc -> ab or ac
            [[0, 0], [1, 2], [0, 1], [0, 2], [-1, -1], [-1, -1]],
            [[1, 1], [0, 2], [0, 1], [1, 2], [-1, -1], [-1, -1]],
            # ab x ab -> aa or ab or bb
            [[0, 1], [0, 1], [0, 0], [0, 1], [1, 1], [-1, -1]],
            [[1, 2], [1, 2], [1, 1], [1, 2], [2, 2], [-1, -1]],
            [[0, 2], [0, 2], [0, 0], [0, 2], [2, 2], [-1, -1]],
            # ab x bc -> ab or ac or bb or bc
            [[0, 1], [1, 2], [0, 1], [0, 2], [1, 1], [1, 2]],
            [[0, 1], [0, 2], [0, 0], [0, 1], [0, 1], [1, 2]],
            # ab x cd -> ac or ad or bc or bd
            [[0, 1], [2, 3], [0, 2], [0, 3], [1, 2], [1, 3]],
        ])
        expect = np.zeros((17, 4))
        self._test(genotypes, expect)

    def test_error_nonparental(self):
        genotypes = np.array([
            # aa x aa -> ab or ac or bb or cc
            [[0, 0], [0, 0], [0, 1], [0, 2], [1, 1], [2, 2]],
            [[1, 1], [1, 1], [0, 1], [1, 2], [0, 0], [2, 2]],
            [[2, 2], [2, 2], [0, 2], [1, 2], [0, 0], [1, 1]],
            # aa x ab -> ac or bc or cc
            [[0, 0], [0, 1], [0, 2], [1, 2], [2, 2], [2, 2]],
            [[0, 0], [0, 2], [0, 1], [1, 2], [1, 1], [1, 1]],
            [[1, 1], [0, 1], [1, 2], [0, 2], [2, 2], [2, 2]],
            # aa x bb -> ac or bc or cc
            [[0, 0], [1, 1], [0, 2], [1, 2], [2, 2], [2, 2]],
            [[0, 0], [2, 2], [0, 1], [1, 2], [1, 1], [1, 1]],
            [[1, 1], [2, 2], [0, 1], [0, 2], [0, 0], [0, 0]],
            # ab x ab -> ac or bc or cc
            [[0, 1], [0, 1], [0, 2], [1, 2], [2, 2], [2, 2]],
            [[0, 2], [0, 2], [0, 1], [1, 2], [1, 1], [1, 1]],
            [[1, 2], [1, 2], [0, 1], [0, 2], [0, 0], [0, 0]],
            # ab x bc -> ad or bd or cd or dd
            [[0, 1], [1, 2], [0, 3], [1, 3], [2, 3], [3, 3]],
            [[0, 1], [0, 2], [0, 3], [1, 3], [2, 3], [3, 3]],
            [[0, 2], [1, 2], [0, 3], [1, 3], [2, 3], [3, 3]],
            # ab x cd -> ae or be or ce or de
            [[0, 1], [2, 3], [0, 4], [1, 4], [2, 4], [3, 4]],
        ])
        expect = np.array([
            [1, 1, 2, 2],
            [1, 1, 2, 2],
            [1, 1, 2, 2],
            [1, 1, 2, 2],
            [1, 1, 2, 2],
            [1, 1, 2, 2],
            [1, 1, 2, 2],
            [1, 1, 2, 2],
            [1, 1, 2, 2],
            [1, 1, 2, 2],
            [1, 1, 2, 2],
            [1, 1, 2, 2],
            [1, 1, 1, 2],
            [1, 1, 1, 2],
            [1, 1, 1, 2],
            [1, 1, 1, 1],
        ])
        self._test(genotypes, expect)

    def test_error_hemiparental(self):
        genotypes = np.array([
            # aa x ab -> bb
            [[0, 0], [0, 1], [1, 1], [-1, -1]],
            [[0, 0], [0, 2], [2, 2], [-1, -1]],
            [[1, 1], [0, 1], [0, 0], [-1, -1]],
            # ab x bc -> aa or cc
            [[0, 1], [1, 2], [0, 0], [2, 2]],
            [[0, 1], [0, 2], [1, 1], [2, 2]],
            [[0, 2], [1, 2], [0, 0], [1, 1]],
            # ab x cd -> aa or bb or cc or dd
            [[0, 1], [2, 3], [0, 0], [1, 1]],
            [[0, 1], [2, 3], [2, 2], [3, 3]],
        ])
        expect = np.array([
            [1, 0],
            [1, 0],
            [1, 0],
            [1, 1],
            [1, 1],
            [1, 1],
            [1, 1],
            [1, 1],
        ])
        self._test(genotypes, expect)

    def test_error_uniparental(self):
        genotypes = np.array([
            # aa x bb -> aa or bb
            [[0, 0], [1, 1], [0, 0], [1, 1]],
            [[0, 0], [2, 2], [0, 0], [2, 2]],
            [[1, 1], [2, 2], [1, 1], [2, 2]],
            # aa x bc -> aa or bc
            [[0, 0], [1, 2], [0, 0], [1, 2]],
            [[1, 1], [0, 2], [1, 1], [0, 2]],
            # ab x cd -> ab or cd
            [[0, 1], [2, 3], [0, 1], [2, 3]],
        ])
        expect = np.array([
            [1, 1],
            [1, 1],
            [1, 1],
            [1, 1],
            [1, 1],
            [1, 1],
        ])
        self._test(genotypes, expect)

    def test_parent_missing(self):
        genotypes = np.array([
            [[-1, -1], [0, 0], [0, 0], [1, 1]],
            [[0, 0], [-1, -1], [0, 0], [2, 2]],
            [[-1, -1], [-1, -1], [1, 1], [2, 2]],
        ])
        expect = np.array([
            [0, 0],
            [0, 0],
            [0, 0],
        ])
        self._test(genotypes, expect)
