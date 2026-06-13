import logging
from pathlib import Path

import cv2
import numpy as np

from config.settings import TEMPLATES_DIR

logger = logging.getLogger(__name__)


class PortalDetector:
    def __init__(self, portal_template=None, template_threshold=0.65):
        self._portal_template = None
        self._portal_kp = None
        self._portal_des = None
        self._orb = cv2.ORB_create(nfeatures=500)
        self._portal_file = None
        self._template_threshold = template_threshold
        self._load_templates(portal_template)

    def _load_templates(self, portal_template):
        self._portal_file = portal_template or "exit_portal.png"
        portal_path = TEMPLATES_DIR / self._portal_file
        if portal_path.exists():
            data = np.fromfile(portal_path, dtype=np.uint8)
            self._portal_template = cv2.imdecode(data, cv2.IMREAD_GRAYSCALE)
            if self._portal_template is not None:
                self._portal_kp, self._portal_des = self._orb.detectAndCompute(
                    self._portal_template, None)
                logger.info("Portal template loaded: %s", portal_path)
        else:
            logger.warning("Portal template not found: %s", portal_path)

    def detect(self, frame):
        if frame is None:
            return None
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        result = {"portal": None}

        portal = None
        if self._portal_des is not None:
            portal = self._match_orb(gray, self._portal_kp, self._portal_des,
                                     self._portal_template.shape)

        if portal is None and self._portal_file:
            portal = self._match_template(frame)

        if portal:
            result["portal"] = portal
            logger.info("Portal detected: center=(%d,%d) size=%d (method: %s)",
                        portal["center"][0], portal["center"][1],
                        portal["size"], portal.get("method", "orb"))

        return result

    def _match_template(self, frame):
        if not self._portal_file:
            return None
        try:
            from recognition.template import find_template
            r = find_template(frame, self._portal_file, threshold=self._template_threshold,
                              scale_range=(0.3, 1.5), scale_steps=13)
            if r:
                return {
                    "center": r["center"],
                    "bbox": r.get("bbox"),
                    "matches": 0,
                    "size": (r["bbox"][2] - r["bbox"][0]) * (r["bbox"][3] - r["bbox"][1]) if r.get("bbox") else 10000,
                    "method": "template",
                }
        except Exception as e:
            logger.warning("Template fallback for portal failed: %s", e)
        return None

    def _match_orb(self, gray, kp, des, template_shape):
        kp_frame, des_frame = self._orb.detectAndCompute(gray, None)
        if des_frame is None or len(kp_frame) < 6:
            return None

        try:
            index = dict(algorithm=6, table_number=6, key_size=12, multi_probe_level=1)
            search = dict(checks=20)
            flann = cv2.FlannBasedMatcher(index, search)
            matches = flann.knnMatch(des, des_frame, k=2)
        except cv2.error:
            bf = cv2.BFMatcher(cv2.NORM_HAMMING)
            matches = bf.knnMatch(des, des_frame, k=2)

        good = []
        for m in matches:
            if len(m) == 2:
                m1, m2 = m
                if m1.distance < 0.80 * m2.distance:
                    good.append(m1)

        if len(good) < 6:
            return None

        src = np.float32([kp[m.queryIdx].pt for m in good]).reshape(-1, 1, 2)
        dst = np.float32([kp_frame[m.trainIdx].pt for m in good]).reshape(-1, 1, 2)
        M, mask = cv2.findHomography(src, dst, cv2.RANSAC, 5.0)

        if M is not None and template_shape is not None:
            h, w = template_shape
            pts = np.float32([[0, 0], [0, h - 1], [w - 1, h - 1], [w - 1, 0]]).reshape(-1, 1, 2)
            box = cv2.perspectiveTransform(pts, M)
            x1 = int(box[:, 0, 0].min())
            y1 = int(box[:, 0, 1].min())
            x2 = int(box[:, 0, 0].max())
            y2 = int(box[:, 0, 1].max())
            return {
                "center": ((x1 + x2) // 2, (y1 + y2) // 2),
                "bbox": (x1, y1, x2, y2),
                "matches": len(good),
                "size": (x2 - x1) * (y2 - y1),
                "method": "orb",
            }

        mean_pt = np.mean([kp_frame[m.trainIdx].pt for m in good], axis=0)
        return {
            "center": (int(mean_pt[0]), int(mean_pt[1])),
            "bbox": None,
            "matches": len(good),
            "size": 0,
            "method": "orb",
        }
