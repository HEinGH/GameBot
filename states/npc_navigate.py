import time
import logging
import traceback

from core.fsm import BaseState
from recognition.template import find_template
from config.settings import parse_template_ref, parse_template_chain

logger = logging.getLogger(__name__)


class NPCNavigateState(BaseState):

    def __init__(self, controller):
        self.controller = controller
        self._phase = "scan"
        self._last_pos = None
        self._stuck = 0
        self._w_held = False
        self._jumps = 0
        self._last_ts = 0
        self._interval = 0.08
        self._lost = 0
        self._wait = 0
        self._npc_tpl = None
        self._enter_chain = []
        self._enter_chain_idx = 0
        self._enter_attempts = 0
        self._npc_thr = 0.65
        self._move_ck = 0
        self._seek_dir = 0
        self._reversal_count = 0

    def _set_phase(self, name):
        if name != self._phase:
            logger.debug("Phase: %s -> %s", self._phase, name)
        self._phase = name

    def enter(self, blackboard):
        super().enter(blackboard)
        self._set_phase("scan")
        self._last_pos = None
        self._stuck = 0
        self._w_held = False
        self._jumps = 0
        self._last_ts = 0
        self._lost = 0
        self._wait = 0
        self._move_ck = 0
        self._seek_dir = 0
        self._reversal_count = 0
        self._enter_chain = []
        self._enter_chain_idx = 0
        self._enter_attempts = 0
        self._release_w()
        self.controller.release_all()
        preset = blackboard["preset"]
        nav = preset.get("town_nav", {})
        self._npc_tpl, self._npc_thr = parse_template_ref(nav.get("npc_marker_template") or preset.get("npc_template") or "")
        ce = nav.get("confirm_enter_template") or ""
        if isinstance(ce, list):
            self._enter_chain = parse_template_chain(ce)
        else:
            name, thr = parse_template_ref(ce)
            self._enter_chain = [(name, thr)] if name else []
        rect = blackboard.get("_window_rect")
        if not rect or len(rect) != 4:
            title = preset.get("window_title", "")
            rect = self._find_window_rect(title)
            if rect:
                blackboard["_window_rect"] = rect
                logger.info("NPCNavigate: self-found window rect=%s", rect)
        if rect and len(rect) == 4:
            self._gw_l, self._gw_t, self._gw_r, self._gw_b = rect
        else:
            self._gw_l = self._gw_t = self._gw_r = self._gw_b = 0
        self._gw_w = self._gw_r - self._gw_l
        self._gw_h = self._gw_b - self._gw_t
        self._gw_cx = (self._gw_l + self._gw_r) // 2
        self._gw_cy = (self._gw_t + self._gw_b) // 2
        logger.debug("State: NPCNavigate npc=%s enter=%d steps win=(%d,%d-%d,%d) ctr=(%d,%d) %dx%d",
                    self._npc_tpl or "(none)", len(self._enter_chain),
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
        logger.info("Exit: NPCNavigate")

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

    def _wd_reset(self, blackboard):
        try:
            wd = blackboard.get("_watchdog")
            if wd: wd.reset()
        except Exception: pass

    def _find_npc(self, frame):
        r = self._find(self._npc_tpl, frame, threshold=self._npc_thr,
                        scale_range=(0.7, 1.35), scale_steps=7)
        if not r and self._last_pos and self._npc_thr > 0.65:
            r = self._find(self._npc_tpl, frame, threshold=0.65,
                            scale_range=(0.7, 1.35), scale_steps=7)
            if r:
                fw = frame.shape[1] if frame is not None else self._gw_w
                ref_w = self._gw_w if self._gw_w > 0 else fw
                dx = abs(r["center"][0] - self._last_pos[0])
                if dx > ref_w * 0.30:
                    logger.debug("NPC soft-fallback rejected: jump %dpx", dx)
                    return None
                logger.debug("NPC soft-fallback accepted at conf=%.2f", r["confidence"])
        if r and self._last_pos:
            fw = frame.shape[1] if frame is not None else self._gw_w
            ref_w = self._gw_w if self._gw_w > 0 else fw
            dx = abs(r["center"][0] - self._last_pos[0])
            if dx > ref_w * 0.30:
                logger.debug("NPC pos jumped %dpx (%.0f%%), rejecting",
                             dx, dx / max(ref_w, 1) * 100)
                return None
        return r

    def _find(self, tpl, frame, threshold=0.65, scale_range=(0.5, 1.5), scale_steps=11):
        if not tpl or frame is None: return None
        try:
            return find_template(frame, tpl, threshold=threshold,
                                 scale_range=scale_range, scale_steps=scale_steps)
        except Exception as e:
            logger.error("find('%s'): %s", tpl, e)
            return None

    def _rotate(self, angle):
        try: self.controller.rotate_camera(angle)
        except Exception as e: logger.error("rotate(%.0f): %s", angle, e)

    def _do_rotate(self, off, gw_w):
        abs_off = abs(off)
        if abs_off < gw_w * 0.02: return
        if abs_off < gw_w * 0.05: step = 5
        elif abs_off < gw_w * 0.10: step = 10
        elif abs_off < gw_w * 0.20: step = 18
        else: step = 30
        step = max(5, step // (self._reversal_count + 1))
        angle = -step if off < 0 else step
        logger.debug("  rotate off=%d(%.0f%%) deg=%.0f", off, off / gw_w * 100, angle)
        self._rotate(angle)
        time.sleep(0.05)

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
                    logger.info("NPCNavigate: window by title '%s' -> '%s' (%dx%d)",
                                title_keyword, best[1].title, b.width, b.height)
                    return (b.left, b.top, b.left + b.width, b.top + b.height)
                logger.warning("NPCNavigate: no window matching title '%s', falling back to largest", title_keyword)
            best = max(all_visible, key=lambda x: x[0])
            b = best[1].box
            logger.info("NPCNavigate: auto-detected window '%s' (%dx%d)", best[1].title, b.width, b.height)
            return (b.left, b.top, b.left + b.width, b.top + b.height)
        except Exception as e:
            logger.warning("NPCNavigate: _find_window_rect failed: %s", e)
        return None

    def update(self, blackboard):
        try: self._do_update(blackboard)
        except Exception:
            logger.error("NPCNavigate crash:\n%s", traceback.format_exc())
            self._release_w()
            blackboard["_fsm"].transition("domain_loading", blackboard)

    def _do_update(self, blackboard):
        if not blackboard["running"]: self._release_w(); return
        if blackboard["stuck"]: self._release_w(); return

        frame = blackboard["current_frame"]
        if frame is None: return

        now = time.time()
        if now - self._last_ts < self._interval: return
        self._last_ts = now
        self._wd_reset(blackboard)

        gw_w, gw_h = self._gw_w, self._gw_h
        if gw_w <= 0:
            logger.warning("NPCNavigate: _window_rect not available, using frame center")
            gw_w, gw_h = frame.shape[1], frame.shape[0]
            win_cx = gw_w // 2
            win_cy = gw_h // 2
        else:
            win_cx = gw_w // 2
            win_cy = gw_h // 2

        if self._enter_chain and self._enter_chain_idx < len(self._enter_chain):
            tpl_name, tpl_thr = self._enter_chain[self._enter_chain_idx]
            enter = self._find(tpl_name, frame, threshold=tpl_thr)
            if enter:
                c = enter["confidence"]
                if c >= tpl_thr:
                    self._release_w()
                    try: self.controller.click_at(enter["center"][0], enter["center"][1])
                    except Exception: pass
                    self._enter_chain_idx += 1
                    if self._enter_chain_idx >= len(self._enter_chain):
                        logger.info("Confirm enter chain complete (step %d/%d conf=%.2f)",
                                    self._enter_chain_idx, len(self._enter_chain), c)
                        time.sleep(1.0)
                        blackboard["_fsm"].transition("domain_loading", blackboard)
                    else:
                        logger.info("Confirm enter step %d/%d: %s (conf=%.2f)",
                                    self._enter_chain_idx, len(self._enter_chain), tpl_name, c)
                        time.sleep(self.controller.jitter_delay(1.5))
                    return
                else:
                    self._enter_attempts += 1
                    if self._enter_attempts % 5 == 1:
                        logger.debug("Enter sub-threshold (conf=%.2f), attempt %d", c, self._enter_attempts)
            else:
                self._enter_attempts += 1
            if self._enter_attempts > 120:
                logger.warning("Confirm enter timeout after %d attempts, forcing transition",
                               self._enter_attempts)
                self._release_w()
                blackboard["_fsm"].transition("domain_loading", blackboard)
                return
        else:
            self._enter_attempts = 0

        if self._enter_chain_idx > 0:
            return

        if self._phase == "scan":
            self._do_scan(frame, gw_h, gw_w, win_cx, win_cy)
        elif self._phase == "seek":
            self._do_seek(frame, gw_h, gw_w, win_cx, win_cy)
        elif self._phase == "center":
            self._do_center(frame, gw_h, gw_w, win_cx, win_cy)
        elif self._phase == "move":
            self._do_move(frame, gw_w, win_cx, win_cy)
        elif self._phase == "recover":
            self._do_recover(blackboard)
        elif self._phase == "enter":
            self._do_enter(blackboard)

    def _do_scan(self, frame, gh, gw, win_cx, win_cy):
        self._reversal_count = 0
        if not self._npc_tpl:
            self._release_w()
            blackboard["_fsm"].transition("domain_loading", blackboard)
            return
        icon = self._find_npc(frame)
        if icon:
            self._last_pos = icon["center"]
            self._lost = 0; self._wait = 0
            x, y = self._last_pos
            rel_x = x - self._gw_l
            rel_y = y - self._gw_t
            off_x = rel_x - win_cx
            off_y = rel_y - win_cy
            logger.debug("Icon at (%d,%d) rel=(%d,%d) off=(%.0f%%,%.0f%%)",
                        x, y, rel_x, rel_y,
                        off_x / gw * 100, off_y / gw * 100)
            if rel_y > win_cy + gh * 0.25:
                logger.debug("  clearly lower -> seek dir=%s", "left" if rel_x < win_cx else "right")
                self._seek_dir = -1 if rel_x < win_cx else 1
                self._set_phase("seek")
            else:
                self._set_phase("center")
        else:
            self._lost += 1
            if self._lost % 5 == 1: logger.debug("Scanning... (%d/80)", self._lost)
            if self._lost > 80:
                logger.warning("Icon not found, direct entry")
                self._set_phase("enter"); self._wait = 0

    def _do_seek(self, frame, gh, gw, win_cx, win_cy):
        icon = self._find_npc(frame)
        if icon:
            self._lost = 0
            self._last_pos = icon["center"]
            x, y = icon["center"]
            rel_x = x - self._gw_l
            rel_y = y - self._gw_t
            h_off = rel_x - win_cx
            h_ratio = abs(h_off) / gw if gw > 0 else 0
            v_ratio = rel_y / gh if gh > 0 else 0
            logger.debug("Seek: icon off=(%.0f%%,%.0f%%) h_ratio=%.2f v_ratio=%.2f",
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

            self._seek_dir = -1 if h_off < 0 else 1
            step = max(5, step // (self._reversal_count + 1))
            angle = self._seek_dir * max(8, step)
            logger.debug("  seek rotate %d deg (step=%d h=%.2f v=%.2f)", angle, step, h_ratio, v_ratio)
            self._rotate(angle)
        else:
            self._lost += 1
            if self._lost == 6:
                logger.debug("  seek lost, reversing")
                self._seek_dir = -self._seek_dir; self._lost = 0
                self._reversal_count += 1
            elif self._lost > 20:
                self._set_phase("enter"); self._wait = 0; return

        time.sleep(0.04)

    def _do_center(self, frame, gh, gw, win_cx, win_cy):
        if self._last_pos is None: self._set_phase("scan"); self._lost = 0; return
        icon = self._find_npc(frame)
        if icon:
            self._last_pos = icon["center"]; self._lost = 0
            x, y = icon["center"]
            rel_y = y - self._gw_t
            if rel_y > win_cy + gh * 0.15:
                rel_x = x - self._gw_l
                self._seek_dir = -1 if rel_x < win_cx else 1
                self._set_phase("seek"); return
        else:
            self._lost += 1
            if self._lost == 5:
                if self._last_pos and self._last_pos[0] - self._gw_l > win_cx: self._rotate(-8)
                else: self._rotate(8)
                time.sleep(0.1)
            if self._lost > 30: self._set_phase("scan"); self._lost = 0; return
            return
        if self._last_pos is None: self._set_phase("scan"); return

        rel_x = self._last_pos[0] - self._gw_l
        off = rel_x - win_cx
        if abs(off) < gw * 0.025:
            rel_y = self._last_pos[1] - self._gw_t
            if rel_y > win_cy + gh * 0.20:
                self._seek_dir = -1 if rel_x < win_cx else 1
                self._set_phase("seek"); return
            logger.debug("  centered (off=%d %.1f%%), moving", off, off / gw * 100)
            self._set_phase("move"); self._stuck = 0; self._move_ck = 0; self._hold_w(); return
        if abs(off) < gw * 0.04:
            logger.debug("  near center (%.0f%%), move + fine-tune", off / gw * 100)
            self._set_phase("move"); self._stuck = 0; self._hold_w(); self._move_ck = 0; return
        self._do_rotate(off, gw)

    def _do_move(self, frame, gw, win_cx, win_cy):
        self._move_ck += 1
        if self._move_ck % 2 != 0: return
        if self._last_pos is None: self._release_w(); self._set_phase("scan"); self._lost = 0; return
        icon = self._find_npc(frame)
        if icon:
            cur = icon["center"]
            if self._last_pos:
                d = abs(cur[0] - self._last_pos[0]) + abs(cur[1] - self._last_pos[1])
                if d < 8:
                    self._stuck += 1
                    if self._stuck > 15:
                        logger.warning("Stuck %d", self._stuck)
                        self._set_phase("recover"); self._stuck = 0
                        self._release_w(); return
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
            if self._lost > 20:
                logger.debug("Icon lost, near NPC?")
                self._release_w()
                self._set_phase("enter"); self._wait = 0; self._lost = 0; return

    def _do_recover(self, blackboard):
        if self._jumps == 0:
            logger.info("Recover: jump")
            try: self.controller.tap_key("space", duration=0.1, delay_after=1.0)
            except Exception: pass
            self._jumps += 1; self._set_phase("move"); self._hold_w()
        elif self._jumps <= 2:
            k = "a" if self._jumps == 1 else "d"
            logger.info("Recover: strafe %s", k)
            try: self.controller.tap_key(k, duration=0.3, delay_after=0.5)
            except Exception: pass
            self._jumps += 1; self._set_phase("move"); self._hold_w()
        else:
            logger.warning("All recovery failed")
            self._release_w()
            try:
                self.controller.tap_key("esc", duration=0.1, delay_after=1.0)
                self.controller.tap_key("esc", duration=0.1, delay_after=1.0)
            except Exception: pass
            blackboard["_fsm"].transition("town_exit", blackboard)

    def _do_enter(self, blackboard):
        self._wait += 1
        if self._wait % 5 == 1: logger.info("Waiting confirm... (%d/40)", self._wait)
        if self._wait > 40:
            logger.warning("No confirm, forcing domain")
            self._release_w()
            blackboard["_fsm"].transition("domain_loading", blackboard)
