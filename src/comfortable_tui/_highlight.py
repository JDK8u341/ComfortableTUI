"""Shared fractional highlight geometry, using independent leading/trailing edges."""


class AnimatedHighlight:
    def __init__(self, make_value, *, lead=28.0, trail=16.0):
        self.edges = [make_value(0, speed=lead, damping=0) for _ in range(4)]
        self.lead, self.trail, self.initialized = lead, trail, False

    def move_to(self, x, y, width, height):
        goals = (x, y, x + width, y + height)
        if not self.initialized:
            for edge, goal in zip(self.edges, goals):
                edge.jump(goal)
            self.initialized = True
            return
        dx = goals[0] + goals[2] - self.edges[0].target - self.edges[2].target
        dy = goals[1] + goals[3] - self.edges[1].target - self.edges[3].target
        for index, (edge, goal) in enumerate(zip(self.edges, goals)):
            direction = dx if index in (0, 2) else dy
            leading = direction < 0 if index in (0, 1) else direction > 0
            # Repeated layouts must not switch the speed of a moving edge.
            if edge.target != goal:
                edge.speed = self.lead if leading else self.trail
            edge.set(goal)

    def hide(self):
        for edge in self.edges:
            edge.snap()
        self.initialized = False

    @property
    def bounds(self):
        left, top, right, bottom = (edge.current for edge in self.edges)
        return left, top, max(0, right - left), max(0, bottom - top)

    def paint(self, canvas, style, *, dx=0, dy=0):
        if self.initialized:
            x, y, width, height = self.bounds
            canvas.tint(x + dx, y + dy, width, height, style)
