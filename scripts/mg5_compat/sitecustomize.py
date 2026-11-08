"""Run MG5 2.9.27 against the pinned SMEFTsim 3.0.2 UFO without editing it.

The upstream SMEFTsim 3.0.2 UFO is written for Python 2, while the pinned
MadGraph 5 2.9.27 launcher runs Python 3.  MG5's own ``convert model`` command
would edit the UFO in place; that is forbidden here because checkers 2 and 3
verify the exact bytes of the pinned model source.

This module is loaded as ``sitecustomize`` only through ``scripts/mg5.sh``.
It installs an import hook that applies MadGraph's own documented Python-3
syntax conversion *in memory* to exactly the three support files that
``convert model`` touches, using the same rules:

* ``object_library.py``: ``.iteritems()`` -> ``.items()`` and
  ``raise Error, "text"`` -> ``raise Error("text")``;
* ``write_param_card.py``: replaced by MadGraph's own
  ``models/sm/write_param_card.py`` copy;
* ``__init__.py``: prepend ``import object_library`` / ``import function_library``
  when missing (load order expected by the MG5 UFO reader).

Every other UFO file, including all physics declarations (couplings,
parameters, particles, vertices, lorentz, decays), is read byte-for-byte from
the pinned source.  No file is written; bytecode caching is disabled for the
patched modules.  The conversion is recorded in the delivery evidence.
"""
from __future__ import annotations

import importlib.abc
import importlib.machinery
import importlib.util
import os
import re
import sys

_RAISE = re.compile(r'raise (\w+)\s*,\s*["\']([^"]+)["\']')
_TO_IMPORT = ('object_library', 'function_library')


def _converted_object_library(text: str) -> str:
    text = text.replace('.iteritems()', '.items()')
    return _RAISE.sub(r'raise \g<1>("\g<2>")', text)


def _converted_init(text: str) -> str:
    for module in _TO_IMPORT:
        if 'import %s' % module not in text:
            text = 'import %s \n' % module + text
    return text


class _UfoCompatLoader(importlib.machinery.SourceFileLoader):
    """Source loader that serves the Python-3 syntax conversion in memory."""

    def __init__(self, fullname, path, replacements):
        super().__init__(fullname, path)
        self._replacements = replacements

    def get_data(self, path):
        data = super().get_data(path)
        patch = self._replacements.get(os.path.basename(path))
        if patch is None:
            return data
        return patch(data.decode('utf-8')).encode('utf-8')

    def get_code(self, fullname):
        """Compile the patched source directly; never read or write bytecode."""
        source_path = self.get_filename(fullname)
        return compile(self.get_data(source_path), source_path, 'exec', dont_inherit=True)

    # Never write __pycache__ into the pinned model directory.
    def set_data(self, path, data, *, _mode=0o666):
        return None


class _UfoCompatFinder(importlib.abc.MetaPathFinder):
    """Delegate to the regular finder, then patch files inside the UFO dirs."""

    def __init__(self, ufo_dirs, replacements):
        self._ufo_dirs = tuple(os.path.realpath(p) for p in ufo_dirs)
        self._replacements = replacements

    def find_spec(self, fullname, path=None, target=None):
        spec = importlib.machinery.PathFinder.find_spec(fullname, path, target)
        if spec is None or not spec.origin or not spec.origin.endswith('.py'):
            return None
        origin = os.path.realpath(spec.origin)
        if not any(origin.startswith(root + os.sep) for root in self._ufo_dirs):
            return None
        loader = _UfoCompatLoader(fullname, spec.origin, self._replacements)
        return importlib.util.spec_from_file_location(fullname, spec.origin, loader=loader)


def _install():
    ufo_dirs = [p for p in os.environ.get('NSBI_MG5_PY2_UFO_DIRS', '').split(':') if p]
    sm_card = os.environ.get('NSBI_MG5_SM_WRITE_PARAM_CARD', '')
    if not ufo_dirs or not sm_card or not os.path.isfile(sm_card):
        return
    with open(sm_card, encoding='utf-8') as stream:
        sm_source = stream.read()
    replacements = {
        'object_library.py': _converted_object_library,
        'write_param_card.py': lambda text: sm_source,
        '__init__.py': _converted_init,
    }
    sys.meta_path.insert(0, _UfoCompatFinder(ufo_dirs, replacements))


_install()
