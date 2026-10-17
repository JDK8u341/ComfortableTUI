"""Expressive motion lab: Tab focus, F6 menu, Enter actions, Ctrl+Q exits."""
from comfortable_tui import (App, BarChart, Button, Checkbox, Collapsible, HBox, Input,
    Label, Panel, ProgressBar, Select, Slider, Switch, Tabs, VBox)


def main():
    progress = ProgressBar(35, height=1)
    bars = BarChart({"Render": 35, "Input": 65, "Layout": 90})
    counter = [0]

    def update(value):
        progress.set_value(value)
        bars.samples = {"Render": value, "Input": 100 - value, "Layout": 90}
        bars.invalidate()

    def notify():
        counter[0] += 1
        app.notify(f"Saved {counter[0]} · press again to interrupt the animation", duration=1.5)

    form = VBox(Input(placeholder="Focus glides without moving the text", height=1),
        Switch("Enable previews", height=1), Checkbox("Remember choice", height=1),
        Slider(35, on_change=update, height=1), progress,
        HBox(Button("Notify", notify), Button("Dialog", lambda: app.show_modal(
            Panel(VBox(Label("Focus enters immediately; Esc returns immediately."),
                Button("Close", app.close_modal, height=1)), "Animated dialog", width=56, height=9))),
             height=1, gap=1),
        Collapsible("Details", Label("Spatial springs bounce; measured numbers and colors stay bounded.")), gap=1)
    tabs = Tabs({"Controls": Panel(form, "Try quick direction changes"), "Metrics": Panel(bars, "Demo data")})
    scheme = Select(["Expressive", "Subtle"], on_change=lambda i, _: app.set_motion(("expressive", "subtle")[i]), height=1)
    with App(VBox(Label("ComfortableTUI motion · Tab / F6 / Enter / Ctrl+Q", height=1),
                  scheme, tabs, gap=1), fps=60) as app:
        app.run()


if __name__ == "__main__":
    main()
