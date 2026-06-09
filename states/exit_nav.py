import time
import logging

from core.fsm import BaseState
from recognition.portal_detector import PortalDetector
from input.controller import Controller
from config.settings import Settings

logger = logging.getLogger(__name__)


class ExitNavState(BaseState):
    def __init__(self, controller):
        self.controller = controller
        self.detector = PortalDetector()
        self._lost_counter = 0
        self._search_angle = 0
        self._max_rotate_frames = 120

    def enter(self, blackboard):
        super().enter(blackboard)
        self._lost_counter = 0
        self._search_angle = 0
        self.controller.release_all()
        logger.debug("State: ExitNav")

    def update(self, blackboard):
        if not blackboard["running"]:
            return
        if blackboard["stuck"]:
            return
        frame = blackboard["current_frame"]
        if frame is None:
            return

        result = self.detector.detect(frame)
        portal = result.get("portal") if result else None
        arrow = result.get("arrow") if result else None

        if portal and portal["size"] > 12000:
            logger.info("Portal very close! Transitioning to exit menu")
            self.controller.release_all()
            blackboard["_fsm"].transition("exit_menu", blackboard)
            return

        if portal:
            self._navigate_to_target(portal["center"], frame)
            self._lost_counter = 0
            return

        if arrow:
            self._navigate_to_target(arrow["center"], frame)
            self._lost_counter = 0
            return

        self._lost_counter += 1
        if self._lost_counter > self._max_rotate_frames:
            logger.info("Lost target, rotating camera")
            self.controller.rotate_camera(angle_deg=30)
            self._lost_counter = 0
            time.sleep(0.3)
        else:
            self.controller.move_direction("forward", duration=0.05)

    def _navigate_to_target(self, center, frame):
        fh, fw = frame.shape[:2]
        cx, cy = center
        offset_x = cx - fw / 2
        deadzone = fw * 0.08
        if offset_x < -deadzone:
            self.controller.move_direction("left", duration=0.06)
        elif offset_x > deadzone:
            self.controller.move_direction("right", duration=0.06)
        else:
            self.controller.move_direction("forward", duration=0.08)
