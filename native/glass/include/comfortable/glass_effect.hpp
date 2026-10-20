#pragma once
#include <cstddef>
#include <cstdint>
#include <d3d11_1.h>
#include <string>

namespace comfortable {
// Trusted in-process native effects. Definitions and device programs are
// registered once; parameter vectors and output targets belong to each node.
struct GlassEffectParameter {
    const char *name{};
    float minimum{}, maximum{1}, default_value{};
};
struct GlassEffectPaint {
    ID3D11DeviceContext *context{};
    ID3D11ShaderResourceView *input{}; // The completed card-ink texture.
    ID3D11RenderTargetView *output{};  // Separate, node-local output texture.
    std::uint32_t input_width{}, input_height{};
    std::uint32_t output_width{}, output_height{};
    std::int32_t input_x{}, input_y{}; // Physical-pixel origin within input.
    float dpi{1};
    const float *parameters{};
    std::size_t parameter_count{};
};
struct GlassEffectType {
    const char *name{};
    std::uint32_t version{1};
    std::uint32_t shader_id{}; // Runtime-defined shader family; custom HLSL is a later SDK layer.
    const GlassEffectParameter *parameters{};
    std::size_t parameter_count{};
    float sampling_radius{}; // Logical pixels; expands the output patch.
    void *(*create_device_resources)(ID3D11Device *){};
    void (*destroy_device_resources)(void *){};
    bool (*render)(void *, const GlassEffectPaint &){};
};
inline constexpr const char *glass_effect_extension_capsule="comfortable.glass.effects.v1";
struct GlassEffectExtension {
    std::uint32_t abi{1}, structure_size{sizeof(GlassEffectType)}, pointer_size{sizeof(void *)};
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
    const GlassEffectType *types{};
    std::size_t count{};
};
void register_glass_effects(const GlassEffectExtension &);
const GlassEffectType &glass_effect_type(const std::string &);
} // namespace comfortable
