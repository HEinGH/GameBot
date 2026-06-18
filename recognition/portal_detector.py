import logging

from recognition.template import find_template

logger = logging.getLogger(__name__)


class PortalDetector:
    def __init__(self, portal_template=None, template_threshold=0.65):
        self._portal_file = portal_template or "exit_portal.png"
        self._template_threshold = template_threshold

    def detect(self, frame):
        if frame is None:
            return None
        result = {"portal": None}
        portal = self._match_template(frame)
        if portal:
            result["portal"] = portal
            logger.info("识别到副本出口: 位置(%d,%d) 大小=%d",
                        portal["center"][0], portal["center"][1], portal["size"])
        return result

    def _match_template(self, frame):
        if not self._portal_file:
            return None
        r = find_template(frame, self._portal_file, threshold=self._template_threshold,
                          scale_range=(0.7, 1.35), scale_steps=11)
        if r:
            return {
                "center": r["center"],
                "bbox": r.get("bbox"),
                "size": (r["bbox"][2] - r["bbox"][0]) * (r["bbox"][3] - r["bbox"][1]) if r.get("bbox") else 10000,
            }
        return None
