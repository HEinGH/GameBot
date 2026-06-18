import time
import logging
from typing import Optional

import pywinctl as pwc

logger = logging.getLogger(__name__)


class WindowManager:
    def __init__(self, title_keyword: str = "", class_name: str = ""):
        self.title_keyword = title_keyword
        self.class_name = class_name
        self._window: Optional[pwc.Window] = None
        self._saved_box = None

    def find_window(self, retries=5, interval=1.0) -> bool:
        for i in range(retries):
            if self.title_keyword:
                wins = pwc.getWindowsWithTitle(self.title_keyword)
                if wins:
                    best = None
                    best_area = 0
                    for w in wins:
                        try:
                            b = w.box
                            if b.left < -100 or b.top < -100:
                                continue
                            area = b.width * b.height
                            if area > best_area:
                                best_area = area
                                best = w
                        except Exception:
                            continue
                    if best:
                        self._window = best
                        _bb = best.box
                        logger.info("找到窗口: %s (句柄=%s 大小=%dx%d)",
                                    best.title, best.getHandle(), _bb.width, _bb.height)
                        return True
            else:
                best = None
                best_area = 0
                for w in pwc.getAllWindows():
                    try:
                        if not w.visible or w.isMinimized:
                            continue
                        b = w.box
                        if b.width < 800 or b.height < 600:
                            continue
                        if b.left < -100 or b.top < -100:
                            continue
                        area = b.width * b.height
                        if area > best_area:
                            best_area = area
                            best = w
                    except Exception:
                        continue
                if best:
                    self._window = best
                    _bb = best.box
                    logger.info("找到窗口(无标题关键词): %s (大小=%dx%d)",
                                best.title, _bb.width, _bb.height)
                    return True
            if self.class_name:
                all_wins = pwc.getAllWindows()
                for w in all_wins:
                    if w.getHandle() and self._class_matches(w):
                        self._window = w
                        logger.info("按类名找到窗口: 句柄=%s 标题=%s",
                                    w.getHandle(), w.title)
                        return True
            logger.debug("未找到窗口 (尝试 %d/%d)", i + 1, retries)
            time.sleep(interval)
        return False

    def _class_matches(self, win) -> bool:
        try:
            import win32gui
            cls = win32gui.GetClassName(win.getHandle())
            return self.class_name.lower() in cls.lower()
        except Exception:
            return False

    @property
    def is_focused(self) -> bool:
        if not self._window:
            return False
        try:
            return self._window.isActive
        except Exception:
            return False

    @property
    def is_minimized(self) -> bool:
        if not self._window:
            return False
        try:
            return self._window.isMinimized
        except Exception:
            return False

    @property
    def exists(self) -> bool:
        if not self._window:
            return False
        try:
            return self._window.isAlive
        except Exception:
            return False

    def activate(self) -> bool:
        if not self._window:
            return False
        try:
            if self.is_minimized:
                self._window.restore()
                time.sleep(0.3)
            self._window.activate()
            time.sleep(0.3)
            return True
        except Exception as e:
            logger.warning("激活窗口失败: %s", e)
            return False

    def save_position(self):
        if not self._window:
            return
        try:
            self._saved_box = self._window.box
            logger.debug("已保存窗口位置: %s", self._saved_box)
        except Exception as e:
            logger.warning("保存窗口位置失败: %s", e)

    def restore_position(self):
        if not self._window or not self._saved_box:
            return
        try:
            self._window.moveTo(self._saved_box.left, self._saved_box.top)
            self._window.resizeTo(self._saved_box.width, self._saved_box.height)
            logger.info("已恢复窗口位置: %s", self._saved_box)
        except Exception as e:
            logger.warning("恢复窗口位置失败: %s", e)

    def get_monitor_index(self) -> int:
        if not self._window:
            return 0
        try:
            mon = self._window.getMonitor()
            monitors = pwc.getAllScreens()
            for i, m in enumerate(monitors):
                if m == mon:
                    return i
        except Exception:
            pass
        return 0

    def move_to_monitor(self, monitor_index: int):
        if not self._window:
            return False
        try:
            monitors = pwc.getAllScreens()
            if monitor_index >= len(monitors):
                logger.warning("显示器 %d 不存在，共 %d 个", monitor_index, len(monitors))
                return False
            target = monitors[monitor_index]
            h = self._window.height
            self._window.moveTo(target.left + 50, target.top + 50)
            logger.info("窗口已移至显示器 %d 位置(%d, %d)",
                        monitor_index, target.left + 50, target.top + 50)
            return True
        except Exception as e:
            logger.warning("移动窗口到显示器失败: %s", e)
            return False

    @property
    def hwnd(self):
        if not self._window:
            return 0
        try:
            return self._window.getHandle()
        except Exception:
            return 0

    @property
    def title(self):
        if not self._window:
            return ""
        return self._window.title or ""

    @property
    def rect(self):
        if not self._window:
            return None
        try:
            b = self._window.box
            return (b.left, b.top, b.left + b.width, b.top + b.height)
        except Exception:
            return None

    def close(self):
        self.restore_position()
        self._window = None
