"""osu-inspired carousel, animated mutations and stacked pages."""
from comfortable_tui import App, Button, HBox, Label, ListView, PageStack, Panel, VBox


def build():
    menu = ListView([f"Track {i:02d} / spring motion" for i in range(1, 31)], item_height=2)
    stack = PageStack(Panel(menu, "Song selection · arrows / Enter"))
    app = App(stack, fps=120)

    def open_track(index, title):
        stack.push(Panel(VBox(Label(title, height=1),
                             Label("Escape returns; the list keeps selection and scroll.", height=2),
                             HBox(Button("Play", lambda: app.notify("Demo playback")),
                                  Button("Back", stack.pop), height=1, gap=1), gap=1), "Track details"))
    menu.on_select = open_track
    app.bind("ctrl+n", lambda: menu.insert_item(menu.selected, "New track · expands from zero"))
    app.bind("ctrl+d", lambda: menu.remove_item(menu.selected) if menu.items else None)
    return app


if __name__ == "__main__":
    with build() as app:
        app.run()
