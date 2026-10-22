#pragma once
#include "glass_effect.hpp"
#include <memory>

namespace comfortable {
struct GlassMotionBinding {std::string type;std::vector<float> parameters;};
struct GlassVisual {float x{},y{},opacity{1};};
struct GlassMotionEffects {
    virtual ~GlassMotionEffects()=default;
    // Valid only inside frame(), on the UI worker. Updates commit after callback.
    virtual void set(std::size_t index,const float *parameters,std::size_t count)=0;
};
struct GlassMotionFrame {
    double time{},dt{},elapsed{};
    std::uint64_t index{};
    float target_x{},target_y{},width{},height{};
    float pointer_x{},pointer_y{};
    float layout_x{},layout_y{};
    bool card{},reduced_motion{},visible{},exiting{},hovered{},focused{},pressed{};
    // Bitmask: target/size=1, hover=2, focus=4, press=8, visibility=16, restart=32, activate=64, accessibility=128.
    unsigned changes{};
    GlassMotionEffects *effects{};
    std::size_t effect_count{};
};
struct GlassMotionType {
    const char *name{};
    std::uint32_t version{1};
    const GlassEffectParameter *parameters{}; // Shared schema format, not an effect binding.
    std::size_t parameter_count{};
    void *(*create)(const float *,std::size_t){};
    void (*destroy)(void *){};
    // UI worker only, no Python/GIL. Return false to sleep until state changes.
    bool (*frame)(void *,const GlassMotionFrame &,GlassVisual &){};
};
inline constexpr const char *glass_motion_extension_capsule="comfortable.glass.motions.v1";
struct GlassMotionExtension {
    std::uint32_t abi{1},structure_size{sizeof(GlassMotionType)},pointer_size{sizeof(void *)};
#ifdef _MSC_VER
    std::uint32_t compiler{_MSC_VER};
#else
    std::uint32_t compiler{};
#endif
#ifdef _ITERATOR_DEBUG_LEVEL
    std::uint32_t iterator_debug{_ITERATOR_DEBUG_LEVEL};
#else
    std::uint32_t iterator_debug{};
#endif
#ifdef _DLL
    std::uint32_t dynamic_crt{1};
#else
    std::uint32_t dynamic_crt{};
#endif
#ifdef _DEBUG
    std::uint32_t debug_crt{1};
#else
    std::uint32_t debug_crt{};
#endif
    const GlassMotionType *motions{};
    std::size_t count{};
};
void register_glass_motions(const GlassMotionExtension &);
std::vector<float> glass_motion_parameters(const std::string &,const std::vector<float> &);
// Noncopyable, UI-thread-owned per-node state; immutable definitions may be shared.
class GlassMotionInstance {
    struct Impl;std::unique_ptr<Impl> impl_;
  public:
    explicit GlassMotionInstance(const GlassMotionBinding &,const GlassVisual &);
    ~GlassMotionInstance();
    bool tick(GlassMotionFrame,GlassVisual &);
    bool matches(const GlassMotionBinding &)const;
    bool active()const;
    void wake(unsigned changes);
    void restart();
};
}
