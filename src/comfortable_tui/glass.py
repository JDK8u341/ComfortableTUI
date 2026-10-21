"""Optional native Windows cards; UI rendering remains independent of browser services."""
from __future__ import annotations

from dataclasses import dataclass, field
import math
import os
import itertools
import time
from typing import Callable

from .gpu import GpuFrameInfo, GpuFrameSource
from .overlay import FrameBuffer, TerminalHost

_KINDS = {"text", "button", "toggle", "slider", "input", "choice", "progress", "sparkline", "canvas"}
_HEIGHTS = {"text": 40, "slider": 58, "progress": 58, "sparkline": 84, "canvas": 128}
_GENERATIONS = itertools.count(1)
_EXTENSIONS = set()
_EFFECT_EXTENSIONS = set()
_CUSTOM_KINDS = set()


def register_glass_controls(module):
    """Register an explicitly imported, trusted native extension. No DLL scanning.

    module.glass_controls() returns the capsule defined in glass_control.hpp.
    Registration is process-wide and immutable; the runtime retains the module.
    """
    if module in _EXTENSIONS:
        return
    from . import _native_glass
    if getattr(_native_glass, "glass_protocol_version", None) != 5:
        raise RuntimeError("Rebuild Glass runtime for control protocol 5")
    names = _native_glass.register_controls(module, module.glass_controls())
    _KINDS.update(names)
    _CUSTOM_KINDS.update(names)
    for name in names:
        _HEIGHTS[name] = _native_glass.measure_control(name)
    _EXTENSIONS.add(module)


def register_glass_effects(module):
    """Register an explicitly imported, trusted native effect extension."""
    if module in _EFFECT_EXTENSIONS:
        return
    from . import _native_glass
    if getattr(_native_glass, "glass_protocol_version", None) != 5:
        raise RuntimeError("Rebuild Glass runtime for effect protocol 5")
    names = _native_glass.register_effects(module, module.glass_effects())
    _EFFECT_EXTENSIONS.add(module)
    return tuple(names)


@dataclass(frozen=True)
class GlassFrame:
    """An opt-in callback's monotonic clock, in seconds; first dt is zero."""
    time: float
    dt: float
    elapsed: float
    index: int
    reduced_motion: bool


class _FrameRequest:
    def __init__(self, workspace, callback, node):
        self.workspace, self.callback, self.node = workspace, callback, node
        self.started = self.last = None
        self.index = 0
        self.cancelled = False

    def cancel(self):
        if self.cancelled:
            return
        self.cancelled = True
        self.workspace._frames.pop(id(self), None)
        self.callback = self.node = self.workspace = None


def _text(value, limit, name):
    if not isinstance(value, str) or len(value.encode("utf-8")) > limit or "\0" in value:
        raise ValueError(f"{name} must be a bounded string without NUL")
    return value


@dataclass
class GlassBackground:
    """Per-card optical material. No frame-provider or browser semantics."""
    kind: str = "liquid"
    tint: int = 0xFFFFFF
    opacity: float = .04
    blur: float = 1.2
    refraction: float = 20.
    dispersion: float = .45
    corner_radius: float = 24.

    def snapshot(self):
        if self.kind not in ("liquid", "frosted", "mica", "solid", "transparent"):
            raise ValueError("background kind must be liquid, frosted, mica, solid or transparent")
        if type(self.tint) is not int or not 0 <= self.tint <= 0xFFFFFF:
            raise ValueError("background tint must be an RGB integer")
        for key, maximum in (("opacity", 1), ("blur", 40), ("refraction", 48),
                             ("dispersion", 3), ("corner_radius", 64)):
            value = getattr(self, key)
            if not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= maximum:
                raise ValueError(f"background {key} must be in 0..{maximum}")
        return dict(kind=self.kind, tint=self.tint, opacity=float(self.opacity), blur=float(self.blur),
                    refraction=float(self.refraction), dispersion=float(self.dispersion),
                    corner_radius=float(self.corner_radius))


