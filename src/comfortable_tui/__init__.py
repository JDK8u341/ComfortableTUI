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
from .overlay import (CanvasEvent, CanvasTransform, FrameBuffer, FrameBufferInfo, FrameLease,
                      OverlayCanvas, PixelRect, TerminalHost)
from .renderer import FrameStats, FrameTiming, Renderer
from .style import Style, Theme
from .widget import Widget
from .browser import BrowserService, BrowserProfile, BrowserPage
from .webview import WebView
from .window_host import WindowHost

__version__ = "0.1.1"
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
    "BrowserService", "BrowserProfile", "BrowserPage", "WebView",
    "WindowHost",
]
