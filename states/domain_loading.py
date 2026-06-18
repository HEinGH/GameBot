import time
import logging

from core.fsm import BaseState
from recognition.template import find_template
from config.settings import parse_template_ref

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
            logger.warning("副本加载超时，假定已加载")
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
        skill_name, skill_thr = parse_template_ref(skill_bar_template)
        if not skill_name:
            skill_name = preset.get("skill_bar_template") or "skill_bar.png"

        result = find_template(frame, skill_name, threshold=skill_thr, auto_update=True)
        if result:
            logger.info("识别到技能栏 '%s'，副本加载完成 (%.1f秒)",
                        skill_name, time.time() - self._start)
            blackboard["_fsm"].transition("domain_combat", blackboard)
