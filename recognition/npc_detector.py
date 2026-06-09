import logging
from pathlib import Path

import cv2
import numpy as np

from config.settings import TEMPLATES_DIR

logger = logging.getLogger(__name__)


class NPCDetector:
    def __init__(self, template_name="npc_icon.png", min_match_count=15):
        self.template_name = template_name
        self.min_match_count = min_match_count
        self._orb = cv2.ORB_create(nfeatures=1000)
        self._template = None
        self._template_kp = None
        self._template_des = None
        self._load_template()

    def _load_template(self):
        path = TEMPLATES_DIR / self.template_name
        if not path.exists():
            logger.warning("NPC template not found: %s. Detection disabled.", path)
            return
        data = np.fromfile(path, dtype=np.uint8)
        self._template = cv2.imdecode(data, cv2.IMREAD_GRAYSCALE)
        if self._template is None:
            logger.error("Failed to load NPC template: %s", path)
            return
        self._template_kp, self._template_des = self._orb.detectAndCompute(self._template, None)
        logger.info("NPC template loaded: %s (features: %d)", path,
                    0 if self._template_des is None else len(self._template_kp))

    def detect(self, frame, roi=None):
        if self._template_des is None or frame is None:
            return None
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        if roi:
            x1, y1, x2, y2 = roi
            gray = gray[y1:y2, x1:x2]
            offset_x, offset_y = x1, y1
        else:
            offset_x, offset_y = 0, 0
        kp, des = self._orb.detectAndCompute(gray, None)
        if des is None or len(kp) < self.min_match_count:
            return None
        FLANN_INDEX_LSH = 6
        index_params = dict(algorithm=FLANN_INDEX_LSH,
                            table_number=6, key_size=12, multi_probe_level=1)
        search_params = dict(checks=30)
        try:
            flann = cv2.FlannBasedMatcher(index_params, search_params)
            matches = flann.knnMatch(self._template_des, des, k=2)
        except cv2.error:
            bf = cv2.BFMatcher(cv2.NORM_HAMMING)
            matches = bf.knnMatch(self._template_des, des, k=2)
        good = []
        for m in matches:
            if len(m) == 2:
                m1, m2 = m
                if m1.distance < 0.75 * m2.distance:
                    good.append(m1)
        if len(good) >= self.min_match_count:
            src_pts = np.float32([self._template_kp[m.queryIdx].pt for m in good]).reshape(-1, 1, 2)
            dst_pts = np.float32([kp[m.trainIdx].pt for m in good]).reshape(-1, 1, 2)
            M, mask = cv2.findHomography(src_pts, dst_pts, cv2.RANSAC, 5.0)
            if M is not None:
                h, w = self._template.shape
                pts = np.float32([[0, 0], [0, h - 1], [w - 1, h - 1], [w - 1, 0]]).reshape(-1, 1, 2)
                dst = cv2.perspectiveTransform(pts, M)
                bx1 = int(dst[:, 0, 0].min()) + offset_x
                by1 = int(dst[:, 0, 1].min()) + offset_y
                bx2 = int(dst[:, 0, 0].max()) + offset_x
                by2 = int(dst[:, 0, 1].max()) + offset_y
                return {
                    "center": ((bx1 + bx2) // 2, (by1 + by2) // 2),
                    "bbox": (bx1, by1, bx2, by2),
                    "matches": len(good),
                    "size": (bx2 - bx1) * (by2 - by1),
                }
        if len(good) >= self.min_match_count // 2:
            mean_pt = np.mean([kp[m.trainIdx].pt for m in good], axis=0)
            return {
                "center": (int(mean_pt[0]) + offset_x, int(mean_pt[1]) + offset_y),
                "bbox": None,
                "matches": len(good),
                "size": 0,
            }
        return None
