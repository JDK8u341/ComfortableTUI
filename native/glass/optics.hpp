#pragma once
// Private implementation of the upper-level glass service, not a frame SDK API.
#include "comfortable/glass.hpp"
#include "../canvas/internal.hpp"

namespace comfortable::glass_detail {
struct OpticalStats {
    std::uint64_t captures{}, rendered{}, copied_pixels{};
    std::uint64_t unchanged{};
    bool active{}, image_ready{};
    std::string error;
};
class OpticalPanel {
  public:
    ~OpticalPanel();
    FrameDescriptor descriptor() const;
  private:
    struct Impl;
    std::unique_ptr<Impl> impl_;
    explicit OpticalPanel(ID3D11Device1 *, unsigned, unsigned);
    friend class OpticalScene;
};
class OpticalScene {
  public:
    explicit OpticalScene(HWND owner);
    ~OpticalScene();
    std::unique_ptr<OpticalPanel> panel(unsigned width,unsigned height);
    void update(bool visible);
    bool render(OpticalPanel &,const RECT &screen,const GlassBackground &,float dpi,
                float pointer_x,float pointer_y,bool dragging,bool force=false);
    OpticalStats stats() const;
    LUID adapter() const;
    static bool exclude(HWND);
  private:
    struct Impl;
    std::unique_ptr<Impl> impl_;
};
} // namespace comfortable::glass_detail
