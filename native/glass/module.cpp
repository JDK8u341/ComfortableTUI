#include <pybind11/pybind11.h>
#include <pybind11/stl.h>
#include "bindings.hpp"

PYBIND11_MODULE(_native_glass, module) {
    comfortable::bind_glass(module);
}
