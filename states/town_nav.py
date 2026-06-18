import time
import logging

from core.fsm import BaseState
from recognition.template import find_template
from config.settings import parse_template_ref, parse_template_chain

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
            name, thr = parse_template_ref(daily)
            if name:
                chain.append((name, thr))
        for s, t in parse_template_chain(domain_steps):
            chain.append((s, t))
        for c in (challenges if isinstance(challenges, list) else [challenges]):
            name, thr = parse_template_ref(c)
            if name:
                chain.append((name, thr))
        return chain

    def _try_transition(self, blackboard):
        preset = blackboard["preset"]
        nav = preset.get("town_nav", {})
        npc_name, npc_thr = parse_template_ref(nav.get("npc_marker_template") or preset.get("npc_template"))
        frame = blackboard["current_frame"]
        if frame is None:
            return

        if npc_name:
            result = find_template(frame, npc_name, threshold=npc_thr)
            if result:
                logger.info("识别到NPC图标，进入NPC寻路 (置信度=%.2f)",
                            result["confidence"])
                self._alt_release_safe()
                blackboard["_fsm"].transition("npc_navigate", blackboard)
                return
        else:
            logger.info("未配置NPC图标模板，直接进入副本")
            self._alt_release_safe()
            blackboard["_fsm"].transition("domain_loading", blackboard)
            return

        self._post_chain_attempts += 1

        if self._post_chain_attempts >= 3:
            char_index = blackboard.get("current_character_index", 0)
            chars = preset.get("characters", [])
            char_cfg = chars[char_index] if char_index < len(chars) else {}
            skill_tpl = char_cfg.get("skill_bar_template") or preset.get("skill_bar_template")
            if skill_tpl:
                s_name, s_thr = parse_template_ref(skill_tpl)
                if s_name:
                    s_r = find_template(frame, s_name, threshold=s_thr)
                    if s_r:
                        logger.info("NPC搜索中识别到技能栏 (置信度=%.2f)，进入副本战斗",
                                    s_r["confidence"])
                        self._alt_release_safe()
                        blackboard["_fsm"].transition("domain_combat", blackboard)
                        return
                    elif self._post_chain_attempts == 3:
                        logger.debug("技能栏未匹配 (模板=%s 阈值=%.2f)", s_name, s_thr)

        if self._post_chain_attempts % 5 == 1:
            logger.info("操作链完成，搜索NPC图标... (尝试 %d/20) 模板=%s",
                        self._post_chain_attempts, npc_name)
            wd = blackboard.get("_watchdog")
            if wd:
                wd.reset()
        if self._post_chain_attempts > 20:
            logger.warning("NPC图标 20 次未匹配，强制进入副本")
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
            avatar_name, avatar_thr = parse_template_ref(avatar_template)
            if avatar_name:
                result = find_template(frame, avatar_name, threshold=avatar_thr, auto_update=True)
                if result:
                    logger.info("识别到城镇头像，确认在城镇中")
                    self._avatar_done = True
                    time.sleep(0.5)
                else:
                    self._avatar_attempts += 1
                    if self._avatar_attempts > 60:
                        logger.warning("城镇头像 60 次未匹配，强制继续")
                        self._avatar_done = True
                        time.sleep(0.5)
                    return
            else:
                logger.warning("未配置城镇头像模板，跳过头像检测")
                self._avatar_done = True
                time.sleep(0.3)
            return

        chain = self._build_chain(blackboard)
        if not chain:
            logger.warning("未配置城镇操作链，强制跳转")
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
            logger.debug("操作链预热 %d/3 (等待UI)", self._chain_warmup)
            time.sleep(self.controller.jitter_delay(0.5))
            return

        tpl, thr = chain[self._chain_index]
        result = find_template(frame, tpl, threshold=thr)
        if result:
            cx, cy = result["center"]
            reaction = self.controller.jitter_delay(0.15)
            time.sleep(reaction)
            self.controller.click_at(cx, cy)
            logger.debug("城镇操作 %d/%d: %s (置信度=%.2f)",
                        self._chain_index + 1, len(chain), tpl, result["confidence"])
            self._chain_index += 1
            self._chain_attempts = 0
            wait = self.controller.jitter_delay(2.0) if self._chain_index == 1 else self.controller.jitter_delay(1.2)
            time.sleep(wait)
        else:
            self._chain_attempts += 1
            if self._chain_attempts % 10 == 1:
                logger.debug("搜索 '%s'... (尝试 %d/45)", tpl, self._chain_attempts)
            if self._chain_attempts % 5 == 0:
                wd = blackboard.get("_watchdog")
                if wd:
                    wd.reset()
            if self._chain_attempts > 45:
                logger.warning("'%s' 45次未匹配，保存调试截图", tpl)
                try:
                    import cv2
                    from utils.logger import DEBUG_DIR
                    time_str = time.strftime("%H%M%S")
                    snap_path = DEBUG_DIR / "debug_town_step_{}_{}.png".format(self._chain_index, time_str)
                    success, encoded = cv2.imencode(".png", frame)
                    if success:
                        with open(snap_path, "wb") as fp:
                            fp.write(encoded)
                        logger.info("调试截图已保存: %s", snap_path)
                except Exception:
                    pass
                self._chain_index += 1
                self._chain_attempts = 0
