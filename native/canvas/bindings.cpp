#include <pybind11/pybind11.h>
#include <pybind11/stl.h>
#include "comfortable/compositor.hpp"
#include "../glass/bindings.hpp"
namespace py = pybind11;
using namespace comfortable;
using namespace pybind11::literals;
static py::dict descriptor_dict(const FrameDescriptor &d) {
    return py::dict("name"_a = d.name, "width"_a = d.width, "height"_a = d.height,
                    "adapter_low"_a = d.adapter_low, "adapter_high"_a = d.adapter_high,
                    "version"_a = d.version);
}
PYBIND11_MODULE(_native_gpu, m) {
    bind_glass(m);
    m.attr("protocol_version") = 1;
    m.def("describe", [](const std::string &name) {
        FrameDescriptor d;
        {
            py::gil_scoped_release release;
            d = describe(name);
        }
        return descriptor_dict(d);
    });
    m.def("adapters", [] {
        py::list result;
        for (const auto &a : adapters())
            result.append(
                py::dict("name"_a = a.name, "low"_a = a.low, "high"_a = a.high, "software"_a = a.software));
        return result;
    });
    py::class_<Compositor>(m, "Compositor")
        .def(py::init<std::uintptr_t, std::uint32_t, std::int32_t>(), "hwnd"_a, "adapter_low"_a = 0,
             "adapter_high"_a = 0)
        .def("configure",
             [](Compositor &self, py::list input, std::uint32_t w, std::uint32_t h) {
                 std::vector<NativeView> views;
                 for (auto object : input) {
                     auto d = py::cast<py::dict>(object);
                     NativeView v;
                     v.id = py::cast<std::uint64_t>(d["id"]);
                     v.gpu = py::cast<bool>(d["gpu"]);
                     v.source.name = py::cast<std::string>(d["name"]);
                     v.source.width = py::cast<std::uint32_t>(d["width"]);
                     v.source.height = py::cast<std::uint32_t>(d["height"]);
                     if (v.gpu) {
                         v.source.adapter_low = py::cast<std::uint32_t>(d["adapter_low"]);
                         v.source.adapter_high = py::cast<std::int32_t>(d["adapter_high"]);
                     } else
                         v.stride = py::cast<std::uint32_t>(d["stride"]);
                     v.pixel_format = py::cast<std::string>(d["pixel_format"]);
                     v.fit = py::cast<std::string>(d["fit"]);
                     v.smooth = py::cast<bool>(d["smooth"]);
                     auto box = [](py::handle object) {
                         auto a = py::cast<std::array<float, 4>>(object);
                         return PixelBox{a[0], a[1], a[2], a[3]};
                     };
                     v.full = box(d["full"]);
                     v.clip = box(d["clip"]);
                     views.push_back(v);
                 }
                 py::gil_scoped_release release;
                 self.configure(std::move(views), w, h);
             })
        .def("visible", &Compositor::visible, py::call_guard<py::gil_scoped_release>())
        .def("close", &Compositor::close, py::call_guard<py::gil_scoped_release>())
        .def("metrics", [](Compositor &self) {
            CompositorMetrics stats;
            {
                py::gil_scoped_release release;
                stats = self.metrics();
            }
            py::list views;
            for (const auto &v : stats.views)
                views.append(py::dict("id"_a = v.id, "sequence"_a = v.sequence, "frames"_a = v.frames,
                                      "skipped_frames"_a = v.skipped, "frame_age_ms"_a = v.source_age_ms,
                                      "ready_age_ms"_a = (v.gpu ? py::cast(v.ready_age_ms) : py::none()),
                                      "acquire_ms"_a = v.acquire_ms, "closed"_a = v.closed,
                                      "gpu_shared"_a = v.gpu));
            return py::dict("presents"_a = stats.presents, "submit_ms"_a = stats.last_submit_ms,
                            "views"_a = views, "error"_a = stats.error);
        });
}
