import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import json
import os
import threading
import queue
import logging
import time
from pathlib import Path
from datetime import datetime

from config.settings import Settings, PRESETS_DIR, ROOT_DIR, CHARACTERS_DIR, COMBO_DIR
from config.settings import (resolve_characters, serialize_characters,
                              load_character_profile, save_character_profile,
                              list_character_profiles, CHARACTER_PROFILE_FIELDS,
                              load_combo, list_combos, save_combo, delete_combo)

logger = logging.getLogger(__name__)

_last_browse_dir = None  # shared across all file pickers


def _pack_tpl_value(name, thr_val, original=None):
    n = (name or "").strip()
    if not n:
        return ""
    try:
        t = float(thr_val)
    except (ValueError, TypeError):
        t = 0.65
    extra = {}
    if isinstance(original, dict):
        extra = {k: v for k, v in original.items() if k not in ("template", "threshold")}
    if abs(t - 0.65) < 0.001 and not extra:
        return n
    result = {"template": n, "threshold": round(t, 2)}
    result.update(extra)
    return result


def _unpack_tpl_value(value):
    if isinstance(value, dict):
        return value.get("template", ""), value.get("threshold", 0.65)
    if value:
        return str(value), 0.65
    return "", 0.65


def _center_on_parent(dialog, parent, width, height):
    dialog.withdraw()
    parent.update_idletasks()
    px = parent.winfo_rootx()
    py = parent.winfo_rooty()
    x = px + max(0, (parent.winfo_width() - width) // 2)
    y = py + max(0, (parent.winfo_height() - height) // 2)
    dialog.geometry(f"{width}x{height}+{x}+{y}")
    dialog.update_idletasks()
    dialog.deiconify()


def _import_template_file(src_path, templates_dir):
    src = Path(src_path)
    tpl_dir = templates_dir.resolve()
    if src.resolve().is_relative_to(tpl_dir):
        return str(src.resolve().relative_to(tpl_dir))
    dst = tpl_dir / src.name
    if dst.exists():
        stem, ext = src.stem, src.suffix
        counter = 1
        while dst.exists():
            dst = tpl_dir / f"{stem}_{counter}{ext}"
            counter += 1
    import shutil
    shutil.copy2(str(src), str(dst))
    logger.info("模板已导入: %s -> %s", src.name, dst.name)
    return dst.name


class QueueHandler(logging.Handler):
    def __init__(self, log_queue):
        super().__init__()
        self.log_queue = log_queue

    def emit(self, record):
        self.log_queue.put(self.format(record))


def get_script_dir():
    import sys
    if getattr(sys, 'frozen', False):
        return Path(os.path.dirname(os.path.abspath(sys.executable)))
    return ROOT_DIR





class ChainStepList(tk.Frame):
    def __init__(self, master, templates_dir=None, import_func=None, canvas_height=80, **kwargs):
        super().__init__(master, **kwargs)
        self.templates_dir = templates_dir
        self.import_func = import_func
        self._items = []
        self._entries = []
        self._row_refs = []
        self._selected_entry = None
        self._selected_row = None

        self.canvas = tk.Canvas(self, highlightthickness=0,
                                bg=self._bg_color(), bd=0, height=canvas_height)
        self.scrollbar_v = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.inner = ttk.Frame(self.canvas)

        self.inner.bind("<Configure>", self._on_inner_configure)
        self._cw = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.canvas.configure(yscrollcommand=self.scrollbar_v.set)

        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.scrollbar_v.grid(row=0, column=1, sticky="ns")
        self.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)
        self.canvas.bind("<Configure>", self._on_canvas_configure)
        self.canvas.bind("<Enter>", self._bind_mwheel)
        self.canvas.bind("<Leave>", self._unbind_mwheel)

        btn_bar = ttk.Frame(self)
        btn_bar.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(4, 0))
        ttk.Button(btn_bar, text="+ 添加步骤", command=self._add_step).pack(side="left", padx=1)
        ttk.Button(btn_bar, text="批量浏览", command=self._browse_multi).pack(side="left", padx=1)
        ttk.Button(btn_bar, text="△ 上移", command=lambda: self._move(-1)).pack(side="left", padx=1)
        ttk.Button(btn_bar, text="▽ 下移", command=lambda: self._move(1)).pack(side="left", padx=1)
        ttk.Button(btn_bar, text="删除", command=self._delete_focused).pack(side="left", padx=1)
        ttk.Button(btn_bar, text="清空", command=self._clear).pack(side="left", padx=1)

    @staticmethod
    def _bg_color():
        try:
            style = ttk.Style()
            return style.lookup("TFrame", "background") or "#f0f0f0"
        except Exception:
            return "#f0f0f0"

    def _on_inner_configure(self, event=None):
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _on_canvas_configure(self, event):
        self.canvas.itemconfig(self._cw, width=event.width)

    def _bind_mwheel(self, event):
        self.canvas.bind("<MouseWheel>", self._on_mwheel, "+")

    def _unbind_mwheel(self, event):
        self.canvas.unbind("<MouseWheel>")

    def _on_mwheel(self, event):
        bbox = self.canvas.bbox("all")
        if bbox and bbox[3] <= self.canvas.winfo_height():
            return
        self.canvas.yview_scroll(int(-event.delta / 120), "units")
        return "break"

    def _make_row(self, idx, tmpl_val):
        if isinstance(tmpl_val, dict):
            name = tmpl_val.get("template", "")
            thr = tmpl_val.get("threshold", 0.65)
        else:
            name = tmpl_val or ""
            thr = 0.65
        row = ttk.Frame(self.inner)
        row.pack(fill="x", pady=(1, 1))

        step_label = ttk.Label(row, text="步骤 {}".format(idx + 1), width=8,
                   anchor="center", font=("", 9, "bold"))
        step_label.pack(side="left", padx=1)
        ttk.Label(row, text="→", font=("", 10)).pack(side="left")

        entry = ttk.Entry(row, width=18)
        entry.pack(side="left", padx=2)
        if name:
            entry.insert(0, name)

        def _select_this_row(r=row, en=entry):
            self._set_selected(r, en)
        entry.bind("<FocusIn>", lambda e: _select_this_row())
        entry.bind("<Button-1>", lambda e: _select_this_row())
        step_label.bind("<Button-1>", lambda e: _select_this_row())
        row.bind("<Button-1>", lambda e: _select_this_row())

        thr_var = tk.StringVar(value=str(thr))
        thr_spin = ttk.Spinbox(row, from_=0.30, to=0.99, increment=0.05, width=4, textvariable=thr_var)
        thr_spin.bind("<MouseWheel>", lambda e: "break")
        thr_spin.bind("<Button-1>", lambda e: _select_this_row(), "+")
        thr_spin.pack(side="left", padx=1)

        def pick_template(en, tv):
            def inner_pick():
                from tkinter import filedialog as fd
                path = fd.askopenfilename(
                    title="选择模板图片 - 步骤 {}".format(idx + 1),
                    filetypes=[("图片", "*.png *.jpg *.jpeg *.bmp"), ("所有", "*.*")])
                if path and self.import_func:
                    imported = self.import_func(path, self.templates_dir)
                    en.delete(0, "end")
                    en.insert(0, imported)
            return inner_pick

        browse_btn = ttk.Button(row, text="浏览", width=4,
                   command=pick_template(entry, thr_var))
        browse_btn.pack(side="left", padx=1)
        browse_btn.bind("<Button-1>", lambda e: _select_this_row(), "+")

        return (entry, thr_var), row

    def _set_selected(self, row, entry):
        sel_style = "SelRow.TFrame"
        try:
            style = ttk.Style()
            style.configure(sel_style, background="#b0d0ff")
        except Exception:
            pass
        for r, _ in self._row_refs:
            try:
                r.configure(style="TFrame")
            except Exception:
                pass
        self._selected_entry = entry
        self._selected_row = row
        try:
            self._selected_row.configure(style=sel_style)
        except Exception:
            pass

    def _deselect(self):
        for r, _ in self._row_refs:
            try:
                r.configure(style="TFrame")
            except Exception:
                pass
        self._selected_entry = None
        self._selected_row = None

    def _sync_entries(self):
        old_items = list(self._items)
        self._items = []
        for i, (_, (entry, thr_var)) in enumerate(self._row_refs):
            val = entry.get().strip()
            if val:
                try:
                    t = float(thr_var.get())
                except (ValueError, TypeError):
                    t = 0.65
                orig = old_items[i] if i < len(old_items) else None
                extra = {}
                if isinstance(orig, dict):
                    extra = {k: v for k, v in orig.items() if k not in ("template", "threshold")}
                if abs(t - 0.65) < 0.001 and not extra:
                    self._items.append(val)
                else:
                    result = {"template": val, "threshold": round(t, 2)}
                    result.update(extra)
                    self._items.append(result)

    def _refresh(self):
        for w in self.inner.winfo_children():
            w.destroy()
        self._row_refs = []
        for i, item in enumerate(self._items):
            widgets, r = self._make_row(i, item)
            self._row_refs.append((r, widgets))
        self.inner.update_idletasks()
        self._on_inner_configure()

    def _add_step(self, name=""):
        self._sync_entries()
        self._items.append(name)
        self._refresh()

    def _browse_multi(self):
        from tkinter import filedialog as fd
        paths = fd.askopenfilenames(
            title="按顺序选择多张模板图片",
            filetypes=[("图片", "*.png *.jpg *.jpeg *.bmp"), ("所有", "*.*")])
        if paths:
            self._sync_entries()
            for p in paths:
                if self.import_func:
                    name = self.import_func(p, self.templates_dir)
                    self._items.append(name)
            self._refresh()

    def _delete_focused(self):
        self._sync_entries()
        del_idx = None
        for i, (r, _) in enumerate(self._row_refs):
            if r is self._selected_row:
                del_idx = i
                break
        if del_idx is not None and del_idx < len(self._items):
            self._items.pop(del_idx)
        self._selected_entry = None
        self._selected_row = None
        self._refresh()

    def _move(self, direction):
        self._sync_entries()
        sel_idx = None
        for i, (r, _) in enumerate(self._row_refs):
            if r is self._selected_row:
                sel_idx = i
                break
        if sel_idx is None:
            return
        new_idx = sel_idx + direction
        if 0 <= new_idx < len(self._items):
            self._items[sel_idx], self._items[new_idx] = self._items[new_idx], self._items[sel_idx]
        self._refresh()
        if new_idx < len(self._row_refs):
            r, (entry, _) = self._row_refs[new_idx]
            self._set_selected(r, entry)

    def _clear(self):
        self._items = []
        self._row_refs = []
        self._refresh()

    def get_items(self):
        self._sync_entries()
        return [x for x in self._items if x and (isinstance(x, str) and x.strip() or isinstance(x, dict) and x.get("template", "").strip())]

    def set_items(self, items):
        if isinstance(items, str):
            items = [items] if items else []
        self._items = list(items) if items else []
        self._refresh()


