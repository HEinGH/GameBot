import logging

import cv2
import numpy as np

logger = logging.getLogger(__name__)


class LineFollower:
    def __init__(self, hsv_lower=(100, 140, 140), hsv_upper=(130, 255, 255)):
        self.hsv_lower = np.array(hsv_lower, dtype=np.uint8)
        self.hsv_upper = np.array(hsv_upper, dtype=np.uint8)
        self._no_line_frames = 0

    def set_hsv_range(self, lower, upper):
        self.hsv_lower = np.array(lower, dtype=np.uint8)
        self.hsv_upper = np.array(upper, dtype=np.uint8)

    def analyze(self, frame, screen_width, screen_height):
        if frame is None:
            return {"direction": "stop", "centered": False, "found": False}

        h, w = frame.shape[:2]
        roi_y1 = int(h * 0.55)
        roi_y2 = int(h * 0.90)
        roi_x1 = int(w * 0.15)
        roi_x2 = int(w * 0.85)
        roi = frame[roi_y1:roi_y2, roi_x1:roi_x2]
        hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
        mask = cv2.inRange(hsv, self.hsv_lower, self.hsv_upper)
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7))
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not contours:
            self._no_line_frames += 1
            return {"direction": "forward", "centered": True, "found": False,
                    "no_line_frames": self._no_line_frames}

        self._no_line_frames = 0
        largest = max(contours, key=cv2.contourArea)
        M = cv2.moments(largest)
        if M["m00"] == 0:
            return {"direction": "forward", "centered": True, "found": False}
        cx = int(M["m10"] / M["m00"])
        cy = int(M["m01"] / M["m00"])
        roi_center_x = roi.shape[1] / 2
        offset = cx - roi_center_x
        norm_offset = offset / roi_center_x
        deadzone = 0.1
        if norm_offset < -deadzone:
            direction = "left"
        elif norm_offset > deadzone:
            direction = "right"
        else:
            direction = "forward"
        return {
            "direction": direction,
            "centered": abs(norm_offset) < deadzone,
            "offset": norm_offset,
            "found": True,
            "cx": cx + roi_x1,
            "cy": cy + roi_y1,
            "area": cv2.contourArea(largest),
            "no_line_frames": 0,
        }