@dataclass
class GlassEffect:
    """An independent, trusted native effect instance attached to a node.

    ``parameters`` is an ordered vector defined by the registered effect type.
    The framework owns the instance state; effect programs and device resources
    may be shared by the native implementation.
    """
    type: str
    target: str = "overlay"
    parameters: tuple[float, ...] = ()
    tint: int = 0xFFFFFF
    opacity: float = 1.
    blur: float = 0.
    refraction: float = 0.
    dispersion: float = 0.
    intensity: float = 1.
    radius: float = 0.
    progress: float = 0.

    def snapshot(self):
        _text(self.type, 128, "effect type")
        if self.type not in ("tint", "blur", "liquid", "glow") and not self.type:
            raise ValueError("effect type cannot be empty")
        if self.target not in ("background", "content", "overlay"):
            raise ValueError("effect target must be background, content or overlay")
        if len(self.parameters) > 16 or any(not isinstance(v, (int, float)) or not math.isfinite(v)
                                            for v in self.parameters):
            raise ValueError("effect parameters must contain at most 16 finite numbers")
        if type(self.tint) is not int or not 0 <= self.tint <= 0xFFFFFF:
            raise ValueError("effect tint must be an RGB integer")
        for key, maximum in (("opacity", 1), ("blur", 40), ("refraction", 48),
                             ("dispersion", 3), ("intensity", 4), ("radius", 128),
                             ("progress", 1)):
            value = getattr(self, key)
            if not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= maximum:
                raise ValueError(f"effect {key} must be in 0..{maximum}")
        return dict(type=self.type, target=self.target, parameters=[float(v) for v in self.parameters],
                    tint=self.tint, opacity=float(self.opacity), blur=float(self.blur),
                    refraction=float(self.refraction), dispersion=float(self.dispersion),
                    intensity=float(self.intensity), radius=float(self.radius),
                    progress=float(self.progress))


@dataclass
class GlassTheme:
    material: str = "glass"
    accent: int = 0x85E5D1
    reduced_motion: bool = False
    reduced_transparency: bool = False

    def __post_init__(self):
        if self.material not in ("glass", "liquid", "frosted", "mica", "solid", "transparent"):
            raise ValueError("invalid default glass material")
        if type(self.accent) is not int or not 0 <= self.accent <= 0xFFFFFF:
            raise ValueError("accent must be an RGB integer")


@dataclass(frozen=True)
class GlassEvent:
    kind: str
    card: str = ""
    control: str = ""
    text: str = ""
    value: float = 0.
    position: tuple[float, float] = (0., 0.)
    sequence: int = 0
    modifiers: tuple[str, ...] = ()
    scan_code: int = 0
    repeat: bool = False
    button: str = "left"
    buttons: int = 0
    clicks: int = 1
    inside_source: bool = True
    source_name: str = ""
    generation: int = 0