class GameBotGUI:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("GameBot - 自动化清体力")
        self.root.geometry("960x680")
        self.root.minsize(800, 600)
        self.script_dir = get_script_dir()
        self.presets_path = self.script_dir / "config" / "presets"
        if not self.presets_path.exists():
            self.presets_path = PRESETS_DIR
        self.templates_path = self.script_dir / "templates"
        self.current_preset_path = None
        self.preset_data = self._load_default_preset()
        Settings().load()
        self._load_last_preset()
        self.bot_thread = None
        self.bot_running = False
        self.bot_stop_event = threading.Event()
        self.log_queue = queue.Queue()
        self._setup_log_handler()
        self._build_ui()
        self._refresh_preset_list()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        cfg = Settings()
        if cfg.last_log_debug:
            self.log_debug_var.set(True)
            self._log_handler.setLevel(logging.DEBUG)
            logging.getLogger().setLevel(logging.DEBUG)
        self.root.after(200, self._poll_log)
        self.root.after(20, self._poll_hotkeys)

    def _make_file_picker(self, entry):
        global _last_browse_dir
        def pick():
            global _last_browse_dir
            start = _last_browse_dir if _last_browse_dir else self.templates_path
            path = filedialog.askopenfilename(
                title="选择模板图片",
                initialdir=start,
                filetypes=[("图片文件", "*.png *.jpg *.jpeg *.bmp"), ("所有文件", "*.*")],
            )
            if path:
                _last_browse_dir = Path(path).parent
                name = _import_template_file(path, self.templates_path)
                entry.delete(0, "end")
                entry.insert(0, name)
        return pick

    def _load_default_preset(self):
        return {
            "description": "",
            "window_title": "",
            "char_count": 1,
            "stealth": False,
            "background": False,
            "enter_game_template": "",
            "rechallenge_template": "",
            "exit_domain_template": "",
            "portal_template": "",
            "town_nav": {
                "domain_select_steps": [],
                "alt_for_mouse": True,
                "confirm_enter_template": "",
                "npc_marker_template": "",
            },
            "town_exit": {
                "settings_template": "",
                "switch_character_template": "",
                "exit_game_template": "",
                "confirm_exit_template": "",
            },
            "characters": [],
        }

    def _load_last_preset(self):
        cfg = Settings()
        last = cfg.last_preset
        if last:
            path = self.presets_path / f"{last}.json"
            if path.exists():
                try:
                    with open(path, encoding="utf-8") as f:
                        data = json.load(f)
                    data["characters"] = resolve_characters(data, last)
                    self.preset_data = data
                    self.current_preset_path = path
                    logger.info("加载上次预设: %s", last)
                except Exception:
                    pass

    def _setup_log_handler(self):
        self._log_handler = QueueHandler(self.log_queue)
        self._log_handler.setFormatter(logging.Formatter(
            "%(asctime)s [%(levelname)-7s] %(message)s",
            datefmt="%H:%M:%S",
        ))
        self._log_handler.setLevel(logging.INFO)
        logging.getLogger().addHandler(self._log_handler)

    def _on_log_level_change(self):
        debug = self.log_debug_var.get()
        if debug:
            self._log_handler.setLevel(logging.DEBUG)
            logging.getLogger().setLevel(logging.DEBUG)
        else:
            self._log_handler.setLevel(logging.INFO)
            logging.getLogger().setLevel(logging.INFO)
        Settings().last_log_debug = debug
        Settings().save()

    def _build_ui(self):
        self.root.columnconfigure(0, weight=0)
        self.root.columnconfigure(1, weight=1)
        self.root.rowconfigure(0, weight=1)
        self._build_sidebar()
        self._build_main_area()

    def _build_sidebar(self):
        sidebar = ttk.Frame(self.root, width=160, relief="ridge", padding=6)
        sidebar.grid(row=0, column=0, sticky="ns")
        sidebar.grid_propagate(False)
        ttk.Label(sidebar, text="GameBot", font=("", 14, "bold")).pack(pady=(0, 12))
        self._nav_btns = {}
        nav_items = [
            ("dashboard", "运行控制"),
            ("characters", "预设管理"),
            ("charlib", "角色库"),
            ("recorder", "\u8fde\u62db\u7ba1\u7406"),
            ("screenshot", "截图工具"),
            ("settings", "全局设置"),
            ("devtools", "开发者工具"),
        ]
        self._current_page = tk.StringVar(value="dashboard")
        for key, label in nav_items:
            btn = ttk.Button(sidebar, text=label, width=16,
                             command=lambda k=key: self._switch_page(k))
            btn.pack(pady=2)
            self._nav_btns[key] = btn
        ttk.Separator(sidebar, orient="horizontal").pack(fill="x", pady=10)
        ttk.Label(sidebar, text="Bot Status", font=("", 9, "bold")).pack()
        self.status_label = ttk.Label(sidebar, text="● 空闲", foreground="gray")
        self.status_label.pack(pady=2)
        self.start_btn = ttk.Button(sidebar, text="▶ 启动", command=self._toggle_bot)
        self.start_btn.pack(pady=4)
        ttk.Label(sidebar, text="Ctrl+Alt+B", font=("", 8), foreground="#888").pack()

    def _build_main_area(self):
        container = ttk.Frame(self.root, padding=8)
        container.grid(row=0, column=1, sticky="nsew")
        container.columnconfigure(0, weight=1)
        container.rowconfigure(0, weight=1)
        self._pages = {}
        for key in ("dashboard", "characters", "charlib", "devtools", "recorder", "screenshot", "settings"):
            frame = ttk.Frame(container)
            frame.grid(row=0, column=0, sticky="nsew")
            frame.columnconfigure(0, weight=1)
            frame.rowconfigure(0, weight=1)
            self._pages[key] = frame
        self._build_dashboard()
        self._build_characters()
        self._build_charlib()
        self._build_devtools()
        self._build_recorder()
        self._build_screenshot()
        self._build_settings()
        self._switch_page("dashboard")

    def _should_forward_scroll(self, event):
        w = event.widget
        if not hasattr(w, 'winfo_name'):
            return False
        name = w.winfo_name()
        cls = w.winfo_class()
        skip_classes = {"Treeview", "TCombobox", "Listbox", "Text", "TScrollbar", "TSpinbox"}
        if cls in skip_classes or "toplevel" in name.lower():
            return False
        parent = w.winfo_toplevel()
        if parent and isinstance(parent, tk.Toplevel) and parent != self.root:
            return False
        try:
            p = w
            while p and p != self.root:
                if hasattr(self, "_char_canvas") and p is self._char_canvas:
                    return False
                if hasattr(self, "domain_chain") and p is self.domain_chain.canvas:
                    return False
                if hasattr(self, "confirm_enter_chain") and p is self.confirm_enter_chain.canvas:
                    return False
                p = p.master
        except Exception:
            pass
        return True

    def _switch_page(self, key):
        self.root.unbind_all("<MouseWheel>")
        self.root.unbind_all("<Button-1>")
        self._current_page.set(key)
        for k, f in self._pages.items():
            f.grid_remove()
        self._pages[key].grid()
        if key == "characters":
            self._refresh_char_table()
            self.root.bind_all("<MouseWheel>",
                lambda e: self._on_page_scroll(e), "+")
            self.root.bind_all("<Button-1>",
                lambda e: self._on_preset_page_click(e), "+")
            self._page_canvas.update_idletasks()
            self._page_canvas.yview_moveto(0)
        elif key == "recorder":
            if hasattr(self, "_rec_canvas"):
                self.root.bind_all("<MouseWheel>",
                    lambda e: self._on_recorder_scroll(e), "+")
                self._rec_canvas.update_idletasks()
                self._rec_canvas.yview_moveto(0)

    def _on_recorder_scroll(self, event):
        if not self._should_forward_scroll(event):
            return
        bbox = self._rec_canvas.bbox("all")
        if bbox and bbox[3] <= self._rec_canvas.winfo_height():
            return
        self._rec_canvas.yview_scroll(int(-event.delta / 120), "units")

    def _on_page_scroll(self, event):
        if hasattr(self, "_char_canvas"):
            w = event.widget
            if hasattr(w, 'master'):
                try:
                    p = w
                    while p and p != self.root:
                        if p is self._char_canvas:
                            bbox = self._char_canvas.bbox("all")
                            if bbox and bbox[3] <= self._char_canvas.winfo_height():
                                return
                            self._char_canvas.yview_scroll(int(-event.delta / 120), "units")
                            return
                        p = p.master
                except Exception:
                    pass
        if self._should_forward_scroll(event):
            bbox = self._page_canvas.bbox("all")
            if bbox and bbox[3] <= self._page_canvas.winfo_height():
                return
            self._page_canvas.yview_scroll(int(-event.delta / 120), "units")

    def _clear_log(self):
        self.dash_status_text.configure(state="normal")
        self.dash_status_text.delete("1.0", "end")
        self.dash_status_text.configure(state="disabled")

    def _trim_log_lines(self):
        lines = int(self.dash_status_text.index("end-1c").split(".")[0])
        if lines > self._log_max_lines:
            self.dash_status_text.delete("1.0", f"{lines - self._log_max_lines + 1}.0")

    def _poll_log(self):
        try:
            while True:
                msg = self.log_queue.get_nowait()
                self.dash_status_text.configure(state="normal")
                self.dash_status_text.insert("end", msg + "\n")
                self._trim_log_lines()
                self.dash_status_text.see("end")
                self.dash_status_text.configure(state="disabled")
        except queue.Empty:
            pass
        self.root.after(200, self._poll_log)

    def _poll_hotkeys(self):
        try:
            import ctypes
            u32 = ctypes.windll.user32
            c = u32.GetAsyncKeyState(0x11) & 0x8000
            a = u32.GetAsyncKeyState(0x12) & 0x8000
            b = u32.GetAsyncKeyState(0x42) & 0x8000
            ca_held = bool(c and a)
            if ca_held and not getattr(self, '_hk_armed', False):
                self._hk_armed = True
            elif not ca_held:
                self._hk_armed = False
            if getattr(self, '_hk_armed', False) and b:
                if not getattr(self, '_hk_bot_pressed', False):
                    self._hk_bot_pressed = True
                    logger.debug("[热键] 触发!")
                    self._toggle_bot()
            else:
                self._hk_bot_pressed = False
        except Exception:
            pass
        self.root.after(20, self._poll_hotkeys)

    def _build_dashboard(self):
        f = self._pages["dashboard"]
        ttk.Label(f, text="运行控制", font=("", 14, "bold")).pack(anchor="w")
        card = ttk.LabelFrame(f, text="预设选择", padding=10)
        card.pack(fill="x", pady=6)
        row = ttk.Frame(card)
        row.pack(fill="x")
        ttk.Label(row, text="预设文件:").pack(side="left")
        self.dash_preset_var = tk.StringVar()
        self.dash_preset_combo = ttk.Combobox(row, textvariable=self.dash_preset_var, width=30, state="readonly")
        self.dash_preset_combo.bind("<<ComboboxSelected>>", self._on_preset_selected)
        self.dash_preset_combo.pack(side="left", padx=6)
        ttk.Button(row, text="确定", command=self._edit_selected_preset).pack(side="left", padx=4)
        ttk.Button(row, text="删除", command=self._delete_preset).pack(side="left")
        ttk.Button(row, text="保存", command=self._save_preset).pack(side="left", padx=4)
        row2 = ttk.Frame(card)
        row2.pack(fill="x", pady=(4, 0))
        ttk.Label(row2, text="从第几个角色开始:").pack(side="left")
        self.dash_char_start = tk.IntVar(value=1)
        self.dash_char_start_spin = ttk.Spinbox(row2, from_=1, to_=20, textvariable=self.dash_char_start, width=4)
        self.dash_char_start_spin.pack(side="left", padx=6)
        ttk.Label(row2, text="执行角色数:").pack(side="left")
        self.dash_char_count = tk.IntVar(value=1)
        self.dash_char_spin = ttk.Spinbox(row2, from_=1, to_=20, textvariable=self.dash_char_count, width=4)
        self.dash_char_spin.pack(side="left", padx=6)
        ttk.Label(row2, text=" 隐身模式:").pack(side="left")
        self.dash_stealth = tk.BooleanVar()
        ttk.Checkbutton(row2, variable=self.dash_stealth).pack(side="left")
        ttk.Label(row2, text=" 后台模式:").pack(side="left")
        self.dash_background = tk.BooleanVar()
        ttk.Checkbutton(row2, variable=self.dash_background).pack(side="left")
        self.dash_exit_after_done = tk.BooleanVar(value=True)
        ttk.Label(row2, text=" 完成后退出:").pack(side="left")
        ttk.Checkbutton(row2, variable=self.dash_exit_after_done).pack(side="left")
        self.status_frame = ttk.LabelFrame(f, text="运行状态", padding=10)
        self.status_frame.pack(fill="both", expand=True, pady=6)
        status_top = ttk.Frame(self.status_frame)
        status_top.pack(fill="x", pady=(0, 4))
        ttk.Label(status_top, text="日志级别:").pack(side="left")
        self.log_info_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(status_top, text="INFO", variable=self.log_info_var,
                         command=self._on_log_level_change).pack(side="left", padx=4)
        self.log_debug_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(status_top, text="DEBUG", variable=self.log_debug_var,
                         command=self._on_log_level_change).pack(side="left", padx=4)
        ttk.Button(status_top, text="清空", command=self._clear_log).pack(side="right")
        log_container = ttk.Frame(self.status_frame)
        log_container.pack(fill="both", expand=True)
        log_container.rowconfigure(0, weight=1)
        log_container.columnconfigure(0, weight=1)
        self.dash_status_text = tk.Text(log_container, height=10, wrap="word",
                                         state="disabled", font=("Microsoft YaHei", 9))
        self._status_scrollbar = ttk.Scrollbar(log_container, orient="vertical",
                                                command=self.dash_status_text.yview)
        self.dash_status_text.configure(yscrollcommand=self._status_scrollbar.set)
        self.dash_status_text.grid(row=0, column=0, sticky="nsew")
        self._status_scrollbar.grid(row=0, column=1, sticky="ns")
        self._log_max_lines = 500

    def _refresh_preset_list(self):
        presets = []
        if self.presets_path.exists():
            for p in sorted(self.presets_path.glob("*.json")):
                presets.append(p.stem)
        self.dash_preset_combo["values"] = presets
        if presets:
            cfg = Settings()
            last = cfg.last_preset
            if last and last in presets:
                self.dash_preset_var.set(last)
            elif not self.dash_preset_var.get():
                self.dash_preset_var.set(presets[0])
        self._on_preset_selected()

    def _on_preset_selected(self, event=None):
        name = self.dash_preset_var.get()
        if not name:
            return
        path = self.presets_path / f"{name}.json"
        if path.exists():
            try:
                with open(path, encoding="utf-8") as f:
                    data = json.load(f)
                data["characters"] = resolve_characters(data, name)
                self.preset_data = data
                self.current_preset_path = path
                count = len(data.get("characters", []))
                cc = data.get("char_count", max(1, count))
                if cc > count:
                    cc = max(1, count)
                cs = data.get("char_start", 1)
                if cs > count:
                    cs = max(1, count)
                self.dash_char_count.set(cc)
                self.dash_char_spin.configure(to_=max(1, count))
                self.dash_char_start.set(cs)
                self.dash_char_start_spin.configure(to_=max(1, count))
                self.dash_stealth.set(data.get("stealth", False))
                self.dash_background.set(data.get("background", False))
                self.dash_exit_after_done.set(data.get("exit_after_done", True))
                self._refresh_char_table()
            except Exception:
                pass
        cfg = Settings()
        cfg.last_preset = name
        cfg.save()

    def _edit_selected_preset(self):
        name = self.dash_preset_var.get()
        if not name:
            messagebox.showwarning("提示", "请先选择一个预设")
            return
        path = self.presets_path / f"{name}.json"
        if path.exists():
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            data["characters"] = resolve_characters(data, name)
            self.preset_data = data
            self.current_preset_path = path
            self._switch_page("characters")
        else:
            messagebox.showerror("错误", f"预设文件不存在:\n{path}")

    def _delete_preset(self):
        name = self.dash_preset_var.get()
        if not name:
            messagebox.showwarning("提示", "请先选择一个预设")
            return
        if not messagebox.askyesno("确认删除", f"确定要删除预设 \"{name}\" 吗？\n此操作不可撤销。"):
            return
        path = self.presets_path / f"{name}.json"
        try:
            path.unlink()
            logger.info("已删除预设: %s", name)
        except Exception as e:
            messagebox.showerror("错误", f"删除失败:\n{e}")
            return
        self._refresh_preset_list()
        names = list(self.dash_preset_combo["values"])
        if names:
            new_name = names[0]
            self.dash_preset_var.set(new_name)
            self._on_preset_selected()
            self._switch_page("characters")

    # ========== CHARACTERS PAGE ==========
    def _build_characters(self):
        f = self._pages["characters"]

        top = ttk.Frame(f)
        top.pack(fill="x")
        ttk.Label(top, text="预设管理", font=("", 14, "bold")).pack(side="left")
        ttk.Button(top, text="新建预设", command=self._new_preset).pack(side="right", padx=2)
        ttk.Button(top, text="保存预设", command=self._save_preset).pack(side="right", padx=2)
        ttk.Button(top, text="另存为...", command=self._save_preset_as).pack(side="right", padx=2)

        mid = ttk.Frame(f)
        mid.pack(fill="x", pady=(4, 2))
        ttk.Label(mid, text="预设描述:").pack(side="left")
        self.char_desc = ttk.Entry(mid, width=60)
        self.char_desc.pack(side="left", padx=6, fill="x", expand=True)

        self._page_canvas = tk.Canvas(f, highlightthickness=0, bd=0)
        page_scroll = ttk.Scrollbar(f, orient="vertical", command=self._page_canvas.yview)
        page_content = ttk.Frame(self._page_canvas)
        self._preset_content = page_content

        page_content.bind("<Configure>",
            lambda e: self._page_canvas.configure(scrollregion=self._page_canvas.bbox("all")))
        self._page_cw = self._page_canvas.create_window((0, 0), window=page_content, anchor="nw",
                                                          tags=("page_content",))
        self._page_canvas.configure(yscrollcommand=page_scroll.set)

        self._page_canvas.pack(side="left", fill="both", expand=True)
        page_scroll.pack(side="right", fill="y")

        def _on_page_resize(event):
            self._page_canvas.itemconfig(self._page_cw, width=event.width - 2)
            self._page_canvas.configure(scrollregion=self._page_canvas.bbox("all"))
        self._page_canvas.bind("<Configure>", _on_page_resize)

        cfg_frame = ttk.LabelFrame(page_content, text="预设全局配置", padding=6)
        cfg_frame.pack(fill="x", pady=2)
        ttk.Label(cfg_frame, text="提示：置信度默认 0.65，每个模板可单独调整",
                  font=("", 8), foreground="#888").pack(anchor="w", pady=(0, 4))

        def cfg_file(parent, label, attr):
            f = ttk.Frame(parent)
            f.pack(side="left", padx=2)
            ttk.Label(f, text=label).pack(side="left")
            e = ttk.Entry(f, width=10)
            e.pack(side="left", padx=1)
            thr = ttk.Spinbox(f, from_=0.30, to=0.99, increment=0.05, width=4)
            thr.bind("<MouseWheel>", lambda e: "break")
            thr.set("0.65")
            thr.pack(side="left", padx=1)
            btn = ttk.Button(f, text="浏览", width=4)
            btn.configure(command=self._make_file_picker(e))
            btn.pack(side="left")
            setattr(self, attr, e)
            setattr(self, attr + "_thr", thr)

        def cfg_text(parent, label, attr, width=10):
            f = ttk.Frame(parent)
            f.pack(side="left", padx=2)
            ttk.Label(f, text=label).pack(side="left")
            e = ttk.Entry(f, width=width)
            e.pack(side="left", padx=1)
            setattr(self, attr, e)

        def cfg_check(parent, label):
            f = ttk.Frame(parent)
            f.pack(side="left", padx=2)
            ttk.Label(f, text=label).pack(side="left")
            ttk.Checkbutton(f, variable=self.cfg_town_alt).pack(side="left")

        def cfg_header(parent, text):
            sep = ttk.Frame(parent)
            sep.pack(fill="x", pady=(6, 0))
            ttk.Label(sep, text=text, font=("", 8, "bold"), foreground="#555").pack(anchor="w")

        self.cfg_town_alt = tk.BooleanVar(value=True)

        cfg_header(cfg_frame, "\u258e\u8fdb\u5165\u6e38\u620f")
        r = ttk.Frame(cfg_frame); r.pack(fill="x")
        cfg_file(r, "进入游戏:", "cfg_enter_game")
        cfg_text(r, "窗口标题:", "cfg_window_title", width=20)

        cfg_header(cfg_frame, "▎城镇导航")
        ttk.Label(cfg_frame, text=(
            "提示：操作链从日常按钮开始，顺序识别并点击各个副本按钮。\n"
            "      若副本有多个确认步骤（如：确认进入→确认难度→确认消耗），可在下方「确认进入链」中配置多个模板。\n"
            "      单步骤副本只需配一张确认图即可。Alt显示鼠标用于全屏游戏下呼出光标。NPC图标用于自动寻路。"
        ), font=("", 8), foreground="#888", justify="left").pack(anchor="w", pady=(0, 4))
        r = ttk.Frame(cfg_frame); r.pack(fill="x", pady=(0, 2))
        cfg_check(r, "Alt显示鼠标:")
        cfg_file(r, "NPC图标:", "cfg_npc_marker")

        domain_chain_frame = ttk.LabelFrame(cfg_frame, text="城镇导航操作链（顺序识别）", padding=4)
        domain_chain_frame.pack(fill="x", pady=(2, 0))

        self.domain_chain = ChainStepList(domain_chain_frame,
                                          templates_dir=self.templates_path,
                                          import_func=_import_template_file,
                                          canvas_height=140)
        self.domain_chain.pack(fill="both", expand=True)

        enter_chain_frame = ttk.LabelFrame(cfg_frame, text="确认进入链（按顺序识别，单步骤只需一张图）", padding=4)
        enter_chain_frame.pack(fill="x", pady=(2, 0))
        self.confirm_enter_chain = ChainStepList(enter_chain_frame,
                                                  templates_dir=self.templates_path,
                                                  import_func=_import_template_file,
                                                  canvas_height=80)
        self.confirm_enter_chain.pack(fill="both", expand=True)

        cfg_header(cfg_frame, "▎副本战斗")
        r = ttk.Frame(cfg_frame); r.pack(fill="x")
        cfg_file(r, "再次挑战:", "cfg_rechallenge")
        cfg_file(r, "退出副本:", "cfg_exit_domain")
        cfg_file(r, "出口图标:", "cfg_portal")

        cfg_header(cfg_frame, "▎退出流程")
        r = ttk.Frame(cfg_frame); r.pack(fill="x")
        cfg_file(r, "退出→设置:", "cfg_exit_settings")
        cfg_file(r, "切换角色:", "cfg_exit_switch")
        cfg_file(r, "退出游戏:", "cfg_exit_game")
        r = ttk.Frame(cfg_frame); r.pack(fill="x")
        cfg_file(r, "确认退出:", "cfg_exit_confirm")

        cfg_header(cfg_frame, "\u258e\u89d2\u8272\u5217\u8868")
        table_frame = ttk.LabelFrame(cfg_frame, text="\u89d2\u8272\u5217\u8868", padding=6)
        table_frame.pack(fill="x", pady=(2, 0))
        self._char_table_frame = table_frame
        table_frame.columnconfigure(0, weight=1)

        hdr = ttk.Frame(table_frame)
        hdr.pack(fill="x", pady=(0, 2))
        ttk.Label(hdr, text="#", width=3, anchor="center").pack(side="left")
        ttk.Label(hdr, text="\u540d\u79f0", width=10, anchor="w").pack(side="left", padx=(4, 0))
        ttk.Label(hdr, text="\u6b21\u6570", width=5, anchor="center").pack(side="left", padx=(8, 0))
        ttk.Label(hdr, text="\u8fde\u62db", width=18, anchor="w").pack(side="left", padx=(8, 0))

        canvas_row = ttk.Frame(table_frame)
        canvas_row.pack(fill="both", expand=True)
        self._char_canvas = tk.Canvas(canvas_row, highlightthickness=0,
                                       bg=self._char_bg_color(), bd=0, height=140)
        char_scroll = ttk.Scrollbar(canvas_row, orient="vertical", command=self._char_canvas.yview)
        self._char_inner = ttk.Frame(self._char_canvas)
        self._char_inner.bind("<Configure>",
            lambda e: self._char_canvas.configure(scrollregion=self._char_canvas.bbox("all")))
        self._char_cw = self._char_canvas.create_window((0, 0), window=self._char_inner, anchor="nw")
        self._char_canvas.configure(yscrollcommand=char_scroll.set)
        self._char_canvas.pack(side="left", fill="both", expand=True)
        char_scroll.pack(side="right", fill="y")
        def _char_resize(event):
            self._char_canvas.itemconfig(self._char_cw, width=event.width - 2)
            self._char_canvas.configure(scrollregion=self._char_canvas.bbox("all"))
        self._char_canvas.bind("<Configure>", _char_resize)

        btn_frame = ttk.Frame(table_frame)
        btn_frame.pack(fill="x", pady=(4, 0))
        ttk.Button(btn_frame, text="+ \u6dfb\u52a0\u89d2\u8272", command=self._add_character).pack(side="left", padx=2)
        ttk.Button(btn_frame, text="\u67e5\u770b\u89d2\u8272", command=self._view_character).pack(side="left", padx=2)
        ttk.Button(btn_frame, text="\u79fb\u9664\u89d2\u8272", command=self._delete_character).pack(side="left", padx=2)
        ttk.Button(btn_frame, text="\u25b3 \u4e0a\u79fb", command=lambda: self._move_char(-1)).pack(side="left", padx=2)
        ttk.Button(btn_frame, text="\u25bd \u4e0b\u79fb", command=lambda: self._move_char(1)).pack(side="left", padx=2)
        ttk.Button(btn_frame, text="\u515c\u5e95\u8fde\u62db", command=self._pick_preset_fallback).pack(side="left", padx=8)

        self._char_rows = []
        self._char_selected = None

    def _is_inside_list_area(self, rx, ry):
        try:
            for container in (self._char_table_frame, self.domain_chain, self.confirm_enter_chain):
                cx = container.winfo_rootx()
                cy = container.winfo_rooty()
                cw = container.winfo_width()
                ch = container.winfo_height()
                if cx <= rx <= cx + cw and cy <= ry <= cy + ch:
                    return True
        except Exception:
            pass
        return False

    def _on_preset_page_click(self, event):
        if not self._is_inside_list_area(event.x_root, event.y_root):
            self._deselect_char()
            self.domain_chain._deselect()
            self.confirm_enter_chain._deselect()

    def _deselect_char(self):
        if self._char_selected is not None:
            for r_info in self._char_rows:
                try:
                    r_info["frame"].configure(style="TFrame")
                except Exception:
                    pass
        self._char_selected = None

    @staticmethod
    def _char_bg_color():
        try:
            return ttk.Style().lookup("TFrame", "background") or "#f0f0f0"
        except Exception:
            return "#f0f0f0"

    def _refresh_char_table(self):
        for child in self._char_inner.winfo_children():
            child.destroy()
        self._char_rows.clear()
        self._char_selected = None

        data = self.preset_data
        self.char_desc.delete(0, "end")
        self.char_desc.insert(0, data.get("description", ""))

        n, t = self._unpack_tpl(data.get("enter_game_template", ""))
        self.cfg_enter_game.delete(0, "end"); self.cfg_enter_game.insert(0, n)
        self.cfg_enter_game_thr.set(str(t))

        n, t = self._unpack_tpl(data.get("rechallenge_template", ""))
        self.cfg_rechallenge.delete(0, "end"); self.cfg_rechallenge.insert(0, n)
        self.cfg_rechallenge_thr.set(str(t))

        n, t = self._unpack_tpl(data.get("exit_domain_template", ""))
        self.cfg_exit_domain.delete(0, "end"); self.cfg_exit_domain.insert(0, n)
        self.cfg_exit_domain_thr.set(str(t))

        n, t = self._unpack_tpl(data.get("portal_template", ""))
        self.cfg_portal.delete(0, "end"); self.cfg_portal.insert(0, n)
        self.cfg_portal_thr.set(str(t))

        self.cfg_window_title.delete(0, "end")
        self.cfg_window_title.insert(0, data.get("window_title", ""))
        tn = data.get("town_nav", {})
        chain = tn.get("domain_select_steps", [])
        if isinstance(chain, str):
            chain = [chain] if chain else []
        self.domain_chain.set_items(chain)
        self.cfg_town_alt.set(tn.get("alt_for_mouse", True))

        self.confirm_enter_chain.set_items(tn.get("confirm_enter_template") or [])

        n, t = self._unpack_tpl(tn.get("npc_marker_template", ""))
        self.cfg_npc_marker.delete(0, "end"); self.cfg_npc_marker.insert(0, n)
        self.cfg_npc_marker_thr.set(str(t))
        te = data.get("town_exit", {})
        n, t = self._unpack_tpl(te.get("settings_template", ""))
        self.cfg_exit_settings.delete(0, "end"); self.cfg_exit_settings.insert(0, n)
        self.cfg_exit_settings_thr.set(str(t))

        n, t = self._unpack_tpl(te.get("switch_character_template", ""))
        self.cfg_exit_switch.delete(0, "end"); self.cfg_exit_switch.insert(0, n)
        self.cfg_exit_switch_thr.set(str(t))

        n, t = self._unpack_tpl(te.get("exit_game_template", ""))
        self.cfg_exit_game.delete(0, "end"); self.cfg_exit_game.insert(0, n)
        self.cfg_exit_game_thr.set(str(t))

        n, t = self._unpack_tpl(te.get("confirm_exit_template", ""))
        self.cfg_exit_confirm.delete(0, "end"); self.cfg_exit_confirm.insert(0, n)
        self.cfg_exit_confirm_thr.set(str(t))

        all_combos = list_combos()
        chars = data.get("characters", [])
        for i, ch in enumerate(chars):
            ch_name = ch.get("name", "").lower()
            matched = [c for c in all_combos if c and ch_name in c.lower()]
            rest = [c for c in all_combos if c and c not in matched]
            combo_names = [""] + matched + rest
            row = ttk.Frame(self._char_inner)
            row.pack(fill="x", pady=1)

            ttk.Label(row, text=str(i + 1), width=3, anchor="center").pack(side="left")

            name_label = ttk.Label(row, text=ch.get("name", ""), width=10, anchor="w")
            name_label.pack(side="left", padx=(4, 0))
            name_label.bind("<Button-1>", lambda e, idx=i: self._select_char(idx))
            row.bind("<Button-1>", lambda e, idx=i: self._select_char(idx))

            runs_var = tk.StringVar(value=str(ch.get("runs", 1)))
            sp = ttk.Spinbox(row, from_=1, to_=99, width=4, textvariable=runs_var)
            sp.pack(side="left", padx=(8, 0))
            sp.bind("<FocusOut>", lambda e, idx=i, v=runs_var: self._on_char_runs_changed(idx, v))
            sp.bind("<Return>", lambda e, idx=i, v=runs_var: self._on_char_runs_changed(idx, v))

            combo_var = tk.StringVar(value=ch.get("combo", "") or "")
            cb = ttk.Combobox(row, textvariable=combo_var, values=combo_names,
                              state="readonly", width=16)
            cb.pack(side="left", padx=(4, 0))
            cb.bind("<<ComboboxSelected>>", lambda e, idx=i, v=combo_var: self._on_char_combo_changed(idx, v))

            row_info = {"frame": row, "runs_var": runs_var, "combo_var": combo_var}
            self._char_rows.append(row_info)

        self._char_canvas.configure(scrollregion=self._char_canvas.bbox("all"))

    def _select_char(self, idx):
        self._char_selected = idx
        sel_style = "SelRow.TFrame"
        try:
            style = ttk.Style()
            bg = style.lookup("TFrame", "background") or "#e0e0e0"
            style.configure(sel_style, background="#b0d0ff")
        except Exception:
            pass
        for i, r in enumerate(self._char_rows):
            try:
                r["frame"].configure(style=sel_style if i == idx else "TFrame")
            except Exception:
                pass

    def _on_char_runs_changed(self, idx, var):
        chars = self.preset_data.get("characters", [])
        if 0 <= idx < len(chars):
            try:
                chars[idx]["runs"] = int(var.get())
            except ValueError:
                var.set(str(chars[idx].get("runs", 1)))

    def _on_char_combo_changed(self, idx, var):
        chars = self.preset_data.get("characters", [])
        if 0 <= idx < len(chars):
            val = var.get() or None
            chars[idx]["combo"] = val
            chars[idx].pop("combos", None)

    def _view_character_at(self, idx):
        chars = self.preset_data.get("characters", [])
        if 0 <= idx < len(chars):
            CharacterViewDialog(self.root, chars[idx])

    def _delete_character_at(self, idx):
        chars = self.preset_data.get("characters", [])
        if 0 <= idx < len(chars):
            name = chars[idx].get("name", "")
            if messagebox.askyesno("\u786e\u8ba4", f"\u79fb\u9664\u89d2\u8272\u300c{name}\u300d\uff1f"):
                chars.pop(idx)
                self._refresh_char_table()

    def _move_char_at(self, idx, direction):
        chars = self.preset_data.get("characters", [])
        new_idx = idx + direction
        if 0 <= new_idx < len(chars):
            chars[idx], chars[new_idx] = chars[new_idx], chars[idx]
            self._refresh_char_table()
            self._select_char(new_idx)

    def _new_preset(self):
        self.preset_data = self._load_default_preset()
        self.current_preset_path = None
        self._refresh_char_table()

    def _save_preset(self):
        self._sync_global_config()
        write_data = dict(self.preset_data)
        write_data["characters"] = serialize_characters(self.preset_data.get("characters", []))
        if self.current_preset_path:
            self.current_preset_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.current_preset_path, "w", encoding="utf-8") as f:
                json.dump(write_data, f, indent=2, ensure_ascii=False)
            messagebox.showinfo("保存成功", f"已保存到:\n{self.current_preset_path}")
            self._refresh_preset_list()
        else:
            self._save_preset_as()

    def _save_preset_as(self):
        self._sync_global_config()
        path = filedialog.asksaveasfilename(
            defaultextension=".json",
            initialdir=self.presets_path,
            filetypes=[("JSON", "*.json")],
        )
        if path:
            write_data = dict(self.preset_data)
            write_data["characters"] = serialize_characters(self.preset_data.get("characters", []))
            with open(path, "w", encoding="utf-8") as f:
                json.dump(write_data, f, indent=2, ensure_ascii=False)
            self.current_preset_path = Path(path)
            messagebox.showinfo("保存成功", f"已保存到:\n{path}")
            self._refresh_preset_list()

    @staticmethod
    def _pack_tpl(name, thr_val, original=None):
        return _pack_tpl_value(name, thr_val, original)

    @staticmethod
    def _unpack_tpl(value):
        return _unpack_tpl_value(value)

    def _sync_global_config(self):
        p = self.preset_data
        p["description"] = self.char_desc.get()
        p["window_title"] = self.cfg_window_title.get()
        p["enter_game_template"] = self._pack_tpl(self.cfg_enter_game.get(), self.cfg_enter_game_thr.get(), p.get("enter_game_template"))
        p["rechallenge_template"] = self._pack_tpl(self.cfg_rechallenge.get(), self.cfg_rechallenge_thr.get(), p.get("rechallenge_template"))
        p["exit_domain_template"] = self._pack_tpl(self.cfg_exit_domain.get(), self.cfg_exit_domain_thr.get(), p.get("exit_domain_template"))
        p["portal_template"] = self._pack_tpl(self.cfg_portal.get(), self.cfg_portal_thr.get(), p.get("portal_template"))
        fb = p.get("fallback_combo", "")
        if fb:
            p["fallback_combo"] = fb
        else:
            p.pop("fallback_combo", None)
        p.pop("fallback_combos", None)

        p.setdefault("town_nav", {})
        p["town_nav"]["domain_select_steps"] = self.domain_chain.get_items()
        p["town_nav"]["confirm_enter_template"] = self.confirm_enter_chain.get_items()
        p["town_nav"]["npc_marker_template"] = self._pack_tpl(self.cfg_npc_marker.get(), self.cfg_npc_marker_thr.get(), p["town_nav"].get("npc_marker_template"))
        p["town_nav"]["alt_for_mouse"] = self.cfg_town_alt.get()
        p.setdefault("town_exit", {})
        p["town_exit"]["settings_template"] = self._pack_tpl(self.cfg_exit_settings.get(), self.cfg_exit_settings_thr.get(), p["town_exit"].get("settings_template"))
        p["town_exit"]["switch_character_template"] = self._pack_tpl(self.cfg_exit_switch.get(), self.cfg_exit_switch_thr.get(), p["town_exit"].get("switch_character_template"))
        p["town_exit"]["exit_game_template"] = self._pack_tpl(self.cfg_exit_game.get(), self.cfg_exit_game_thr.get(), p["town_exit"].get("exit_game_template"))
        p["town_exit"]["confirm_exit_template"] = self._pack_tpl(self.cfg_exit_confirm.get(), self.cfg_exit_confirm_thr.get(), p["town_exit"].get("confirm_exit_template"))
        p["char_count"] = self.dash_char_count.get()
        p["char_start"] = self.dash_char_start.get()
        p["stealth"] = self.dash_stealth.get()
        p["background"] = self.dash_background.get()
        p["exit_after_done"] = self.dash_exit_after_done.get()

    def _add_character(self):
        names = self._charlib_ordered_names()
        if not names:
            if messagebox.askyesno("提示", "角色库为空，是否创建新角色？"):
                char = {"name": "新角色", "runs": 4}
                self.preset_data.setdefault("characters", []).append(char)
                self._refresh_char_table()
            return
        _PickCharacterDialog(self.root, names, self._on_char_picked)

    def _on_char_picked(self, name):
        profile = load_character_profile(name) or {}
        char = {"name": name, "runs": 4}
        for k in CHARACTER_PROFILE_FIELDS:
            if k in profile:
                char[k] = profile[k]
        self.preset_data.setdefault("characters", []).append(char)
        self._refresh_char_table()

    def _view_character(self):
        if self._char_selected is None:
            messagebox.showwarning("\u63d0\u793a", "\u8bf7\u5148\u9009\u62e9\u4e00\u4e2a\u89d2\u8272")
            return
        self._view_character_at(self._char_selected)

    def _delete_character(self):
        if self._char_selected is None:
            return
        self._delete_character_at(self._char_selected)

    def _move_char(self, direction):
        if self._char_selected is None:
            return
        self._move_char_at(self._char_selected, direction)

    def _pick_preset_fallback(self):
        names = [""] + list_combos()
        top = tk.Toplevel(self.root)
        top.title("\u9009\u62e9\u515c\u5e95\u8fde\u62db")
        _center_on_parent(top, self.root, 300, 120)
        top.transient(self.root)
        top.grab_set()
        var = tk.StringVar(value=self.preset_data.get("fallback_combo", "") or "")
        cb = ttk.Combobox(top, textvariable=var, values=names, state="readonly", width=30)
        cb.pack(padx=12, pady=(12, 4))
        def _ok():
            self.preset_data["fallback_combo"] = var.get() or None
            self.preset_data.pop("fallback_combos", None)
            top.destroy()
        ttk.Button(top, text="\u786e\u5b9a", command=_ok).pack(pady=(0, 8))
        top.wait_window()

    # ========== CHARACTER LIBRARY PAGE ==========
    def _build_charlib(self):
        f = self._pages["charlib"]
        top = ttk.Frame(f)
        top.pack(fill="x")
        ttk.Label(top, text="角色库", font=("", 14, "bold")).pack(side="left")
        ttk.Button(top, text="新建角色", command=self._charlib_new).pack(side="right", padx=2)
        ttk.Button(top, text="编辑", command=self._charlib_edit).pack(side="right", padx=2)
        ttk.Button(top, text="删除", command=self._charlib_delete).pack(side="right", padx=2)
        ttk.Button(top, text="上移", command=lambda: self._charlib_move(-1)).pack(side="right", padx=2)
        ttk.Button(top, text="下移", command=lambda: self._charlib_move(1)).pack(side="right", padx=2)

        cols = ("#", "名称", "选人头像", "技能栏", "结算画面", "城镇头像")
        self.charlib_tree = ttk.Treeview(f, columns=cols, show="headings", selectmode="browse")
        for c in cols:
            self.charlib_tree.heading(c, text=c)
            self.charlib_tree.column(c, width=30 if c == "#" else 100)
        self.charlib_tree.column("名称", width=80)
        scroll = ttk.Scrollbar(f, orient="vertical", command=self.charlib_tree.yview)
        self.charlib_tree.configure(yscrollcommand=scroll.set)
        self.charlib_tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.charlib_tree.bind("<Double-1>", lambda e: self._charlib_edit())

        self._refresh_charlib()

    def _charlib_order_path(self):
        return CHARACTERS_DIR / "_order.json"

    def _charlib_load_order(self):
        path = self._charlib_order_path()
        if path.exists():
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        return []

    def _charlib_save_order(self, order):
        with open(self._charlib_order_path(), "w", encoding="utf-8") as f:
            json.dump(order, f, ensure_ascii=False)

    def _charlib_ordered_names(self):
        order = self._charlib_load_order()
        existing = set(list_character_profiles())
        ordered = [n for n in order if n in existing]
        for n in sorted(existing - set(ordered)):
            ordered.append(n)
        self._charlib_save_order(ordered)
        return ordered

    def _refresh_charlib(self):
        self.charlib_tree.delete(*self.charlib_tree.get_children(""))
        for i, name in enumerate(self._charlib_ordered_names()):
            p = load_character_profile(name) or {}
            self.charlib_tree.insert("", "end", values=(
                i + 1,
                name,
                _unpack_tpl_value(p.get("portrait_template", ""))[0],
                _unpack_tpl_value(p.get("skill_bar_template", "") or "")[0],
                _unpack_tpl_value(p.get("result_screen_template", "") or "")[0],
                _unpack_tpl_value(p.get("avatar_template", "") or "")[0],
            ))

    def _charlib_move(self, direction):
        sel = self.charlib_tree.selection()
        if not sel:
            return
        idx = int(self.charlib_tree.item(sel[0], "values")[0]) - 1
        order = self._charlib_ordered_names()
        new_idx = idx + direction
        if 0 <= new_idx < len(order):
            order[idx], order[new_idx] = order[new_idx], order[idx]
            self._charlib_save_order(order)
            self._refresh_charlib()

    def _charlib_new(self):
        self._charlib_edit_at(None)

    def _charlib_edit(self):
        sel = self.charlib_tree.selection()
        if not sel:
            return
        name = self.charlib_tree.item(sel[0], "values")[1]
        self._charlib_edit_at(name)

    def _charlib_edit_at(self, name):
        _CharLibEditDialog(self.root, name, self.templates_path,
                           lambda: self._on_charlib_saved())

    def _charlib_delete(self):
        sel = self.charlib_tree.selection()
        if not sel:
            return
        name = self.charlib_tree.item(sel[0], "values")[1]
        if messagebox.askyesno("确认", f"删除角色「{name}」？\n模板文件不会被删除。"):
            path = CHARACTERS_DIR / f"{name}.json"
            if path.exists():
                path.unlink()
            order = self._charlib_load_order()
            if name in order:
                order.remove(name)
                self._charlib_save_order(order)
            self._on_charlib_saved()

    def _on_charlib_saved(self):
        self._refresh_charlib()
        if self.preset_data and self._pages["characters"].winfo_ismapped():
            from config.settings import resolve_characters
            pn = self.current_preset_path.stem if self.current_preset_path else ""
            self.preset_data["characters"] = resolve_characters(self.preset_data, pn)
            self._refresh_char_table()

    # ========== DEVTOOLS PAGE ==========
    def _build_devtools(self):
        f = self._pages["devtools"]
        ttk.Label(f, text="开发者工具", font=("", 14, "bold")).pack(anchor="w", pady=(0, 8))
        card = ttk.LabelFrame(f, text="调试选项", padding=10)
        card.pack(fill="x")
        r = ttk.Frame(card); r.pack(fill="x", pady=2)
        ttk.Label(r, text="起始状态:").pack(side="left")
        self.dash_start_state = tk.StringVar(value="character_select")
        states = [
            ("character_select", "角色选择（默认流程）"),
            ("town_nav", "城镇导航"),
            ("npc_navigate", "NPC寻路"),
            ("domain_loading", "副本加载中"),
            ("domain_combat", "副本战斗"),
            ("dungeon_exit_nav", "副本出口寻路"),
            ("map_loading", "地图加载中"),
            ("town_exit", "城镇退出（测试角色切换）"),
            ("complete", "全部完成"),
        ]
        state_combo = ttk.Combobox(r, textvariable=self.dash_start_state,
                                    values=[s[1] for s in states], state="readonly", width=28)
        state_combo.pack(side="left", padx=4)
        self._state_keys = dict(states)
        self._state_cn_to_key = {cn: key for key, cn in states}
        state_combo.set("角色选择（默认流程）")

        card2 = ttk.LabelFrame(f, text="模板匹配测试", padding=10)
        card2.pack(fill="x", pady=(8, 0))
        r2 = ttk.Frame(card2); r2.pack(fill="x")
        ttk.Label(r2, text="点击按钮截取当前画面，测试所有角色模板匹配度").pack(side="left")
        ttk.Button(r2, text="测试角色模板匹配", command=self._test_all_templates).pack(side="left", padx=10)

    def _test_all_templates(self):
        def _run():
            try:
                import mss
                from config.settings import list_character_profiles, load_character_profile, parse_template_ref
                from recognition.template import find_template
                import logging
                logger = logging.getLogger(__name__)
                logger.info("=" * 50)
                logger.info("开始角色模板匹配测试")
                with mss.mss() as sct:
                    mon = sct.monitors[1]
                    img = sct.grab(mon)
                    import numpy as np
                    frame = np.array(img)
                    frame = frame[:, :, :3]
                    frame = frame[:, :, ::-1]
                for name in list_character_profiles():
                    profile = load_character_profile(name)
                    if not profile:
                        continue
                    logger.info("--- 角色: %s ---", name)
                    for label, field in [
                        ("选人界面头像", "portrait_template"),
                        ("技能栏", "skill_bar_template"),
                        ("结算画面", "result_screen_template"),
                        ("城镇头像", "avatar_template"),
                    ]:
                        val = profile.get(field)
                        tpl_name, tpl_thr = parse_template_ref(val)
                        if not tpl_name:
                            logger.info("  %s: 未配置", label)
                            continue
                        r = find_template(frame, tpl_name, threshold=0.10)
                        if r:
                            logger.info("  %s: %s 置信度=%.3f 位置(%d,%d) [阈值=%.2f]",
                                        label, tpl_name, r["confidence"],
                                        r["center"][0], r["center"][1], tpl_thr)
                        else:
                            logger.info("  %s: %s 未匹配 (最高<0.10)", label, tpl_name)
                logger.info("角色模板匹配测试完成")
                logger.info("=" * 50)
            except Exception as e:
                import logging
                logging.getLogger(__name__).error("模板测试失败: %s", e)
        import threading
        threading.Thread(target=_run, daemon=True).start()

    # ========== COMBO MANAGEMENT PAGE ==========
    def _build_recorder(self):
        f = self._pages["recorder"]
        f.columnconfigure(0, weight=1)
        f.rowconfigure(0, weight=1)

        rec_canvas = tk.Canvas(f, highlightthickness=0, bd=0)
        rec_scroll = ttk.Scrollbar(f, orient="vertical", command=rec_canvas.yview)
        rec_content = ttk.Frame(rec_canvas)
        rec_content.bind("<Configure>",
            lambda e: rec_canvas.configure(scrollregion=rec_canvas.bbox("all")))
        rec_cw = rec_canvas.create_window((0, 0), window=rec_content, anchor="nw")
        rec_canvas.configure(yscrollcommand=rec_scroll.set)
        rec_canvas.grid(row=0, column=0, sticky="nsew")
        rec_scroll.grid(row=0, column=1, sticky="ns")
        self._rec_canvas = rec_canvas
        def _rec_resize(event):
            rec_canvas.itemconfig(rec_cw, width=event.width - 2)
            rec_canvas.configure(scrollregion=rec_canvas.bbox("all"))
        rec_canvas.bind("<Configure>", _rec_resize)
        rec_canvas.bind("<MouseWheel>",
            lambda e: rec_canvas.yview_scroll(int(-e.delta / 120), "units"), "+")

        rec_content.columnconfigure(0, weight=1)

        ttk.Label(rec_content, text="\u8fde\u62db\u7ba1\u7406", font=("", 14, "bold")).grid(row=0, column=0, sticky="w", pady=(0, 4))
        instr = ttk.LabelFrame(rec_content, text="\u4f7f\u7528\u8bf4\u660e", padding=8)
        instr.grid(row=1, column=0, sticky="ew", pady=(0, 4))
        ttk.Label(instr, text=(
            "\u5f55\u5236: \u6309F5\u5f00\u59cb/\u505c\u6b62\uff0c3\u79d2\u5012\u8ba1\u65f6\u540e\u5f55\u5236\uff0c\u505c\u6b62\u540e\u81ea\u52a8\u586b\u5145\u5230\u201c\u9009\u4e2d\u8fde\u62db\u8be6\u60c5\u201d\n"
            "\u624b\u52a8: \u70b9\u51fb\u201c\u65b0\u5efa\u8fde\u62db\u201d\uff0c\u81ea\u5b9a\u4e49\u6309\u952e\u3001\u5ef6\u8fdf\u3001\u91cd\u590d\u7b49\u53c2\u6570"
        ), justify="left").pack(anchor="w")

        row = ttk.Frame(rec_content)
        row.grid(row=2, column=0, sticky="ew", pady=2)
        self.rec_btn = ttk.Button(row, text="\u25b6 F5 \u5f00\u59cb\u5f55\u5236", command=self._toggle_recording)
        self.rec_btn.pack(side="left", padx=(0, 6))
        self.rec_status = ttk.Label(row, text="\u5c31\u7eea", foreground="gray")
        self.rec_status.pack(side="left", padx=6)
        ttk.Button(row, text="\u4fdd\u5b58\u5f55\u5236\u7ed3\u679c", command=self._save_recorded).pack(side="left", padx=4)

        sep = ttk.Separator(rec_content, orient="horizontal")
        sep.grid(row=3, column=0, sticky="ew", pady=6)

        rec_content.rowconfigure(5, weight=1)
        rec_content.rowconfigure(6, weight=1)
        self._build_combo_manager(rec_content, row_start=5)

        self._recorded_actions = None
        self._recorder_instance = None
        self._setup_recorder_hotkeys()

    def _build_combo_manager(self, parent, row_start):
        mgr = ttk.LabelFrame(parent, text="\u8fde\u62db\u5e93", padding=4)
        mgr.grid(row=row_start, column=0, sticky="nsew", pady=2)
        parent.rowconfigure(row_start, weight=1)
        mgr.columnconfigure(0, weight=1)
        mgr.rowconfigure(1, weight=1)

        cols = ("\u540d\u79f0", "\u52a8\u4f5c\u6570", "\u65f6\u957f(s)", "\u6765\u6e90")
        search_row = ttk.Frame(mgr)
        search_row.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 2))
        ttk.Label(search_row, text="\u641c\u7d22:").pack(side="left")
        self.combo_search_var = tk.StringVar()
        self.combo_search_entry = ttk.Entry(search_row, textvariable=self.combo_search_var, width=18)
        self.combo_search_entry.pack(side="left", padx=4)
        self.combo_search_entry.bind("<KeyRelease>", lambda e: self._refresh_combo_list())
        ttk.Button(search_row, text="\u2715", width=2,
                   command=lambda: (self.combo_search_var.set(""), self._refresh_combo_list())).pack(side="left")
        self.combo_tree = ttk.Treeview(mgr, columns=cols, show="headings", height=8)
        self.combo_tree.heading("\u540d\u79f0", text="\u540d\u79f0")
        self.combo_tree.heading("\u52a8\u4f5c\u6570", text="\u52a8\u4f5c\u6570")
        self.combo_tree.heading("\u65f6\u957f(s)", text="\u65f6\u957f(s)")
        self.combo_tree.heading("\u6765\u6e90", text="\u6765\u6e90")
        self.combo_tree.column("\u540d\u79f0", width=140)
        self.combo_tree.column("\u52a8\u4f5c\u6570", width=60)
        self.combo_tree.column("\u65f6\u957f(s)", width=60)
        self.combo_tree.column("\u6765\u6e90", width=80)
        scroll_c = ttk.Scrollbar(mgr, orient="vertical", command=self.combo_tree.yview)
        self.combo_tree.configure(yscrollcommand=scroll_c.set)
        self.combo_tree.grid(row=1, column=0, sticky="nsew")
        scroll_c.grid(row=1, column=1, sticky="ns")
        self.combo_tree.bind("<Double-1>", lambda e: self._edit_combo())
        self.combo_tree.bind("<<TreeviewSelect>>", lambda e: self._show_combo_detail())

        btn_row = ttk.Frame(mgr)
        btn_row.grid(row=2, column=0, columnspan=2, pady=4, sticky="ew")
        ttk.Button(btn_row, text="\u65b0\u5efa\u8fde\u62db", command=self._new_combo).pack(side="left", padx=2)
        ttk.Button(btn_row, text="\u7f16\u8f91", command=self._edit_combo).pack(side="left", padx=2)
        ttk.Button(btn_row, text="\u5220\u9664", command=self._delete_combo).pack(side="left", padx=2)
        ttk.Button(btn_row, text="\u9884\u89c8", command=self._preview_combo).pack(side="left", padx=2)
        ttk.Button(btn_row, text="\u6253\u5f00\u6587\u4ef6\u5939", command=self._open_combos_folder).pack(side="left", padx=2)

        detail_frame = ttk.LabelFrame(parent, text="\u9009\u4e2d\u8fde\u62db\u8be6\u60c5", padding=4)
        detail_frame.grid(row=row_start + 1, column=0, sticky="nsew", pady=(4, 0))
        parent.rowconfigure(row_start + 1, weight=1)
        detail_frame.columnconfigure(0, weight=1)
        detail_frame.rowconfigure(0, weight=1)
        detail_cols = ("#", "\u6309\u952e", "\u6301\u7eed", "\u524d\u5ef6", "\u540e\u5ef6", "\u6309\u4f4f", "\u91cd\u590d")
        self.combo_detail_tree = ttk.Treeview(detail_frame, columns=detail_cols,
                                               show="headings", height=10)
        for c in detail_cols:
            self.combo_detail_tree.heading(c, text=c)
            w = 30 if c == "#" else 55
            self.combo_detail_tree.column(c, width=w)
        self.combo_detail_tree.column("\u6309\u952e", width=110)
        detail_scroll = ttk.Scrollbar(detail_frame, orient="vertical",
                                       command=self.combo_detail_tree.yview)
        self.combo_detail_tree.configure(yscrollcommand=detail_scroll.set)
        self.combo_detail_tree.grid(row=0, column=0, sticky="nsew")
        detail_scroll.grid(row=0, column=1, sticky="ns")

        self._combo_dir = COMBO_DIR
        self._refresh_combo_list()

    def _refresh_combo_list(self):
        self.combo_tree.delete(*self.combo_tree.get_children(""))
        kw = self.combo_search_var.get().strip().lower()
        for name in list_combos():
            if kw and kw not in name.lower():
                continue
            data = load_combo(name)
            if data:
                data = self._ensure_combo_metadata(name, data)
                actions = data.get("actions", [])
                dur = data.get("duration_sec", 0)
                source = data.get("source", "")
                self.combo_tree.insert("", "end", values=(name, len(actions),
                                        f"{dur:.1f}" if dur else "-", source),
                                        tags=(name,))
            else:
                self.combo_tree.insert("", "end", values=(name, "?", "-", ""),
                                        tags=(name,))

    @staticmethod
    def _ensure_combo_metadata(name, data):
        changed = False
        if not data.get("source"):
            data["source"] = "\u5f55\u5236" if data.get("recorded_at") else "\u624b\u52a8\u914d\u7f6e"
            changed = True
        if not data.get("duration_sec"):
            dur = sum(a.get("duration", 0.1) + a.get("delay_before", 0) + a.get("delay_after", 0)
                      for a in data.get("actions", []))
            data["duration_sec"] = round(dur, 1)
            changed = True
        if changed:
            save_combo(name, data)
        return data

    def _show_combo_detail(self):
        self.combo_detail_tree.delete(*self.combo_detail_tree.get_children(""))
        sel = self.combo_tree.selection()
        if not sel:
            return
        name = self.combo_tree.item(sel[0], "tags")[0]
        data = load_combo(name)
        if not data:
            return
        for i, act in enumerate(data.get("actions", [])):
            self.combo_detail_tree.insert("", "end", values=(
                i + 1,
                "+".join(act.get("keys", [])),
                act.get("duration", 0.1),
                act.get("delay_before", 0.0),
                act.get("delay_after", 0.0),
                "\u662f" if act.get("hold") else "\u5426",
                act.get("repeat", 1),
            ))

    def _new_combo(self):
        ComboEditDialog(self.root, None, lambda: self._refresh_combo_list())

    def _edit_combo(self):
        sel = self.combo_tree.selection()
        if not sel:
            messagebox.showwarning("\u63d0\u793a", "\u8bf7\u5148\u9009\u62e9\u4e00\u4e2a\u8fde\u62db")
            return
        name = self.combo_tree.item(sel[0], "tags")[0]
        data = load_combo(name)
        if not data:
            return
        ComboEditDialog(self.root, data, lambda: self._refresh_combo_list())

    def _delete_combo(self):
        sel = self.combo_tree.selection()
        if not sel:
            return
        name = self.combo_tree.item(sel[0], "tags")[0]
        refs = []
        for pn in self.presets_path.glob("*.json"):
            try:
                with open(pn, encoding="utf-8") as f:
                    pd = json.load(f)
                for ch in pd.get("characters", []):
                    if ch.get("combo") == name or ch.get("fallback_combo") == name:
                        refs.append(f"{pn.stem}/{ch.get('name','?')}")
                if pd.get("fallback_combo") == name:
                    refs.append(f"{pn.stem}(\u9884\u8bbe\u515c\u5e95)")
            except Exception:
                pass
        msg = f"\u786e\u5b9a\u5220\u9664\u8fde\u62db \u201c{name}\u201d\uff1f"
        if refs:
            msg += f"\n\n\u4ee5\u4e0b\u89d2\u8272\u6b63\u5728\u4f7f\u7528\u6b64\u8fde\u62db:\n" + "\n".join(refs[:10])
            if len(refs) > 10:
                msg += f"\n... \u8fd8\u6709 {len(refs) - 10} \u5904\u5f15\u7528"
        if messagebox.askyesno("\u786e\u8ba4\u5220\u9664", msg):
            delete_combo(name)
            self._refresh_combo_list()

    def _preview_combo(self):
        sel = self.combo_tree.selection()
        if not sel:
            return
        name = self.combo_tree.item(sel[0], "tags")[0]
        data = load_combo(name)
        if not data:
            return
        text = json.dumps(data, indent=2, ensure_ascii=False)
        top = tk.Toplevel(self.root)
        top.title(f"\u8fde\u62db\u9884\u89c8 - {name}")
        _center_on_parent(top, self.root, 700, 500)
        txt = tk.Text(top, wrap="word", font=("Consolas", 9))
        txt.pack(fill="both", expand=True, padx=6, pady=6)
        txt.insert("1.0", text)
        txt.configure(state="disabled")

    def _open_combos_folder(self):
        self._combo_dir.mkdir(parents=True, exist_ok=True)
        os.startfile(str(self._combo_dir))

    def _notify(self, title, message, timeout=3):
        try:
            from plyer import notification
            threading.Thread(target=lambda: notification.notify(
                title=title, message=message, timeout=timeout, app_name="GameBot"
            ), daemon=True).start()
        except Exception:
            pass

    def _setup_recorder_hotkeys(self):
        self._hotkeys_enabled = True
        self._rec_f5_prev = False
        def _poll():
            if not hasattr(self, 'root'):
                return
            try:
                import ctypes
                u32 = ctypes.windll.user32
                f5 = bool(u32.GetAsyncKeyState(0x74) & 0x8000)
                if self._hotkeys_enabled:
                    if f5 and not self._rec_f5_prev:
                        self.root.after(0, self._toggle_recording)
                self._rec_f5_prev = f5
            except Exception:
                pass
            self.root.after(20, _poll)
        self.root.after(20, _poll)

    def _toggle_recording(self):
        if not hasattr(self, "_recording_active") or not self._recording_active:
            self.rec_btn.configure(text="⏹ F5 停止录制")
            self.rec_status.configure(text="倒计时3s...", foreground="orange")
            self._recording_active = True
            self._recorded_actions = None
            self._recorder_instance = None
            self._notify("GameBot 连招录制", "3秒后开始录制，按 F5 停止")
            threading.Thread(target=self._do_record, daemon=True).start()
        else:
            self._stop_recording()

    def _stop_recording(self):
        self._recording_active = False
        if self._recorder_instance:
            self._recorder_instance._recording = False
        self.rec_btn.configure(text="▶ F5 开始录制")
        self.rec_status.configure(text="已停止", foreground="red")
        self._recorder_instance = None
        self._notify("GameBot 录制已停止", "连招录制已手动停止")

    def _do_record(self):
        from utils.macro_recorder import MacroRecorder
        import uuid
        output_dir = self._combo_dir
        temp_name = f"_temp_{uuid.uuid4().hex[:8]}"
        recorder = MacroRecorder(output_dir=output_dir)
        self._recorder_instance = recorder
        result = recorder.record(name=temp_name)
        self._recording_active = False
        self._recorder_instance = None
        self.root.after(0, lambda: self._recording_done(result))

    def _recording_done(self, result):
        self.rec_btn.configure(text="▶ F5 开始录制")
        if result:
            self._recorded_actions = result["actions"]
            if self._recorded_actions and self._recorded_actions[0].get("delay_before", 0.0) < 1.0:
                self._recorded_actions[0]["delay_before"] = 1.0
            dur = result["duration_sec"]
            self.rec_status.configure(text=f"录制完成 ({dur}s)", foreground="green")
            self._notify("GameBot 录制完成", f"录制 {dur}s，共 {len(result['actions'])} 个动作")
            self._recorded_duration = dur
            self.combo_detail_tree.delete(*self.combo_detail_tree.get_children(""))
            for i, act in enumerate(result["actions"]):
                self.combo_detail_tree.insert("", "end", values=(
                    i + 1,
                    "+".join(act.get("keys", [])),
                    act.get("duration", 0.1),
                    act.get("delay_before", 0.0),
                    act.get("delay_after", 0.0),
                    "\u662f" if act.get("hold") else "\u5426",
                    act.get("repeat", 1),
                ))
            self.root.after(200, self._cleanup_temp_combos)
        else:
            self.rec_status.configure(text="录制失败或无事件", foreground="red")

    def _cleanup_temp_combos(self):
        for p in self._combo_dir.glob("_temp_*.json"):
            try:
                p.unlink()
            except Exception:
                pass

    def _prompt_save_combo(self):
        if not self._recorded_actions:
            self._cleanup_temp_combos()
            return
        name = filedialog.asksaveasfilename(
            title="\u4fdd\u5b58\u8fde\u62db\u6587\u4ef6",
            initialdir=self._combo_dir,
            defaultextension=".json",
            filetypes=[("JSON", "*.json")],
        )
        if not name:
            return
        name = Path(name)
        dur = getattr(self, '_recorded_duration', 0.0)
        if self._recorded_actions and self._recorded_actions[0].get("delay_before", 0.0) < 1.0:
            self._recorded_actions[0]["delay_before"] = 1.0
        output = {
            "name": name.stem,
            "source": "\u5f55\u5236",
            "recorded_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "duration_sec": dur,
            "actions": self._recorded_actions,
        }
        save_combo(name.stem, output)
        self._cleanup_temp_combos()
        self._refresh_combo_list()
        self.rec_status.configure(text=f"\u5df2\u4fdd\u5b58: {name.name}", foreground="green")

    def _save_recorded(self):
        if not self._recorded_actions:
            messagebox.showwarning("提示", "没有录制的连招可保存")
            return
        self._prompt_save_combo()

    # ========== SCREENSHOT PAGE ==========
    def _build_screenshot(self):
        f = self._pages["screenshot"]
        ttk.Label(f, text="截图工具", font=("", 14, "bold")).pack(anchor="w")
        instr = ttk.LabelFrame(f, text="使用说明", padding=10)
        instr.pack(fill="x", pady=6)
        ttk.Label(instr, text=(
            "1. 将游戏窗口调整到合适位置（保持前置）\n"
            "2. 点击「开始截图」，GUI 自动隐藏\n"
            "3. 3 秒倒计时后截取全屏\n"
            "4. 在预览图中用鼠标拖拽选择目标区域\n"
            "5. 输入文件名，保存到 templates/ 目录\n\n"
            "提示: 截取后可在预设管理中使用「浏览」按钮引用新模板"
        ), justify="left").pack(anchor="w")
        self.screenshot_list_frame = ttk.LabelFrame(f, text="已保存模板", padding=6)
        self.screenshot_list_frame.pack(fill="both", expand=True, pady=4)
        self.screenshot_list_frame.columnconfigure(0, weight=1)
        self.screenshot_list_frame.rowconfigure(0, weight=1)
        cols = ("文件名", "大小(KB)", "修改时间")
        self.screenshot_tree = ttk.Treeview(self.screenshot_list_frame, columns=cols,
                                             show="headings", height=6)
        for c in cols:
            self.screenshot_tree.heading(c, text=c)
            self.screenshot_tree.column(c, width=80)
        self.screenshot_tree.column("文件名", width=200)
        scroll_st = ttk.Scrollbar(self.screenshot_list_frame, orient="vertical",
                                   command=self.screenshot_tree.yview)
        self.screenshot_tree.configure(yscrollcommand=scroll_st.set)
        self.screenshot_tree.grid(row=0, column=0, sticky="nsew")
        scroll_st.grid(row=0, column=1, sticky="ns")
        self.screenshot_tree.bind("<Double-1>", lambda e: self._open_template_folder())
        btn_frame = ttk.Frame(f)
        btn_frame.pack(fill="x", pady=6)
        ttk.Button(btn_frame, text="📷 开始截图", command=self._start_screenshot).pack(side="left", padx=2)
        ttk.Button(btn_frame, text="打开模板文件夹", command=self._open_template_folder).pack(side="left", padx=2)
        ttk.Button(btn_frame, text="刷新列表", command=self._refresh_screenshot_list).pack(side="left", padx=2)
        self.screenshot_status = ttk.Label(f, text="就绪", foreground="gray")
        self.screenshot_status.pack(anchor="w", pady=(0, 4))
        self._refresh_screenshot_list()

    def _refresh_screenshot_list(self):
        self.screenshot_tree.delete(*self.screenshot_tree.get_children(""))
        if not self.templates_path.exists():
            return
        for f in sorted(self.templates_path.iterdir()):
            if f.is_file() and f.suffix.lower() in (".png", ".jpg", ".jpeg", ".bmp"):
                size_kb = f.stat().st_size // 1024
                mtime = datetime.fromtimestamp(f.stat().st_mtime).strftime("%m-%d %H:%M")
                self.screenshot_tree.insert("", "end", values=(f.name, size_kb, mtime))

    def _open_template_folder(self):
        self.templates_path.mkdir(parents=True, exist_ok=True)
        os.startfile(str(self.templates_path))

    def _start_screenshot(self):
        self.screenshot_status.configure(text="准备截图...", foreground="orange")
        self.root.withdraw()
        self.root.after(3000, self._capture_fullscreen)

    def _capture_fullscreen(self):
        try:
            import mss
            import numpy as np
            sct = mss.mss()
            monitor = sct.monitors[0]
            raw = sct.grab(monitor)
            img = np.array(raw)[:, :, :3]
            sct.close()
        except Exception as e:
            logger.error("截图失败: %s", e)
            self.root.deiconify()
            self.screenshot_status.configure(text=f"截图失败: {e}", foreground="red")
            return
        self.root.deiconify()
        ScreenshotCropDialog(self.root, img, self.templates_path,
                              on_save=lambda: self._refresh_screenshot_list())
        self.screenshot_status.configure(text="就绪", foreground="gray")

    # ========== SETTINGS PAGE ==========
    def _build_settings(self):
        f = self._pages["settings"]
        ttk.Label(f, text="全局设置", font=("", 14, "bold")).pack(anchor="w")
        note = ttk.LabelFrame(f, text="说明", padding=6)
        note.pack(fill="x", pady=4)
        ttk.Label(note, text=(
            "这些设置保存在 settings.json 中，影响 Bot 运行时的行为。"
        ), wraplength=600).pack(anchor="w")
        self._settings_widgets = {}
        cfg_frame = ttk.LabelFrame(f, text="Bot 设置", padding=10)
        cfg_frame.pack(fill="x", pady=6)
        fields = [
            ("capture_method", "捕获方式", ["auto", "dxcam", "mss"]),
            ("fps_limit", "FPS 限制", None),
            ("stuck_threshold_sec", "卡死判定(秒)", None),
            ("ssim_threshold", "SSIM 阈值(0-1)", None),
            ("combo_randomness", "连招随机度(0-1)", None),
            ("click_jitter_px", "点击抖动(像素)", None),
            ("min_npc_match_count", "NPC最小匹配数", None),
            ("mouse_bezier_steps", "鼠标贝塞尔步数", None),
            ("blue_hsv_lower", "蓝线HSV下限", None),
            ("blue_hsv_upper", "蓝线HSV上限", None),
        ]
        cfg = Settings()
        cfg.load()
        for key, label, opts in fields:
            row = ttk.Frame(cfg_frame)
            row.pack(fill="x", pady=1)
            ttk.Label(row, text=label + ":", width=20, anchor="e").pack(side="left")
            val = cfg._data.get(key, "")
            if opts:
                var = tk.StringVar(value=str(val))
                w = ttk.Combobox(row, textvariable=var, values=opts, width=12, state="readonly")
            elif isinstance(val, list):
                var = tk.StringVar(value=",".join(str(v) for v in val))
                w = ttk.Entry(row, textvariable=var, width=28)
            else:
                var = tk.StringVar(value=str(val))
                w = ttk.Entry(row, textvariable=var, width=28)
            w.pack(side="left", padx=6)
            self._settings_widgets[key] = var
        ttk.Button(cfg_frame, text="保存设置", command=self._save_settings).pack(pady=6)

    def _save_settings(self):
        cfg = Settings()
        for key, var in self._settings_widgets.items():
            raw = var.get()
            default = cfg._data.get(key)
            if isinstance(default, list):
                try:
                    cfg._data[key] = [int(x.strip()) if x.strip() else 0 for x in raw.split(",")]
                except Exception:
                    cfg._data[key] = [int(x) for x in raw.split(",")]
            elif isinstance(default, bool):
                cfg._data[key] = raw.lower() in ("true", "1", "yes")
            elif isinstance(default, int):
                cfg._data[key] = int(float(raw))
            elif isinstance(default, float):
                try:
                    cfg._data[key] = float(raw)
                except ValueError:
                    cfg._data[key] = raw
            else:
                cfg._data[key] = raw
        cfg.save()
        messagebox.showinfo("保存成功", "设置已保存到 settings.json")

    # ========== BOT CONTROL ==========
    def _toggle_bot(self):
        if self.bot_running:
            self._stop_bot()
        else:
            self._start_bot()

    def _start_bot(self):
        name = self.dash_preset_var.get()
        if not name:
            messagebox.showwarning("提示", "请先选择一个预设")
            return
        self._sync_global_config()
        chars = self.preset_data.get("characters", [])
        if not chars:
            messagebox.showwarning("提示", "预设中没有配置角色，请先添加角色")
            return
        counts = self.dash_char_count.get()
        char_start = self.dash_char_start.get()
        stealth = self.dash_stealth.get()
        bg = self.dash_background.get()
        self._vdm = None
        if bg:
            from utils.virtual_display import VirtualDisplayManager
            vdm = VirtualDisplayManager()
            if not vdm.is_installed():
                msg = ("后台模式需要安装虚拟显示器驱动(VDD)。\n\n"
                       "点击\"是\"自动安装（约30秒）。\n"
                       "安装期间窗口可能暂时无响应。")
                if not messagebox.askyesno("安装虚拟显示器", msg):
                    return
                ok = vdm.install()
                if not ok:
                    messagebox.showerror("安装失败",
                        "VDD安装失败，请手动执行以下命令：\n\n"
                        "winget install --id=VirtualDrivers.Virtual-Display-Driver -e")
                    return
                messagebox.showinfo("安装成功", "虚拟显示器驱动已安装")
            self._vdm = vdm
        exit_after_done = self.dash_exit_after_done.get()
        start_state_cn = self.dash_start_state.get()
        start_state = self._state_cn_to_key.get(start_state_cn, "character_select")
        cfg = Settings()
        cfg.last_preset = name
        cfg.save()
        self._hotkeys_enabled = False
        self.bot_stop_event.clear()
        self.bot_running = True
        self.status_label.configure(text="● 运行中", foreground="green")
        self.start_btn.configure(text="⏹ 停止")

        if start_state != "character_select":
            try:
                import pywinctl as pwc
                preset = self.preset_data
                title = preset.get("window_title", "")
                if title:
                    wins = pwc.getWindowsWithTitle(title)
                else:
                    wins = [w for w in pwc.getAllWindows()
                            if w.visible and not w.isMinimized and w.width > 800 and w.height > 600]
                if wins:
                    best = max(wins, key=lambda w: w.width * w.height)
                    best.activate()
                    time.sleep(0.5)
                    logger.info("预激活游戏窗口: %s", best.title)
            except Exception as e:
                logger.warning("预激活窗口失败: %s", e)

        self.bot_thread = threading.Thread(
            target=self._run_bot,
            args=(name, counts, char_start, stealth, bg, start_state, exit_after_done),
            daemon=True,
        )
        self.bot_thread.start()

    def _run_bot(self, preset_name, total_chars, char_start, stealth, bg, start_state="character_select", exit_after_done=True):
        controller = None
        capture = None
        window_mgr = None
        try:
            from config.settings import Settings, resolve_characters
            from core.blackboard import Blackboard
            from core.fsm import FSM
            from capture.screen import ScreenCapture
            from input.controller import Controller, _SAFE_STEALTH_STATES
            from core.watchdog import Watchdog
            from states.character_select import CharacterSelectState
            from states.town_nav import TownNavState
            from states.domain_loading import DomainLoadingState
            from states.domain_combat import DomainCombatState
            from states.dungeon_exit_nav import DungeonExitNavState
            from states.map_loading import MapLoadingState
            from states.town_exit import TownExitState
            from states.complete import CompleteState
            from states.stuck_recovery import StuckRecoveryState
            from states.npc_navigate import NPCNavigateState

            cfg = Settings()
            cfg.load()
            preset = self.preset_data
            preset["characters"] = resolve_characters(preset, preset_name)
            blackboard = Blackboard()
            self._blackboard = blackboard
            blackboard["preset_name"] = preset_name
            blackboard["preset"] = preset
            blackboard["total_characters"] = max(1, char_start) - 1 + max(1, total_chars)
            blackboard["current_character_index"] = max(0, char_start - 1)
            blackboard["domain_run_count"] = 0
            blackboard["exit_after_done"] = exit_after_done

            capture = ScreenCapture()
            capture.start(method=cfg.capture_method, fps_limit=cfg.fps_limit)
            blackboard["_capture"] = capture

            controller = Controller(stealth=stealth,
                                    combo_randomness=cfg.combo_randomness,
                                    bezier_steps=cfg.mouse_bezier_steps,
                                    click_jitter=cfg.click_jitter_px,
                                    background_mode=bg)
            self._controller = controller

            fsm = FSM()
            blackboard["_fsm"] = fsm
            fsm.add("character_select", CharacterSelectState(controller))
            fsm.add("town_nav", TownNavState(controller))
            fsm.add("domain_loading", DomainLoadingState())
            fsm.add("domain_combat", DomainCombatState(controller, cfg.combo_randomness))
            fsm.add("dungeon_exit_nav", DungeonExitNavState(controller))
            fsm.add("map_loading", MapLoadingState())
            fsm.add("town_exit", TownExitState(controller))
            fsm.add("complete", CompleteState(controller))
            fsm.add("stuck_recovery", StuckRecoveryState(controller))
            fsm.add("npc_navigate", NPCNavigateState(controller))
            watchdog = Watchdog(threshold_sec=cfg.stuck_threshold_sec,
                                ssim_threshold=cfg.ssim_threshold)
            blackboard["_watchdog"] = watchdog

            window_mgr = None
            from utils.window_manager import WindowManager
            import pywinctl as _pwc
            title = preset.get("window_title", "")
            blackboard["_window_rect"] = None

            wm = WindowManager(title_keyword=title)
            blackboard["_window_mgr"] = wm
            if wm.find_window(retries=5, interval=0.5):
                _b = wm._window.box
                _rect = (_b.left, _b.top, _b.left + _b.width, _b.top + _b.height)
                if _rect[0] < 0 or _rect[1] < 0:
                    logger.info("窗口已最小化，正在激活...")
                    wm.activate()
                    time.sleep(0.5)
                    _b = wm._window.box
                    _rect = (_b.left, _b.top, _b.left + _b.width, _b.top + _b.height)
                blackboard["_window_rect"] = _rect
                logger.info("游戏窗口: hwnd=%s rect=(%d,%d,%d,%d)", wm.hwnd, *_rect)

            if blackboard["_window_rect"] is None:
                best_rect = None
                best_win = None
                for _w in _pwc.getAllWindows():
                    _t = _w.title or ""
                    if not _w.visible or _w.isMinimized:
                        continue
                    if title and title not in _t:
                        continue
                    try:
                        _b = _w.box
                        _rw = _b.left + _b.width
                        _rh = _b.top + _b.height
                    except Exception:
                        continue
                    if _rw > 1000 and _rh > 700 and _b.top >= 0 and _b.left >= 0:
                        _area = (_rw - _b.left) * (_rh - _b.top)
                        if best_rect is None or _area > best_rect[0]:
                            best_rect = (_area, _b.left, _b.top, _rw, _rh)
                            best_win = _w
                if best_rect:
                    blackboard["_window_rect"] = best_rect[1:]
                    logger.info("游戏窗口(备用检测): rect=(%d,%d,%d,%d)", *best_rect[1:])
                    if best_win and wm:
                        wm._window = best_win

            if wm and wm._window:
                if bg and self._vdm:
                    wm.save_position()
                    logger.info("正在启用虚拟显示器...")
                    if self._vdm.enable(timeout=10):
                        vdd_idx = self._vdm.get_monitor_index()
                        if vdd_idx >= 0:
                            wm.move_to_monitor(vdd_idx)
                            time.sleep(1.0)
                            try:
                                _b = wm._window.box
                                _rect = (_b.left, _b.top, _b.left + _b.width, _b.top + _b.height)
                                blackboard["_window_rect"] = _rect
                                logger.info("游戏已移至虚拟显示器 %d: rect=(%d,%d,%d,%d)", vdd_idx, *_rect)
                            except Exception as e:
                                logger.warning("VDD移动后更新窗口坐标失败: %s", e)
                            dxcam_info = self._vdm.get_dxcam_output_idx()
                            capture.stop()
                            time.sleep(0.3)
                            if isinstance(dxcam_info, tuple) and dxcam_info[0] >= 0:
                                capture.start(method=cfg.capture_method, fps_limit=cfg.fps_limit,
                                              device_idx=dxcam_info[0], output_idx=dxcam_info[1])
                            else:
                                capture.start(method=cfg.capture_method, fps_limit=cfg.fps_limit,
                                              monitor=vdd_idx)
                            logger.info("截图已重定向到虚拟显示器")
                        else:
                            logger.warning("无法确定虚拟显示器索引，使用前台模式")
                    else:
                        logger.warning("虚拟显示器启用失败，使用前台模式")
                elif bg:
                    wm.save_position()
                window_mgr = wm

            # 开发者模式：无前置状态的直启场景，游戏窗口可能未聚焦。
            # 点击窗口中央触发 Windows 自动聚焦，避免后续 pydirectinput 落空。
            if window_mgr and not window_mgr.is_focused and not bg:
                rect = blackboard.get("_window_rect")
                if rect and len(rect) == 4:
                    cx = (rect[0] + rect[2]) // 2
                    cy = (rect[1] + rect[3]) // 2
                    controller.click_at(cx, cy, bezier=False)
                    time.sleep(0.3)
                    logger.info("焦点激活: 点击窗口中心 (%d,%d)", cx, cy)

            if start_state != "character_select":
                logger.info("开发者模式：从 %s 状态开始", start_state)
            fsm.transition(start_state, blackboard)
            logger.info("Bot已启动 预设=%s 角色数=%d", preset_name, total_chars)

            while blackboard["running"] and not self.bot_stop_event.is_set():
                if window_mgr:
                    if window_mgr.is_minimized:
                        if bg:
                            window_mgr.activate()
                        else:
                            while window_mgr.is_minimized and not self.bot_stop_event.is_set():
                                time.sleep(0.5)
                    if not window_mgr.is_focused and not bg:
                        while not window_mgr.is_focused and not self.bot_stop_event.is_set():
                            time.sleep(0.5)
                    if bg and not window_mgr.is_focused:
                        window_mgr.activate()
                frame = capture.frame
                blackboard["current_frame"] = frame
                if frame is not None:
                    watchdog.update(frame, blackboard)
                if blackboard["stuck"] and fsm.current != "stuck_recovery":
                    watchdog.reset()
                    fsm.transition("stuck_recovery", blackboard)
                fsm.update(blackboard)
                if stealth and fsm.current in _SAFE_STEALTH_STATES:
                    controller.occasional_look_around()
                time.sleep(1.0 / max(cfg.fps_limit, 1))

            logger.info("Bot已停止")
        except Exception as e:
            logger.exception("Bot thread crashed: %s", e)
        finally:
            if controller:
                controller.release_all()
            if capture:
                capture.stop()
            if window_mgr:
                window_mgr.restore_position()
            _vdm = getattr(self, '_vdm', None)
            if _vdm and _vdm.is_enabled():
                _vdm.disable()
            self.bot_running = False
            self.root.after(0, self._bot_stopped)
            self.log_queue.put("==== Bot stopped ====")

    def _stop_bot(self):
        self.bot_stop_event.set()
        try:
            ctrl = getattr(self, '_controller', None)
            if ctrl:
                ctrl.release_all()
            if hasattr(self, '_blackboard') and self._blackboard:
                self._blackboard["running"] = False
                self._blackboard["stuck"] = False
                cap = self._blackboard.get("_capture")
                if cap and cap._running:
                    cap.stop()
        except Exception:
            pass
        self.status_label.configure(text="● 停止中", foreground="orange")

    def _bot_stopped(self):
        self._hotkeys_enabled = True
        self._controller = None
        self.status_label.configure(text="● 已停止", foreground="red")
        self.start_btn.configure(text="▶ 启动")
        self.bot_running = False

    def _on_close(self):
        if self.bot_running:
            if not messagebox.askyesno("确认", "Bot 正在运行，确定要退出吗？"):
                return
            self._stop_bot()
        self.root.destroy()

    def run(self):
        self.root.mainloop()


