#pragma once
#include "comfortable/glass.hpp"
namespace comfortable {
inline void bind_glass(pybind11::module_ &m) {
    namespace py=pybind11;
    m.attr("glass_protocol_version")=1;
    py::class_<GlassHost>(m,"GlassHost")
        .def(py::init([](std::uintptr_t hwnd,bool inspectable){py::gil_scoped_release release;return std::make_unique<GlassHost>(hwnd,inspectable);}),py::arg("hwnd"),py::arg("inspectable_windows")=false)
        .def("configure",[](GlassHost &host,py::dict input){
            GlassScene s;
            s.layout=py::cast<std::string>(input["layout"]);s.material=py::cast<std::string>(input["material"]);
            s.backdrop=py::cast<std::string>(input["backdrop"]);s.selected=py::cast<std::string>(input["selected"]);
            s.accent=py::cast<std::uint32_t>(input["accent"]);s.visible=py::cast<bool>(input["visible"]);
            s.pane_visible=py::cast<bool>(input["pane_visible"]);s.reduced_motion=py::cast<bool>(input["reduced_motion"]);
            s.reduced_transparency=py::cast<bool>(input["reduced_transparency"]);
            for(auto v:py::cast<py::list>(input["cards"])) {
                auto d=py::cast<py::dict>(v);GlassCard card;
                card.id=py::cast<std::string>(d["id"]);card.title=py::cast<std::string>(d["title"]);
                card.subtitle=py::cast<std::string>(d["subtitle"]);card.eyebrow=py::cast<std::string>(d["eyebrow"]);
                card.width=py::cast<float>(d["width"]);
                for(auto cv:py::cast<py::list>(d["controls"])) {
                    auto c=py::cast<py::dict>(cv);GlassControl control;
                    control.id=py::cast<std::string>(c["id"]);control.kind=py::cast<std::string>(c["kind"]);
                    control.label=py::cast<std::string>(c["label"]);control.text=py::cast<std::string>(c["text"]);
                    control.value=py::cast<double>(c["value"]);control.enabled=py::cast<bool>(c["enabled"]);
                    control.acknowledged=py::cast<std::uint64_t>(c["acknowledged"]);
                    control.series=py::cast<std::vector<double>>(c["series"]);
                    control.options=py::cast<std::vector<std::string>>(c["options"]);
                    control.fit=py::cast<std::string>(c["fit"]);
                    if(control.kind=="canvas") {
                        auto src=py::cast<py::dict>(c["source"]);auto &x=control.canvas;
                        x.source.name=py::cast<std::string>(src["name"]);x.source.width=py::cast<std::uint32_t>(src["width"]);
                        x.source.height=py::cast<std::uint32_t>(src["height"]);x.gpu=py::cast<bool>(src["gpu"]);
                        x.stride=py::cast<std::uint32_t>(src["stride"]);x.pixel_format=py::cast<std::string>(src["pixel_format"]);
                        x.source.adapter_low=py::cast<std::uint32_t>(src["adapter_low"]);
                        x.source.adapter_high=py::cast<std::int32_t>(src["adapter_high"]);
                    }
                    card.controls.push_back(std::move(control));
                }
                s.cards.push_back(std::move(card));
            }
            py::gil_scoped_release release;return host.configure(std::move(s));
        })
        .def("command",&GlassHost::command,py::arg("action"),py::arg("value")="",py::call_guard<py::gil_scoped_release>())
        .def("close",&GlassHost::close,py::call_guard<py::gil_scoped_release>())
        .def("metrics",[](GlassHost &h){
            GlassMetrics s;{py::gil_scoped_release release;s=h.metrics();}
            py::list windows;
            for(std::size_t i=0;i<s.windows.size();i++) {
                auto r=s.rects[i];py::dict w;w["id"]=s.ids[i];w["hwnd"]=s.windows[i];
                w["rect"]=py::make_tuple(r.x,r.y,r.width,r.height);windows.append(w);
            }
            py::dict d;d["applied"]=s.applied;d["frames"]=s.frames;d["tick_ms"]=s.tick_ms;
            d["dropped_events"]=s.dropped_events;d["active"]=s.active;d["compact"]=s.compact;
            d["backdrop_visible"]=s.backdrop_visible;d["high_contrast"]=s.high_contrast;
            d["system_animation"]=s.system_animation;d["material"]=s.material;d["error"]=s.error;
            d["windows"]=windows;d["closed"]=s.closed;d["presents"]=s.presents;d["canvas_frames"]=s.canvas_frames;return d;
        })
        .def("events",[](GlassHost &h){
            std::vector<GlassEvent> events;{py::gil_scoped_release release;events=h.events();}
            py::list out;
            for(const auto &e:events){py::dict d;d["kind"]=e.kind;d["card"]=e.card;d["control"]=e.control;
                d["text"]=e.text;d["value"]=e.value;d["position"]=py::make_tuple(e.x,e.y);d["sequence"]=e.sequence;out.append(d);}
            return out;
        });
}
}
