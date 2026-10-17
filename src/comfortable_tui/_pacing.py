"""Phase-preserving frame deadlines; callers use the high-resolution perf clock."""


class FramePacer:
    def __init__(self, started):
        self.deadline = started

    def advance(self, now, fps):
        self.deadline += 1 / fps
        if self.deadline < now:
            # An over-budget frame is already the latest state. Never replay
            # missed frames or busy-spin to make up a backlog.
            self.deadline = now
        return self.deadline
