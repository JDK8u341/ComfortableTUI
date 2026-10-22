#include "comfortable/glass.hpp"
#include <algorithm>
#include <cmath>
#include <functional>
#include <map>
#include <stdexcept>

namespace comfortable {
namespace {
struct Layout {
    const GlassCard &card;
    std::vector<std::vector<std::size_t>> children;
    std::vector<std::size_t> roots;
    std::vector<PixelBox> boxes,clips;
    std::vector<bool> visible,enabled;
    explicit Layout(const GlassCard &c):card(c),children(c.controls.size()),boxes(c.controls.size()),clips(c.controls.size()),
        visible(c.controls.size()),enabled(c.controls.size()){
        std::map<std::string,std::size_t> ids;std::vector<unsigned> depth(c.controls.size());std::vector<std::string> ancestors;
        for(std::size_t i=0;i<c.controls.size();i++){
            const auto &v=c.controls[i];
            if(!ids.emplace(v.id,i).second)throw std::invalid_argument("duplicate local control id");
            if(!std::isfinite(v.gap)||v.gap<0||v.gap>128||!std::isfinite(v.padding)||v.padding<0||v.padding>128||
               !std::isfinite(v.weight)||v.weight<=0||v.weight>1024)throw std::invalid_argument("invalid local layout dimensions");
            if(v.parent.empty()){roots.push_back(i);visible[i]=v.visible;enabled[i]=v.enabled;ancestors.clear();}
            else{
                auto active=std::find(ancestors.begin(),ancestors.end(),v.parent);
                if(active==ancestors.end())throw std::invalid_argument("local controls must be in tree preorder");
                ancestors.erase(active+1,ancestors.end());
                auto at=ids.find(v.parent);
                if(at==ids.end()||at->second>=i||c.controls[at->second].kind!="container"||
                   (depth[i]=depth[at->second]+1)>8)throw std::invalid_argument("invalid local container tree");
                children[at->second].push_back(i);visible[i]=v.visible&&visible[at->second];enabled[i]=v.enabled&&enabled[at->second];
            }
            if(v.kind=="container"&&v.layout!="row"&&v.layout!="column"&&v.layout!="stack")throw std::invalid_argument("invalid container layout");
            if(v.kind=="container")ancestors.push_back(v.id);
        }
    }
    float height(std::size_t i,float width){
        if(!visible[i])return 0;
        const auto &v=card.controls[i];
        if(v.kind!="container"){
            auto h=glass_control_type(v).measure(v);if(!std::isfinite(h)||h<16||h>1056)throw std::invalid_argument("invalid measured control height");return h;
        }
        const auto widths=child_widths(i,width);float h=0;unsigned count=0;
        for(auto child:children[i])if(visible[child]){
            float next=height(child,widths.at(child));h=v.layout=="column"?h+next:std::max(h,next);count++;
        }
        if(v.layout=="column"&&count>1)h+=(count-1)*v.gap;
        return std::max(16.f,h+2*v.padding);
    }
    std::map<std::size_t,float> child_widths(std::size_t i,float width){
        const auto &v=card.controls[i];float available=width-2*v.padding,total=0;unsigned count=0;
        for(auto child:children[i])if(visible[child]){total+=card.controls[child].weight;count++;}
        if(v.layout=="row"&&count>1)available-=(count-1)*v.gap;
        std::map<std::size_t,float> result;
        for(auto child:children[i]){
            float w=v.layout=="row"&&total>0?available*card.controls[child].weight/total:available;
            if(visible[child]&&w<16)throw std::invalid_argument("container is too narrow for its children");result[child]=std::max(0.f,w);
        }return result;
    }
    void arrange(std::size_t i,float x,float y,float width,PixelBox parent_clip){
        if(!visible[i])return;
        const auto &v=card.controls[i];float h=height(i,width);boxes[i]={x,y,width,h};clips[i]=parent_clip;
        if(v.kind!="container")return;
        float cx=x+v.padding,cy=y+v.padding;
        PixelBox child_clip{std::max(x+v.padding,parent_clip.x),std::max(y+v.padding,parent_clip.y)};
        child_clip.width=std::max(0.f,std::min(x+width-v.padding,parent_clip.x+parent_clip.width)-child_clip.x);
        child_clip.height=std::max(0.f,std::min(y+h-v.padding,parent_clip.y+parent_clip.height)-child_clip.y);
        auto widths=child_widths(i,width);
        for(auto child:children[i])if(visible[child]){
            arrange(child,cx,cy,widths[child],child_clip);
            if(v.layout=="row")cx+=widths[child]+v.gap;
            if(v.layout=="column")cy+=height(child,widths[child])+v.gap;
        }
    }
    float run(){
        float h=0;for(auto root:roots)h+=height(root,card.width-40);
        float y=102;for(auto root:roots)if(visible[root]){arrange(root,20,y,card.width-40,{20,102,card.width-40,h});y+=height(root,card.width-40);}
        return h+122;
    }
};
}
float glass_card_height(const GlassCard &card){Layout layout(card);return layout.run();}
void layout_glass_controls(GlassCard &card){
    Layout layout(card);layout.run();
    for(std::size_t i=0;i<card.controls.size();i++){
        auto &c=card.controls[i];c.bounds=layout.boxes[i];c.clip=layout.clips[i];c.effective_visible=layout.visible[i];c.effective_enabled=layout.enabled[i];
    }
    bool atlas=std::any_of(card.controls.begin(),card.controls.end(),[](const auto &c){return c.kind=="container"||!c.effects.empty()||!c.motion.type.empty()||c.corner_radius>0;});
    card.ink_height=0;float x=0,y=0,line=0;
    for(auto &c:card.controls){
        c.ink=c.bounds;if(!atlas||!c.effective_visible||c.kind=="container")continue;
        if(x>0&&x+c.bounds.width>card.width){x=0;y+=line;line=0;}
        c.ink={x,y,c.bounds.width,c.bounds.height};x+=c.bounds.width;line=std::max(line,c.bounds.height);
    }
    if(atlas)card.ink_height=std::max(1.f,y+line);
}
}
