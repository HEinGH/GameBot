import time
import logging

from core.fsm import BaseState
from combos.executor import ComboExecutor
from config.settings import parse_template_ref

logger = logging.getLogger(__name__)


class DomainCombatState(BaseState):
    def __init__(self, controller, randomness=0.2):
        self.controller = controller
        self.executor = ComboExecutor(controller, randomness)
        self._idle_cycles = 0
        self._combat_start = 0.0
        self._timeout = 180
        self._reload_count = 0
        self._max_reloads = 2
        self._phase = "normal"
        self._fallback = None
        self._cycle_count = 0
        self._dismissing = False
        self._dismiss_time = 0.0
        self._dismiss_retries = 0

    def enter(self, blackboard):
        super().enter(blackboard)
        self._combat_start = time.time()
        self._idle_cycles = 0
        self._reload_count = 0
        self._phase = "normal"
        self._fallback = None
        self._cycle_count = 0
        self._dismissing = False
        self._dismiss_time = 0
        self._dismiss_retries = 0
        self._load_combos(blackboard)
        self.controller.release_all()
        import pydirectinput
        try:
            pydirectinput.mouseUp(button="left")
            pydirectinput.mouseUp(button="right")
        except Exception:
            pass
        logger.debug("State: DomainCombat")

    def _load_combos(self, blackboard):
        preset = blackboard["preset"]
        char_index = blackboard["current_character_index"]
        if preset and "characters" in preset:
            chars = preset["characters"]
            if char_index < len(chars):
                combos = chars[char_index].get("combos", [])
                fallback = chars[char_index].get("fallback_combos")
                if fallback:
                    self._fallback = list(fallback)
                self.executor.load_combos(combos)
                logger.info("Loaded %d combo actions for character %d",
                            len(combos), char_index)

    def _load_fallback(self, blackboard):
        if self._fallback is None:
            preset = blackboard["preset"]
            if preset and preset.get("fallback_combos"):
                self._fallback = list(preset["fallback_combos"])
            else:
                self._fallback = self._gen_fallback()
        self.executor.load_combos(self._fallback)
        self._phase = "fallback"
        logger.info("Switched to fallback rotation (%d actions)", len(self._fallback))

    def _gen_fallback(self):
        return [
            {"keys": ["1"], "duration": 0.1, "delay_after": 0.6},
            {"keys": ["2"], "duration": 0.1, "delay_after": 0.6},
            {"keys": ["3"], "duration": 0.1, "delay_after": 0.6},
            {"keys": ["4"], "duration": 0.1, "delay_after": 0.6},
            {"keys": ["5"], "duration": 0.1, "delay_after": 0.6},
            {"keys": ["e"], "duration": 0.15, "delay_after": 1.0},
            {"keys": ["q"], "duration": 0.15, "delay_after": 2.0},
        ]

    def update(self, blackboard):
        if not blackboard["running"]:
            return
        if blackboard["stuck"]:
            return

        if time.time() - self._combat_start > self._timeout:
            logger.info("Combat timeout (%ds), assuming cleared", self._timeout)
            self.controller.release_all()
            self.controller.click()
            time.sleep(0.3)
            blackboard["_fsm"].transition("dungeon_exit_nav", blackboard)
            return

        if self._dismissing:
            self._do_dismiss(blackboard)
            return

        if not self.executor.empty:
            self.executor.execute_next()
        else:
            self._idle_cycles += 1
            if self._idle_cycles >= 50:
                if self._phase == "normal":
                    self._reload_count += 1
                    if self._reload_count > self._max_reloads:
                        self._load_fallback(blackboard)
                    else:
                        self._load_combos(blackboard)
                else:
                    fb = self._fallback or self._gen_fallback()
                    if self.controller.stealth and self._cycle_count % 3 == 0:
                        shuffled = self.executor.shuffle_fallback(fb)
                        self.executor.load_combos(shuffled)
                    else:
                        self.executor.load_combos(fb)
                    self._cycle_count += 1
                self._idle_cycles = 0

        if self.executor.empty and time.time() - self._combat_start > 3.0:
            panel = self._detect_character_panel(blackboard)
            if panel:
                self.controller.release_all()
                cx, cy = panel["center"]
                logger.info("Panel at (%d,%d) conf=%.2f template=%s, clicking",
                            cx, cy, panel["confidence"], panel.get("template", "?"))
                self.controller.click_at(cx, cy)
                self._dismissing = True
                self._dismiss_time = time.time()
                self._dismiss_retries = 0

    def _do_dismiss(self, blackboard):
        elapsed = time.time() - self._dismiss_time
        panel = self._detect_character_panel(blackboard)

        if not panel:
            logger.info("Panel dismissed after %.1fs", elapsed)
            self._dismissing = False
            blackboard["_fsm"].transition("dungeon_exit_nav", blackboard)
            return

        if elapsed > 1.0:
            self._dismiss_retries += 1
            if self._dismiss_retries <= 2:
                logger.info("Panel retry click #%d (%.1fs)", self._dismiss_retries, elapsed)
                self.controller.click()
                self._dismiss_time = time.time()
                return
            logger.warning("Panel still visible after %d retries, proceeding anyway", self._dismiss_retries)
            self._dismissing = False
            blackboard["_fsm"].transition("dungeon_exit_nav", blackboard)

    def _detect_character_panel(self, blackboard):
        preset = blackboard["preset"]
        if not preset:
            return None
        char_index = blackboard["current_character_index"]
        chars = preset.get("characters", [])
        template = None
        if char_index < len(chars):
            template = chars[char_index].get("result_screen_template")
        template_name, template_thr = parse_template_ref(template)
        if not template_name:
            template_name, template_thr = parse_template_ref(preset.get("result_screen_template"))
        if not template_name:
            return None
        frame = blackboard["current_frame"]
        if frame is None:
            return None
        from recognition.template import find_template
        r = find_template(frame, template_name, threshold=template_thr)
        if r:
            r["template"] = template
        return r
