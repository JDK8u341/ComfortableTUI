"""Pure Python by default. Opt in with COMFORTABLE_TUI_BUILD_NATIVE=1."""
import os

from setuptools import setup

ext_modules = []
if os.environ.get("COMFORTABLE_TUI_BUILD_NATIVE") == "1":
    from pybind11.setup_helpers import Pybind11Extension
    ext_modules = [Pybind11Extension("comfortable_tui._native", ["native/diff.cpp"], cxx_std=17,
                                    extra_compile_args=["/utf-8"] if os.name == "nt" else [])]

setup(ext_modules=ext_modules)