@dataclass
class GlassControl:
    """A declarative native control; values are normalized except choice indices."""
    id: str
    kind: str
    label: str = ""
    text: str = ""
    value: float = 0.
    enabled: bool = True
    series: tuple[float, ...] = ()
    options: tuple[str, ...] = ()
    source: FrameBuffer | GpuFrameSource | None = None
    fit: str = "contain"
    on_event: Callable[[GlassEvent], bool | None] | None = field(default=None, repr=False, compare=False)
    _acknowledged: int = field(default=0, init=False, repr=False, compare=False)
    canvas_height: float = 96.
    capture_escape: bool = False
    opacity: float = 1.
    effects: list[GlassEffect] = field(default_factory=list)
    _generation: int = field(default_factory=lambda: next(_GENERATIONS), init=False, repr=False, compare=False)
    _managed = False  # Opt-in view lifecycle; ordinary controls have no per-tick hooks.

    def handle_event(self, event):
        return bool(self.on_event(event)) if self.on_event else False

    def default_event(self, event, workspace):
        pass

    def snapshot(self):
        _text(self.id, 128, "control id")
        if not self.id or self.kind not in _KINDS:
            raise ValueError("control needs an id and a supported kind")
        _text(self.label, 4096, "label")
        _text(self.text, 8192, "text")
        if self.kind == "input" and len(self.text.encode("utf-16-le")) // 2 > 2048:
            raise ValueError("input text exceeds 2048 UTF-16 units")
        if not isinstance(self.value, (int, float)) or not math.isfinite(self.value):
            raise ValueError("value must be finite")
        if self.kind in ("toggle", "slider", "progress") and not 0 <= self.value <= 1:
            raise ValueError("value must be in 0..1")
        if len(self.options) > 16 or any(not isinstance(v, str) for v in self.options):
            raise ValueError("options must contain at most 16 strings")
        for option in self.options:
            _text(option, 1024, "choice option")
        if self.kind == "choice" and (not self.options or self.value != int(self.value) or
                                       not 0 <= self.value < len(self.options)):
            raise ValueError("choice requires options and a valid integer index")
        if len(self.series) > 256 or any(not math.isfinite(v) or not 0 <= v <= 1 for v in self.series):
            raise ValueError("series must contain at most 256 normalized samples")
        if self.fit not in ("contain", "cover", "stretch", "pixel"):
            raise ValueError("invalid canvas fit")
        if not isinstance(self.canvas_height, (int, float)) or not math.isfinite(self.canvas_height) or not 16 <= self.canvas_height <= 1024:
            raise ValueError("canvas_height must be in 16..1024 logical pixels")
        if type(self.capture_escape) is not bool:
            raise ValueError("capture_escape must be a bool")
        if not isinstance(self.opacity, (int, float)) or not math.isfinite(self.opacity) or not 0 <= self.opacity <= 1:
            raise ValueError("control opacity must be in 0..1")
        if len(self.effects) > 8 or any(not isinstance(effect, GlassEffect) for effect in self.effects):
            raise ValueError("a control supports at most 8 GlassEffect instances")
        if self.kind == "canvas" and self.opacity != 1:
            raise ValueError("Canvas opacity needs compositor effects, not control ink opacity")
        source = None
        if self.kind == "canvas":
            if not isinstance(self.source, (FrameBuffer, GpuFrameSource)):
                raise TypeError("canvas needs a FrameBuffer or GpuFrameSource")
            self.source._check_open()
            info = self.source.info
            source = dict(name=info.name, width=info.width, height=info.height,
                          gpu=isinstance(info, GpuFrameInfo), stride=getattr(info, "stride", 0),
                          pixel_format=info.pixel_format, adapter_low=getattr(info, "adapter_low", 0),
                          adapter_high=getattr(info, "adapter_high", 0))
        return dict(id=self.id, kind=self.kind, label=self.label, text=self.text, value=float(self.value),
                    enabled=bool(self.enabled), series=list(self.series), options=list(self.options),
                    source=source, fit=self.fit, acknowledged=self._acknowledged,
                    canvas_height=float(self.canvas_height), capture_escape=self.capture_escape,
                    generation=self._generation, opacity=float(self.opacity),
                    effects=[effect.snapshot() for effect in self.effects])