AVAILABLE_KEYS = ["w", "a", "s", "d", "1", "2", "3", "4", "5",
                   "e", "q", "p", "space", "left_shift", "left_ctrl", "left_alt",
                   "left_click", "right_click", "esc", "tab", "f"]



class ComboEditDialog:
    def __init__(self, parent, data, on_save):
        self.on_save = on_save
        self.combos = list(data.get("actions", [])) if data else []
        self.original_name = data.get("name", "") if data else ""
        self.dialog = tk.Toplevel(parent)
        self.dialog.title("\u7f16\u8f91\u8fde\u62db" if data else "\u65b0\u5efa\u8fde\u62db")
        _center_on_parent(self.dialog, parent, 650, 480)
        self.dialog.transient(parent)
        self.dialog.grab_set()
        self._build()
        self.dialog.wait_window()

    def _build(self):
        main = ttk.Frame(self.dialog, padding=8)
        main.pack(fill="both", expand=True)

        top = ttk.Frame(main)
        top.pack(fill="x", pady=(0, 4))
        ttk.Label(top, text="\u540d\u79f0:").pack(side="left")
        self.entry_name = ttk.Entry(top, width=24)
        self.entry_name.pack(side="left", padx=4)
        self.entry_name.insert(0, self.original_name)

        cols = ("#", "\u6309\u952e", "\u6301\u7eed", "\u524d\u5ef6", "\u540e\u5ef6", "\u6309\u4f4f", "\u91cd\u590d")
        self.tree = ttk.Treeview(main, columns=cols, show="headings", height=8)
        for c in cols:
            self.tree.heading(c, text=c)
            w = 30 if c == "#" else 55
            self.tree.column(c, width=w)
        self.tree.column("\u6309\u952e", width=110)
        scroll_tree = ttk.Scrollbar(main, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll_tree.set)
        self.tree.pack(fill="both", expand=True)
        scroll_tree.pack(side="right", fill="y")
        self.tree.bind("<Double-1>", lambda e: self._edit_action())

        btn_frame = ttk.Frame(main)
        btn_frame.pack(fill="x", pady=4)
        ttk.Button(btn_frame, text="\u6dfb\u52a0", command=self._add_action).pack(side="left", padx=2)
        ttk.Button(btn_frame, text="\u7f16\u8f91", command=self._edit_action).pack(side="left", padx=2)
        ttk.Button(btn_frame, text="\u5220\u9664", command=self._del_action).pack(side="left", padx=2)
        ttk.Button(btn_frame, text="\u4e0a\u79fb", command=lambda: self._move(-1)).pack(side="left", padx=2)
        ttk.Button(btn_frame, text="\u4e0b\u79fb", command=lambda: self._move(1)).pack(side="left", padx=2)

        bottom = ttk.Frame(main)
        bottom.pack(fill="x", pady=(4, 0))
        total_acts = len(self.combos)
        est = sum(a.get("duration", 0.1) + a.get("delay_before", 0) + a.get("delay_after", 0)
                  for a in self.combos)
        info = f"\u5171 {total_acts} \u4e2a\u52a8\u4f5c\uff0c\u9884\u4f30\u65f6\u957f ~{est:.1f}s"
        ttk.Label(bottom, text=info, foreground="#666").pack(side="left")
        ttk.Button(bottom, text="\u4fdd\u5b58", command=self._save).pack(side="right", padx=4)
        ttk.Button(bottom, text="\u53d6\u6d88", command=self.dialog.destroy).pack(side="right", padx=4)
        self._refresh()

    def _refresh(self):
        self.tree.delete(*self.tree.get_children(""))
        for i, act in enumerate(self.combos):
            self.tree.insert("", "end", values=(
                i + 1,
                "+".join(act.get("keys", [])),
                act.get("duration", 0.1),
                act.get("delay_before", 0.0),
                act.get("delay_after", 0.0),
                "\u662f" if act.get("hold") else "\u5426",
                act.get("repeat", 1),
            ))

    def _add_action(self):
        ActionDialog(self.dialog, self.combos, -1, self._refresh)

    def _edit_action(self):
        sel = self.tree.selection()
        if not sel:
            return
        idx = int(self.tree.item(sel[0], "values")[0]) - 1
        ActionDialog(self.dialog, self.combos, idx, self._refresh)

    def _del_action(self):
        sel = self.tree.selection()
        if not sel:
            return
        idx = int(self.tree.item(sel[0], "values")[0]) - 1
        if 0 <= idx < len(self.combos):
            del self.combos[idx]
            self._refresh()

    def _move(self, direction):
        sel = self.tree.selection()
        if not sel:
            return
        idx = int(self.tree.item(sel[0], "values")[0]) - 1
        new_idx = idx + direction
        if 0 <= new_idx < len(self.combos):
            self.combos[idx], self.combos[new_idx] = self.combos[new_idx], self.combos[idx]
            self._refresh()

    def _save(self):
        name = self.entry_name.get().strip()
        if not name:
            messagebox.showwarning("\u63d0\u793a", "\u8bf7\u8f93\u5165\u8fde\u62db\u540d\u79f0")
            return
        dur = sum(a.get("duration", 0.1) + a.get("delay_before", 0) + a.get("delay_after", 0)
                  for a in self.combos)
        data = {"name": name, "source": "\u624b\u52a8\u914d\u7f6e", "duration_sec": round(dur, 1),
                "actions": list(self.combos)}
        save_combo(name, data)
        if self.original_name and self.original_name != name:
            delete_combo(self.original_name)
        self.on_save()
        self.dialog.destroy()


