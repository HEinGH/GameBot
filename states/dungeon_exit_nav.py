import time
import logging
import traceback

from core.fsm import BaseState
from recognition.portal_detector import PortalDetector
from recognition.template import find_template
from config.settings import parse_template_ref

logger = logging.getLogger(__name__)


class DungeonExitNavState(BaseState):

    def __init__(self, controller):
        self.controller = controller
        self.detector = None
        self._phase = "scan"
        self._last_pos = None
        self._w_held = False
        self._lost = 0
        self._stuck = 0
        self._move_ck = 0
        self._search_dir = 1
        self._search_skips = 0
        self._settle_frames = 10
        self._last_ts = 0
        self._interval = 0.08
        self._button_attempts = 0
        self._near_exit = False

    def _set_phase(self, name):
        if name != self._phase:
            logger.debug("Phase: %s -> %s", self._phase, name)
        self._phase = name

    def _find_window_rect(self, title_keyword=""):
        try:
            import pywinctl as pwc
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
            if title_keyword:
                matches = [(a, w) for a, w in all_visible if title_keyword in (w.title or "")]
                if matches:
                    best = max(matches, key=lambda x: x[0])
                    b = best[1].box
                    logger.info("DungeonExitNav: window by title '%s' -> '%s' (%dx%d)",
                                title_keyword, best[1].title, b.width, b.height)
                    return (b.left, b.top, b.left + b.width, b.top + b.height)
                logger.warning("DungeonExitNav: no window matching title '%s', falling back to largest", title_keyword)
            best = max(all_visible, key=lambda x: x[0])
            b = best[1].box
            logger.info("DungeonExitNav: auto-detected window '%s' (%dx%d)", best[1].title, b.width, b.height)
            return (b.left, b.top, b.left + b.width, b.top + b.height)
        except Exception as e:
            logger.warning("DungeonExitNav: _find_window_rect failed: %s", e)
        return None

    def enter(self, blackboard):
        super().enter(blackboard)
        preset = blackboard.get("preset", {})
        portal_tpl = preset.get("portal_template") or ""
        portal_name, portal_thr = parse_template_ref(portal_tpl)
        self.detector = PortalDetector(
            portal_template=portal_name if portal_name else None,
            template_threshold=portal_thr,
        )
        self._set_phase("scan")
        self._last_pos = None
        self._w_held = False
        self._lost = 0
        self._stuck = 0
        self._move_ck = 0
        self._search_dir = 1
        self._search_skips = 0
        self._settle_frames = 10
        self._last_ts = 0
        self._click_stage = 0
        self._click_wait = 0
        self._button_attempts = 0
        self._confirm_attempts = 0
        self._near_exit = False
        rect = blackboard.get("_window_rect")
        if not rect or len(rect) != 4:
            title = preset.get("window_title", "")
            rect = self._find_window_rect(title)
            if rect:
                blackboard["_window_rect"] = rect
                logger.info("DungeonExitNav: self-found window rect=%s", rect)
        if rect and len(rect) == 4:
            self._gw_l, self._gw_t, self._gw_r, self._gw_b = rect
        else:
            self._gw_l = self._gw_t = self._gw_r = self._gw_b = 0
        self._gw_w = self._gw_r - self._gw_l
        self._gw_h = self._gw_b - self._gw_t
        self._gw_cx = (self._gw_l + self._gw_r) // 2
        self._gw_cy = (self._gw_t + self._gw_b) // 2
        self.controller.release_all()
        logger.debug("State: DungeonExitNav win=(%d,%d-%d,%d) ctr=(%d,%d) %dx%d",
                    self._gw_l, self._gw_t, self._gw_r, self._gw_b,
                    self._gw_cx, self._gw_cy, self._gw_w, self._gw_h)

    def exit(self, blackboard):
        self._release_w()
        self.controller.release_all()
        import pydirectinput
        try:
            pydirectinput.mouseUp(button="left")
            pydirectinput.mouseUp(button="right")
        except Exception:
            pass
        logger.info("Exit: DungeonExitNav")

    def _release_w(self):
        if self._w_held:
            self._w_held = False
            try: self.controller.key_up("w")
            except Exception: pass

    def _hold_w(self):
        if not self._w_held:
            self._w_held = True
            try: self.controller.key_down("w")
            except Exception: pass

    def _rotate(self, angle):
        try: self.controller.rotate_camera_free(angle)
        except Exception as e: logger.error("rotate_free(%.0f): %s", angle, e)

    def _do_rotate(self, off, gw_w):
        abs_off = abs(off)
        if abs_off < gw_w * 0.02: return
        if abs_off < gw_w * 0.05: step = 5
        elif abs_off < gw_w * 0.10: step = 10
        elif abs_off < gw_w * 0.20: step = 18
        else: step = 30
        angle = -step if off < 0 else step
        logger.debug("  rotate off=%d(%.0f%%) deg=%.0f", off, off / gw_w * 100, angle)
        self._rotate(angle)
        time.sleep(0.05)

    def _find_portal(self, frame):
        result = self.detector.detect(frame)
        return result.get("portal") if result else None

    def _find_button(self, frame, blackboard, threshold=0.65):
        preset = blackboard["preset"]
        if not preset: return None
        char_index = blackboard["current_character_index"]
        chars = preset.get("characters", [])
        char_config = chars[char_index] if char_index < len(chars) else {}
        runs_done = blackboard.get("domain_run_count", 0)
        domain_runs = char_config.get("runs", 1)
        all_done = runs_done + 1 >= domain_runs
        rechallenge_name, rechallenge_thr = parse_template_ref(
            preset.get("rechallenge_template")
            or preset.get("town_nav", {}).get("rechallenge_template"))
        exit_name, exit_thr = parse_template_ref(
            preset.get("exit_domain_template")
            or preset.get("town_nav", {}).get("exit_domain_template"))
        target_name = exit_name if all_done else rechallenge_name
        target_thr = exit_thr if all_done else rechallenge_thr
        if not target_name: return None
        r = find_template(frame, target_name, threshold=target_thr)
        return r

    def _wd_reset(self, blackboard):
        try:
            wd = blackboard.get("_watchdog")
            if wd: wd.reset()
        except Exception: pass

    def update(self, blackboard):
        try: self._do_update(blackboard)
        except Exception:
            logger.error("DungeonExitNav crash:\n%s", traceback.format_exc())
            self._release_w()
            blackboard["_fsm"].transition("domain_loading", blackboard)

    def _do_update(self, blackboard):
        if not blackboard["running"]: self._release_w(); return
        if blackboard["stuck"]: self._release_w(); return

        if self._settle_frames > 0:
            self._settle_frames -= 1
            return
        self._wd_reset(blackboard)

        now = time.time()
        if now - self._last_ts < self._interval: return
        self._last_ts = now

        frame = blackboard["current_frame"]
        if frame is None: return

        if self._phase != "buttons":
            btn = self._find_button(frame, blackboard, threshold=0.65)
            if btn:
                c = btn["confidence"]
                if c >= 0.75 or (c >= 0.50 and self._near_exit):
                    logger.info("Exit button detected (conf=%.3f near_exit=%s), switching to buttons",
                                c, self._near_exit)
                    self._release_w()
                    self.controller.release_all()
                    self._set_phase("buttons")
                    self._click_stage = 0
                    self._click_wait = 0
                    self._button_attempts = 0
                    return

        if self._phase == "buttons":
            self._do_buttons(frame, blackboard)
            return

        gw_w, gw_h = self._gw_w, self._gw_h
        if gw_w <= 0:
            logger.warning("DungeonExitNav: _window_rect not available, using frame center")
            gw_w, gw_h = frame.shape[1], frame.shape[0]
            win_cx = gw_w // 2
            win_cy = gw_h // 2
        else:
            win_cx = gw_w // 2
            win_cy = gw_h // 2

        if self._phase == "scan":
            self._do_scan(frame, gw_h, gw_w, win_cx, win_cy, blackboard)
        elif self._phase == "seek":
            self._do_seek(frame, gw_h, gw_w, win_cx, win_cy)
        elif self._phase == "center":
            self._do_center(frame, gw_h, gw_w, win_cx, win_cy)
        elif self._phase == "move":
            self._do_move(frame, gw_w, win_cx, win_cy, blackboard)

    def _do_scan(self, frame, gh, gw, win_cx, win_cy, blackboard):
        portal = self._find_portal(frame)

        if portal:
            size = portal["size"]
            self._last_pos = portal["center"]
            self._lost = 0
            if size > 50000:
                logger.debug("Portal close (size=%d), switch to buttons", size)
                self._release_w()
                self.controller.release_all()
                self._set_phase("buttons")
                self._click_stage = 0
                self._click_wait = 0
                self._button_attempts = 0
                return
            x, y = self._last_pos
            rel_x = x - self._gw_l
            rel_y = y - self._gw_t
            logger.debug("Portal at (%d,%d) rel=(%d,%d) off=(%.0f%%,%.0f%%)",
                        x, y, rel_x, rel_y,
                        (rel_x - win_cx) / gw * 100, (rel_y - win_cy) / gw * 100)
            if rel_y > win_cy + gh * 0.25:
                self._search_dir = -1 if rel_x < win_cx else 1
                self._set_phase("seek")
            else:
                self._set_phase("center")
            return

        self._lost += 1
        if self._lost % 5 == 1: logger.debug("Scanning... (%d/80)", self._lost)
        if self._lost > 80:
            logger.warning("Portal not found, direct buttons")
            self._release_w()
            self._set_phase("buttons")
            self._click_stage = 0
            self._click_wait = 0
            self._button_attempts = 0

    def _do_seek(self, frame, gh, gw, win_cx, win_cy):
        portal = self._find_portal(frame)
        if portal:
            self._lost = 0
            self._last_pos = portal["center"]
            x, y = portal["center"]
            rel_x = x - self._gw_l
            rel_y = y - self._gw_t
            h_off = rel_x - win_cx
            h_ratio = abs(h_off) / gw if gw > 0 else 0
            v_ratio = rel_y / gh if gh > 0 else 0
            logger.debug("Seek: portal off=(%.0f%%,%.0f%%) h_ratio=%.2f v_ratio=%.2f",
                        (rel_x - win_cx) / gw * 100 if gw else 0,
                        (rel_y - win_cy) / gw * 100 if gw else 0, h_ratio, v_ratio)

            if h_ratio < 0.05 and v_ratio < 0.66:
                logger.debug("  centered, switching to center")
                self._set_phase("center"); return

            if h_ratio < 0.05:
                step = 5
            elif h_ratio < 0.10:
                step = 10
            elif h_ratio < 0.20:
                step = 18
            elif h_ratio < 0.30:
                step = 30
            elif h_ratio < 0.50:
                step = 50
            else:
                step = 65

            if v_ratio < 0.33:
                step = int(step * 0.7)
            elif v_ratio > 0.66:
                step = int(step * 1.3)

            self._search_dir = -1 if h_off < 0 else 1
            angle = self._search_dir * max(8, step)
            logger.debug("  seek rotate %d deg (step=%d h=%.2f v=%.2f)", angle, step, h_ratio, v_ratio)
            self._rotate(angle)
        else:
            self._lost += 1
            if self._lost == 6:
                self._search_dir = -self._search_dir; self._lost = 0
            elif self._lost > 20:
                self._set_phase("buttons"); self._click_stage = 0; self._click_wait = 0; self._button_attempts = 0; return

        time.sleep(0.08)

    def _do_center(self, frame, gh, gw, win_cx, win_cy):
        if self._last_pos is None: self._set_phase("scan"); self._lost = 0; return
        portal = self._find_portal(frame)
        if portal:
            self._last_pos = portal["center"]; self._lost = 0
            x, y = portal["center"]
            rel_y = y - self._gw_t
            if rel_y > win_cy + gh * 0.15:
                rel_x = x - self._gw_l
                self._search_dir = -1 if rel_x < win_cx else 1
                self._set_phase("seek"); return
        else:
            self._lost += 1
            if self._lost == 5:
                if self._last_pos and self._last_pos[0] - self._gw_l > win_cx: self._rotate(-8)
                else: self._rotate(8)
                time.sleep(0.05)
            if self._lost > 30: self._set_phase("scan"); self._lost = 0; return
            return
        if self._last_pos is None: self._set_phase("scan"); return

        rel_x = self._last_pos[0] - self._gw_l
        off = rel_x - win_cx
        if abs(off) < gw * 0.025:
            rel_y = self._last_pos[1] - self._gw_t
            if rel_y > win_cy + gh * 0.20:
                self._search_dir = -1 if rel_x < win_cx else 1
                self._set_phase("seek"); return
            logger.debug("  centered (off=%d %.1f%%), holding W", off, off / gw * 100)
            self._set_phase("move"); self._stuck = 0; self._move_ck = 0; self._hold_w(); return
        if abs(off) < gw * 0.04:
            logger.debug("  near center (%.0f%%), move + fine-tune", off / gw * 100)
            self._set_phase("move"); self._stuck = 0; self._hold_w(); self._move_ck = 0; return
        self._do_rotate(off, gw)

    def _do_move(self, frame, gw, win_cx, win_cy, blackboard):
        self._move_ck += 1
        if self._move_ck % 2 != 0: return
        if self._last_pos is None: self._release_w(); self._set_phase("scan"); self._lost = 0; return

        portal = self._find_portal(frame)
        if portal:
            cur = portal["center"]
            size = portal["size"]
            if size > 50000:
                logger.debug("  portal close (size=%d)", size)
                self._release_w()
                self.controller.release_all()
                self._set_phase("buttons")
                self._click_stage = 0
                self._click_wait = 0
                self._button_attempts = 0
                return
            if self._last_pos:
                d = abs(cur[0] - self._last_pos[0]) + abs(cur[1] - self._last_pos[1])
                if d < 8:
                    self._stuck += 1
                    if self._stuck > 15:
                        logger.warning("Stuck %d", self._stuck)
                        self._release_w()
                        self.controller.tap_key("space", duration=0.1, delay_after=1.0)
                        self._stuck = 0
                        self._set_phase("move"); self._hold_w(); return
                else:
                    self._stuck = max(0, self._stuck - 2)
            self._last_pos = cur; self._lost = 0
            rel_x = cur[0] - self._gw_l
            off = rel_x - win_cx
            if abs(off) > gw * 0.06:
                logger.debug("  drift %.0f%% re-center", off / gw * 100)
                self._release_w()
                self._set_phase("center"); return
            if abs(off) > gw * 0.02:
                self._do_rotate(off, gw)
        else:
            self._lost += 1
            if self._lost > 10:
                self._near_exit = True
            if self._lost > 20:
                logger.info("Portal lost, near exit?")
                self._release_w()
                self._set_phase("buttons"); self._click_stage = 0; self._click_wait = 0; self._button_attempts = 0; return

    def _do_buttons(self, frame, blackboard):
        preset = blackboard["preset"]
        if preset is None: return

        char_index = blackboard["current_character_index"]
        chars = preset.get("characters", [])
        char_config = chars[char_index] if char_index < len(chars) else {}
        domain_runs = char_config.get("runs", 1)
        runs_done = blackboard.get("domain_run_count", 0)
        all_done = runs_done + 1 >= domain_runs

        rechallenge_name, rechallenge_thr = parse_template_ref(
            preset.get("rechallenge_template")
            or preset.get("town_nav", {}).get("rechallenge_template"))
        exit_name, exit_thr = parse_template_ref(
            preset.get("exit_domain_template")
            or preset.get("town_nav", {}).get("exit_domain_template"))
        confirm_name, confirm_thr = parse_template_ref(
            preset.get("confirm_button_template")
            or preset.get("town_exit", {}).get("confirm_exit_template")
            or "确认.png")

        if self._click_wait > 0:
            self._click_wait -= 1
            return

        if self._click_stage == 0:
            target_name = exit_name if all_done else rechallenge_name
            target_thr = exit_thr if all_done else rechallenge_thr
            if not target_name:
                self._force_transition(blackboard, all_done, runs_done)
                return
            r = find_template(frame, target_name, threshold=target_thr)
            if r:
                cx, cy = r["center"]
                self.controller.click_at(cx, cy)
                action = "exit" if all_done else "re-challenge"
                logger.info("Clicked %s (run %d/%d)", action, runs_done + 1, domain_runs)
                self._click_stage = 1
                self._click_wait = 15
                self._confirm_attempts = 20
                self._button_attempts = 0
            else:
                self._button_attempts += 1
                if self._button_attempts > 60:
                    logger.warning("Button not found after 60 attempts, forcing transition")
                    self._force_transition(blackboard, all_done, runs_done)
                return

        if self._click_stage == 1:
            r = find_template(frame, confirm_name, threshold=confirm_thr)
            if r:
                cx, cy = r["center"]
                self.controller.click_at(cx, cy)
                logger.info("Clicked confirm")
                self._click_stage = 2
                self._click_wait = 15
                return
            avatar_tpl = char_config.get("avatar_template")
            avatar_name, avatar_thr = parse_template_ref(avatar_tpl)
            if avatar_name:
                ar = find_template(frame, avatar_name, threshold=avatar_thr)
                if ar:
                    logger.info("Avatar detected in town after exit (conf=%.2f), proceeding",
                                ar["confidence"])
                    self._click_stage = 2
                    self._click_wait = 15
                    return
            self._confirm_attempts -= 1
            if self._confirm_attempts <= 0:
                logger.warning("Confirm button not found after retries, restarting button flow")
                self._click_stage = 0
            return

        if self._click_stage == 2:
            if all_done:
                logger.info("Exiting domain")
                blackboard["_fsm"].transition("map_loading", blackboard)
            else:
                blackboard["domain_run_count"] = runs_done + 1
                logger.info("Re-enter domain (run %d/%d)", runs_done + 1, domain_runs)
                blackboard["_fsm"].transition("domain_loading", blackboard)

    def _force_transition(self, blackboard, all_done, runs_done):
        if all_done:
            blackboard["_fsm"].transition("map_loading", blackboard)
        else:
            blackboard["domain_run_count"] = runs_done + 1
            blackboard["_fsm"].transition("domain_loading", blackboard)
