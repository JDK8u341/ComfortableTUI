"""Small form. Run from the checkout after: python -m pip install -e ."""
from comfortable_tui import App, Button, Input, Label, Panel, VBox


def build():
    message = Label("Hello / 你好", height=1)
    name = Input(placeholder="Your name", height=1)
    root = Panel(VBox(message, name,
                      Button("Greet", lambda: message.set_text(f"Hello, {name.text}!"), height=1),
                      gap=1), "First app")
    return App(root)


if __name__ == "__main__":
    with build() as app:
        app.run()
