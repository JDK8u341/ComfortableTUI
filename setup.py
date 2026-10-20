"""Pure Python by default. Opt in with COMFORTABLE_TUI_BUILD_NATIVE=1."""
import os

from setuptools import setup

ext_modules = []
if any(os.environ.get(key) == "1" for key in ("COMFORTABLE_TUI_BUILD_NATIVE", "COMFORTABLE_TUI_BUILD_GPU", "COMFORTABLE_TUI_BUILD_GLASS")):
    from pybind11.setup_helpers import Pybind11Extension
if os.environ.get("COMFORTABLE_TUI_BUILD_NATIVE") == "1":
    ext_modules = [Pybind11Extension("comfortable_tui._native", ["native/diff.cpp"], cxx_std=17,
                                    extra_compile_args=["/utf-8"] if os.name == "nt" else [])]
if os.environ.get("COMFORTABLE_TUI_BUILD_GPU") == "1" or os.environ.get("COMFORTABLE_TUI_BUILD_GLASS") == "1":
    if os.name != "nt":
        raise RuntimeError("The native GPU frame SDK currently requires Windows")
    ext_modules.append(Pybind11Extension("comfortable_tui._native_gpu",
        ["native/canvas/bindings.cpp", "native/canvas/frame_provider.cpp", "native/canvas/compositor.cpp"],
        include_dirs=["native/canvas/include"], libraries=["d3d11", "dxgi", "bcrypt", "dcomp", "d3dcompiler", "user32"],
        define_macros=[("NOMINMAX", "1"), ("WIN32_LEAN_AND_MEAN", "1")], cxx_std=17,
        extra_compile_args=["/utf-8", "/W4"]))

if os.environ.get("COMFORTABLE_TUI_BUILD_GLASS") == "1":
    ext_modules.append(Pybind11Extension("comfortable_tui._native_glass",
        ["native/glass/module.cpp", "native/glass/glass.cpp", "native/glass/control.cpp", "native/glass/optics.cpp",
         "native/canvas/frame_provider.cpp", "native/canvas/compositor.cpp"],
        include_dirs=["native/canvas/include", "native/glass/include"],
        libraries=["d3d11", "dxgi", "bcrypt", "dcomp", "d3dcompiler", "user32", "gdi32",
                   "d2d1", "dwrite", "dwmapi", "ole32", "oleacc", "oleaut32"],
        define_macros=[("NOMINMAX", "1"), ("WIN32_LEAN_AND_MEAN", "1")], cxx_std=17,
        extra_compile_args=["/utf-8", "/W4"]))

setup(ext_modules=ext_modules)
