#pragma once
#include "comfortable/compositor.hpp"
#include "comfortable/glass_control.hpp"

namespace comfortable {
struct GlassBackground {
    std::string kind{"liquid"};
    std::uint32_t tint{0xffffff};
    float opacity{.04f}, blur{1.2f}, refraction{20}, dispersion{.45f}, corner_radius{24};
};
struct GlassControl {
    std::string id, kind, label, text, fit{"contain"};
    double value{};
    bool enabled{true};
    float canvas_height{96};
    bool capture_escape{};
    std::uint64_t acknowledged{}, edit_serial{};
    std::vector<double> series;
    std::vector<std::string> options;
    NativeView canvas;
    std::uint64_t generation{};
    float opacity{1};
    const GlassControlType *type{};
};
inline const GlassControlType &glass_control_type(const GlassControl &c){return c.type?*c.type:glass_control_type(c.kind);}
struct GlassCard {
    std::string id, title, subtitle, eyebrow, group;
    float width{320};
    std::vector<GlassControl> controls;
    GlassBackground background;
    bool has_background{}, draggable{true};
    std::uint64_t generation{};
};
struct GlassGroup {
    std::string id,parent,layout{"row"};
    std::vector<std::string> children;
    float gap{16},padding{};
    bool visible{true},enabled{true};
};
struct GlassScene {
    std::vector<GlassCard> cards;
    std::vector<GlassGroup> groups;
    std::vector<std::string> roots;
    std::string layout{"orbit"}, material{"glass"}, backdrop{"none"};
    std::string selected;
    std::uint32_t accent{0x85e5d1};
    bool visible{true}, pane_visible{true}, reduced_motion{}, reduced_transparency{};
};
struct GlassEvent {
    std::string kind, card, control, text;
    double value{}, x{}, y{};
    std::uint64_t sequence{};
    std::vector<std::string> modifiers;
    std::int32_t scan_code{};
    bool repeat{};
    std::string button{"left"};
    int buttons{},clicks{1};
    bool inside_source{true};
    std::string source_name;
    std::uint64_t generation{};
};
struct GlassMetrics {
    std::uint64_t applied{}, frames{}, presents{}, canvas_frames{}, dropped_events{};
    double draw_ms{}, tick_ms{};
    // Published after synchronous shutdown; sums across cards where applicable.
    double close_hide_ms{}, close_join_ms{}, close_surfaces_ms{}, close_windows_ms{}, close_resources_ms{};
    bool active{}, compact{}, backdrop_visible{}, high_contrast{}, system_animation{true}, closed{};
    std::string error, material;
    std::vector<std::uintptr_t> windows;
    std::vector<std::string> ids;
    std::vector<PixelBox> rects;
    std::vector<std::string> materials;
    std::vector<bool> pinned, draggable;
    std::vector<bool> visible,enabled;
    std::vector<std::string> focused_control;
    std::uint64_t capture_frames{}, optical_frames{}, copied_pixels{};
    std::uint64_t unchanged_capture_frames{};
    bool capture_active{};
    std::string optics_error,backdrop_material{"none"};
};
class GlassHost {
  public:
    explicit GlassHost(std::uintptr_t owner, bool inspectable_windows = false);
    ~GlassHost();
    GlassHost(const GlassHost &) = delete;
    std::uint64_t configure(GlassScene);
    void command(const std::string &action, const std::string &value = "");
    void begin_drag(const std::string &card,std::uint64_t pointer_sequence);
    void present(const std::string &card,const std::string &control,std::uint64_t generation,float opacity);
    std::vector<GlassEvent> events();
    GlassMetrics metrics() const;
    void close();
  private:
    struct Impl;
    std::unique_ptr<Impl> impl_;
};
} // namespace comfortable
