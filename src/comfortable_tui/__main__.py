import argparse
import sys

from .demo import gallery


def main():
    parser = argparse.ArgumentParser(description="ComfortableTUI interactive widget gallery")
    parser.add_argument("--fps", type=int, default=120)
    parser.add_argument("--reduced-motion", action="store_true", default=None)
    parser.add_argument("--snapshot", action="store_true", help="Print a headless frame and exit")
    args = parser.parse_args()
    with gallery(fps=args.fps, reduced_motion=args.reduced_motion) as app:
        if args.snapshot:
            if hasattr(sys.stdout, "reconfigure"):
                sys.stdout.reconfigure(encoding="utf-8")
            frame = app.render(100, 32, dt=2)
            print(frame.plain())
        else:
            app.run()


if __name__ == "__main__":
    main()
