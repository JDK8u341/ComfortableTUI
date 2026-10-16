"""Time-based animation values; layout sets targets, the clock advances values."""
from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(eq=False)
class Value:
    current: float = 0.0
    speed: float = 18.0
    damping: float = 1.0
    target: float | None = None
    velocity: float = 0.0

    def __post_init__(self):
        if not all(math.isfinite(v) for v in (self.current, self.speed, self.damping, self.velocity)):
            raise ValueError("animation parameters must be finite")
        if self.speed <= 0 or self.damping < 0:
            raise ValueError("speed must be positive and damping nonnegative")
        if self.target is None:
            self.target = self.current
        elif not math.isfinite(self.target):
            raise ValueError("animation target must be finite")

    @property
    def active(self) -> bool:
        return self.current != self.target or self.velocity != 0

    def set(self, target: float):
        if not math.isfinite(target):
            raise ValueError("animation target must be finite")
        self.target = float(target)

    def jump(self, value: float):
        self.set(value)
        self.snap()

    def snap(self):
        self.current, self.velocity = self.target, 0.0

    def kick(self, distance: float):
        if self.damping:
            self.velocity += distance * self.speed * math.e

    def step(self, dt: float) -> bool:
        """Solve the damped oscillator analytically, including over-damping.

        No Euler stability limit and no frame-rate-dependent integration error.
        damping=0 deliberately selects exponential easing, as in osu-mc.
        """
        if not math.isfinite(dt) or dt < 0:
            raise ValueError("dt must be finite and nonnegative")
        if not self.active or dt == 0:
            return False
        x, v, w, z = self.current - self.target, self.velocity, self.speed, self.damping
        if z == 0:
            x *= math.exp(-w * dt)
            v = 0.0
        elif abs(z - 1) < 1e-8:
            b = v + w * x
            decay = math.exp(-w * dt)
            x, v = (x + b * dt) * decay, (v - w * b * dt) * decay
        elif z < 1:
            a, b = z * w, w * math.sqrt(1 - z * z)
            c, s, decay = math.cos(b * dt), math.sin(b * dt), math.exp(-a * dt)
            old_x = x
            x = decay * (x * c + (v + a * x) / b * s)
            v = decay * (v * c - (a * v + w * w * old_x) / b * s)
        else:
            root = math.sqrt(z * z - 1)
            r1, r2 = -w / (z + root), -w * (z + root)
            a = (v - r2 * x) / (r1 - r2)
            b = x - a
            e1, e2 = math.exp(r1 * dt), math.exp(r2 * dt)
            x, v = a * e1 + b * e2, r1 * a * e1 + r2 * b * e2
        self.current, self.velocity = self.target + x, v
        if abs(x) < 0.001 and abs(v) < 0.01:
            self.snap()
        return True


class Animator:
    """Explicit ownership: discarded widgets do not remain in a global registry."""
    def __init__(self, reduced_motion: bool = False):
        self.values: list[Value] = []
        self.reduced_motion = reduced_motion

    def add(self, value: Value) -> Value:
        if value not in self.values:
            self.values.append(value)
        return value

    def remove(self, value: Value):
        if value in self.values:
            self.values.remove(value)

    @property
    def active(self):
        return any(v.active for v in self.values)

    def step(self, dt: float) -> bool:
        if not math.isfinite(dt) or dt < 0:
            raise ValueError("dt must be finite and nonnegative")
        changed = False
        for value in self.values:
            if self.reduced_motion and value.active:
                value.snap()
                changed = True
            else:
                changed = value.step(dt) or changed
        return changed
