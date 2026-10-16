# Changelog

## Unreleased

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
