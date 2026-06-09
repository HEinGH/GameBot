import time
import logging

from core.fsm import BaseState
from recognition.template import find_template

logger = logging.getLogger(__name__)


class ExitMenuState(BaseState):
    def __init__(self, controller):
        self.controller = controller
        self._attempts = 0

    def enter(self, blackboard):
        super().enter(blackboard)
        self._attempts = 0
        self.controller.release_all()
        logger.debug("State: ExitMenu")

    def update(self, blackboard):
        if not blackboard["running"]:
            return
        if blackboard["stuck"]:
            return
        if self._attempts > 15:
            logger.warning("Exit menu max attempts, defaulting to re-challenge")
            self._choose_rechallenge(blackboard)
            return

        preset = blackboard["preset"]
        if preset is None:
            return
        char_index = blackboard["current_character_index"]
        chars = preset.get("characters", [])
        char_config = chars[char_index] if char_index < len(chars) else {}
        domain_runs = char_config.get("runs", 1)
        runs_done = blackboard.get("domain_run_count", 0)

        rechallenge_template = preset.get("rechallenge_template") or preset.get("town_nav", {}).get("rechallenge_template")
        exit_template = preset.get("exit_domain_template") or preset.get("town_nav", {}).get("exit_domain_template")

        frame = blackboard["current_frame"]
        if frame is None:
            self._attempts += 1
            time.sleep(0.5)
            return

        if runs_done >= domain_runs:
            if exit_template:
                r = find_template(frame, exit_template, threshold=0.7)
                if r:
                    cx, cy = r["center"]
                    self.controller.click_at(cx, cy)
                    logger.info("Clicked exit domain (runs done %d/%d)",
                                runs_done, domain_runs)
                    time.sleep(2.0)
                    blackboard["_fsm"].transition("map_loading", blackboard)
                    return
        else:
            if rechallenge_template:
                r = find_template(frame, rechallenge_template, threshold=0.7)
                if r:
                    cx, cy = r["center"]
                    self.controller.click_at(cx, cy)
                    logger.info("Clicked re-challenge (run %d/%d)",
                                runs_done + 1, domain_runs)
                    time.sleep(2.0)
                    blackboard["domain_run_count"] = runs_done + 1
                    blackboard["_fsm"].transition("domain_loading", blackboard)
                    return

        self._attempts += 1
        time.sleep(0.5)

    def _choose_rechallenge(self, blackboard):
        preset = blackboard["preset"]
        char_index = blackboard["current_character_index"]
        runs = 1
        if preset and "characters" in preset:
            chars = preset["characters"]
            if char_index < len(chars):
                runs = chars[char_index].get("runs", 1)
        runs_done = blackboard.get("domain_run_count", 0)
        if runs_done >= runs:
            blackboard["_fsm"].transition("map_loading", blackboard)
        else:
            blackboard["domain_run_count"] = runs_done + 1
            blackboard["_fsm"].transition("domain_loading", blackboard)
