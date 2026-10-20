#include "comfortable/glass.hpp"
#include <pybind11/pybind11.h>
#include <algorithm>
#include <cmath>
#include <atomic>

namespace {
using namespace comfortable;
std::atomic<unsigned> paints{};
float measure(const GlassControl &){return 100;}
bool hit(const GlassControl &,float x,float y,float width,float){
    // Leave the corners outside the circular dial untargeted. The label is ink.
    return std::hypot(x-width/2,y-46)<=35;
}
void paint(GlassPainter &p,const GlassControl &c,const GlassControlPaint &s){
    paints++;
    float x=s.width/2,y=46,a=s.opacity;
    // The same concentric geometry and quiet accents as the existing cards.
    p.circle(x,y,33,0x102331,.85f*a);
    for(int i=0;i<24;i++){
        double angle=(-135+i*270./23)*3.141592653589793/180;
        auto rgb=i/23.<=c.value?s.accent:0x415368;
        p.line(x+27*static_cast<float>(std::sin(angle)),y-27*static_cast<float>(std::cos(angle)),
               x+31*static_cast<float>(std::sin(angle)),y-31*static_cast<float>(std::cos(angle)),rgb,a,2);
    }
    double angle=(-135+270*c.value)*3.141592653589793/180;
    p.line(x,y,x+21*static_cast<float>(std::sin(angle)),y-21*static_cast<float>(std::cos(angle)),s.accent,a,3);
    p.circle(x,y,3,0xe1fff7,a);
    p.text(c.label+"  "+std::to_string(static_cast<int>(100*c.value))+"%",4,84,s.width-8,11,0xdbe9fa,a);
}
GlassControlAction input(GlassControl &c,const GlassControlInput &e){
    if(e.kind==std::string("key")){
        if(e.key!=37&&e.key!=39)return GlassControlAction::none;
        c.value=std::clamp(c.value+(e.key==39?.05:-.05),0.,1.);
    }else c.value=std::clamp(static_cast<double>(e.x/e.width),0.,1.);
    return GlassControlAction::change;
}
std::string value(const GlassControl &c){return std::to_string(static_cast<int>(100*c.value))+" percent";}
const GlassControlType types[]={{"example.dial",1,true,0x33,"Adjust",measure,paint,hit,input,value}};
const GlassControlExtension extension=[] {GlassControlExtension e;e.types=types;e.count=1;return e;}();
}
PYBIND11_MODULE(ctui_glass_controls,m){
    m.def("glass_controls",[]{return pybind11::capsule(&extension,comfortable::glass_extension_capsule);});
    m.def("paint_count",[]{return paints.load();});
    m.def("bad_abi",[]{
        static const GlassControlExtension bad=[] {auto e=extension;e.compiler++;return e;}();
        return pybind11::capsule(&bad,comfortable::glass_extension_capsule);
    });
    m.def("duplicate",[]{
        static const GlassControlType copies[]={types[0],types[0]};
        static const GlassControlExtension duplicate=[] {auto e=extension;e.types=copies;e.count=2;return e;}();
        return pybind11::capsule(&duplicate,comfortable::glass_extension_capsule);
    });
    m.def("atomic_failure",[]{
        static const GlassControlType copy=[] {auto a=types[0];a.name="example.never_registered";return a;}();
        static const GlassControlType list[]={copy,types[0]};
        static const GlassControlExtension bad=[] {auto e=extension;e.types=list;e.count=2;return e;}();
        return pybind11::capsule(&bad,comfortable::glass_extension_capsule);
    });
}
