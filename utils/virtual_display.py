import os
import re
import time
import logging
import subprocess
import ctypes
from ctypes import wintypes

logger = logging.getLogger(__name__)

VDD_DIR = r"C:\VirtualDisplayDriver"
VDD_SETTINGS = os.path.join(VDD_DIR, "vdd_settings.xml")
WINGET_PACKAGE_ID = "VirtualDrivers.Virtual-Display-Driver"

_VDD_KEYWORDS = ["virtual display", "iddsampledriver", "vdd"]

_MONITORENUMPROC = ctypes.WINFUNCTYPE(
    ctypes.c_int,
    ctypes.c_ulong,
    ctypes.c_ulong,
    ctypes.POINTER(wintypes.RECT),
    ctypes.c_double,
)


def _enum_monitors():
    monitors = []
    def _cb(hmon, hdc, lprect, lparam):
        r = lprect.contents
        monitors.append((r.left, r.top, r.right, r.bottom))
        return 1
    ctypes.windll.user32.EnumDisplayMonitors(
        None, None, _MONITORENUMPROC(_cb), 0
    )
    return monitors


class VirtualDisplayManager:

    def __init__(self):
        self._instance_id = None
        self._monitor_rect = None
        self._pre_monitors = []
        self._enabled = False

    def is_installed(self):
        if os.path.isdir(VDD_DIR):
            return True
        return self._find_device_id() is not None

    def is_enabled(self):
        return self._enabled

    def install(self, on_output=None):
        try:
            cmd = [
                "winget", "install",
                "--id", WINGET_PACKAGE_ID,
                "-e",
                "--accept-package-agreements",
                "--accept-source-agreements",
            ]
            logger.info("Installing VDD: %s", " ".join(cmd))
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            for line in proc.stdout:
                line = line.rstrip()
                if line:
                    logger.info("winget: %s", line)
                    if on_output:
                        on_output(line)
            proc.wait(timeout=300)
            ok = proc.returncode == 0
            if ok:
                logger.info("VDD installed successfully")
            else:
                logger.warning("VDD install exited with code %d", proc.returncode)
            return ok
        except FileNotFoundError:
            logger.error("winget not found; please install VDD manually")
            return False
        except Exception as e:
            logger.error("VDD install failed: %s", e)
            return False

    def configure(self, width=1920, height=1080, refresh_rate=60):
        try:
            os.makedirs(VDD_DIR, exist_ok=True)
            xml = (
                '<?xml version="1.0" encoding="utf-8"?>\n'
                "<VddSettings>\n"
                "  <Monitors>\n"
                "    <Monitor>\n"
                f"      <Width>{width}</Width>\n"
                f"      <Height>{height}</Height>\n"
                f"      <RefreshRate>{refresh_rate}</RefreshRate>\n"
                "    </Monitor>\n"
                "  </Monitors>\n"
                "</VddSettings>\n"
            )
            with open(VDD_SETTINGS, "w", encoding="utf-8") as f:
                f.write(xml)
            logger.info("VDD settings written: %dx%d@%dHz", width, height, refresh_rate)
            return True
        except Exception as e:
            logger.error("Failed to write VDD settings: %s", e)
            return False

    def enable(self, timeout=10):
        iid = self._find_device_id()
        if not iid:
            logger.error("VDD device not found, cannot enable")
            return False
        self._pre_monitors = _enum_monitors()
        logger.info("Monitors before enable: %s", self._pre_monitors)
        try:
            r = subprocess.run(
                ["pnputil", "/enable-device", iid],
                capture_output=True, text=True, timeout=15,
                encoding="utf-8", errors="replace",
            )
            logger.info("pnputil enable: %s", r.stdout.strip())
            if r.returncode != 0:
                logger.warning("pnputil enable stderr: %s", r.stderr.strip())
        except Exception as e:
            logger.error("pnputil enable failed: %s", e)
            return False
        deadline = time.time() + timeout
        while time.time() < deadline:
            time.sleep(0.5)
            cur = _enum_monitors()
            if len(cur) > len(self._pre_monitors):
                for m in cur:
                    if m not in self._pre_monitors:
                        self._monitor_rect = m
                        self._enabled = True
                        logger.info("Virtual monitor appeared: %s", m)
                        return True
        logger.warning("Virtual monitor did not appear within %ds", timeout)
        return False

    def disable(self):
        iid = self._find_device_id()
        if not iid:
            return False
        try:
            r = subprocess.run(
                ["pnputil", "/disable-device", iid],
                capture_output=True, text=True, timeout=15,
                encoding="utf-8", errors="replace",
            )
            logger.info("pnputil disable: %s", r.stdout.strip())
        except Exception as e:
            logger.error("pnputil disable failed: %s", e)
            return False
        self._monitor_rect = None
        self._enabled = False
        logger.info("VDD disabled")
        return True

    def get_monitor_rect(self):
        return self._monitor_rect

    def get_monitor_index(self):
        if not self._monitor_rect:
            return -1
        try:
            import pywinctl as pwc
            screens = pwc.getAllScreens()
            vl, vt, vr, vb = self._monitor_rect
            for i, scr in enumerate(screens):
                if hasattr(scr, "left"):
                    if abs(scr.left - vl) < 10 and abs(scr.top - vt) < 10:
                        return i
            for i, scr in enumerate(screens):
                try:
                    pos = scr.position
                    if abs(pos.x - vl) < 10 and abs(pos.y - vt) < 10:
                        return i
                except Exception:
                    continue
        except Exception as e:
            logger.warning("get_monitor_index via pywinctl failed: %s", e)
        monitors = _enum_monitors()
        for i, m in enumerate(monitors):
            if m == self._monitor_rect:
                return i
        return -1

    def get_dxcam_output_idx(self):
        if not self._monitor_rect:
            return -1
        try:
            import dxcam
            info = dxcam.output_info()
            logger.debug("dxcam output_info: %s", info)
            lines = info.strip().split("\n")
            for line in lines:
                m = re.search(r"Device\[(\d+)\]\s+Output\[(\d+)\].*?Res:\((\d+),\s*(\d+)\)", line)
                if m:
                    dev_idx = int(m.group(1))
                    out_idx = int(m.group(2))
                    w = int(m.group(3))
                    h = int(m.group(4))
                    vw = self._monitor_rect[2] - self._monitor_rect[0]
                    vh = self._monitor_rect[3] - self._monitor_rect[1]
                    if w == vw and h == vh:
                        primary_match = re.search(r"Primary:(True|False)", line)
                        is_primary = primary_match and primary_match.group(1) == "True"
                        if not is_primary:
                            return (dev_idx, out_idx)
        except Exception as e:
            logger.warning("get_dxcam_output_idx failed: %s", e)
        return (-1, -1)

    def _find_device_id(self):
        if self._instance_id:
            return self._instance_id
        try:
            r = subprocess.run(
                ["pnputil", "/enum-devices", "/class", "Display"],
                capture_output=True, text=True, timeout=10,
                encoding="utf-8", errors="replace",
            )
            if r.returncode != 0:
                return None
            current_id = None
            for line in r.stdout.splitlines():
                line_stripped = line.strip()
                id_match = re.match(r"(?:Instance ID|实例 ID)\s*:\s*(.+)", line_stripped)
                if id_match:
                    current_id = id_match.group(1).strip()
                    continue
                name_match = re.match(
                    r"(?:Device Description|设备描述|Hardware IDs|硬件 ID)\s*:\s*(.+)",
                    line_stripped,
                )
                if name_match and current_id:
                    desc = name_match.group(1).strip().lower()
                    if any(kw in desc for kw in _VDD_KEYWORDS):
                        self._instance_id = current_id
                        logger.info("VDD device found: %s (%s)", current_id, desc)
                        return current_id
        except Exception as e:
            logger.warning("_find_device_id failed: %s", e)
        return None
