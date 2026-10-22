#include "comfortable/glass.hpp"
#include <cmath>
#include <iostream>
#include <limits>
#include <functional>
using namespace comfortable;
namespace {
unsigned creates{},destroys{},calls{};
double last_dt{};unsigned changes{};
void require(bool x,const char *s){if(!x)throw std::runtime_error(s);}
void bad(const std::function<void()> &fn){bool rejected=false;try{fn();}catch(const std::exception &){rejected=true;}require(rejected,"invalid motion accepted");}
void *create(const float *,std::size_t){creates++;return new int;}
void destroy(void *p){destroys++;delete static_cast<int *>(p);}
bool frame(void *,const GlassMotionFrame &f,GlassVisual &v){calls++;last_dt=f.dt;changes=f.changes;v.x=f.target_x;v.y=f.target_y;v.opacity=f.hovered?.5f:1;return false;}
bool invalid(void *,const GlassMotionFrame &,GlassVisual &v){v.x=std::numeric_limits<float>::quiet_NaN();return true;}
}
int main(){try{
    static const GlassEffectParameter p[]={{"speed",1,20,8}};
    static const GlassMotionType types[]={{"test.motion",1,p,1,create,destroy,frame},{"test.invalid",1,nullptr,0,create,destroy,invalid}};
    GlassMotionExtension e;e.motions=types;e.count=2;register_glass_motions(e);
    auto wrong=e;wrong.abi=0;bad([&]{register_glass_motions(wrong);});bad([&]{register_glass_motions(e);});
    bad([&]{glass_motion_parameters("test.motion",{0});});
    require(glass_motion_parameters("test.motion",{})[0]==8,"motion defaults");
    GlassVisual visual;
    {
        GlassMotionInstance motion({"test.motion",{}},visual);
        GlassMotionFrame f;f.time=10;f.visible=true;f.target_x=40;
        require(motion.tick(f,visual)&&last_dt==0&&visual.x==40&&(changes&32),"initial frame and layout target");
        f.time+=.5;require(!motion.tick(f,visual)&&calls==1,"settled callback must sleep");
        motion.wake(64);f.time+=.01;motion.tick(f,visual);require((changes&64)&&calls==2,"activation must be latched between frames");
        f.hovered=true;require(motion.tick(f,visual)&&visual.opacity==.5f&&(changes&2),"hover wakes callback");
        f.visible=false;f.time++;motion.tick(f,visual);auto hidden_calls=calls;
        f.time++;motion.tick(f,visual);require(calls==hidden_calls,"hidden callback must pause");
        f.visible=true;f.time++;motion.tick(f,visual);require(last_dt==0,"resume dt must be zero");
        auto reduced_calls=calls;f.reduced_motion=true;f.time++;motion.tick(f,visual);
        require(calls==reduced_calls+1&&(changes&128)&&!motion.active(),"accessibility change wakes settled motion then sleeps");
        motion.restart();f.time++;motion.tick(f,visual);require(last_dt==0&&(changes&32),"explicit restart resets clock without allocation");
    }
    {
        GlassMotionInstance motion({"test.invalid",{}},visual);GlassMotionFrame f;f.visible=true;f.time=20;
        const auto before=visual;bad([&]{motion.tick(f,visual);});require(visual.x==before.x,"invalid visual must be atomic");
    }
    require(creates==destroys,"motion instances leaked");
    GlassCard card;card.width=400;
    GlassControl container;container.id="row";container.kind="container";container.layout="row";container.gap=12;container.padding=8;
    GlassControl left;left.id="left";left.kind="button";left.parent="row";
    auto right=left;right.id="right";right.weight=2;
    card.controls={container,left,right};layout_glass_controls(card);
    require(std::abs(card.controls[1].bounds.x-28)<.01&&std::abs(card.controls[1].bounds.width-332.f/3)<.01,"weighted row geometry");
    require(card.controls[2].bounds.x>card.controls[1].bounds.x+card.controls[1].bounds.width,"row gap");
    card.controls[0].layout="stack";layout_glass_controls(card);
    require(card.controls[1].bounds.x==card.controls[2].bounds.x&&card.controls[1].ink.y!=card.controls[2].ink.y,"stack nodes need independent atlas ink");
    card.controls[0].enabled=false;card.controls[2].visible=false;layout_glass_controls(card);
    require(!card.controls[1].effective_enabled&&!card.controls[2].effective_visible,"ancestor enabled/visibility inheritance");
    card.controls[1].parent="missing";bad([&]{layout_glass_controls(card);});
    std::cout<<"{\"result\":\"passed\",\"creates\":"<<creates<<",\"destroys\":"<<destroys<<",\"idle_callbacks\":0}\n";return 0;
}catch(const std::exception &e){std::cerr<<e.what()<<"\n";return 1;}}