class CharacterViewDialog:
    def __init__(self, parent, char):
        self.dialog = tk.Toplevel(parent)
        self.dialog.title(f"查看角色 - {char.get('name', '')}")
        _center_on_parent(self.dialog, parent, 550, 520)
        self.dialog.transient(parent)
        self.dialog.grab_set()
        self._build(char)
        self.dialog.wait_window()

    def _build(self, ch):
        main = ttk.Frame(self.dialog, padding=12)
        main.pack(fill="both", expand=True)

        ttk.Label(main, text=ch.get("name", ""), font=("", 14, "bold")).pack(anchor="w")

        info = ttk.LabelFrame(main, text="模板信息 (角色库)", padding=8)
        info.pack(fill="x", pady=(8, 4))
        for label, field in [
            ("选人界面头像:", "portrait_template"),
            ("技能栏:", "skill_bar_template"),
            ("结算画面:", "result_screen_template"),
            ("城镇头像:", "avatar_template"),
        ]:
            r = ttk.Frame(info)
            r.pack(fill="x", pady=1)
            ttk.Label(r, text=label, width=12, anchor="e").pack(side="left")
            val = _unpack_tpl_value(ch.get(field, "") or "")[0] or "—"
            ttk.Label(r, text=val, foreground="#333").pack(side="left", padx=4)

        config = ttk.LabelFrame(main, text="预设配置", padding=8)
        config.pack(fill="x", pady=4)
        for label, field in [("次数:", "runs"), ("连招:", "combo")]:
            r = ttk.Frame(config)
            r.pack(fill="x", pady=1)
            ttk.Label(r, text=label, width=12, anchor="e").pack(side="left")
            val = ch.get(field)
            if val is None or val == "":
                val = "\u2014"
            ttk.Label(r, text=str(val), foreground="#333").pack(side="left", padx=4)

        combo_name = ch.get("combo", "")
        if combo_name:
            combo_data = load_combo(combo_name)
            if combo_data:
                acts = combo_data.get("actions", [])
                detail_frame = ttk.LabelFrame(main, text=f"\u8fde\u62db\u8be6\u60c5: {combo_name} ({len(acts)}\u4e2a\u52a8\u4f5c)", padding=4)
                detail_frame.pack(fill="both", expand=True, pady=(4, 0))
                detail_frame.columnconfigure(0, weight=1)
                detail_frame.rowconfigure(0, weight=1)
                dcols = ("#", "\u6309\u952e", "\u6301\u7eed", "\u524d\u5ef6", "\u540e\u5ef6", "\u6309\u4f4f", "\u91cd\u590d")
                dtree = ttk.Treeview(detail_frame, columns=dcols, show="headings", height=6)
                for c in dcols:
                    dtree.heading(c, text=c)
                    w = 30 if c == "#" else 55
                    dtree.column(c, width=w)
                dtree.column("\u6309\u952e", width=100)
                dtree.pack(fill="both", expand=True)
                for i, act in enumerate(acts):
                    dtree.insert("", "end", values=(
                        i + 1,
                        "+".join(act.get("keys", [])),
                        act.get("duration", 0.1),
                        act.get("delay_before", 0.0),
                        act.get("delay_after", 0.0),
                        "\u662f" if act.get("hold") else "\u5426",
                        act.get("repeat", 1),
                    ))

        ttk.Button(main, text="关闭", command=self.dialog.destroy).pack(pady=(8, 0))


