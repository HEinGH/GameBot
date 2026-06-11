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

    def start(self, method="auto", fps_limit=30, monitor=0):
        self._fps_limit = fps_limit
        self._monitor_index = monitor
        if self._running:
            return

        if method == "auto":
            try:
                import dxcam
                logging.getLogger("dxcam").setLevel(logging.WARNING)
                self._capture = dxcam.create(output_color="BGR")
                self._capture.start(target_fps=fps_limit, video_mode=True)
                self._method = "dxcam"
                logger.info("ScreenCapture: using dxcam (monitor %d)", monitor)
            except ValueError:
                logger.info("ScreenCapture: dxcam signal init skipped (non-main thread), using mss")
                self._capture = None
                method = "mss"
            except Exception as e:
                logger.warning("dxcam unavailable (%s), falling back to mss", e)
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
            logger.info("ScreenCapture: using mss (monitor=%d -> mss[%d])", monitor, mss_idx)

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

    def change_monitor(self, monitor_index: int):
        if self._method == "dxcam":
            self._monitor_index = monitor_index
            logger.info("dxcam: capture target is monitor %d", monitor_index)
        elif self._method == "mss":
            import mss
            mss_idx = monitor_index + 1
            if mss_idx < len(self._capture.monitors):
                self._monitor = self._capture.monitors[mss_idx]
                self._monitor_index = monitor_index
                logger.info("mss: switched to monitor %d -> mss[%d]", monitor_index, mss_idx)

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
            logger.error("Grab failed: %s", e)
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

    def capture_region(self, left, top, right, bottom):
        full = self.frame
        if full is None:
            return None
        fh, fw = full.shape[:2]
        x1 = max(0, left)
        y1 = max(0, top)
        x2 = min(fw, right)
        y2 = min(fh, bottom)
        if x2 <= x1 or y2 <= y1:
            return None
        return full[y1:y2, x1:x2].copy()

    def capture_window(self, window_mgr):
        rect = window_mgr.get_client_rect()
        if rect is None:
            return self.frame
        mon_index = window_mgr.get_monitor_index()
        if mon_index != self._monitor_index:
            self.change_monitor(mon_index)
            time.sleep(0.05)
        frame = self.frame
        if frame is None:
            return None
        left = rect.get("left", 0)
        top = rect.get("top", 0)
        right = left + rect.get("width", 0)
        bottom = top + rect.get("height", 0)
        import win32gui
        try:
            offset_left, offset_top = win32gui.ScreenToClient(
                window_mgr.hwnd, (left, top))
            hwnd_left, hwnd_top = win32gui.ClientToScreen(
                window_mgr.hwnd, (0, 0))
            left = hwnd_left
            top = hwnd_top
            right = left + rect["width"]
            bottom = top + rect["height"]
        except Exception:
            pass
        return self.capture_region(left, top, right, bottom)
