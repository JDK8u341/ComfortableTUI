// Optional CPython / pybind11 dirty-span scanner. Cell equality remains exact.
#include <pybind11/pybind11.h>
#include <pybind11/stl.h>
#include <vector>
#include <utility>

namespace py = pybind11;

std::vector<std::pair<py::ssize_t, py::ssize_t>> diff_spans(
    const py::list &previous, const py::list &current, py::ssize_t width) {
    const auto size = py::len(current);
    if (width <= 0 || size % width || py::len(previous) != size)
        throw py::value_error("buffers must have equal length and complete rows");
    std::vector<std::pair<py::ssize_t, py::ssize_t>> spans;
    for (py::ssize_t row = 0; row < static_cast<py::ssize_t>(size); row += width) {
        py::ssize_t start = -1;
        for (py::ssize_t i = row; i < row + width; ++i) {
            const int equal = PyObject_RichCompareBool(
                PyList_GET_ITEM(previous.ptr(), i), PyList_GET_ITEM(current.ptr(), i), Py_EQ);
            if (equal < 0) throw py::error_already_set();
            if (!equal && start < 0) start = i;
            if (equal && start >= 0) { spans.emplace_back(start, i); start = -1; }
        }
        if (start >= 0) spans.emplace_back(start, row + width);
    }
    return spans;
}

PYBIND11_MODULE(_native, module) {
    module.doc() = "ComfortableTUI optional exact dirty-span scanner";
    module.def("diff_spans", &diff_spans, py::arg("previous"), py::arg("current"), py::arg("width"));
}
