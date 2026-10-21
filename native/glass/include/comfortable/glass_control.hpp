#pragma once
#include <cstddef>
#include <cstdint>
#include <string>

namespace comfortable {
struct GlassControl;
// Trusted, in-process extensions. Build with the runtime's MSVC/CRT and SDK.
// Coordinates are logical pixels relative to the control, never desktop pixels.
struct GlassPainter {
    virtual ~GlassPainter() = default;
    virtual void fill(float x, float y, float w, float h, std::uint32_t rgb, float alpha, float radius=0)=0;
    virtual void text(const std::string &,float x,float y,float w,float size,std::uint32_t rgb,float alpha,bool bold=false)=0;
    virtual void line(float x,float y,float x2,float y2,std::uint32_t rgb,float alpha,float thickness=1)=0;
    virtual void circle(float x,float y,float radius,std::uint32_t rgb,float alpha)=0;
};
struct GlassControlPaint {
    float width{},height{},dpi{1},opacity{1};
    std::uint32_t accent{};
    bool hovered{},focused{},pressed{},text_selected{},reduced_motion{};
};
enum class GlassControlAction { none, click, change };
struct GlassControlInput {
    // "activate" is mouse release, Enter/Space, or accessibility default action.
    // "key" contains a Windows virtual key. Return none for an unhandled key.
    const char *kind{"activate"};
    float x{},y{},width{},height{};
    unsigned key{};
};
struct GlassControlType {
    const char *name{};
    std::uint32_t version{2};
    bool focusable{};
    long accessibility_role{0x28}; // ROLE_SYSTEM_GRAPHIC
    const char *default_action{};
    float (*measure)(const GlassControl &){};
    void (*paint)(GlassPainter &,const GlassControl &,const GlassControlPaint &){};
    bool (*hit_test)(const GlassControl &,float x,float y,float width,float height){};
    GlassControlAction (*input)(GlassControl &,const GlassControlInput &){};
    std::string (*accessible_value)(const GlassControl &){};
};
// Capsule contains an immutable table. The binding retains its Python owner for
// the interpreter lifetime; no DLL scanning, unregistering or hot unloading.
inline constexpr const char *glass_extension_capsule="comfortable.glass.controls.v2";
struct GlassControlExtension {
    std::uint32_t abi{2}, structure_size{sizeof(GlassControlType)}, pointer_size{sizeof(void *)};
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
    const GlassControlType *types{};
    std::size_t count{};
};
void register_glass_controls(const GlassControlExtension &);
const GlassControlType &glass_control_type(const std::string &);
} // namespace comfortable
