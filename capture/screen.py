import time
import logging
import threading
from threading import Thread, Lock

import numpy as np

logger = logging.getLogger(__name__)


class ScreenCapture:
    _instance = None
    _singleton_lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._singleton_lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        self._capture = None
        self._method = None
        self._latest_frame = None
        self._timestamp = 0.0
        self._lock = Lock()
        self._running = False
        self._thread = None
        self._fps_limit = 30
        self._monitor_index = 0

    def start(self, method="auto", fps_limit=30, monitor=0,
              device_idx=0, output_idx=None):
        self._fps_limit = fps_limit
        self._monitor_index = monitor
        if self._running:
            return

        if method == "auto":
            try:
                import dxcam
                logging.getLogger("dxcam").setLevel(logging.WARNING)
                create_kw = {"output_color": "BGR"}
                if output_idx is not None:
                    create_kw["device_idx"] = device_idx
                    create_kw["output_idx"] = output_idx
                self._capture = dxcam.create(**create_kw)
                self._capture.start(target_fps=fps_limit, video_mode=True)
                self._method = "dxcam"
                logger.info("屏幕截图: 使用dxcam (显示器 %d)", monitor)
            except ValueError:
                logger.info("屏幕截图: dxcam初始化跳过(非主线程)，使用mss")
                self._capture = None
                method = "mss"
            except Exception as e:
                logger.warning("dxcam不可用 (%s)，回退到mss", e)
                self._capture = None
                method = "mss"

        if method == "mss" or self._capture is None:
            import mss
            self._capture = mss.mss()
            self._method = "mss"
            mss_idx = monitor + 1 if monitor + 1 < len(self._capture.monitors) else 1
            if mss_idx < len(self._capture.monitors):
                self._monitor = self._capture.monitors[mss_idx]
            else:
                self._monitor = self._capture.monitors[1]
            logger.info("屏幕截图: 使用mss (显示器=%d -> mss[%d])", monitor, mss_idx)

        self._running = True
        self._thread = Thread(target=self._capture_loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=1)
        if self._method == "dxcam" and self._capture:
            try:
                self._capture.stop()
                del self._capture
                self._capture = None
            except Exception:
                pass

    def _capture_loop(self):
        interval = 1.0 / max(self._fps_limit, 1)
        while self._running:
            t0 = time.perf_counter()
            frame = self._grab()
            if frame is not None:
                with self._lock:
                    self._latest_frame = frame
                    self._timestamp = time.time()
            elapsed = time.perf_counter() - t0
            sleep_time = interval - elapsed
            if sleep_time > 0:
                time.sleep(sleep_time)

    def _grab(self):
        try:
            if self._method == "dxcam":
                return self._capture.get_latest_frame()
            elif self._method == "mss":
                raw = self._capture.grab(self._monitor)
                return np.array(raw)[:, :, :3]
        except Exception as e:
            logger.error("截图失败: %s", e)
        return None

    @property
    def frame(self):
        with self._lock:
            if self._latest_frame is None:
                return None
            return self._latest_frame.copy()

    @property
    def timestamp(self):
        with self._lock:
            return self._timestamp