class ActionDialog:
    def __init__(self, parent, combos, idx, on_save):
        self.combos = combos
        self.idx = idx
        self.on_save = on_save
        self.action = dict(combos[idx]) if 0 <= idx < len(combos) else {
            "keys": [], "duration": 0.1, "delay_before": 0.0,
            "delay_after": 0.0, "hold": False, "repeat": 1}
        self.dialog = tk.Toplevel(parent)
        self.dialog.title("编辑连招动作" if idx >= 0 else "添加连招动作")
        _center_on_parent(self.dialog, parent, 640, 460)
        self.dialog.transient(parent)
        self.dialog.grab_set()
        self._build()
        self.dialog.wait_window()

    def _build(self):
        main = ttk.Frame(self.dialog, padding=10)
        main.pack(fill="both", expand=True)
        ttk.Label(main, text="选择按键（可多选，组合键同时按下）:",
                  font=("", 9, "bold")).pack(anchor="w")
        key_frame = ttk.Frame(main)
        key_frame.pack(fill="x", pady=4)
        self.key_vars = {}
        row = ttk.Frame(key_frame)
        row.pack(fill="x")
        for i, k in enumerate(AVAILABLE_KEYS):
            if i > 0 and i % 7 == 0:
                row = ttk.Frame(key_frame)
                row.pack(fill="x")
            var = tk.BooleanVar(value=k in self.action.get("keys", []))
            ttk.Checkbutton(row, text=k, variable=var, width=10).pack(side="left")
            self.key_vars[k] = var
        params = ttk.LabelFrame(main, text="参数", padding=8)
        params.pack(fill="x", pady=6)
        r0 = ttk.Frame(params)
        r0.pack(fill="x")
        self._add_param(r0, "持续(秒)", "duration", 0.01, 10.0)
        self._add_param(r0, "前延迟", "delay_before", 0.0, 10.0)
        self._add_param(r0, "后延迟", "delay_after", 0.0, 10.0)
        r1 = ttk.Frame(params)
        r1.pack(fill="x")
        self._add_param(r1, "重复", "repeat", 1, 99)
        self.hold_var = tk.BooleanVar(value=self.action.get("hold", False))
        ttk.Checkbutton(r1, text="按住(Hold)", variable=self.hold_var).pack(side="left", padx=10)

        btn_frame = ttk.Frame(main)
        btn_frame.pack(fill="x", pady=6)
        ttk.Button(btn_frame, text="确定", command=self._save).pack(side="right", padx=4)
        ttk.Button(btn_frame, text="取消", command=self.dialog.destroy).pack(side="right", padx=4)

        preview = ttk.LabelFrame(main, text="预览", padding=4)
        preview.pack(fill="x")
        self.preview_label = ttk.Label(preview, text="", foreground="#666")
        self.preview_label.pack()
        self._update_preview()

    def _add_param(self, parent, label, key, min_v, max_v, default=None):
        f = ttk.Frame(parent)
        f.pack(side="left", padx=4)
        ttk.Label(f, text=label).pack()
        if default is None:
            default = 1 if max_v > 20 else 0.1
        var = tk.StringVar(value=str(self.action.get(key, default)))
        spin = ttk.Spinbox(f, from_=min_v, to=max_v, increment=0.05 if max_v < 20 else 1,
                           textvariable=var, width=6)
        spin.pack()
        setattr(self, f"param_{key}", var)

    def _update_preview(self):
        keys = [k for k, v in self.key_vars.items() if v.get()]
        d = self.param_duration.get()
        da = self.param_delay_after.get()
        preview = f"[{'+'.join(keys) if keys else '?'}] dur={d}s da={da}s"
        if self.hold_var.get():
            preview += " HOLD"
        self.preview_label.configure(text=preview)

    def _save(self):
        try:
            keys = sorted(k for k, v in self.key_vars.items() if v.get())
            if not keys:
                messagebox.showwarning("提示", "请至少选择一个按键")
                return
            action = {
                "keys": keys,
                "duration": round(float(self.param_duration.get()), 3),
                "delay_before": round(float(self.param_delay_before.get()), 3),
                "delay_after": round(float(self.param_delay_after.get()), 3),
                "hold": self.hold_var.get(),
                "repeat": int(self.param_repeat.get()),
            }
            if self.idx >= 0 and self.idx < len(self.combos):
                self.combos[self.idx] = action
            else:
                self.combos.append(action)
            self.on_save()
        except Exception as e:
            logger.exception("ActionDialog save failed: %s", e)
            messagebox.showerror("保存失败", str(e))
        finally:
            self.dialog.destroy()




