# Changelog

## 0.1.1 — 2026-10-18

- Fix Windows Python 3.12 pacing being quantized to 32/64 FPS by GetTickCount64; use perf_counter and continuous deadlines in terminal/input/window loops.
- Cache bounded immutable text runs, inherited styles and ANSI styles; add optional real-loop FrameTiming diagnostics and ComfortableTop pacing probes.

- Add semantic Expressive/Subtle motion profiles, interruptible focus/selection springs and press feedback.
- Animate checkbox colors, programmatic switches, slider tracks, bar charts, modal entry/exit and notification entry/exit.
- Cache unchanged braille frame sampling and accelerate whole-cell compositing; preserve reduced-motion and empty settled diffs.
- Validate through the independently installed ComfortableTop app, including native/browser composition.

- Add a separately built pybind11 in-process GPU provider example with a native rendering thread and GIL independence check.

- Add a standalone C++17 frame-provider SDK with shared D3D11 NT textures, bounded latest-frame slots and keyed synchronization.
- Move native frame transport/composition off Python/GIL to an event-driven DirectComposition worker with DXGI frame latency 1.
- Add C++ compatibility ingestion for CPU FrameBuffer, GPU handshake descriptors, native demos, real-desktop verification and latency benchmarks.
- Retain Qt as input/control plane and optional CPU fallback; acknowledge native source attachment/removal before lifecycle events.

- Replace per-canvas GUI processes/windows with one shared terminal overlay layer and non-native Qt surfaces.
- Add virtualized ListView item_widget, DataTable cell_widget and inline Text widget composition.
- Route input per logical canvas, bubble unconsumed keys/wheel, cancel hidden drags and acknowledge detached sources.
- Add shared-layer research, embedded examples, local-composition benchmarks and native Windows verification.
- Shorten shared-memory names for the macOS POSIX limit.

- Add Windows OverlayCanvas: transparent owned windows, shared three-slot image buffers,
  automatic cell/DPI alignment, clipping, scaling, and native input/IME/lifecycle events.
- Keep overlay windows above their terminal, restore group z-order after activation,
  and bind the actual terminal stream through a temporary UIA text marker.
- Add external-renderer, performance and real Windows Terminal verification examples;
  document the optional Qt backend, event contract, research and measured limits.

- Make menu focus visible and reachable with F6, Up at list boundaries, and Enter/Down back into content.
- Remember the last content focus per tab and decode enhanced-terminal function keys.

- Fix content arrow keys bubbling into Tabs: only the focused tab bar changes pages.
- Add spatial focus fallback for unhandled arrows, including table scroll boundaries.

- Inherit painted surfaces for controls and panel borders to remove dark focus cutouts.
- Add a shared, fractional-cell focus highlight with independent leading/trailing edges.
- Animate Tabs selection and content, preserve page velocity on reversal and scroll overflowing headers.
- Tune motion by property, target 120fps in the gallery, and retain reduced-motion / no-color behavior.
- Reuse bounded immutable cells and avoid re-segmenting already-sanitized graphemes.

- Fix Slider reacting to hover: value changes now require a left click or a drag begun on the slider;
  releasing outside the control ends the drag.

## 0.1.0 — 2026-10-17

- Initial alpha with spring/easing animation, target layout, diff rendering and Unicode cells.
- Forms, lists, tables, trees, layouts, scrolling, tabs, collapsibles and animated page stacks.
- POSIX/Windows terminal session, mouse, paste, negotiated synchronized output and Kitty keyboard.
- RGB PixelCanvas with Kitty graphics and braille fallback.
- Optional C++17/pybind11 span scanner, examples, Chinese documentation and reproducible benchmarks.
