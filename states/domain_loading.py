import time
import logging

from core.fsm import BaseState
from recognition.template import find_template

logger = logging.getLogger(__name__)


class DomainLoadingState(BaseState):
    def __init__(self):
        self._timeout = 60
        self._start = 0.0

    def enter(self, blackboard):
        super().enter(blackboard)
        self._start = time.time()
        self._timeout = 60
        logger.debug("State: DomainLoading")

    def update(self, blackboard):
        if not blackboard["running"]:
            return
        if blackboard["stuck"]:
            return
        if time.time() - self._start > self._timeout:
            logger.warning("Domain loading timed out, assuming loaded")
            blackboard["_fsm"].transition("domain_combat", blackboard)
            return

        frame = blackboard["current_frame"]
        if frame is None:
            return

        preset = blackboard["preset"]
        if preset is None:
            return

        char_index = blackboard["current_character_index"]
        chars = preset.get("characters", [])
        skill_bar_template = None
        if char_index < len(chars):
            skill_bar_template = chars[char_index].get("skill_bar_template")
        if not skill_bar_template:
            skill_bar_template = preset.get("skill_bar_template") or "skill_bar.png"

        result = find_template(frame, skill_bar_template, threshold=0.6)
        if result:
            logger.info("Skill bar '%s' detected, domain loaded (%.1fs)",
                        skill_bar_template, time.time() - self._start)
            blackboard["_fsm"].transition("domain_combat", blackboard)