class ScreenshotCropDialog:
    def __init__(self, parent, full_img_np, templates_dir, on_save=None):
        self.full_img = full_img_np
        self.templates_dir = Path(templates_dir)
        self.on_save = on_save
        self.crop_box = None

        self.dialog = tk.Toplevel(parent)
        self.dialog.title("裁剪截图 - 鼠标拖拽选择区域")
        _center_on_parent(self.dialog, parent, 960, 720)
        self.dialog.minsize(640, 480)
        self.dialog.transient(parent)
        self.dialog.grab_set()

        self._prepare_display()
        self.dialog.wait_window()

    def _prepare_display(self):
        import cv2
        h, w = self.full_img.shape[:2]
        max_w, max_h = 920, 580
        scale = min(max_w / w, max_h / h, 1.0)
        disp_w, disp_h = int(w * scale), int(h * scale)
        small = cv2.resize(self.full_img, (disp_w, disp_h))
        self._scale = scale

        success, encoded = cv2.imencode('.png', small)
        if not success:
            messagebox.showerror("错误", "图片编码失败")
            self.dialog.destroy()
            return
        self._photo = tk.PhotoImage(data=encoded.tobytes())

        top_frame = ttk.Frame(self.dialog, padding=4)
        top_frame.pack(fill="x")
        ttk.Label(top_frame, text="在下方图片上拖拽选择区域，松开鼠标完成选择",
                  foreground="#555").pack(side="left")
        ttk.Button(top_frame, text="重置选择", command=self._reset_selection).pack(side="right", padx=2)
        ttk.Button(top_frame, text="确认保存", command=self._confirm_save).pack(side="right", padx=2)

        canvas_frame = ttk.Frame(self.dialog)
        canvas_frame.pack(fill="both", expand=True, padx=4, pady=4)
        self.canvas = tk.Canvas(canvas_frame, bg="#333",
                                 width=disp_w, height=disp_h)
        scroll_x = ttk.Scrollbar(canvas_frame, orient="horizontal",
                                  command=self.canvas.xview)
        scroll_y = ttk.Scrollbar(canvas_frame, orient="vertical",
                                  command=self.canvas.yview)
        self.canvas.configure(xscrollcommand=scroll_x.set,
                              yscrollcommand=scroll_y.set)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        scroll_x.grid(row=1, column=0, sticky="ew")
        scroll_y.grid(row=0, column=1, sticky="ns")
        canvas_frame.columnconfigure(0, weight=1)
        canvas_frame.rowconfigure(0, weight=1)

        self._image_id = self.canvas.create_image(0, 0, anchor="nw",
                                                    image=self._photo)
        self.canvas.configure(scrollregion=(0, 0, disp_w, disp_h))

        self._rect_id = None
        self._start_x = None
        self._start_y = None
        self.canvas.bind("<ButtonPress-1>", self._on_mouse_down)
        self.canvas.bind("<B1-Motion>", self._on_mouse_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_mouse_up)

        status_frame = ttk.Frame(self.dialog, padding=4)
        status_frame.pack(fill="x")
        self.status_var = tk.StringVar(value="拖拽鼠标选择区域")
        ttk.Label(status_frame, textvariable=self.status_var,
                  foreground="#666").pack(side="left")
        self.coord_var = tk.StringVar(value="")
        ttk.Label(status_frame, textvariable=self.coord_var,
                  foreground="#888").pack(side="right")

    def _on_mouse_down(self, event):
        self._start_x = self.canvas.canvasx(event.x)
        self._start_y = self.canvas.canvasy(event.y)
        if self._rect_id:
            self.canvas.delete(self._rect_id)
        self._rect_id = self.canvas.create_rectangle(
            self._start_x, self._start_y, self._start_x, self._start_y,
            outline="#00ff00", width=2, dash=(4, 2))

    def _on_mouse_drag(self, event):
        if not self._rect_id:
            return
        cx, cy = self.canvas.canvasx(event.x), self.canvas.canvasy(event.y)
        self.canvas.coords(self._rect_id, self._start_x, self._start_y, cx, cy)
        w = abs(cx - self._start_x)
        h = abs(cy - self._start_y)
        self.coord_var.set(f"{int(w)}x{int(h)}")

    def _on_mouse_up(self, event):
        if not self._rect_id:
            return
        x1, y1, x2, y2 = self.canvas.coords(self._rect_id)
        x1, x2 = sorted((x1, x2))
        y1, y2 = sorted((y1, y2))
        if x2 - x1 < 5 or y2 - y1 < 5:
            self._reset_selection()
            return
        fmt_w = int((x2 - x1) / self._scale)
        fmt_h = int((y2 - y1) / self._scale)
        self.status_var.set(f"已选择 {fmt_w}x{fmt_h} 像素 — 点击「确认保存」")
        self.coord_var.set(f"{fmt_w}x{fmt_h}")

    def _reset_selection(self):
        if self._rect_id:
            self.canvas.delete(self._rect_id)
            self._rect_id = None
        self.status_var.set("拖拽鼠标选择区域")
        self.coord_var.set("")
        self.crop_box = None

    def _confirm_save(self):
        if not self._rect_id:
            messagebox.showwarning("提示", "请先在图片上拖拽选择区域")
            return
        x1, y1, x2, y2 = self.canvas.coords(self._rect_id)
        x1, x2 = sorted((x1, x2))
        y1, y2 = sorted((y1, y2))
        if x2 - x1 < 5 or y2 - y1 < 5:
            messagebox.showwarning("提示", "选择区域太小，请重新选择")
            return
        self.templates_dir.mkdir(parents=True, exist_ok=True)
        name = filedialog.asksaveasfilename(
            title="保存模板图片",
            initialdir=self.templates_dir,
            defaultextension=".png",
            filetypes=[("PNG", "*.png"), ("JPEG", "*.jpg"), ("所有文件", "*.*")],
        )
        if not name:
            return
        name = Path(name)
        crop_x1 = int(x1 / self._scale)
        crop_y1 = int(y1 / self._scale)
        crop_x2 = int(x2 / self._scale)
        crop_y2 = int(y2 / self._scale)
        cropped = self.full_img[crop_y1:crop_y2, crop_x1:crop_x2]
        import cv2
        success, encoded = cv2.imencode('.png', cropped)
        if success:
            with open(str(name), 'wb') as f:
                f.write(encoded.tobytes())
        logger.info("模板已保存: %s (%dx%d)", name.name,
                     crop_x2 - crop_x1, crop_y2 - crop_y1)
        if self.on_save:
            self.on_save()
        self.dialog.destroy()


