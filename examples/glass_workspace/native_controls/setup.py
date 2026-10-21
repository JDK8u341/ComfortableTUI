"""Build from the repository root; no linking to a second copy of GlassHost."""
from pathlib import Path

from pybind11.setup_helpers import Pybind11Extension
from setuptools import setup

root = Path(__file__).resolve().parents[3]
setup(name="ctui-glass-controls-example", version="1.0",
      ext_modules=[Pybind11Extension("ctui_glass_controls", [str(Path(__file__).with_name("controls.cpp"))],
                                    include_dirs=[str(root / "native/glass/include"),
                                                  str(root / "native/canvas/include")],
                                    define_macros=[("NOMINMAX", "1"), ("WIN32_LEAN_AND_MEAN", "1")],
                                    cxx_std=17, extra_compile_args=["/utf-8", "/W4"]),
                   Pybind11Extension("ctui_glass_effects", [str(Path(__file__).with_name("effects.cpp"))],
                                     include_dirs=[str(root / "native/glass/include"), str(root / "native/canvas/include")],
                                     define_macros=[("NOMINMAX", "1"), ("WIN32_LEAN_AND_MEAN", "1")],
                                     libraries=["d3d11", "d3dcompiler"], cxx_std=17,
                                     extra_compile_args=["/utf-8", "/W4"])])
