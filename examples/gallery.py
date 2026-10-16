"""All widget families; same gallery as python -m comfortable_tui."""
from comfortable_tui.demo import gallery


if __name__ == "__main__":
    with gallery() as app:
        app.run()
