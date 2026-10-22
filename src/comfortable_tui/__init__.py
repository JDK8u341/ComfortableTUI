"""ComfortableTUI public API."""
from .animation import Animator, MotionProfile, Spring, Value
from .app import App, Timer
from .canvas import Canvas, Cell, Rect, crop_text, text_width
from .containers import Collapsible, Grid, HBox, PageStack, Panel, ScrollView, Tabs, VBox
from .controls import (BarChart, Button, Checkbox, Input, Label, Log, Paragraph, ProgressBar,
                       Rule, Slider, Spacer, Sparkline, Spinner, Switch, Text, TextArea)
from .data import DataTable, DirectoryTree, ListView, RadioGroup, Select, SelectionList, Tree, TreeNode
from .events import Capability, InputParser, Key, Mouse, Paste
from .graphics import KittyEncoder, PixelCanvas, Surface
from .gpu import GpuFrameInfo, GpuFrameSource, gpu_available
from .glass import (GlassBackground, GlassCard, GlassCardGroup, GlassControl, GlassContainer, GlassEffect, GlassEvent, GlassFrame, GlassMotion,
                    GlassTheme, GlassWorkspace, register_glass_controls, register_glass_effects, register_glass_motions)
from .overlay import (CanvasEvent, CanvasTransform, FrameBuffer, FrameBufferInfo, FrameLease,
                      OverlayCanvas, PixelRect, TerminalHost)
from .renderer import FrameStats, FrameTiming, Renderer
from .style import Style, Theme
from .widget import Widget
from .browser import BrowserService, BrowserProfile, BrowserPage
from .webview import WebView
from .glass_webview import GlassWebView
from .glass_canvas import GlassCanvas
from .glass_shader import register_glass_shader
from .window_host import WindowHost
from .security import BrowserLimits, BrowserPolicy

__version__ = "0.2.0"
__all__ = [
    "Animator", "MotionProfile", "Spring", "Value", "App", "Timer", "Canvas", "Cell", "Rect", "crop_text", "text_width",
    "Collapsible", "Grid", "HBox", "PageStack", "Panel", "ScrollView", "Tabs", "VBox", "BarChart",
    "Button", "Checkbox", "Input", "Label", "Log", "Paragraph", "ProgressBar", "Rule", "Slider",
    "Spacer", "Sparkline", "Spinner", "Switch", "Text", "TextArea", "DataTable", "DirectoryTree", "ListView",
    "RadioGroup", "Select", "SelectionList", "Tree", "TreeNode", "Capability", "InputParser", "Key",
    "Mouse", "Paste", "KittyEncoder", "PixelCanvas", "Surface", "FrameStats", "FrameTiming", "Renderer", "Style",
    "Theme", "Widget",
    "CanvasEvent", "CanvasTransform", "FrameBuffer", "FrameBufferInfo", "FrameLease",
    "OverlayCanvas", "PixelRect", "TerminalHost",
    "GpuFrameInfo", "GpuFrameSource", "gpu_available",
    "GlassBackground", "GlassCard", "GlassCardGroup", "GlassControl", "GlassContainer", "GlassEffect", "GlassEvent", "GlassTheme", "GlassWorkspace", "GlassFrame", "GlassMotion",
    "register_glass_controls", "register_glass_effects", "register_glass_motions", "register_glass_shader",
    "BrowserService", "BrowserProfile", "BrowserPage", "BrowserPolicy", "BrowserLimits", "WebView", "GlassCanvas", "GlassWebView",
    "WindowHost",
]
