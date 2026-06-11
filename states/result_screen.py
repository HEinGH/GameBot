import time
import logging
import random

from core.fsm import BaseState
from recognition.template import find_template

logger = logging.getLogger(__name__)


class ResultScreenState(BaseState):
    def __init__(self, controller):
        self.controller = controller
        self._click_count = 0
        self._max_clicks = 10

    def enter(self, blackboard):
        super().enter(blackboard)
        self._click_count = 0
        self.controller.release_all()
        logger.debug("State: ResultScreen")

    def update(self, blackboard):
        if not blackboard["running"]:
            return
        if blackboard["stuck"]:
            return

        preset = blackboard["preset"]
        result_template = preset.get("result_screen_template") if preset else None
        frame = blackboard["current_frame"]

        if result_template and frame is not None:
            r = find_template(frame, result_template, threshold=0.65)
            if r:
                cx, cy = r["center"]
                logger.info("Result screen at (%d,%d) conf=%.2f, clicking", cx, cy, r["confidence"])
                self.controller.click_at(cx, cy)
                self._click_count += 1
                time.sleep(random.uniform(0.5, 1.5))
                return
            elif self._click_count > 0:
                logger.info("Result screen closed (clicked %d times)", self._click_count)
                blackboard["_fsm"].transition("exit_nav", blackboard)
                return

        if self._click_count == 0:
            time.sleep(0.5)
            return

        self._click_count += 1
        time.sleep(0.5)
        if self._click_count >= self._max_clicks:
            logger.info("Max clicks reached, forcing exit nav")
            blackboard["_fsm"].transition("exit_nav", blackboard)
