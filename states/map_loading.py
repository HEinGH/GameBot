import time
import logging

from core.fsm import BaseState
from recognition.template import find_template
from config.settings import parse_template_ref

logger = logging.getLogger(__name__)


class MapLoadingState(BaseState):
    def __init__(self):
        self._start = 0.0
        self._timeout = 45

    def enter(self, blackboard):
        super().enter(blackboard)
        self._start = time.time()
        logger.debug("State: MapLoading")

    def update(self, blackboard):
        if not blackboard["running"]:
            return
        if blackboard["stuck"]:
            return
        if time.time() - self._start > self._timeout:
            logger.warning("Map loading timed out")
            blackboard["_fsm"].transition("town_exit", blackboard)
            return

        frame = blackboard["current_frame"]
        if frame is None:
            return

        preset = blackboard["preset"]
        char_index = blackboard["current_character_index"]
        chars = preset.get("characters", [])
        avatar_template = chars[char_index].get("avatar_template") if preset and char_index < len(chars) else None
        if not avatar_template:
            avatar_template = preset.get("town_nav", {}).get("avatar_template") if preset else None
        avatar_name, avatar_thr = parse_template_ref(avatar_template)
        if avatar_name:
            r = find_template(frame, avatar_name, threshold=avatar_thr, auto_update=True)
            if r:
                logger.info("Avatar detected, town loaded (%.1fs)",
                            time.time() - self._start)
                blackboard["_fsm"].transition("town_exit", blackboard)
                return
