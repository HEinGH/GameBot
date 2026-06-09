import time
import json
import logging
import threading
from pathlib import Path
from datetime import datetime
from collections import defaultdict

import ctypes
import ctypes.wintypes

logger = logging.getLogger(__name__)

user32 = ctypes.windll.user32

_VK_MAP = {
    0x30: "0", 0x31: "1", 0x32: "2", 0x33: "3", 0x34: "4",
    0x35: "5", 0x36: "6", 0x37: "7", 0x38: "8", 0x39: "9",
    0x41: "a", 0x42: "b", 0x43: "c", 0x44: "d", 0x45: "e",
    0x46: "f", 0x47: "g", 0x48: "h", 0x49: "i", 0x4A: "j",
    0x4B: "k", 0x4C: "l", 0x4D: "m", 0x4E: "n", 0x4F: "o",
    0x50: "p", 0x51: "q", 0x52: "r", 0x53: "s", 0x54: "t",
    0x55: "u", 0x56: "v", 0x57: "w", 0x58: "x", 0x59: "y",
    0x5A: "z",
    0x70: "f1", 0x71: "f2", 0x72: "f3", 0x73: "f4",
    0x74: "f5", 0x75: "f6", 0x76: "f7", 0x77: "f8",
    0x78: "f9", 0x79: "f10", 0x7A: "f11", 0x7B: "f12",
    0x20: "space", 0x09: "tab", 0x0D: "enter", 0x1B: "esc",
    0x08: "backspace", 0x2E: "delete",
    0x10: "left_shift", 0xA0: "left_shift", 0xA1: "right_shift",
    0x11: "left_ctrl", 0xA2: "left_ctrl", 0xA3: "right_ctrl",
    0x12: "left_alt", 0xA4: "left_alt", 0xA5: "right_alt",
    0x26: "up", 0x28: "down", 0x25: "left", 0x27: "right",
    0x21: "page_up", 0x22: "page_down", 0x24: "home", 0x23: "end",
}

_VK_CODE = {v: k for k, v in _VK_MAP.items()}

_MOUSE_KEYS = ["left_click", "right_click", "middle_click"]
_MOUSE_VK = {"left_click": 0x01, "right_click": 0x02, "middle_click": 0x04}


def _get_async_key_state(vk):
    return bool(user32.GetAsyncKeyState(vk) & 0x8000)


