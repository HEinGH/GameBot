import random
import time
import logging
import ctypes

import pydirectinput

from utils.antidetection import HumanDelay, MouseTrajectory, BehaviorProfile

pydirectinput.PAUSE = 0.0
pydirectinput.FAILSAFE = False

logger = logging.getLogger(__name__)

_SAFE_STEALTH_STATES = {"domain_loading", "map_loading", "complete", "stuck_recovery"}

_PDI_KEY_MAP = {
    "left_alt": "alt", "alt": "alt",
    "left_shift": "shift", "left_ctrl": "ctrl",
    "enter": "return", "return": "return",
    "esc": "esc", "escape": "esc",
}

def _pdi_key(key):
    return _PDI_KEY_MAP.get(key, key)


class _POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class Controller:
    def __init__(self, stealth=False, combo_randomness=0.2, bezier_steps=20, click_jitter=5):
        self.combo_randomness = combo_randomness
        self.bezier_steps = bezier_steps
        self.click_jitter = click_jitter
        self._held_keys = set()
        self.delay = HumanDelay()
        self.trajectory = MouseTrajectory()
        self.profile = BehaviorProfile()
        self.stealth = stealth

        if stealth:
            self._max_strafe_interval = 45.0
            self._last_strafe = time.time()
            logger.info("Stealth mode: behavioral anti-detection active (profile=%s)",
                        self.profile.get_profile_name())

    def release_all(self):
        for k in list(self._held_keys):
            self.key_up(k)
        pydirectinput.mouseUp(button="left")
        pydirectinput.mouseUp(button="right")

    def key_down(self, key):
        pydirectinput.keyDown(_pdi_key(key))
        self._held_keys.add(key)

    def key_up(self, key):
        pydirectinput.keyUp(_pdi_key(key))
        self._held_keys.discard(key)

    def tap_key(self, key, duration=0.05, delay_after=0.0):
        dur = self.delay.hold_duration(duration) if self.stealth else duration
        da = self.delay.vary(delay_after, self.combo_randomness) if self.stealth else delay_after * (1 + random.uniform(-self.combo_randomness, self.combo_randomness))
        self.key_down(key)
        time.sleep(dur)
        self.key_up(key)
        if da > 0:
            time.sleep(da)
        self.delay.random_pause()

    def click(self, button="left", delay_after=0.0):
        flag_down = 0x0002 if button == "left" else 0x0008
        flag_up = 0x0004 if button == "left" else 0x0010
        ctypes.windll.user32.mouse_event(flag_down, 0, 0, 0, 0)
        time.sleep(0.20)
        ctypes.windll.user32.mouse_event(flag_up, 0, 0, 0, 0)
        if delay_after > 0:
            time.sleep(self.delay.vary(delay_after, 0.2) if self.stealth else delay_after)
        self.delay.random_pause()

    def move_to(self, x, y):
        ctypes.windll.user32.SetCursorPos(x, y)

    def move_to_bezier(self, x1, y1, x2, y2, steps=None):
        if self.stealth:
            pts = self.trajectory.generate(x1, y1, x2, y2, steps or self.bezier_steps)
        else:
            pts = self._legacy_bezier(x1, y1, x2, y2, steps)
        for px, py in pts:
            pydirectinput.moveTo(px, py)
            time.sleep(random.uniform(0.001, 0.004))

    def _legacy_bezier(self, x1, y1, x2, y2, steps=None):
        if steps is None:
            steps = self.bezier_steps
        cp1 = (x1 + (x2 - x1) * 0.2 + random.randint(-30, 30),
               y1 + (y2 - y1) * 0.2 + random.randint(-20, 20))
        cp2 = (x1 + (x2 - x1) * 0.8 + random.randint(-30, 30),
               y1 + (y2 - y1) * 0.8 + random.randint(-20, 20))
        pts = []
        for i in range(steps + 1):
            t = i / steps
            mt = 1 - t
            x = int(mt ** 3 * x1 + 3 * mt ** 2 * t * cp1[0] + 3 * mt * t ** 2 * cp2[0] + t ** 3 * x2)
            y = int(mt ** 3 * y1 + 3 * mt ** 2 * t * cp1[1] + 3 * mt * t ** 2 * cp2[1] + t ** 3 * y2)
            pts.append((x, y))
        return pts

    def click_at(self, x, y, button="left", jitter=None, bezier=True):
        if jitter is None:
            jitter = self.click_jitter
        jx = random.randint(-jitter, jitter)
        jy = random.randint(-jitter, jitter)
        tx, ty = x + jx, y + jy
        if self.stealth and bezier:
            pt = _POINT()
            ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
            self.move_to_bezier(pt.x, pt.y, tx, ty)
        else:
            ctypes.windll.user32.SetCursorPos(tx, ty)
        time.sleep(random.uniform(0.08, 0.15))
        flag_down = 0x0002 if button == "left" else 0x0008
        flag_up = 0x0004 if button == "left" else 0x0010
        ctypes.windll.user32.mouse_event(flag_down, 0, 0, 0, 0)
        time.sleep(random.uniform(0.25, 0.40))
        ctypes.windll.user32.mouse_event(flag_up, 0, 0, 0, 0)
        time.sleep(random.uniform(0.10, 0.20))

    def move_direction(self, direction, duration=0.1):
        if direction == "forward":
            self.key_down("w")
            time.sleep(self.delay.vary(duration, 0.2) if self.stealth else duration)
            self.key_up("w")
        elif direction == "left":
            self.key_down("a")
            self.key_down("w")
            time.sleep(self.delay.vary(duration, 0.2) if self.stealth else duration)
            self.key_up("a")
            self.key_up("w")
        elif direction == "right":
            self.key_down("d")
            self.key_down("w")
            time.sleep(self.delay.vary(duration, 0.2) if self.stealth else duration)
            self.key_up("d")
            self.key_up("w")
        elif direction == "backward":
            self.key_down("s")
            time.sleep(self.delay.vary(duration, 0.2) if self.stealth else duration)
            self.key_up("s")
        elif direction == "stop":
            pass

    def move_rel(self, dx, dy):
        pydirectinput.moveRel(dx, dy)
        if self.stealth:
            time.sleep(random.uniform(0.01, 0.04))

    def rotate_camera(self, angle_deg, sensitivity=200):
        pixels = int(angle_deg / 90.0 * sensitivity)
        if pixels == 0:
            return
        pydirectinput.mouseDown(button="left")
        time.sleep(0.02)
        pydirectinput.moveRel(pixels, 0)
        time.sleep(0.02)
        pydirectinput.mouseUp(button="left")
        time.sleep(0.03)

    def rotate_camera_free(self, angle_deg, sensitivity=400):
        pixels = int(angle_deg / 90.0 * sensitivity)
        if abs(pixels) < 3:
            return
        pydirectinput.moveRel(pixels, 0)
        time.sleep(0.03)

    def alt_press(self):
        self.key_down("left_alt")

    def alt_release(self):
        self.key_up("left_alt")

    def mouse_scroll(self, amount, x=None, y=None):
        if x is not None and y is not None:
            x = max(10, min(x, 65530))
            y = max(10, min(y, 65530))
            ctypes.windll.user32.SetCursorPos(x, y)
            time.sleep(0.02)
        ctypes.windll.user32.mouse_event(0x0800, 0, 0, amount, 0)
        time.sleep(random.uniform(0.05, 0.1))

    def jitter_delay(self, base):
        if self.stealth:
            return self.delay.delay(base, 0.3)
        factor = 1.0 + random.uniform(-self.combo_randomness, self.combo_randomness)
        return max(0.01, base * factor)

    def occasional_look_around(self):
        if not self.stealth:
            return
        now = time.time()
        if now - getattr(self, '_last_strafe', 0) > random.uniform(20, 50):
            dx, dy, dur = self.trajectory.random_strafe()
            pydirectinput.moveRel(dx, dy)
            time.sleep(dur)
            pydirectinput.moveRel(-dx // 2, -dy // 2)
            time.sleep(0.1)
            self._last_strafe = now
            logger.debug("Looked around (anti-detection)")
