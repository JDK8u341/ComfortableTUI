#include "comfortable/glass.hpp"
#include <algorithm>
#include <cmath>
#include <map>
#include <mutex>
#include <stdexcept>
#include <cstring>

namespace comfortable {
namespace {
enum Builtin { text,button,toggle,input,choice,slider,progress,sparkline,canvas };
template<Builtin K> float measure(const GlassControl &c) {
    if constexpr(K==canvas)return c.canvas_height+32;
    else if constexpr(K==sparkline)return 84;
    else if constexpr(K==slider||K==progress)return 58;
    else if constexpr(K==text)return 40;
    else return 46;
}
bool rectangle(const GlassControl &,float x,float y,float w,float h){return x>=0&&x<w&&y>=0&&y<h;}
std::string value(const GlassControl &c) {
    if(c.kind=="choice"&&!c.options.empty())return c.options.at(static_cast<std::size_t>(c.value));
    if(c.kind=="input"||c.kind=="text")return c.text;
    return std::to_string(c.value);
}
template<Builtin K> GlassControlAction action(GlassControl &c,const GlassControlInput &e) {
    bool key=std::strcmp(e.kind,"key")==0;
    if constexpr(K==slider) {
        if(key){if(e.key!=37&&e.key!=39)return GlassControlAction::none;c.value=std::clamp(c.value+(e.key==39?.02:-.02),0.,1.);}
        else c.value=std::clamp(static_cast<double>((e.x-8)/(e.width-16)),0.,1.);
        return GlassControlAction::change;
    }else {
        if(key)return GlassControlAction::none;
        if constexpr(K==button)return GlassControlAction::click;
        else if constexpr(K==toggle){c.value=c.value>=.5?0:1;return GlassControlAction::change;}
        else if constexpr(K==choice){c.value=std::fmod(c.value+1,static_cast<double>(c.options.size()));return GlassControlAction::change;}
        else return GlassControlAction::none;
    }
}
template<Builtin K> void paint(GlassPainter &p,const GlassControl &c,const GlassControlPaint &s) {
    float w=s.width,h=s.height,a=s.opacity;
    if constexpr(K==text){p.text(c.label,4,2,w-8,11,0x9db0c9,a);p.text(c.text,4,17,w-8,14,0xe5efff,a);}
    if constexpr(K==button){p.fill(0,2,w,h-8,s.accent,(s.hovered?.24f:.14f)*a,10);p.text(c.label,14,11,w-35,13,0xe4fff8,a,true);p.text("↗",w-25,9,20,17,s.accent,a);}
    if constexpr(K==toggle){
        p.text(c.label,6,10,w-70,13,0xe0ebfb,a);p.fill(w-49,10,42,22,c.value>=.5?s.accent:0x526078,.85f*a,11);
        p.circle(c.value>=.5?w-19:w-37,21,7,c.value>=.5?0x12332f:0xdde8f9,a);
    }
    if constexpr(K==input||K==choice){
        p.fill(0,2,w,h-8,0x060b14,.3f*a,10);
        if constexpr(K==input){if(s.focused&&s.text_selected)p.fill(8,9,w-16,23,s.accent,.27f*a,2);}
        std::string content=K==choice&&!c.options.empty()?c.options.at(static_cast<std::size_t>(c.value)):c.text;
        p.text(content.empty()?c.label:content,10,11,w-26,13,content.empty()?0x8a9cb4:0xe4efff,a);
    }
    if constexpr(K==slider||K==progress){
        p.text(c.label,6,3,w-60,12,0xb0c3da,a);p.text(std::to_string(static_cast<int>(c.value*100))+"%",w-46,3,42,12,s.accent,a,true);
        p.fill(8,32,w-16,4,0x60748d,.25f*a,2);float at=8+(w-16)*static_cast<float>(c.value);
        p.fill(8,32,at-8,4,s.accent,a,2);if constexpr(K==slider)p.circle(at,34,5,0xe1fff7,a);
    }
    if constexpr(K==sparkline){
        p.text(c.label,6,3,w-10,12,0xb0c3da,a);p.fill(4,25,w-8,47,0x061522,.32f*a,8);
        for(std::size_t j=1;j<c.series.size();j++)p.line(8+(w-16)*static_cast<float>(j-1)/(c.series.size()-1),68-static_cast<float>(c.series[j-1])*36,
            8+(w-16)*static_cast<float>(j)/(c.series.size()-1),68-static_cast<float>(c.series[j])*36,s.accent,a,1.6f);
    }
    if constexpr(K==canvas){p.text(c.label,6,2,w-10,11,0xb0c3da,a);p.fill(4,22,w-8,c.canvas_height,0x060c18,.7f*a,8);}
}
template<Builtin K> GlassControlType builtin(const char *name,bool focusable,long role,const char *verb="") {
    return {name,1,focusable,role,verb,measure<K>,paint<K>,rectangle,action<K>,value};
}
struct Registry {
    std::mutex mutex;
    std::map<std::string,GlassControlType> types;
    Registry(){
        for(auto type:{builtin<text>("text",false,0x29),builtin<button>("button",true,0x2b,"Press"),builtin<toggle>("toggle",true,0x2c,"Toggle"),
            builtin<input>("input",true,0x2a),builtin<choice>("choice",true,0x2e,"Next"),builtin<slider>("slider",true,0x33,"Adjust"),
            builtin<progress>("progress",false,0x30),builtin<sparkline>("sparkline",false,0x28),builtin<canvas>("canvas",true,0x28)})types.emplace(type.name,type);
    }
};
Registry &registry(){static Registry r;return r;}
}
void register_glass_controls(const GlassControlExtension &extension) {
    GlassControlExtension expected;
    if(extension.abi!=expected.abi||extension.structure_size!=expected.structure_size||extension.pointer_size!=expected.pointer_size||
       extension.compiler!=expected.compiler||extension.iterator_debug!=expected.iterator_debug||extension.dynamic_crt!=expected.dynamic_crt||
       extension.debug_crt!=expected.debug_crt||!extension.types||!extension.count||extension.count>32)
        throw std::invalid_argument("incompatible Glass control extension ABI/toolchain");
    auto &r=registry();std::lock_guard<std::mutex> lock(r.mutex);
    std::map<std::string,GlassControlType> additions;
    for(std::size_t i=0;i<extension.count;i++) {
        auto t=extension.types[i];std::string name=t.name?t.name:"";
        if(name.empty()||name.size()>128||t.version!=1||!t.measure||!t.paint||!t.hit_test||!t.accessible_value||
           t.accessibility_role<1||t.accessibility_role>0x40||
           (t.focusable&&(!t.input||!t.default_action))||r.types.count(name)||!additions.emplace(name,t).second)
            throw std::invalid_argument("invalid or duplicate Glass control type");
        GlassControl probe;probe.kind=name;auto h=t.measure(probe);
        if(!std::isfinite(h)||h<16||h>1056)throw std::invalid_argument("invalid control default measure");
    }
    r.types.merge(additions);
}
const GlassControlType &glass_control_type(const std::string &name) {
    auto &r=registry();std::lock_guard<std::mutex> lock(r.mutex);
    auto at=r.types.find(name);if(at==r.types.end())throw std::invalid_argument("unregistered Glass control type: "+name);
    return at->second; // map entries are immutable, never erased.
}
}
