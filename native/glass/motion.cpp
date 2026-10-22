#include "comfortable/glass_motion.hpp"
#include <map>
#include <mutex>
#include <set>
#include <cmath>
#include <stdexcept>
#include <algorithm>

namespace comfortable {
namespace {
struct Registry {std::mutex mutex;std::map<std::string,GlassMotionType> types;};
Registry &registry(){static Registry r;return r;}
const GlassMotionType &type(const std::string &name){
    auto &r=registry();std::lock_guard<std::mutex> lock(r.mutex);auto at=r.types.find(name);
    if(at==r.types.end())throw std::invalid_argument("unregistered Glass motion: "+name);return at->second;
}
}
void register_glass_motions(const GlassMotionExtension &e){
    GlassMotionExtension expected;
    if(e.abi!=expected.abi||e.structure_size!=expected.structure_size||e.pointer_size!=expected.pointer_size||
       e.compiler!=expected.compiler||e.iterator_debug!=expected.iterator_debug||e.dynamic_crt!=expected.dynamic_crt||
       e.debug_crt!=expected.debug_crt||!e.motions||!e.count||e.count>32)
        throw std::invalid_argument("incompatible Glass motion ABI/toolchain");
    auto &r=registry();std::lock_guard<std::mutex> lock(r.mutex);std::map<std::string,GlassMotionType> additions;
    for(std::size_t i=0;i<e.count;i++){
        const auto &t=e.motions[i];std::string name=t.name?t.name:"";
        if(name.empty()||name.size()>128||t.version!=1||!t.frame||!t.create||!t.destroy||t.parameter_count>16||
           (t.parameter_count&&!t.parameters)||r.types.count(name)||!additions.emplace(name,t).second)
            throw std::invalid_argument("invalid or duplicate Glass motion type");
        std::set<std::string> names;
        for(std::size_t j=0;j<t.parameter_count;j++){
            const auto &p=t.parameters[j];std::string n=p.name?p.name:"";
            if(n.empty()||n.size()>64||!names.insert(n).second||!std::isfinite(p.minimum)||!std::isfinite(p.maximum)||
               !std::isfinite(p.default_value)||p.minimum>p.maximum||p.default_value<p.minimum||p.default_value>p.maximum)
                throw std::invalid_argument("invalid motion parameter schema");
        }
    }r.types.merge(additions);
}
std::vector<float> glass_motion_parameters(const std::string &name,const std::vector<float> &values){
    const auto &t=type(name);if(values.size()>t.parameter_count)throw std::invalid_argument("too many motion parameters");
    std::vector<float> result;result.reserve(t.parameter_count);
    for(std::size_t i=0;i<t.parameter_count;i++){
        float v=i<values.size()?values[i]:t.parameters[i].default_value;
        if(!std::isfinite(v)||v<t.parameters[i].minimum||v>t.parameters[i].maximum)throw std::invalid_argument("motion parameter outside schema");
        result.push_back(v);
    }return result;
}
struct GlassMotionInstance::Impl {
    GlassMotionBinding binding;const GlassMotionType *definition{};void *state{};
    GlassMotionFrame previous{};double started{},last{};std::uint64_t index{};
    bool running{true},initial{true},was_visible{};
    unsigned pending{};
    Impl(const GlassMotionBinding &b):binding(b),definition(&type(b.type)){
        auto p=glass_motion_parameters(b.type,b.parameters);state=definition->create(p.data(),p.size());
        if(!state)throw std::runtime_error("motion instance creation failed: "+b.type);
    }
    ~Impl(){if(state)definition->destroy(state);}
};
GlassMotionInstance::GlassMotionInstance(const GlassMotionBinding &b,const GlassVisual &):impl_(std::make_unique<Impl>(b)){}
GlassMotionInstance::~GlassMotionInstance()=default;
bool GlassMotionInstance::matches(const GlassMotionBinding &b)const{return impl_->binding.type==b.type&&impl_->binding.parameters==b.parameters;}
bool GlassMotionInstance::active()const{return impl_->running;}
void GlassMotionInstance::wake(unsigned changes){impl_->pending|=changes;impl_->running=true;}
void GlassMotionInstance::restart(){auto &p=*impl_;p.running=p.initial=true;p.started=p.last=0;p.index=0;p.pending|=32;}
bool GlassMotionInstance::tick(GlassMotionFrame f,GlassVisual &visual){
    auto &p=*impl_;const auto old=p.previous;
    f.changes=(f.target_x!=old.target_x||f.target_y!=old.target_y||f.width!=old.width||f.height!=old.height||f.layout_x!=old.layout_x||f.layout_y!=old.layout_y?1:0)|
        (f.hovered!=old.hovered?2:0)|(f.focused!=old.focused?4:0)|(f.pressed!=old.pressed?8:0)|
        (f.visible!=old.visible||f.exiting!=old.exiting?16:0)|(p.initial?32:0)|
        (f.reduced_motion!=old.reduced_motion?128:0)|p.pending;
    p.previous=f;
    p.previous.effects=nullptr;
    if(f.changes)p.running=true;
    if(!f.visible&&!f.exiting&&!old.visible&&!p.initial)return false;
    // Give the hide transition one callback, then pause unless it elects to finish an exit.
    if(!p.running)return false;
    p.pending=0;
    if(!p.started)p.started=f.time;
    f.dt=p.initial||(!p.was_visible&&f.visible)?0:std::max(0.,f.time-p.last);
    f.elapsed=std::max(0.,f.time-p.started);f.index=p.index++;
    p.initial=false;p.last=f.time;p.was_visible=f.visible;
    auto next=visual;
    try{p.running=p.definition->frame(p.state,f,next);}catch(...){throw std::runtime_error("native motion callback threw: "+p.binding.type);}
    if(!std::isfinite(next.x)||!std::isfinite(next.y)||std::abs(next.x)>1e7||std::abs(next.y)>1e7||
       !std::isfinite(next.opacity)||next.opacity<0||next.opacity>1)throw std::runtime_error("native motion returned invalid visual state");
    if(f.reduced_motion)p.running=false;
    bool changed=next.x!=visual.x||next.y!=visual.y||next.opacity!=visual.opacity;visual=next;return changed;
}
}