@dataclass
class GlassCard:
    id: str
    title: str
    controls: list[GlassControl] = field(default_factory=list)
    subtitle: str = ""
    eyebrow: str = ""
    width: float = 320
    background: GlassBackground | None = None
    draggable: bool = True
    on_event: Callable[[GlassEvent], bool | None] | None = field(default=None, repr=False, compare=False)
    effects: list[GlassEffect] = field(default_factory=list)
    _generation: int = field(default_factory=lambda: next(_GENERATIONS), init=False, repr=False, compare=False)

    def handle_event(self, event):
        return bool(self.on_event(event)) if self.on_event else False

    def default_event(self, event, workspace):
        """Element-owned behavior, run only after an unconsumed routed event."""
        if event.kind == "card_close_request" and not event.control:
            if any(card is self for card in workspace.cards):
                workspace.remove_card(self.id)
        elif self.draggable and not event.control and event.kind == "pointer_down" and event.text == "header":
            workspace._native.begin_drag(self.id, event.sequence)

    def snapshot(self):
        _text(self.id, 128, "card id")
        if not self.id:
            raise ValueError("card id cannot be empty")
        _text(self.title, 4096, "title")
        _text(self.subtitle, 4096, "subtitle")
        _text(self.eyebrow, 256, "eyebrow")
        if not math.isfinite(self.width) or not 220 <= self.width <= 1600 or len(self.controls) > 32:
            raise ValueError("card width must be 220..1600, with at most 32 controls")
        controls = [v.snapshot() for v in self.controls]
        if len({v["id"] for v in controls}) != len(controls):
            raise ValueError("control ids must be unique within a card")
        if sum(v["kind"] == "canvas" for v in controls) > 1:
            raise ValueError("each card supports one canvas")
        def height(v):
            if v["kind"] in _CUSTOM_KINDS:
                from . import _native_glass
                return _native_glass.measure_control(v["kind"], v)
            return v["canvas_height"] + 32 if v["kind"] == "canvas" else _HEIGHTS.get(v["kind"], 46)
        if 122 + sum(height(v) for v in controls) > 1200:
            raise ValueError("card content exceeds 1200 logical pixels")
        if type(self.draggable) is not bool:
            raise ValueError("draggable must be a bool")
        if len(self.effects) > 8 or any(not isinstance(effect, GlassEffect) for effect in self.effects):
            raise ValueError("a card supports at most 8 GlassEffect instances")
        if self.background is not None and not isinstance(self.background, GlassBackground):
            raise TypeError("background must be a GlassBackground or None")
        return dict(id=self.id, title=self.title, subtitle=self.subtitle, eyebrow=self.eyebrow,
                    width=float(self.width), controls=controls, draggable=self.draggable,
                    background=self.background.snapshot() if self.background is not None else None,
                    generation=self._generation,
                    effects=[effect.snapshot() for effect in self.effects])


@dataclass
class GlassCardGroup:
    id: str
    children: list[GlassCard | GlassCardGroup] = field(default_factory=list)
    layout: str = "row"
    gap: float = 16.
    padding: float = 0.
    visible: bool = True
    enabled: bool = True
    on_preview_event: Callable[[GlassEvent], bool | None] | None = field(default=None, repr=False, compare=False)
    on_event: Callable[[GlassEvent], bool | None] | None = field(default=None, repr=False, compare=False)

    def handle_event(self, event):
        return bool(self.on_event(event)) if self.on_event else False


