import random
import time
import math
from functools import lru_cache


class HumanDelay:
    def __init__(self, seed=None):
        self._rng = random.Random(seed)
        self._session_rhythm = self._rng.uniform(0.85, 1.15)

    def delay(self, base_ms, variance=0.3):
        delay = self._rng.gauss(base_ms, base_ms * variance / 2.5)
        delay *= self._session_rhythm
        delay = max(0.008, delay)
        if self._rng.random() < 0.04:
            delay += self._rng.uniform(0.06, 0.18)
        return delay

    def hold_duration(self, base):
        dur = base * self._rng.gauss(1.0, 0.15)
        return max(0.02, dur)

    def random_pause(self):
        if self._rng.random() < 0.03:
            time.sleep(self._rng.uniform(0.3, 0.8))

    def vary(self, value, ratio=0.15):
        return value * self._rng.gauss(1.0, ratio / 2)


class MouseTrajectory:
    @staticmethod
    def generate(x1, y1, x2, y2, steps=24):
        dist = math.hypot(x2 - x1, y2 - y1)
        steps = max(8, min(40, int(dist / 8)))

        overshoot = dist * random.uniform(0.02, 0.08)
        angle = math.atan2(y2 - y1, x2 - x1) + random.uniform(-0.3, 0.3)
        tx = x2 + math.cos(angle) * overshoot
        ty = y2 + math.sin(angle) * overshoot

        mid_x = (x1 + tx) / 2 + random.uniform(-dist * 0.05, dist * 0.05)
        mid_y = (y1 + ty) / 2 + random.uniform(-dist * 0.05, dist * 0.05)

        points = []
        for i in range(steps):
            t = (i + 1) / steps
            if t < 0.5:
                ease = 2 * t * t
            else:
                ease = 1 - (-2 * t + 2) ** 2 / 2

            bx = (1 - ease) ** 2 * x1 + 2 * (1 - ease) * ease * mid_x + ease ** 2 * tx
            by = (1 - ease) ** 2 * y1 + 2 * (1 - ease) * ease * mid_y + ease ** 2 * ty

            tremor = 1 + random.gauss(0, 0.006)
            bx *= tremor
            by *= tremor

            points.append((int(bx), int(by)))

        if abs(tx - x2) > 2 or abs(ty - y2) > 2:
            corr_steps = max(2, int(overshoot / 4))
            for i in range(corr_steps):
                t = (i + 1) / corr_steps
                cx = tx + (x2 - tx) * t + random.gauss(0, 1.5)
                cy = ty + (y2 - ty) * t + random.gauss(0, 1.5)
                points.append((int(cx), int(cy)))
        else:
            points.append((x2, y2))

        return points

    @staticmethod
    def random_strafe(duration=0.3):
        """Generate a brief random mouse movement (looking around)"""
        dx = random.randint(-200, 200)
        dy = random.randint(-100, 100)
        return dx, dy, duration * random.uniform(0.5, 1.5)


class BehaviorProfile:
    def __init__(self):
        self.session_id = random.randint(0, 2 ** 16)

    @property
    def name(self):
        return f"profile_{self.session_id % 1000}"

    @lru_cache(maxsize=1)
    def get_profile_name(self):
        return self.name