class _CharLibEditDialog:
    def __init__(self, parent, name, templates_path, on_save):
        self.original_name = name
        self.templates_path = templates_path
        self.on_save = on_save
        if name:
            self.data = load_character_profile(name) or {"name": name}
        else:
            self.data = {"name": ""}
        self.dialog = tk.Toplevel(parent)
        self.dialog.title(f"编辑角色 - {name}" if name else "新建角色")
        color = parent.cget("bg") if parent.winfo_exists() else "#f0f0f0"
        self.dialog.configure(bg=color)
        _center_on_parent(self.dialog, parent, 600, 360)
        self.dialog.transient(parent)
        self.dialog.grab_set()
        self._build()
        self.dialog.wait_window()

    def _pick_template(self, entry, title):
        global _last_browse_dir
        start = _last_browse_dir if _last_browse_dir else self.templates_path
        path = filedialog.askopenfilename(
            title=title, initialdir=start,
            filetypes=[("图片文件", "*.png *.jpg *.jpeg *.bmp"), ("所有文件", "*.*")],
        )
        if path:
            _last_browse_dir = Path(path).parent
            ename = _import_template_file(path, self.templates_path)
            entry.delete(0, "end"); entry.insert(0, ename)

    def _build(self):
        main = ttk.Frame(self.dialog, padding=8)
        main.pack(fill="both", expand=True)
        r0 = ttk.Frame(main); r0.pack(fill="x", pady=2)
        ttk.Label(r0, text="角色名:").pack(side="left")
        self.name_entry = ttk.Entry(r0, width=20)
        self.name_entry.pack(side="left", padx=4)
        self.name_entry.insert(0, self.data.get("name", ""))

        def _row(label, field):
            f = ttk.Frame(main); f.pack(fill="x", pady=1)
            ttk.Label(f, text=label).pack(side="left")
            e = ttk.Entry(f, width=24); e.pack(side="left", padx=2)
            btn = ttk.Button(f, text="浏览", width=4, command=lambda en=e: self._pick_template(en, label))
            btn.pack(side="left")
            thr = ttk.Spinbox(f, from_=0.30, to=0.99, increment=0.05, width=4)
            thr.bind("<MouseWheel>", lambda e: "break")
            thr.set("0.65"); thr.pack(side="left", padx=1)
            n, t = _unpack_tpl_value(self.data.get(field, ""))
            e.insert(0, n); thr.set(str(t))
            return e, thr

        self.fields = {}
        for lbl, key in [("选人界面头像:", "portrait_template"),
                         ("角色技能栏:", "skill_bar_template"),
                         ("结算画面:", "result_screen_template"),
                         ("城镇头像:", "avatar_template")]:
            self.fields[key] = _row(lbl, key)

        btn_frame = ttk.Frame(main); btn_frame.pack(fill="x", pady=6)
        ttk.Button(btn_frame, text="保存", command=self._save).pack(side="right", padx=4)
        ttk.Button(btn_frame, text="取消", command=self.dialog.destroy).pack(side="right", padx=4)

    def _save(self):
        name = self.name_entry.get().strip()
        if not name:
            messagebox.showwarning("提示", "角色名不能为空")
            return
        if self.original_name and name != self.original_name:
            old_path = CHARACTERS_DIR / f"{self.original_name}.json"
            if old_path.exists():
                old_path.unlink()
        profile = {}
        for key, (entry, thr) in self.fields.items():
            val = _pack_tpl_value(entry.get(), thr.get(), self.data.get(key))
            if val:
                profile[key] = val
        save_character_profile(name, profile)
        self.on_save()
        self.dialog.destroy()


