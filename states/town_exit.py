import time
import logging

from core.fsm import BaseState
from recognition.template import find_template

logger = logging.getLogger(__name__)


class TownExitState(BaseState):
    def __init__(self, controller):
        self.controller = controller
        self._step = 0
        self._max_attempts = 10

    def enter(self, blackboard):
        super().enter(blackboard)
        self._step = 0
        self.controller.release_all()
        self._activate_game_window(blackboard)
        logger.debug("State: TownExit")

    def _activate_game_window(self, blackboard):
        rect = blackboard.get("_window_rect")
        if not rect or len(rect) != 4:
            rect = self._find_window_rect(blackboard)
            if rect:
                blackboard["_window_rect"] = rect
                logger.info("TownExit: self-found window rect=%s", rect)
        if rect and len(rect) == 4:
            cx = (rect[0] + rect[2]) // 2
            cy = (rect[1] + rect[3]) // 2
            import ctypes
            ctypes.windll.user32.SetCursorPos(cx, cy)
            time.sleep(0.05)
            ctypes.windll.user32.mouse_event(0x0002, 0, 0, 0, 0)
            time.sleep(0.08)
            ctypes.windll.user32.mouse_event(0x0004, 0, 0, 0, 0)
            time.sleep(0.2)
            logger.info("TownExit: clicked game center (%d,%d) to ensure focus", cx, cy)

    def _find_window_rect(self, blackboard):
        try:
            import pywinctl as pwc
            preset = blackboard.get("preset", {})
            title = preset.get("window_title", "")
            all_visible = []
            for w in pwc.getAllWindows():
                try:
                    if not w.visible or w.isMinimized:
                        continue
                    b = w.box
                    if b.width < 800 or b.height < 600:
                        continue
                    area = b.width * b.height
                    all_visible.append((area, w))
                except Exception:
                    continue
            if not all_visible:
                return None
            if title:
                matches = [(a, w) for a, w in all_visible if title in (w.title or "")]
                if matches:
                    best = max(matches, key=lambda x: x[0])
                    b = best[1].box
                    return (b.left, b.top, b.left + b.width, b.top + b.height)
            best = max(all_visible, key=lambda x: x[0])
            b = best[1].box
            return (b.left, b.top, b.left + b.width, b.top + b.height)
        except Exception as e:
            logger.warning("TownExit: _find_window_rect failed: %s", e)
        return None

    def update(self, blackboard):
        if not blackboard["running"]:
            return
        if blackboard["stuck"]:
            return

        char_index = blackboard["current_character_index"]
        total = blackboard["total_characters"]
        next_index = char_index + 1
        all_done = next_index >= total

        preset = blackboard["preset"]
        if preset is None:
            return

        # ESC -> settings
        if self._step == 0:
            self.controller.tap_key("esc", duration=0.15, delay_after=0.5)
            self._step = 1
            return

        frame = blackboard["current_frame"]
        if frame is None:
            return

        settings_template = preset.get("town_exit", {}).get("settings_template")
        switch_char_template = preset.get("town_exit", {}).get("switch_character_template")
        exit_game_template = preset.get("town_exit", {}).get("exit_game_template")
        confirm_exit_template = preset.get("town_exit", {}).get("confirm_exit_template")

        if self._step == 1:
            if settings_template:
                r = find_template(frame, settings_template, threshold=0.7)
                if r:
                    cx, cy = r["center"]
                    self.controller.click_at(cx, cy)
                    logger.info("Clicked settings")
                    self._step = 2
                    time.sleep(1.0)
                    return
                self._step = 1
                time.sleep(0.5)
                return
            else:
                logger.warning("No settings_template, skipping to step 2")
                self._step = 2

        if self._step == 2:
            if all_done:
                if exit_game_template:
                    r = find_template(frame, exit_game_template, threshold=0.7)
                    if r:
                        cx, cy = r["center"]
                        self.controller.click_at(cx, cy)
                        logger.info("All done, clicking exit game")
                        self._step = 3
                        time.sleep(1.5)
                        return
                logger.info("All done, no exit template, going to confirm step")
                self._step = 3
                time.sleep(0.5)
                return
            else:
                if switch_char_template:
                    r = find_template(frame, switch_char_template, threshold=0.7)
                    if r:
                        cx, cy = r["center"]
                        self.controller.click_at(cx, cy)
                        logger.info("Clicked switch character")
                        self._step = 4
                        time.sleep(2.0)
                        return

            self._max_attempts -= 1
            if self._max_attempts <= 0:
                logger.warning("Town exit: template not found, using fallback")
                self._step = 4
                self._max_attempts = 10
            time.sleep(0.5)
            return

        if self._step == 3:
            if confirm_exit_template:
                r = find_template(frame, confirm_exit_template, threshold=0.7)
                if r:
                    cx, cy = r["center"]
                    self.controller.click_at(cx, cy)
                    logger.info("Confirmed exit game")
                    blackboard["_fsm"].transition("complete", blackboard)
                    return
            self._max_attempts -= 1
            if self._max_attempts <= 0:
                self.controller.tap_key("enter", duration=0.1, delay_after=1.0)
                blackboard["_fsm"].transition("complete", blackboard)
                return
            time.sleep(0.5)
            return

        if self._step == 4:
            blackboard["current_character_index"] = next_index
            blackboard["domain_run_count"] = 0
            blackboard["_fsm"].transition("character_select", blackboard)
