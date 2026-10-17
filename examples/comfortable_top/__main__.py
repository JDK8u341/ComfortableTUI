"""Run with python -m examples.comfortable_top or python examples/comfortable_top."""
if not __package__:
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from examples.comfortable_top.main import main

main()
