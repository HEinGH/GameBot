import time
import logging

from core.fsm import BaseState
from input.controller import Controller

logger = logging.getLogger(__name__)


class StuckRecoveryState(BaseState):
    def __init__(self, controller):
        self.controller = controller
        self._recovery_actions = [
            ("release_all", 0.5),
            ("tap_key", "esc", 0.15, 1.0),
            ("tap_key", "esc", 0.15, 1.0),
            ("tap_key", "m", 0.15, 1.5),
            ("tap_key", "m", 0.15, 1.0),
            ("tap_key", "enter", 0.15, 1.0),
            ("tap_key", "space", 0.15, 0.5),
        ]

    def enter(self, blackboard):
        super().enter(blackboard)
        logger.warning("Entering stuck recovery")
        self._action_index = 0
        blackboard["stuck"] = False

    def update(self, blackboard):
        if not blackboard["running"]:
            return
        if self._action_index >= len(self._recovery_actions):
            logger.info("Recovery actions done, returning to character_select")
            blackboard["_fsm"].transition("character_select", blackboard)
            return
        action = self._recovery_actions[self._action_index]
        action_type = action[0]
        if action_type == "release_all":
            self.controller.release_all()
            time.sleep(action[1])
        elif action_type == "tap_key":
            self.controller.tap_key(action[1], duration=action[2], delay_after=action[3])
        self._action_index += 1
