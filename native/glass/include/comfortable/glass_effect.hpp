#pragma once
#include <cstddef>
#include <cstdint>
#include <d3d11_1.h>
#include <string>
#include <vector>
#include "comfortable/compositor.hpp"

namespace comfortable {
// Trusted in-process native effects. Definitions and device programs are
// registered once; parameter vectors and output targets belong to each node.
struct GlassEffectParameter {
    const char *name{};
    float minimum{}, maximum{1}, default_value{};
};
struct GlassEffectPaint {
    ID3D11DeviceContext *context{};
    ID3D11ShaderResourceView *input{}; // Expanded, premultiplied local input patch.
    ID3D11RenderTargetView *output{};  // Separate, node-local output texture.
    std::uint32_t input_width{}, input_height{};
    std::uint32_t output_width{}, output_height{};
    std::int32_t input_x{}, input_y{}; // Physical-pixel origin within input.
    float dpi{1};
    std::uint32_t tint{0xffffff};
    float opacity{1};
    const float *parameters{};
    std::size_t parameter_count{};
};
struct GlassEffectType {
    const char *name{};
    std::uint32_t version{2};
    std::uint32_t shader_id{}; // Reserved for builtins. Extensions must use 0 and render().
    const GlassEffectParameter *parameters{};
    std::size_t parameter_count{};
    float sampling_radius{}; // Logical pixels; expands the output patch.
    void *(*create_device_resources)(ID3D11Device *){};
    void (*destroy_device_resources)(void *){};
    bool (*render)(void *, const GlassEffectPaint &){};
};
inline constexpr const char *glass_effect_extension_capsule="comfortable.glass.effects.v2";
struct GlassEffectExtension {
    std::uint32_t abi{2}, structure_size{sizeof(GlassEffectType)}, pointer_size{sizeof(void *)};
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
struct GlassEffectBinding {
    std::string type;
    std::string target{"content"};
    std::vector<float> parameters;
    std::uint32_t tint{0xffffff};
    float opacity{1};
};
void validate_glass_effect(const GlassEffectBinding &);
void register_glass_shader(const std::string &name,const std::string &source,const std::string &entry,
                           const std::vector<GlassEffectParameter> &parameters,float sampling_radius);
std::vector<float> glass_effect_parameters(const std::string &, const std::vector<float> &);
std::shared_ptr<NativeImageProcessor> make_glass_effect_chain(
    const std::vector<GlassEffectBinding> &, float dpi,float corner_radius=0);
bool update_glass_effect_chain(const std::shared_ptr<NativeImageProcessor> &,
                              const std::vector<GlassEffectBinding> &);
std::uint64_t glass_effect_surface_bytes(); // Local effect textures only, for diagnostics/tests.
// Cumulative allocation/compilation counts. Updated only on resource creation,
// never per pixel or per parameter update; process-local, not driver VRAM usage.
struct GlassEffectDiagnostics {
    std::uint64_t surface_bytes{}, surface_allocations{}, shader_compilations{}, program_sets{},package_shader_compilations{};
};
GlassEffectDiagnostics glass_effect_diagnostics();
} // namespace comfortable
