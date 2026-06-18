import time
import logging

from core.fsm import BaseState
from recognition.template import find_template
from config.settings import parse_template_ref

logger = logging.getLogger(__name__)

_MAX_ESC_PER_CYCLE = 4
_MAX_CYCLES = 3
_MAX_STUCK_COUNT = 3


class StuckRecoveryState(BaseState):
    def __init__(self, controller):
        self.controller = controller
        self._settings_tpl = None
        self._settings_thr = 0.65
        self._step = 0
        self._cycle = 0

    def enter(self, blackboard):
        super().enter(blackboard)
        stuck_count = blackboard.get("stuck_count", 0)
        logger.warning("进入卡死恢复 (累计卡死=%d次)", stuck_count)
        blackboard["stuck"] = False
        if stuck_count >= _MAX_STUCK_COUNT:
            logger.error("连续卡死次数达到上限 (%d/%d)，停止运行",
                         stuck_count, _MAX_STUCK_COUNT)
            blackboard["running"] = False
            return
        self._step = 0
        self._cycle = 0
        self.controller.release_all()
        time.sleep(0.5)
        preset = blackboard.get("preset", {})
        tpl = (preset.get("town_exit", {}).get("settings_template")
               or preset.get("settings_template"))
        self._settings_tpl, self._settings_thr = parse_template_ref(tpl)

    def update(self, blackboard):
        if not blackboard["running"]:
            return
        if self._step >= _MAX_ESC_PER_CYCLE:
            self._cycle += 1
            if self._cycle >= _MAX_CYCLES:
                logger.error("所有恢复周期用尽 (%d×%d次ESC)，停止运行",
                             _MAX_CYCLES, _MAX_ESC_PER_CYCLE)
                blackboard["running"] = False
                return
            logger.info("恢复周期 %d/%d 失败，冷却5秒",
                        self._cycle, _MAX_CYCLES)
            time.sleep(5.0)
            self._step = 0
            return
        self.controller.tap_key("esc", duration=0.15, delay_after=2.0)
        capture = blackboard.get("_capture")
        frame = capture.frame if capture else blackboard.get("current_frame")
        if frame is not None and self._settings_tpl:
            r = find_template(frame, self._settings_tpl, threshold=self._settings_thr)
            if r:
                logger.info("识别到设置按钮 (置信度=%.2f) ESC第%d/%d次 周期%d，进入退出流程",
                            r["confidence"], self._step + 1, _MAX_ESC_PER_CYCLE, self._cycle + 1)
                blackboard["_fsm"].transition("town_exit", blackboard)
                return
        self._step += 1
        logger.info("ESC第%d/%d次 周期%d/%d，设置按钮未匹配",
                    self._step, _MAX_ESC_PER_CYCLE, self._cycle + 1, _MAX_CYCLES)
        time.sleep(1.0)
