"""Interactive widget gallery, also used by python -m comfortable_tui."""
import math

from .app import App
from .containers import Collapsible, Grid, HBox, PageStack, Panel, ScrollView, Tabs, VBox
from .controls import (BarChart, Button, Checkbox, Input, Label, Log, Paragraph, ProgressBar,
                       Rule, Slider, Sparkline, Spinner, Switch, TextArea)
from .data import DataTable, DirectoryTree, ListView, RadioGroup, Select, SelectionList, Tree, TreeNode
from .graphics import PixelCanvas


def gallery(*, fps=120, reduced_motion=None) -> App:
    status = Label("F6 menu/content · Tab focus · arrows edit/select · Enter activates · Ctrl+Q exits", height=1)
    log = Log(max_lines=200)
    log.write("Welcome to ComfortableTUI. All controls are interactive.")
    progress = ProgressBar(35, height=1)
    slider = Slider(35, on_change=lambda value: progress.set_value(value), height=1)
    app = None

    def changed(text):
        log.write(text)

    def notify():
        app.notify("Hello! This is a timed notification.")

    form = VBox(
        Label("PROFILE / 个人设置", height=1),
        Input(placeholder="Your name / 输入中文", height=1,
              on_submit=lambda text: changed(f"Submitted: {text}")),
        Input(placeholder="Password", password=True, height=1),
        HBox(Checkbox("Notifications", True, on_change=lambda value: changed(f"Notify: {value}")),
             Switch("Smooth motion", True), height=1, gap=1),
        Select(["Tokyo", "Hong Kong", "San Francisco", "Berlin"], height=1,
               on_change=lambda i, name: changed(f"City: {name}")),
        Label("Volume — Left/Right adjusts", height=1), slider, progress,
        HBox(Button("Notify", notify), Button("Open page", lambda: pages.push(
            Panel(VBox(Paragraph("A page stack preserves form state and animates both pages.", height=3),
                       Button("Back", lambda: pages.pop(), height=1)), "Another page"))), height=1, gap=1),
        Collapsible("Advanced options", VBox(
            Checkbox("Experimental mode", height=1),
            RadioGroup(["Automatic", "Performance", "Battery saver"], height=3)), height=5),
        gap=1)
    form_scroll = ScrollView(form)
    pages = PageStack(Panel(form_scroll, "Forms & motion"))
    song_list = ListView([f"{i + 1:03d}  {name}  /  {120 + i % 90} BPM" for i, name in
                          enumerate(["Starlight", "Blue Zenith", "Freedom Dive", "Nightfall", "Aurora"] * 200)],
                         item_height=2, on_select=lambda i, text: app.notify(text))
    data = Grid(Panel(song_list, "Virtual list · 1,000 tracks"),
                Panel(DataTable(["Service", "State", "Latency"],
                                [("renderer", "ready", "1.2ms"), ("network", "online", "42ms"),
                                 ("audio", "idle", "0.8ms")] * 15), "Data table"),
                Panel(Tree(TreeNode("Workspace", [TreeNode("src", [TreeNode("app.py"), TreeNode("widgets.py")], True),
                                                  TreeNode("docs", [TreeNode("index.md")]),
                                                  TreeNode("examples")], True)), "Tree"),
                Panel(SelectionList(["Python", "C++", "Rust", "Go", "TypeScript"], checked=[0, 1]),
                      "Selection list"), columns=2)
    editor = HBox(Panel(TextArea("# A small note\n\nEdit this text.\n中文和 emoji 👩‍💻 are welcome."), "Text area"),
                  Panel(DirectoryTree("."), "Directory tree"), gap=1)

    def paint(surface, elapsed):
        surface.clear()
        surface.rect(6, 7, 116, 50, (30, 38, 59))
        for i in range(5):
            x = 12 + i * 23
            y = 32 + math.sin(elapsed * 2.2 + i * 0.7) * 16
            surface.circle(x, y, 5, (255, 124, 184))
        surface.line(8, 54, 120, 54, (122, 225, 190))
    graphics = VBox(Paragraph("128×64 RGB canvas. Kitty graphics is negotiated automatically; other terminals use braille.",
                              height=2),
                    Panel(PixelCanvas(painter=paint, animate=True), "OLED spirit · pixel canvas"),
                    HBox(Panel(BarChart({"CPU": 38, "Memory": 62, "I/O": 21}), "Metrics"),
                         Panel(VBox(Sparkline([math.sin(i * .4) for i in range(80)], height=1),
                                    Spinner("Animation clock", height=1)), "Signals"), height=6, gap=1), gap=1)
    tabs = Tabs({"Playground": HBox(pages, Panel(log, "Event log", width=30), gap=1),
                 "Collections": data, "Editor": editor, "Graphics": graphics})
    root = VBox(Label("  ComfortableTUI   /   spring-driven interfaces", height=1), Rule(height=1), tabs,
                Rule(height=1), status, gap=0)
    app = App(root, fps=fps, reduced_motion=reduced_motion)
    app.bind("ctrl+1", lambda: tabs.select(0))
    app.bind("ctrl+2", lambda: tabs.select(1))
    return app
