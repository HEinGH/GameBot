import time
import logging
import cv2
import numpy as np

from utils.logger import DEBUG_DIR

logger = logging.getLogger(__name__)


class Watchdog:
    def __init__(self, threshold_sec=15, ssim_threshold=0.95):
        self.threshold_sec = threshold_sec
        self.ssim_threshold = ssim_threshold
        self._last_active = time.time()
        self._last_frame = None
        self._stuck_count = 0

    def update(self, frame, blackboard):
        if frame is None:
            return
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        if self._last_frame is not None:
            h, w = self._last_frame.shape
            gray_resized = cv2.resize(gray, (w, h))
            score = self._similarity_score(self._last_frame, gray_resized)
            if score > self.ssim_threshold:
                elapsed = time.time() - self._last_active
                if elapsed > self.threshold_sec:
                    self._stuck_count += 1
                    logger.warning("卡死检测 #%d | 相似度=%.3f", self._stuck_count, score)
                    time_str = time.strftime("%H%M%S")
                    snap_path = DEBUG_DIR / f"stuck_{time_str}_{self._stuck_count}.png"
                    success, encoded = cv2.imencode(".png", frame)
                    if success:
                        with open(snap_path, "wb") as fp:
                            fp.write(encoded)
                    blackboard["stuck"] = True
                    blackboard["stuck_count"] = self._stuck_count
            else:
                self._last_active = time.time()
                blackboard["stuck"] = False
        else:
            self._last_active = time.time()
        self._last_frame = gray

    def reset(self):
        self._last_active = time.time()
        self._last_frame = None

    def _similarity_score(self, img1, img2):
        diff = cv2.absdiff(img1, img2).astype(np.float32)
        mse = np.mean(diff ** 2)
        if mse < 1.0:
            return 1.0
        return 1.0 - np.sqrt(mse) / 255.0
