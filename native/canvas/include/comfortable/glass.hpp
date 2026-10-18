#pragma once
#include "compositor.hpp"

namespace comfortable {
struct GlassControl {
    std::string id, kind, label, text, fit{"contain"};
    double value{};
    bool enabled{true};
    std::uint64_t acknowledged{}, edit_serial{};
    std::vector<double> series;
    std::vector<std::string> options;
    NativeView canvas;
};
struct GlassCard {
    std::string id, title, subtitle, eyebrow;
    float width{320};
    std::vector<GlassControl> controls;
};
struct GlassScene {
    std::vector<GlassCard> cards;
    std::string layout{"orbit"}, material{"glass"}, backdrop{"none"};
    std::string selected;
    std::uint32_t accent{0x85e5d1};
    bool visible{true}, pane_visible{true}, reduced_motion{}, reduced_transparency{};
};
struct GlassEvent {
    std::string kind, card, control, text;
    double value{}, x{}, y{};
    std::uint64_t sequence{};
};
struct GlassMetrics {
    std::uint64_t applied{}, frames{}, presents{}, canvas_frames{}, dropped_events{};
    double draw_ms{}, tick_ms{};
    bool active{}, compact{}, backdrop_visible{}, high_contrast{}, system_animation{true}, closed{};
    std::string error, material;
    std::vector<std::uintptr_t> windows;
    std::vector<std::string> ids;
    std::vector<PixelBox> rects;
};
class GlassHost {
  public:
    explicit GlassHost(std::uintptr_t owner, bool inspectable_windows = false);
    ~GlassHost();
    GlassHost(const GlassHost &) = delete;
    std::uint64_t configure(GlassScene);
    void command(const std::string &action, const std::string &value = "");
    std::vector<GlassEvent> events();
    GlassMetrics metrics() const;
    void close();
  private:
    struct Impl;
    std::unique_ptr<Impl> impl_;
};
} // namespace comfortable
