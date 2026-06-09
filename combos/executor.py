import time
import logging
import random
from collections import deque

import pydirectinput

from utils.antidetection import HumanDelay

logger = logging.getLogger(__name__)


class ComboExecutor:
    def __init__(self, controller, randomness=0.2):
        self.controller = controller
        self.randomness = randomness
        self.delay = HumanDelay()
        self._queue = deque()
        self._running = False
        self._current_action = None
        self._cycle_count = 0

    def load_combos(self, combo_list):
        self._queue.clear()
        for action in combo_list:
            repeat = action.get("repeat", 1)
            if repeat > 1:
                for _ in range(repeat):
                    a = dict(action)
                    a["repeat"] = 1
                    self._queue.append(a)
            else:
                self._queue.append(action)

    def append(self, action):
        self._queue.append(action)

    def clear(self):
        self._queue.clear()

    @property
    def empty(self):
        return len(self._queue) == 0

    @property
    def remaining(self):
        return len(self._queue)

    def _jitter(self, base):
        return self.delay.delay(base * 1000, 0.3) / 1000

    def execute_next(self):
        if not self._queue:
            return False
        action = self._queue.popleft()
        keys = action.get("keys", [])
        hold = action.get("hold", False)
        duration = action.get("duration", 0.1)
        delay_before = action.get("delay_before", 0.0)
        delay_after = action.get("delay_after", 0.0)

        logger.info("Combo: keys=%s hold=%s dur=%.2f before=%.2f after=%.2f",
                    keys, hold, duration, delay_before, delay_after)

        db = self._jitter(delay_before)
        dur = self._jitter(duration)
        da = self._jitter(delay_after)

        if db > 0:
            time.sleep(db)

        if len(keys) > 1:
            for k in keys:
                self._press_down(k)
            time.sleep(dur)
            for k in reversed(keys):
                self._release_key(k)
        elif hold:
            self._hold_key(keys[0], dur)
        else:
            self._press_key(keys[0], dur)

        if da > 0:
            time.sleep(da)

        return True

    def _press_down(self, key):
        if key in ("right_click", "right"):
            logger.debug("Skipping %s (unsupported in combos)", key)
            return
        if key == "left_click":
            pydirectinput.mouseDown(button="left")
        else:
            self.controller.key_down(key)

    def _release_key(self, key):
        if key in ("right_click", "right"):
            return
        if key == "left_click":
            pydirectinput.mouseUp(button="left")
        else:
            self.controller.key_up(key)

    def execute_all(self, timeout=120):
        self._running = True
        start = time.time()
        while self._queue and self._running:
            if time.time() - start > timeout:
                logger.warning("Combo execution timed out (%ds)", timeout)
                break
            self.execute_next()
        self._running = False

    def stop(self):
        self._running = False
        self.controller.release_all()

    def _press_key(self, key, duration):
        if key in ("right_click", "right"):
            logger.debug("Skipping %s (unsupported in combos)", key)
            return
        if key == "left_click":
            pydirectinput.mouseDown(button="left")
            time.sleep(duration)
            pydirectinput.mouseUp(button="left")
        else:
            logger.info("  press %s dur=%.2f", key, duration)
            self.controller.tap_key(key, duration=duration)

    def _hold_key(self, key, duration):
        if key in ("right_click", "right"):
            logger.debug("Skipping %s (unsupported in combos)", key)
            return
        if key == "left_click":
            pydirectinput.mouseDown(button="left")
            time.sleep(duration)
            pydirectinput.mouseUp(button="left")
        else:
            self.controller.key_down(key)
            time.sleep(duration)
            self.controller.key_up(key)

    def shuffle_fallback(self, fallback_list):
        if not fallback_list:
            return fallback_list
        shuffled = list(fallback_list)
        random.shuffle(shuffled)
        return shuffled