class _PickCharacterDialog:
    def __init__(self, parent, names, on_pick):
        self.names = names
        self.on_pick = on_pick
        self.dialog = tk.Toplevel(parent)
        self.dialog.title("从角色库添加")
        _center_on_parent(self.dialog, parent, 350, 300)
        self.dialog.transient(parent)
        self.dialog.grab_set()
        self._build()
        self.dialog.wait_window()

    def _build(self):
        main = ttk.Frame(self.dialog, padding=8)
        main.pack(fill="both", expand=True)
        ttk.Label(main, text="选择角色:", font=("", 9, "bold")).pack(anchor="w", pady=(0, 4))
        self.lb = tk.Listbox(main, height=8)
        self.lb.pack(fill="both", expand=True)
        for n in self.names:
            self.lb.insert("end", n)
        self.lb.bind("<Double-Button-1>", lambda e: self._confirm())
        btn_frame = ttk.Frame(main); btn_frame.pack(fill="x", pady=6)
        ttk.Button(btn_frame, text="确定", command=self._confirm).pack(side="right", padx=4)
        ttk.Button(btn_frame, text="取消", command=self.dialog.destroy).pack(side="right", padx=4)

    def _confirm(self):
        sel = self.lb.curselection()
        if sel:
            self.on_pick(self.names[sel[0]])
            self.dialog.destroy()