class MacroRecorder:
    def __init__(self, output_dir, stop_key="f6"):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.stop_key = stop_key
        self._events = []
        self._start_time = 0.0
        self._recording = False
        self._poll_thread = None

    def record(self, name=None):
        if name is None:
            name = f"combo_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

        print("\n=== Macro Recorder ===")
        print("Recording will start after 3 seconds...")
        print(f"Press {self.stop_key.upper()} to STOP recording")
        print("Press Ctrl+C to cancel\n")
        time.sleep(3)

        self._events = []
        self._start_time = time.time()
        self._recording = True

        self._poll_thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._poll_thread.start()

        print(f"RECORDING... ({self.stop_key.upper()} to stop)")

        stop_vk = _VK_CODE.get(self.stop_key)
        try:
            while self._recording:
                if stop_vk and _get_async_key_state(stop_vk):
                    print(f"\n{self.stop_key.upper()} pressed, stopping...")
                    self._recording = False
                    break
                time.sleep(0.05)
        except KeyboardInterrupt:
            print("\nCancelled by user")
        finally:
            self._recording = False
            if self._poll_thread:
                self._poll_thread.join(timeout=2)

        if not self._events:
            print("No events recorded.")
            return None

        actions = self._convert_to_combos()
        if not actions:
            print("No combo actions generated.")
            return None

        output = {
            "name": name,
            "recorded_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "duration_sec": round(self._events[-1]["t"] - self._events[0]["t"], 2) if len(self._events) > 1 else 0,
            "actions": actions,
        }

        path = self.output_dir / f"{name}.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(output, f, indent=2, ensure_ascii=False)

        print(f"\nRecorded {len(actions)} actions ({output['duration_sec']}s)")
        print(f"Saved to: {path}")
        return output

    def _poll_loop(self):
        prev_keys = {}
        all_vk = list(_VK_MAP.keys())
        for name in _MOUSE_KEYS:
            prev_keys[name] = False
        for vk in all_vk:
            prev_keys[vk] = False

        while self._recording:
            now = time.time() - self._start_time

            for name, vk in _MOUSE_VK.items():
                pressed = _get_async_key_state(vk)
                if pressed != prev_keys[name]:
                    prev_keys[name] = pressed
                    etype = "key_down" if pressed else "key_up"
                    self._events.append({"t": now, "type": etype, "key": name})

            for vk in all_vk:
                name = _VK_MAP[vk]
                if name in (self.stop_key, "f5"):
                    continue
                pressed = _get_async_key_state(vk)
                if pressed != prev_keys[vk]:
                    prev_keys[vk] = pressed
                    etype = "key_down" if pressed else "key_up"
                    self._events.append({"t": now, "type": etype, "key": name})

            time.sleep(0.01)

    def _convert_to_combos(self):
        actions = []
        held = set()
        pending = []

        sorted_events = sorted(self._events, key=lambda e: e["t"])
        groups = defaultdict(list)
        for ev in sorted_events:
            groups[ev["t"]].append(ev)

        def emit_action(pending_list):
            if not pending_list:
                return
            down_events = [e for e in pending_list if e["type"] in ("key_down", "mouse_down")]
            up_events = [e for e in pending_list if e["type"] in ("key_up", "mouse_up")]
            if not down_events:
                if up_events:
                    for u in up_events:
                        actions.append({
                            "keys": [u["key"]],
                            "duration": 0.05,
                            "delay_after": 0.0,
                            "_hold": False,
                            "_end_t": u["t"],
                        })
                return
            keys = sorted(set(e["key"] for e in down_events))
            t_start = down_events[0]["t"]
            t_end = up_events[-1]["t"] if up_events else t_start + 0.05
            duration = round(t_end - t_start, 3)
            hold = duration > 0.2 and len(keys) == 1
            actions.append({
                "keys": keys,
                "duration": duration,
                "delay_after": 0.0,
                "_hold": hold,
                "_end_t": t_end,
            })

        for t in sorted(groups.keys()):
            group = groups[t]
            has_up = any(e["type"] in ("key_up", "mouse_up") for e in group)
            if has_up:
                for ev in group:
                    if ev["type"] in ("key_up", "mouse_up"):
                        held.discard(ev["key"])
                for ev in group:
                    pending.append(ev)
                if not held:
                    emit_action(pending)
                    pending = []
            else:
                for ev in group:
                    if ev["type"] in ("key_down", "mouse_down"):
                        held.add(ev["key"])
                    pending.append(ev)

        if pending:
            now = self._events[-1]["t"] if self._events else 0
            pending.append({"t": now, "type": "key_up", "key": "___flush___"})
            emit_action(pending)
            pending = []

        if not actions:
            return []

        seen_keys_at = {}
        filtered = []
        for a in actions:
            keys = a["keys"]
            end_t = a["_end_t"]
            is_mouse_only = len(keys) == 1 and keys[0] in ("left_click", "right_click")
            if is_mouse_only and filtered:
                prev = filtered[-1]
                if keys[0] in prev["keys"] and abs(end_t - prev["_end_t"]) < 0.15:
                    continue
            filtered.append(a)
        actions = filtered

        for i in range(len(actions) - 1):
            delay = round(actions[i + 1]["_end_t"] - actions[i]["_end_t"], 3)
            actions[i]["delay_after"] = max(0.0, round(delay, 3))

        for a in actions:
            a.pop("_hold", None)
            a.pop("_end_t", None)

        return actions