class GlassWorkspace:
    """Native peripheral UI. Use as a context manager; callbacks run in poll/App thread.

    `bind(app)` pins the same terminal output stream as OverlayCanvas. `start(hwnd)`
    explicitly binds any already-owned window for integrations and visual tests.
    Scene submissions retain all frame sources until native attachment acknowledgement.
    """
    def __init__(self, cards=(), *, layout="orbit", theme=None, backdrop="none", on_event=None, host=None):
        self.children = list(cards)
        self.layout, self.theme, self.backdrop = layout, theme or GlassTheme(), backdrop
        self.on_event, self.host = on_event, host or TerminalHost()
        self.selected = self.cards[0].id if self.cards else ""
        self.visible, self._pane_visible = True, True
        self._pane_error = ""
        self._native = self._tracker = self._app = self._unsubscribe = None
        self._pending = {}
        self._pending_controls, self._bound_controls = {}, {}
        self._active_controls, self._event_sources = [], []
        self._attached_refs = []
        self._dirty, self._closed = True, False
        self._metrics = {}
        self._frames = {}
        self.snapshot()
        self._desired_controls = self._managed_views()

    def _managed_views(self):
        return {id(v): (card.id, v) for card in self.cards for v in card.controls if v._managed}

    def watch_events(self, callback):
        """Poll a service once per workspace tick when no App owns its loop."""
        if self._closed:
            raise RuntimeError("workspace is closed")
        self._event_sources.append(callback)
        stopped = False
        def stop():
            nonlocal stopped
            if not stopped:
                stopped = True
                if callback in self._event_sources:
                    self._event_sources.remove(callback)
        return stop

    def _tree(self):
        cards, groups, routes, seen = [], [], {}, set()
        def visit(node, parents):
            if len(parents) > 8 or not isinstance(node, (GlassCard, GlassCardGroup)):
                raise ValueError("glass tree needs cards/groups and at most 8 group levels")
            _text(node.id, 128, "node id")
            if not node.id or node.id in seen:
                raise ValueError("card/group ids must be unique; cycles and shared children are forbidden")
            seen.add(node.id)
            if isinstance(node, GlassCard):
                if len(cards) >= 12:
                    raise ValueError("a workspace supports at most 12 cards")
                cards.append(node)
                routes[node.id] = parents
                return
            if len(groups) >= 24 or len(node.children) > 36 or node.layout not in ("row", "column", "stack"):
                raise ValueError("at most 24 groups with row, column or stack layout")
            if type(node.visible) is not bool or type(node.enabled) is not bool:
                raise ValueError("group visible/enabled must be bools")
            if any(not math.isfinite(v) or not 0 <= v <= 128 for v in (node.gap, node.padding)):
                raise ValueError("group gap/padding must be in 0..128")
            if any(not isinstance(child, (GlassCard, GlassCardGroup)) for child in node.children):
                raise ValueError("groups contain cards or groups")
            groups.append(dict(id=node.id, parent=parents[-1].id if parents else "", layout=node.layout,
                               gap=float(node.gap), padding=float(node.padding), visible=node.visible,
                               enabled=node.enabled, children=[v.id for v in node.children]))
            for child in node.children:
                visit(child, (*parents, node))
        for node in self.children:
            visit(node, ())
        return cards, groups, routes

    @property
    def cards(self):
        """Leaf cards; use children/set_cards to change the retained tree."""
        return self._tree()[0]

    def snapshot(self):
        self.theme.__post_init__()
        if self.layout not in ("orbit", "deck", "ribbon", "focus"):
            raise ValueError("layout must be orbit, deck, ribbon or focus")
        if self.backdrop not in ("none", "dim", "frosted", "mica"):
            raise ValueError("backdrop must be none, dim, frosted or mica")
        if len(self.cards) > 12:
            raise ValueError("a workspace supports at most 12 cards")
        leaves, groups, routes = self._tree()
        cards = [c.snapshot() for c in leaves]
        for card in cards:
            card["group"] = routes[card["id"]][-1].id if routes[card["id"]] else ""
        if len({c["id"] for c in cards}) != len(cards):
            raise ValueError("card ids must be unique")
        names = [v["source"]["name"] for c in cards for v in c["controls"] if v["kind"] == "canvas"]
        if len(set(names)) != len(names):
            raise ValueError("a frame channel may be displayed by one glass card")
        if self.selected and self.selected not in {c["id"] for c in cards}:
            raise ValueError("selected card is unavailable")
        return dict(cards=cards, groups=groups, roots=[node.id for node in self.children],
                    layout=self.layout, material=self.theme.material, accent=self.theme.accent,
                    selected=self.selected, backdrop=self.backdrop, visible=bool(self.visible),
                    pane_visible=self._pane_visible, reduced_motion=self.theme.reduced_motion,
                    reduced_transparency=self.theme.reduced_transparency)

    def bind(self, app):
        if self._app is not None or self._native is not None or self._closed:
            raise RuntimeError("workspace already bound, started or closed")
        self._app = app
        self._unsubscribe = app.watch_events(self.poll)
        app._glass_workspaces.append(self)
        return self

    def start(self, hwnd, *, inspectable_windows=False):
        if self._closed or self._native is not None:
            raise RuntimeError("workspace already started or closed")
        if os.name != "nt":
            raise RuntimeError("GlassWorkspace currently requires a local Windows desktop")
        from . import _native_glass
        if getattr(_native_glass, "glass_protocol_version", None) != 5:
            raise RuntimeError("Rebuild with COMFORTABLE_TUI_BUILD_GLASS=1 for GlassWorkspace")
        self._native = _native_glass.GlassHost(hwnd, inspectable_windows)
        self._dirty = True
        try:
            self.poll()
        except BaseException:
            self.close()
            raise
        return self

    def update(self):
        """Validate changed models; submit on the next poll, without copying frame pixels."""
        if self._closed:
            raise RuntimeError("workspace is closed")
        self.snapshot()
        self._desired_controls = self._managed_views()
        self._dirty = True

    def set_cards(self, cards):
        previous, selected = self.children, self.selected
        old_cards = self.cards
        self.children = list(cards)
        try:
            new_cards = self.cards
        except Exception:
            self.children = previous
            raise
        revisions = {(c.id, v.id, v.kind): v._acknowledged for c in old_cards for v in c.controls}
        for card in new_cards:
            for control in card.controls:
                control._acknowledged = max(control._acknowledged,
                                             revisions.get((card.id, control.id, control.kind), 0))
        if self.selected not in {c.id for c in self.cards}:
            self.selected = self.cards[0].id if self.cards else ""
        try:
            self.update()
        except Exception:
            self.children, self.selected = previous, selected
            raise

    def configure(self, *, layout=None, theme=None, backdrop=None):
        previous = self.layout, self.theme, self.backdrop
        self.layout = self.layout if layout is None else layout
        self.theme = self.theme if theme is None else theme
        self.backdrop = self.backdrop if backdrop is None else backdrop
        try:
            self.update()
        except Exception:
            self.layout, self.theme, self.backdrop = previous
            raise

    def remove_card(self, card_id):
        """Remove one card; native detach is acknowledged before sources retire.

        Groups and sibling instances remain intact. The caller owns the canvas,
        page and producer; this operation does not close their sources.
        """
        if self._closed:
            raise RuntimeError("workspace is closed")
        if card_id not in {card.id for card in self.cards}:
            raise ValueError("unknown card")
        changes = []
        def without_card(children):
            result = []
            for node in children:
                if isinstance(node, GlassCardGroup):
                    new_children = without_card(node.children)
                    if len(new_children) != len(node.children):
                        changes.append((node, node.children, new_children))
                    result.append(node)
                elif node.id != card_id:
                    result.append(node)
            return result
        roots = without_card(self.children)
        for group, _, children in changes:
            group.children = children
        try:
            self.set_cards(roots)
        except BaseException:
            for group, children, _ in changes:
                group.children = children
            raise

    def show(self):
        self.visible = True
        self.update()

    def hide(self):
        self.visible = False
        self.update()

    def command(self, action, value=""):
        if action not in ("next", "previous", "next_control", "previous_control", "select", "focus_terminal", "focus_card", "dismiss", "reflow"):
            raise ValueError("invalid glass command")
        if action == "select" and value not in {c.id for c in self.cards}:
            raise ValueError("unknown card")
        if self._native is None or self._closed:
            raise RuntimeError("workspace is not running")
        self._native.command(action, value)

    def request_frames(self, callback, *, node=None):
        """Run callback(GlassFrame) on poll's thread while it returns True.

        Bind to a Card/control instance to stop on removal or replacement. There
        is no callback when idle. cancel() is idempotent; newly requested frames
        begin on the next poll. This is an application hook, not a render thread.
        """
        if self._closed:
            raise RuntimeError("workspace is closed")
        if not callable(callback):
            raise TypeError("frame callback must be callable")
        if node is not None and not any(node is c or any(node is v for v in c.controls) for c in self.cards):
            raise ValueError("frame node must belong to this workspace")
        request = _FrameRequest(self, callback, node)
        self._frames[id(request)] = request
        return request

    def present(self, card, control, *, opacity):
        """Submit control ink opacity without rebuilding/configuring the scene.

        Accept instances (not IDs), protecting replacements from late callbacks.
        This changes neither geometry nor hit/semantics. Canvas uses its own
        compositor layer and deliberately rejects this ink-only operation.
        """
        if self._closed or self._native is None:
            raise RuntimeError("workspace is not running")
        if not any(card is c for c in self.cards) or not any(control is v for v in card.controls):
            raise ValueError("presentation target was removed or replaced")
        if control.kind == "canvas":
            raise ValueError("Canvas opacity needs compositor effects")
        if not isinstance(opacity, (int, float)) or not math.isfinite(opacity) or not 0 <= opacity <= 1:
            raise ValueError("opacity must be in 0..1")
        self._native.present(card.id, control.id, control._generation, float(opacity))
        control.opacity = float(opacity)

    def set_effect_parameters(self, card, node, effect, parameters):
        """Update one effect instance without rebuilding the whole scene.

        ``card`` and ``node`` must be the live object instances. The native
        side validates the effect type and parameter count before queueing the
        update; a replaced node cannot receive a late update.
        """
        if self._closed or self._native is None:
            raise RuntimeError("workspace is not running")
        if not isinstance(card, GlassCard) or not any(card is c for c in self.cards):
            raise ValueError("effect card was removed or replaced")
        if node is not card and not any(node is v for v in card.controls):
            raise ValueError("effect node was removed or replaced")
        effects = card.effects if node is card else node.effects
        if not isinstance(effect, int) or not 0 <= effect < len(effects):
            raise ValueError("unknown effect instance")
        if not isinstance(parameters, (tuple, list)) or len(parameters) > 16:
            raise ValueError("effect parameters must contain at most 16 values")
        values = tuple(float(value) for value in parameters)
        if any(not math.isfinite(value) for value in values):
            raise ValueError("effect parameters must be finite")
        binding = effects[effect]
        binding.parameters = values
        self._native.set_effect_parameters(card.id, "" if node is card else node.id,
                                           card._generation if node is card else node._generation,
                                           effect, binding.type, list(values))

    def _advance_frames(self):
        if not self._frames:
            return
        now = time.perf_counter()
        reduced = self.theme.reduced_motion or not self._metrics.get("system_animation", True)
        windows = self._metrics.get("windows")
        visible = {w["id"] for w in windows if w["visible"]} if windows is not None else None
        for request in tuple(self._frames.values()):
            if request.cancelled:
                continue
            if request.node is not None and not any(request.node is c or any(request.node is v for v in c.controls) for c in self.cards):
                request.cancel()
                continue
            owner = next((c for c in self.cards if request.node is c or
                          any(request.node is v for v in c.controls)), None) if request.node else None
            node_hidden = visible is not None and (owner.id not in visible if owner else not visible)
            if not self.visible or not self._pane_visible or node_hidden:
                request.last = None
                continue
            if request.started is None:
                request.started = now
            frame = GlassFrame(now, 0. if request.last is None else now-request.last,
                               now-request.started, request.index, reduced)
            request.last, request.index = now, request.index+1
            try:
                keep = request.callback(frame)
            except BaseException:
                request.cancel()
                raise
            if self._closed:
                return
            if keep is not True or reduced:
                request.cancel()

    def poll(self):
        if self._closed:
            return
        for callback in tuple(self._event_sources):
            callback()
            if self._closed:
                return
        if self._native is None:
            if self._app is None or self._app._terminal is None:
                return
            from ._win32_host import HostTracker, resolve_host
            host = getattr(self._app, "_native_overlay_host", None) if self.host == TerminalHost() else None
            if host is None:
                try:
                    host = resolve_host(self.host, terminal=self._app._terminal, style=self._app.theme.background)
                finally:
                    self._app.renderer.invalidate()
                    self._app.invalidate()
                if self.host == TerminalHost():
                    self._app._native_overlay_host = host
            self._tracker = HostTracker(host, self._app._size, visibility_only=True)
            self._pane_visible = False
            self.start(host.hwnd)
            return
        self._metrics = self._native.metrics()
        if self._metrics.get("error"):
            raise RuntimeError("GlassWorkspace: " + self._metrics["error"])
        if self._metrics.get("closed"):
            self.close()
            return
        if self._tracker:
            self._tracker.grid = self._app._size
            snapshot = self._tracker.snapshot()
            self._pane_error = snapshot.error
            available = snapshot.available and self._app.modal is None
            if available != self._pane_visible:
                self._pane_visible, self._dirty = available, True
        applied = self._metrics.get("applied", 0)
        attached = [serial for serial in self._pending if serial <= applied]
        if attached:
            self._attached_refs = self._pending[max(attached)]
            self._active_controls = self._pending_controls[max(attached)]
            for control, info in self._active_controls:
                control._attached(info)
        self._pending = {serial: refs for serial, refs in self._pending.items() if serial > applied}
        self._pending_controls = {serial: refs for serial, refs in self._pending_controls.items() if serial > applied}
        retained = {id(v) for v, _ in self._active_controls}
        retained.update(id(v) for controls in self._pending_controls.values() for v, _ in controls)
        retained.update(self._desired_controls)
        for token, (_, control) in tuple(self._bound_controls.items()):
            if token not in retained:
                control._unmount(self)
                self._bound_controls.pop(token)
        windows = {w["id"]: w for w in self._metrics.get("windows", ())}
        for card_id, control in tuple(self._bound_controls.values()):
            control._poll(self, windows.get(card_id))
        for raw in self._native.events():
            event = GlassEvent(**raw)
            leaves, _, routes = self._tree()
            card = next((c for c in leaves if c.id == event.card), None)
            control = next((v for v in card.controls if v.id == event.control), None) if card else None
            target = control if event.control else card
            if event.generation and (target is None or event.generation != target._generation):
                continue
            if event.kind == "selected":
                self.selected = event.card
            elif event.kind == "dismissed":
                self.visible = False
            if control:
                if event.kind == "change":
                    control._acknowledged = event.sequence
                    if control.kind == "input":
                        control.text = event.text
                    else:
                        control.value = event.value
                if control._managed and event.kind in ("control_focus_out", "mouse_cancel"):
                    # Focus/capture loss cannot be canceled by input preview.
                    control.default_event(event, self)
            consumed = False
            ancestors = routes.get(event.card, ())
            for group in ancestors:
                if group.on_preview_event and group.on_preview_event(event):
                    consumed = True
                    break
            if not consumed and control:
                consumed = control.handle_event(event)
            if not consumed and card:
                consumed = card.handle_event(event)
            if not consumed:
                for group in reversed(ancestors):
                    if group.handle_event(event):
                        consumed = True
                        break
            if not consumed and self.on_event:
                consumed = bool(self.on_event(event))
            if not consumed and card and not self._closed:
                if control:
                    control.default_event(event, self)
                card.default_event(event, self)
            if event.kind == "close_request" and self._app:
                self._app.exit()
            if self._closed:
                return
        if self._dirty and len(self._pending) < 8:
            for token, (card_id, control) in self._desired_controls.items():
                if token not in self._bound_controls:
                    control._mount(self)
                self._bound_controls[token] = (card_id, control)
            controls = [(control, control.source.info) for _, control in self._desired_controls.values()]
            self._dirty = False
            try:
                for control, info in controls:
                    control._attaching(info)
                serial = self._native.configure(self.snapshot())
            except BaseException:
                self._dirty = True
                raise
            self._pending[serial] = [v.source for c in self.cards for v in c.controls if v.source]
            self._pending_controls[serial] = controls
        # Configuration is queued first so a first-frame presentation delta can
        # target the node on the same native wake, without a full scene per frame.
        self._advance_frames()

    def metrics(self):
        result = self._native.metrics() if self._native and not self._closed else dict(self._metrics)
        result.update(pane_available=self._pane_visible, pane_error=self._pane_error)
        return result

    def close(self):
        if self._closed:
            return
        self._closed = True
        if self._unsubscribe:
            self._unsubscribe()
        if self._tracker:
            self._tracker.request_close()
        if self._native:
            self._native.close()
            self._metrics = self._native.metrics()
            self._native = None
        if self._tracker:
            self._tracker.close()
        for _, control in tuple(self._bound_controls.values()):
            control._unmount(self)
        self._bound_controls.clear()
        self._active_controls.clear()
        self._pending_controls.clear()
        self._event_sources.clear()
        for request in tuple(self._frames.values()):
            request.cancel()
        self._pending.clear()
        self._attached_refs.clear()
        self._metrics.update(closed=True, windows=[], active=False, backdrop_visible=False, capture_active=False)
        if self._app and self in self._app._glass_workspaces:
            self._app._glass_workspaces.remove(self)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
