#pragma once
#include "comfortable/glass.hpp"
#include <cmath>
namespace comfortable {
inline void bind_glass(pybind11::module_ &m) {
    namespace py=pybind11;
    m.attr("glass_protocol_version")=6;
    m.def("effect_diagnostics",[]{
        auto s=glass_effect_diagnostics();py::dict out;
        out["surface_bytes"]=s.surface_bytes;out["surface_allocations"]=s.surface_allocations;
        out["shader_compilations"]=s.shader_compilations;out["program_sets"]=s.program_sets;return out;
    });
    auto owners=py::list();
    auto effect_owners=py::list();
    m.def("register_controls",[owners](py::object owner,py::capsule capsule)mutable {
        if(!capsule.name()||std::string(capsule.name())!=glass_extension_capsule)throw std::invalid_argument("invalid Glass extension capsule");
        auto extension=static_cast<const GlassControlExtension *>(capsule.get_pointer());
        if(!extension)throw std::invalid_argument("empty Glass extension capsule");
        register_glass_controls(*extension);owners.append(owner);owners.append(capsule);
        py::list out;
        for(std::size_t i=0;i<extension->count;i++)out.append(py::str(extension->types[i].name));
        return out;
    });
    m.def("register_effects",[effect_owners](py::object owner,py::capsule capsule)mutable {
        if(!capsule.name()||std::string(capsule.name())!=glass_effect_extension_capsule)
            throw std::invalid_argument("invalid Glass effect extension capsule");
        auto extension=static_cast<const GlassEffectExtension *>(capsule.get_pointer());
        if(!extension)throw std::invalid_argument("empty Glass effect extension capsule");
        register_glass_effects(*extension);effect_owners.append(owner);effect_owners.append(capsule);
        py::list out;
        for(std::size_t i=0;i<extension->count;i++)out.append(py::str(extension->types[i].name));
        return out;
    });
    m.def("effect_schema",[](const std::string &name){
        const auto &type=glass_effect_type(name);py::list result;
        for(std::size_t i=0;i<type.parameter_count;i++){
            const auto &s=type.parameters[i];py::dict p;p["name"]=s.name;p["minimum"]=s.minimum;
            p["maximum"]=s.maximum;p["default"]=s.default_value;result.append(p);
        }return result;
    });
    m.def("validate_effect_parameters",&glass_effect_parameters);
    m.def("measure_control",[](const std::string &name,py::object model){
        GlassControl c;c.kind=name;
        if(!model.is_none()) {
            auto d=py::cast<py::dict>(model);
            c.id=py::cast<std::string>(d["id"]);c.label=py::cast<std::string>(d["label"]);c.text=py::cast<std::string>(d["text"]);
            c.value=py::cast<double>(d["value"]);c.enabled=py::cast<bool>(d["enabled"]);c.canvas_height=py::cast<float>(d["canvas_height"]);
            c.series=py::cast<std::vector<double>>(d["series"]);c.options=py::cast<std::vector<std::string>>(d["options"]);
        }
        auto height=glass_control_type(name).measure(c);
        if(!std::isfinite(height)||height<16||height>1056)throw std::invalid_argument("invalid measured control height");
        return height;
    },py::arg("name"),py::arg("model")=py::none());
    py::class_<GlassHost>(m,"GlassHost")
        .def(py::init([](std::uintptr_t hwnd,bool inspectable){py::gil_scoped_release release;return std::make_unique<GlassHost>(hwnd,inspectable);}),py::arg("hwnd"),py::arg("inspectable_windows")=false)
        .def("configure",[](GlassHost &host,py::dict input){
            GlassScene s;
            s.roots=py::cast<std::vector<std::string>>(input["roots"]);
            s.layout=py::cast<std::string>(input["layout"]);s.material=py::cast<std::string>(input["material"]);
            s.backdrop=py::cast<std::string>(input["backdrop"]);s.selected=py::cast<std::string>(input["selected"]);
            s.accent=py::cast<std::uint32_t>(input["accent"]);s.visible=py::cast<bool>(input["visible"]);
            s.pane_visible=py::cast<bool>(input["pane_visible"]);s.reduced_motion=py::cast<bool>(input["reduced_motion"]);
            s.reduced_transparency=py::cast<bool>(input["reduced_transparency"]);
            for(auto item:py::cast<py::list>(input["groups"])) {
                auto d=py::cast<py::dict>(item);GlassGroup group;
                group.id=py::cast<std::string>(d["id"]);group.parent=py::cast<std::string>(d["parent"]);
                group.layout=py::cast<std::string>(d["layout"]);group.children=py::cast<std::vector<std::string>>(d["children"]);
                group.gap=py::cast<float>(d["gap"]);group.padding=py::cast<float>(d["padding"]);
                group.visible=py::cast<bool>(d["visible"]);group.enabled=py::cast<bool>(d["enabled"]);s.groups.push_back(std::move(group));
            }
            for(auto v:py::cast<py::list>(input["cards"])) {
                auto d=py::cast<py::dict>(v);GlassCard card;
                card.id=py::cast<std::string>(d["id"]);card.title=py::cast<std::string>(d["title"]);
                card.subtitle=py::cast<std::string>(d["subtitle"]);card.eyebrow=py::cast<std::string>(d["eyebrow"]);
                card.width=py::cast<float>(d["width"]);
                card.generation=py::cast<std::uint64_t>(d["generation"]);
                card.group=py::cast<std::string>(d["group"]);
                card.draggable=py::cast<bool>(d["draggable"]);
                for(auto ev:py::cast<py::list>(d["effects"])) {
                    auto e=py::cast<py::dict>(ev);GlassEffectBinding effect;
                    effect.type=py::cast<std::string>(e["type"]);effect.target=py::cast<std::string>(e["target"]);
                    effect.parameters=py::cast<std::vector<float>>(e["parameters"]);
                    effect.tint=py::cast<std::uint32_t>(e["tint"]);effect.opacity=py::cast<float>(e["opacity"]);
                    card.effects.push_back(std::move(effect));
                }
                if(!d["background"].is_none()) {
                    card.has_background=true;auto b=py::cast<py::dict>(d["background"]);
                    card.background.kind=py::cast<std::string>(b["kind"]);
                    card.background.tint=py::cast<std::uint32_t>(b["tint"]);
                    card.background.opacity=py::cast<float>(b["opacity"]);
                    card.background.blur=py::cast<float>(b["blur"]);
                    card.background.refraction=py::cast<float>(b["refraction"]);
                    card.background.dispersion=py::cast<float>(b["dispersion"]);
                    card.background.corner_radius=py::cast<float>(b["corner_radius"]);
                }
                for(auto cv:py::cast<py::list>(d["controls"])) {
                    auto c=py::cast<py::dict>(cv);GlassControl control;
                    control.id=py::cast<std::string>(c["id"]);control.kind=py::cast<std::string>(c["kind"]);
                    control.generation=py::cast<std::uint64_t>(c["generation"]);
                    control.opacity=py::cast<float>(c["opacity"]);
                    control.label=py::cast<std::string>(c["label"]);control.text=py::cast<std::string>(c["text"]);
                    control.value=py::cast<double>(c["value"]);control.enabled=py::cast<bool>(c["enabled"]);
                    control.canvas_height=py::cast<float>(c["canvas_height"]);
                    control.capture_escape=py::cast<bool>(c["capture_escape"]);
                    control.acknowledged=py::cast<std::uint64_t>(c["acknowledged"]);
                    control.series=py::cast<std::vector<double>>(c["series"]);
                    control.options=py::cast<std::vector<std::string>>(c["options"]);
                    control.fit=py::cast<std::string>(c["fit"]);
                    for(auto ev:py::cast<py::list>(c["effects"])) {
                        auto e=py::cast<py::dict>(ev);GlassEffectBinding effect;
                        effect.type=py::cast<std::string>(e["type"]);effect.target=py::cast<std::string>(e["target"]);
                        effect.parameters=py::cast<std::vector<float>>(e["parameters"]);
                        effect.tint=py::cast<std::uint32_t>(e["tint"]);effect.opacity=py::cast<float>(e["opacity"]);
                                    control.effects.push_back(std::move(effect));
                    }
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
        .def("begin_drag",&GlassHost::begin_drag,py::call_guard<py::gil_scoped_release>())
        .def("present",&GlassHost::present,py::call_guard<py::gil_scoped_release>())
        .def("set_effect_parameters",&GlassHost::set_effect_parameters,py::arg("card"),py::arg("node"),
             py::arg("generation"),py::arg("index"),py::arg("type"),py::arg("parameters"),
             py::call_guard<py::gil_scoped_release>())
        .def("close",&GlassHost::close,py::call_guard<py::gil_scoped_release>())
        .def("metrics",[](GlassHost &h){
            GlassMetrics s;{py::gil_scoped_release release;s=h.metrics();}
            py::list windows;
            for(std::size_t i=0;i<s.windows.size();i++) {
                auto r=s.rects[i];py::dict w;w["id"]=s.ids[i];w["hwnd"]=s.windows[i];
                w["rect"]=py::make_tuple(r.x,r.y,r.width,r.height);windows.append(w);
                w["material"]=s.materials[i];w["pinned"]=static_cast<bool>(s.pinned[i]);w["draggable"]=static_cast<bool>(s.draggable[i]);
                w["visible"]=static_cast<bool>(s.visible[i]);w["enabled"]=static_cast<bool>(s.enabled[i]);w["focused_control"]=s.focused_control[i];
            }
            py::dict d;d["applied"]=s.applied;d["frames"]=s.frames;d["tick_ms"]=s.tick_ms;
            d["dropped_events"]=s.dropped_events;d["active"]=s.active;d["compact"]=s.compact;
            d["backdrop_visible"]=s.backdrop_visible;d["high_contrast"]=s.high_contrast;
            d["system_animation"]=s.system_animation;d["material"]=s.material;d["error"]=s.error;
            d["windows"]=windows;d["closed"]=s.closed;d["presents"]=s.presents;d["canvas_frames"]=s.canvas_frames;
            d["capture_active"]=s.capture_active;d["capture_frames"]=s.capture_frames;d["optical_frames"]=s.optical_frames;
            d["copied_pixels"]=s.copied_pixels;d["optics_error"]=s.optics_error;
            d["effects_rendered"]=s.effects_rendered;
            d["close_hide_ms"]=s.close_hide_ms;d["close_join_ms"]=s.close_join_ms;
            d["close_surfaces_ms"]=s.close_surfaces_ms;d["close_windows_ms"]=s.close_windows_ms;d["close_resources_ms"]=s.close_resources_ms;
            d["unchanged_capture_frames"]=s.unchanged_capture_frames;d["backdrop_material"]=s.backdrop_material;return d;
        })
        .def("events",[](GlassHost &h){
            std::vector<GlassEvent> events;{py::gil_scoped_release release;events=h.events();}
            py::list out;
            for(const auto &e:events){py::dict d;d["kind"]=e.kind;d["card"]=e.card;d["control"]=e.control;
                d["text"]=e.text;d["value"]=e.value;d["position"]=py::make_tuple(e.x,e.y);d["sequence"]=e.sequence;d["generation"]=e.generation;
                d["modifiers"]=py::tuple(py::cast(e.modifiers));d["scan_code"]=e.scan_code;d["repeat"]=e.repeat;
                d["button"]=e.button;d["buttons"]=e.buttons;d["clicks"]=e.clicks;d["inside_source"]=e.inside_source;d["source_name"]=e.source_name;out.append(d);}
            return out;
        });
}
}
