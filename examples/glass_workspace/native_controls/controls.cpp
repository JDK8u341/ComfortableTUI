#include "comfortable/glass.hpp"
#include <pybind11/pybind11.h>
#include <algorithm>
#include <cmath>
#include <atomic>
#include <thread>

namespace {
using namespace comfortable;
std::atomic<unsigned> paints{};
std::atomic<unsigned> created{},destroyed{},updated{},wrong_thread{},motion_created{},motion_destroyed{},motion_frames{};
std::atomic<unsigned> motion_stops{},motion_wakes{},last_changes{};
std::atomic<float> last_residual{},last_dt{};
std::atomic<unsigned> effect_writes{};
struct Instance{std::thread::id thread=std::this_thread::get_id();};
void *create_control(const GlassControl &){created++;return new Instance;}
void destroy_control(void *p){auto *s=static_cast<Instance *>(p);if(s->thread!=std::this_thread::get_id())wrong_thread++;destroyed++;delete s;}
void update_control(void *p,const GlassControl &){if(static_cast<Instance *>(p)->thread!=std::this_thread::get_id())wrong_thread++;updated++;}
struct Motion:Instance{float speed,layout_x{},layout_y{};bool initialized{};explicit Motion(float s):speed(s){}};
void *create_motion(const float *p,std::size_t){motion_created++;return new Motion(p[0]);}
void destroy_motion(void *p){auto *s=static_cast<Motion *>(p);if(s->thread!=std::this_thread::get_id())wrong_thread++;motion_destroyed++;delete s;}
bool frame_motion(void *p,const GlassMotionFrame &f,GlassVisual &v){
    auto *s=static_cast<Motion *>(p);motion_frames++;
    if(f.changes){motion_wakes++;last_changes=f.changes;}
    if(s->thread!=std::this_thread::get_id())wrong_thread++;
    if(!f.card&&s->initialized&&(f.changes&1)){v.x+=s->layout_x-f.layout_x;v.y+=s->layout_y-f.layout_y;}
    s->layout_x=f.layout_x;s->layout_y=f.layout_y;s->initialized=true;
    float target_alpha=f.exiting?0.f:f.hovered||f.focused?1.f:.88f;
    float k=f.reduced_motion?1.f:static_cast<float>(1-std::exp(-s->speed*f.dt));
    v.x+=(f.target_x-v.x)*k;v.y+=(f.target_y-v.y)*k;v.opacity+=(target_alpha-v.opacity)*k;
    last_residual=std::abs(v.x-f.target_x)+std::abs(v.y-f.target_y)+std::abs(v.opacity-target_alpha);last_dt=static_cast<float>(f.dt);
    if(std::abs(v.x-f.target_x)<.04&&std::abs(v.y-f.target_y)<.04&&std::abs(v.opacity-target_alpha)<.002){
        v.x=f.target_x;v.y=f.target_y;v.opacity=target_alpha;motion_stops++;return false;
    }return true;
}
const GlassEffectParameter motion_parameters[]={{"speed",1,60,14}};
bool frame_pulse(void *p,const GlassMotionFrame &f,GlassVisual &){
    auto *s=static_cast<Motion *>(p);motion_frames++;
    if(s->thread!=std::this_thread::get_id())wrong_thread++;
    if(!f.visible||!f.effects||!f.effect_count)return false;
    float amount=f.reduced_motion?.5f:static_cast<float>(.5+.45*std::sin(f.elapsed*s->speed));
    f.effects->set(0,&amount,1);effect_writes++;return !f.reduced_motion&&f.elapsed<.8;
}
const GlassMotionType motions[]={{"example.ease",1,motion_parameters,1,create_motion,destroy_motion,frame_motion},
    {"example.pulse",1,motion_parameters,1,create_motion,destroy_motion,frame_pulse}};
const GlassMotionExtension motion_extension=[] {GlassMotionExtension e;e.motions=motions;e.count=2;return e;}();
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
const GlassControlType types[]={{"example.dial",3,true,0x33,"Adjust",measure,paint,hit,input,value,create_control,destroy_control,update_control}};
const GlassControlExtension extension=[] {GlassControlExtension e;e.types=types;e.count=1;return e;}();
}
PYBIND11_MODULE(ctui_glass_controls,m){
    m.def("glass_controls",[]{return pybind11::capsule(&extension,comfortable::glass_extension_capsule);});
    m.def("paint_count",[]{return paints.load();});
    m.def("glass_motions",[]{return pybind11::capsule(&motion_extension,comfortable::glass_motion_extension_capsule);});
    m.def("instance_stats",[]{pybind11::dict d;d["created"]=created.load();d["destroyed"]=destroyed.load();d["updated"]=updated.load();
        d["wrong_thread"]=wrong_thread.load();d["motion_created"]=motion_created.load();d["motion_destroyed"]=motion_destroyed.load();d["motion_frames"]=motion_frames.load();
        d["motion_stops"]=motion_stops.load();d["motion_wakes"]=motion_wakes.load();d["last_changes"]=last_changes.load();d["last_residual"]=last_residual.load();d["last_dt"]=last_dt.load();d["effect_writes"]=effect_writes.load();return d;});
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
