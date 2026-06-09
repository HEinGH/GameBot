import time
import logging

from core.fsm import BaseState
from recognition.template import find_template

logger = logging.getLogger(__name__)


class TownNavState(BaseState):
    def __init__(self, controller):
        self.controller = controller
        self._avatar_attempts = 0
        self._chain_index = 0
        self._chain_attempts = 0
        self._chain_warmup = 0
        self._post_chain_attempts = 0
        self._alt_held = False
        self._last_match_time = 0
        self._match_interval = 0.8

    def enter(self, blackboard):
        super().enter(blackboard)
        self._avatar_attempts = 0
        self._chain_index = 0
        self._chain_attempts = 0
        self._chain_warmup = 0
        self._post_chain_attempts = 0
        self._avatar_done = False
        self._alt_release_safe()
        self.controller.release_all()
        logger.debug("State: TownNav")

    def _alt_press_safe(self):
        if not self._alt_held:
            self.controller.alt_press()
            self._alt_held = True

    def _alt_release_safe(self):
        if self._alt_held:
            self.controller.alt_release()
            self._alt_held = False

    def _build_chain(self, blackboard):
        preset = blackboard["preset"]
        nav = preset.get("town_nav", {})

        daily = nav.get("daily_button_template")
        domain_steps = nav.get("domain_select_steps") or nav.get("domain_template") or []
        if isinstance(domain_steps, str):
            domain_steps = [domain_steps]
        challenges = nav.get("challenge_templates", [])

        chain = []
        if daily:
            chain.append(daily)
        for s in domain_steps:
            if s:
                chain.append(s)
        for c in challenges:
            if c:
                chain.append(c)
        return chain

    def _try_transition(self, blackboard):
        preset = blackboard["preset"]
        nav = preset.get("town_nav", {})
        npc_tpl = nav.get("npc_marker_template") or preset.get("npc_template")
        frame = blackboard["current_frame"]
        if frame is None:
            return

        if npc_tpl:
            result = find_template(frame, npc_tpl, threshold=0.40)
            if result:
                logger.info("NPC icon detected, transitioning to npc_navigate (conf=%.2f)",
                            result["confidence"])
                self._alt_release_safe()
                blackboard["_fsm"].transition("npc_navigate", blackboard)
                return
        else:
            logger.info("No npc_marker_template configured, going direct to domain")
            self._alt_release_safe()
            blackboard["_fsm"].transition("domain_loading", blackboard)
            return

        self._post_chain_attempts += 1
        if self._post_chain_attempts % 5 == 1:
            logger.info("Post-chain looking for NPC icon... (attempt %d/20) tpl=%s",
                        self._post_chain_attempts, npc_tpl)
            wd = blackboard.get("_watchdog")
            if wd:
                wd.reset()
        if self._post_chain_attempts > 20:
            logger.warning("NPC icon not found after 20 attempts, forcing domain entry")
            self._alt_release_safe()
            blackboard["_fsm"].transition("domain_loading", blackboard)

    def update(self, blackboard):
        if not blackboard["running"]:
            return
        if blackboard["stuck"]:
            return
        preset = blackboard["preset"]
        if preset is None:
            return

        frame = blackboard["current_frame"]
        if frame is None:
            return

        now = time.time()
        if now - self._last_match_time < self._match_interval:
            return
        self._last_match_time = now

        nav = preset.get("town_nav", {})
        alt_needed = nav.get("alt_for_mouse", nav.get("ctrl_for_mouse", True))

        if not self._avatar_done:
            char_index = blackboard["current_character_index"]
            chars = preset.get("characters", [])
            avatar_template = chars[char_index].get("avatar_template") if char_index < len(chars) else None
            if not avatar_template:
                avatar_template = nav.get("avatar_template")
            if avatar_template:
                result = find_template(frame, avatar_template, threshold=0.7)
                if result:
                    logger.info("Avatar detected, confirmed in town")
                    self._avatar_done = True
                    time.sleep(0.5)
                else:
                    self._avatar_attempts += 1
                    if self._avatar_attempts > 60:
                        logger.warning("Avatar not found after 60 attempts, proceeding anyway")
                        self._avatar_done = True
                        time.sleep(0.5)
                    return
            else:
                logger.warning("No avatar_template configured, skipping avatar check")
                self._avatar_done = True
                time.sleep(0.3)
            return

        chain = self._build_chain(blackboard)
        if not chain:
            logger.warning("No town action steps configured, forcing transition")
            self._alt_release_safe()
            blackboard["_fsm"].transition("domain_loading", blackboard)
            return

        if self._chain_index >= len(chain):
            self._try_transition(blackboard)
            return

        if self._chain_warmup < 3:
            self._chain_warmup += 1
            if self._chain_warmup == 1 and alt_needed:
                self._alt_press_safe()
                time.sleep(0.3)
            logger.debug("Chain warmup %d/3 (waiting for UI)", self._chain_warmup)
            time.sleep(self.controller.jitter_delay(0.5))
            return

        tpl = chain[self._chain_index]
        result = find_template(frame, tpl, threshold=0.45)
        if result:
            cx, cy = result["center"]
            reaction = self.controller.jitter_delay(0.15)
            time.sleep(reaction)
            self.controller.click_at(cx, cy)
            logger.debug("Town action %d/%d: %s (conf=%.2f)",
                        self._chain_index + 1, len(chain), tpl, result["confidence"])
            self._chain_index += 1
            self._chain_attempts = 0
            wait = self.controller.jitter_delay(2.0) if self._chain_index == 1 else self.controller.jitter_delay(1.2)
            time.sleep(wait)
        else:
            self._chain_attempts += 1
            if self._chain_attempts % 10 == 1:
                logger.debug("Looking for '%s'... (attempt %d/45)", tpl, self._chain_attempts)
            if self._chain_attempts % 5 == 0:
                wd = blackboard.get("_watchdog")
                if wd:
                    wd.reset()
            if self._chain_attempts > 45:
                logger.warning("'%s' not found after 45 attempts, saving debug frame", tpl)
                try:
                    import cv2
                    from utils.logger import DEBUG_DIR
                    time_str = time.strftime("%H%M%S")
                    snap_path = DEBUG_DIR / "debug_town_step_{}_{}.png".format(self._chain_index, time_str)
                    success, encoded = cv2.imencode(".png", frame)
                    if success:
                        with open(snap_path, "wb") as fp:
                            fp.write(encoded)
                        logger.info("Debug frame saved: %s", snap_path)
                except Exception:
                    pass
                self._chain_index += 1
                self._chain_attempts = 0
