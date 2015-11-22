# -*- coding: utf-8 -*-
from __future__ import absolute_import, print_function, division
import tempfile
import atexit
import shutil


import bcolz


class BcolzStorage(object):

    def __init__(self, **kwargs):
        self.defaults = kwargs

    def _set_defaults(self, kwargs):
        for k, v in self.defaults.items():
            kwargs.setdefault(k, v)
        return kwargs

    def array(self, data, expectedlen=None, **kwargs):
        kwargs = self._set_defaults(kwargs)
        return bcolz.carray(data, expectedlen=expectedlen, **kwargs)

    def table(self, data, expectedlen=None, **kwargs):
        kwargs = self._set_defaults(kwargs)
        return bcolz.ctable(data, expectedlen=expectedlen, **kwargs)


class BcolzMemStorage(BcolzStorage):

    # noinspection PyShadowingBuiltins
    def _set_defaults(self, kwargs):
        for k, v in self.defaults.items():
            kwargs.setdefault(k, v)
        kwargs['rootdir'] = None
        return kwargs


class BcolzTmpStorage(BcolzStorage):

    # noinspection PyShadowingBuiltins
    def _set_defaults(self, kwargs):
        for k, v in self.defaults.items():
            kwargs.setdefault(k, v)
        suffix = kwargs.pop('suffix', '.bcolz')
        prefix = kwargs.pop('prefix', 'scikit_allel_')
        dir = kwargs.pop('dir', None)
        rootdir = tempfile.mkdtemp(suffix=suffix, prefix=prefix, dir=dir)
        atexit.register(shutil.rmtree, rootdir)
        kwargs['rootdir'] = rootdir
        kwargs['mode'] = 'w'
        return kwargs


# singleton instances for convenience
bcolzmem_storage = BcolzMemStorage()
bcolztmp_storage = BcolzTmpStorage()
