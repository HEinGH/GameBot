import time
import logging

from core.fsm import BaseState
from recognition.template import find_template
from config.settings import Settings, parse_template_ref
from utils.logger import DEBUG_DIR

logger = logging.getLogger(__name__)


class CharacterSelectState(BaseState):
    def __init__(self, controller):
        self.controller = controller
        self._step = 0
        self._portrait_attempts = 0
        self._enter_attempts = 0
        self._max_portrait_attempts = 4
        self._max_enter_attempts = 60
        self._last_match_time = 0
        self._match_interval = 0.3
        self._scroll_count = 0
        self._max_scrolls = 8
        self._scroll_exhausted_attempts = 0

    def enter(self, blackboard):
        super().enter(blackboard)
        self._step = 0
        self._portrait_attempts = 0
        self._enter_attempts = 0
        self._last_match_time = 0
        self._scroll_count = 0
        self._scroll_exhausted_attempts = 0
        self.controller.release_all()
        logger.debug("State: CharacterSelect")

    def _click(self, x, y, blackboard, button="left"):
        self.controller.click_at(x, y, button)

    def _scroll_list(self, blackboard):
        if self._scroll_count >= self._max_scrolls:
            logger.warning("Reached max scrolls (%d), giving up", self._max_scrolls)
            return
        rect = blackboard.get("_window_rect")
        if rect:
            sx = rect[0] + int((rect[2] - rect[0]) * 0.15)
            sy = rect[1] + int((rect[3] - rect[1]) * 0.45)
        else:
            import ctypes
            sw = ctypes.windll.user32.GetSystemMetrics(0)
            sh = ctypes.windll.user32.GetSystemMetrics(1)
            sx = int(sw * 0.20)
            sy = int(sh * 0.45)
        logger.info("Scrolling %d/%d at (%d,%d)", self._scroll_count + 1, self._max_scrolls, sx, sy)
        self.controller.mouse_scroll(-600, sx, sy)
        self._scroll_count += 1
        self._portrait_attempts = 0
        time.sleep(0.5)

    def update(self, blackboard):
        if not blackboard["running"]:
            return
        if blackboard["stuck"]:
            return
        preset = blackboard["preset"]
        if preset is None:
            return
        char_index = blackboard["current_character_index"]
        chars = preset.get("characters", [])
        if char_index >= len(chars):
            logger.warning("No characters in preset or index out of range (%d >= %d)", char_index, len(chars))
            blackboard["running"] = False
            return
        char_config = chars[char_index]
        portrait_name, portrait_thr = parse_template_ref(char_config.get("portrait_template"))
        enter_name, enter_thr = parse_template_ref(preset.get("enter_game_template"))

        frame = blackboard["current_frame"]
        if frame is None:
            return

        now = time.time()
        if now - self._last_match_time < self._match_interval:
            return
        self._last_match_time = now

        if self._step == 0:
            if portrait_name:
                result = find_template(frame, portrait_name, threshold=portrait_thr, auto_update=True)
                if result:
                    cx, cy = result["center"]
                    self._click(cx, cy, blackboard)
                    logger.info("Selected character %d via %s (conf=%.2f)", char_index, portrait_name, result["confidence"])
                    self._step = 1
                    time.sleep(self.controller.jitter_delay(2.0))
                    return
                else:
                    self._portrait_attempts += 1
                    if self._scroll_count >= self._max_scrolls:
                        self._scroll_exhausted_attempts += 1
                        if self._scroll_exhausted_attempts >= self._max_portrait_attempts * 3:
                            logger.warning("Portrait not found after scrolls exhausted, skipping to enter")
                            self._step = 1
                            self._portrait_attempts = 0
                            return
                    if self._portrait_attempts >= self._max_portrait_attempts:
                        if self._scroll_count >= self._max_scrolls:
                            logger.warning("Portrait not found, scrolls exhausted, skipping selection")
                            self._step = 1
                            self._portrait_attempts = 0
                            return
                        logger.info("Portrait not found in %d attempts, scrolling", self._max_portrait_attempts)
                        self._scroll_list(blackboard)
            else:
                logger.info("No portrait_template for character %d", char_index)
                self._step = 1

        if self._step == 1:
            if enter_name:
                result = find_template(frame, enter_name, threshold=enter_thr)
                if result:
                    cx, cy = result["center"]
                    self._click(cx, cy, blackboard)
                    logger.info("Clicked enter game (conf=%.2f)", result["confidence"])
                    blackboard["_fsm"].transition("town_nav", blackboard)
                    return
                else:
                    self._enter_attempts += 1
                    if self._enter_attempts >= self._max_enter_attempts:
                        logger.warning("Enter game template not found after %d attempts, saving debug frame", self._max_enter_attempts)
                        try:
                            import cv2, numpy as np
                            time_str = time.strftime("%H%M%S")
                            snap_path = DEBUG_DIR / f"debug_enter_{time_str}.png"
                            success, encoded = cv2.imencode(".png", frame)
                            if success:
                                with open(snap_path, "wb") as fp:
                                    fp.write(encoded)
                                logger.info("Debug frame saved: %s", snap_path)
                        except Exception:
                            pass
                        logger.warning("Restarting selection flow")
                        self._step = 0
                        self._enter_attempts = 0
                        self._portrait_attempts = 0
                    elif self._enter_attempts % 10 == 0:
                        logger.info("Waiting for enter_game_template... (attempt %d/%d)", self._enter_attempts, self._max_enter_attempts)
            else:
                logger.warning("No enter_game_template configured, cannot enter game")
                blackboard["running"] = False
                return
