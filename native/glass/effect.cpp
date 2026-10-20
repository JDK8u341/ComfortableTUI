#include "comfortable/glass_effect.hpp"
#include <cmath>
#include <cstring>
#include <map>
#include <mutex>
#include <set>
#include <stdexcept>

namespace comfortable {
namespace {
struct Registry {
    std::mutex mutex;
    std::map<std::string, GlassEffectType> types;
    Registry() {
        static const GlassEffectParameter tint_params[]={{"amount",0,1,.35f}};
        static const GlassEffectParameter blur_params[]={{"radius",0,24,6.f}};
        static const GlassEffectParameter liquid_params[]={{"refraction",0,48,12.f},{"dispersion",0,3,.35f}};
        static const GlassEffectParameter glow_params[]={{"intensity",0,4,1.f},{"radius",0,64,18.f}};
        auto noop=[](void *,const GlassEffectPaint &){return true;};
        types.emplace("tint",GlassEffectType{"tint",1,1,tint_params,1,0,nullptr,nullptr,noop});
        types.emplace("blur",GlassEffectType{"blur",1,2,blur_params,1,24,nullptr,nullptr,noop});
        types.emplace("liquid",GlassEffectType{"liquid",1,3,liquid_params,2,24,nullptr,nullptr,noop});
        types.emplace("glow",GlassEffectType{"glow",1,4,glow_params,2,64,nullptr,nullptr,noop});
    }
};
Registry &registry() { static Registry value; return value; }
}
void register_glass_effects(const GlassEffectExtension &extension) {
    GlassEffectExtension expected;
    if (extension.abi != expected.abi || extension.structure_size != expected.structure_size ||
        extension.pointer_size != expected.pointer_size || extension.compiler != expected.compiler ||
        extension.iterator_debug != expected.iterator_debug || extension.dynamic_crt != expected.dynamic_crt ||
        extension.debug_crt != expected.debug_crt || !extension.types || !extension.count || extension.count > 32)
        throw std::invalid_argument("incompatible Glass effect extension ABI/toolchain");
    auto &r = registry();
    std::lock_guard<std::mutex> lock(r.mutex);
    std::map<std::string, GlassEffectType> additions;
    for (std::size_t i = 0; i < extension.count; ++i) {
        const auto type = extension.types[i];
        const std::string name = type.name ? type.name : "";
        if (name.empty() || name.size() > 128 || type.version != 1 || !type.render || type.shader_id > 0xffff ||
            type.parameter_count > 16 || (type.parameter_count && !type.parameters) ||
            !std::isfinite(type.sampling_radius) || type.sampling_radius < 0 || type.sampling_radius > 128 ||
            (type.create_device_resources == nullptr) != (type.destroy_device_resources == nullptr) ||
            r.types.count(name) || additions.count(name))
            throw std::invalid_argument("invalid or duplicate Glass effect type");
        std::set<std::string> parameters;
        for (std::size_t p = 0; p < type.parameter_count; ++p) {
            const auto &parameter = type.parameters[p];
            const std::string parameter_name = parameter.name ? parameter.name : "";
            if (parameter_name.empty() || parameter_name.size() > 64 || !parameters.insert(parameter_name).second ||
                !std::isfinite(parameter.minimum) || !std::isfinite(parameter.maximum) ||
                !std::isfinite(parameter.default_value) || parameter.minimum > parameter.maximum ||
                parameter.default_value < parameter.minimum || parameter.default_value > parameter.maximum)
                throw std::invalid_argument("invalid Glass effect parameter schema");
        }
        additions.emplace(name, type);
    }
    r.types.merge(additions);
}
const GlassEffectType &glass_effect_type(const std::string &name) {
    auto &r = registry();
    std::lock_guard<std::mutex> lock(r.mutex);
    const auto at = r.types.find(name);
    if (at == r.types.end()) throw std::invalid_argument("unregistered Glass effect type: " + name);
    return at->second;
}
} // namespace comfortable
