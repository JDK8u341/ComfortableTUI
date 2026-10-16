"""Timer-driven dashboard with worker completion on the UI thread."""
import math

from comfortable_tui import App, Button, HBox, Label, Log, Panel, ProgressBar, Sparkline, VBox


def build():
    status = Label("Timers + worker callbacks", height=1)
    progress = ProgressBar(0, height=1)
    chart = Sparkline([], height=1)
    log = Log()
    app = App(Panel(VBox(status, progress, chart,
                          HBox(Button("Compute", lambda: app.run_worker(
                              lambda: sum(i * i for i in range(300_000)),
                              lambda result: log.write(f"Worker result: {result}"))),
                               Button("Quit", lambda: app.exit()), height=1, gap=1), log, gap=1),
                    "Dashboard"))
    elapsed = 0.0

    def update():
        nonlocal elapsed
        elapsed += 0.1
        value = 50 + math.sin(elapsed) * 40
        progress.set_value(value)
        chart.samples = (chart.samples + [value])[-160:]
        chart.invalidate()
    app.call_later(0.1, update, repeat=True)
    return app


if __name__ == "__main__":
    with build() as app:
        app.run()
