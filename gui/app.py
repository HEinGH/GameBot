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

from config.settings import Settings, PRESETS_DIR, ROOT_DIR

logger = logging.getLogger(__name__)

_last_browse_dir = None  # shared across all file pickers


def _pack_tpl_value(name, thr_val):
    n = (name or "").strip()
    if not n:
        return ""
    try:
        t = float(thr_val)
    except (ValueError, TypeError):
        t = 0.65
    if abs(t - 0.65) < 0.001:
        return n
    return {"template": n, "threshold": round(t, 2)}


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
    dst = templates_dir / src.name
    if src.resolve().parent == templates_dir.resolve():
        return src.name
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        stem, ext = src.stem, src.suffix
        counter = 1
        while dst.exists():
            dst = templates_dir / f"{stem}_{counter}{ext}"
            counter += 1
    import shutil
    shutil.copy2(str(src), str(dst))
    logger.info("Template imported: %s -> %s", src.name, dst.name)
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
        self._selected_entry = None

        self.canvas = tk.Canvas(self, highlightthickness=0,
                                bg=self._bg_color(), bd=0, height=canvas_height)
        self.scrollbar_v = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.scrollbar_h = ttk.Scrollbar(self, orient="horizontal", command=self.canvas.xview)
        self.inner = ttk.Frame(self.canvas)

        self.inner.bind("<Configure>", self._on_inner_configure)
        self._cw = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.canvas.configure(yscrollcommand=self.scrollbar_v.set,
                              xscrollcommand=self.scrollbar_h.set)

        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.scrollbar_v.grid(row=0, column=1, sticky="ns")
        self.scrollbar_h.grid(row=1, column=0, sticky="ew")
        self.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)
        self.canvas.bind("<Enter>", self._bind_mwheel)
        self.canvas.bind("<Leave>", self._unbind_mwheel)

        btn_bar = ttk.Frame(self)
        btn_bar.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(4, 0))
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
        self.canvas.bind("<Shift-MouseWheel>", self._on_hmwheel, "+")

    def _unbind_mwheel(self, event):
        self.canvas.unbind("<MouseWheel>")
        self.canvas.unbind("<Shift-MouseWheel>")

    def _on_mwheel(self, event):
        bbox = self.canvas.bbox("all")
        if bbox and bbox[3] <= self.canvas.winfo_height():
            return
        self.canvas.yview_scroll(int(-event.delta / 120), "units")
        return "break"

    def _on_hmwheel(self, event):
        self.canvas.xview_scroll(int(-event.delta / 120), "units")
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

        ttk.Label(row, text="步骤 {}".format(idx + 1), width=8,
                  anchor="center", font=("", 9, "bold")).pack(side="left", padx=1)
        ttk.Label(row, text="→", font=("", 10)).pack(side="left")

        entry = ttk.Entry(row, width=18)
        entry.pack(side="left", padx=2)
        if name:
            entry.insert(0, name)
        entry.bind("<FocusIn>", lambda e, r=row: self._set_selected(r, entry))
        entry.bind("<Button-1>", lambda e, r=row, en=entry: self._set_selected(r, en))

        thr_var = tk.StringVar(value=str(thr))
        thr_spin = ttk.Spinbox(row, from_=0.30, to=0.99, increment=0.05, width=4, textvariable=thr_var)
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

        ttk.Button(row, text="浏览", width=4,
                   command=pick_template(entry, thr_var)).pack(side="left", padx=1)

        def del_row(del_row_ref):
            self._sync_entries()
            del_idx = None
            for i, (r, _) in enumerate(self._row_refs):
                if r is del_row_ref:
                    del_idx = i
                    break
            if del_idx is not None and del_idx < len(self._items):
                self._items.pop(del_idx)
            self._refresh()

        ttk.Button(row, text="✕", width=2,
                   command=lambda r=row: del_row(r)).pack(side="left", padx=1)
        return (entry, thr_var), row

    def _set_selected(self, row, entry):
        for r, _ in self._row_refs:
            try:
                r.configure(style="TFrame")
            except Exception:
                pass
        self._selected_entry = entry
        self._selected_row = row
        try:
            self._selected_row.configure(style="")
            self._selected_row.configure(relief="solid", borderwidth=0)
        except Exception:
            pass

    def _sync_entries(self):
        self._items = []
        for _, (entry, thr_var) in self._row_refs:
            val = entry.get().strip()
            if val:
                try:
                    t = float(thr_var.get())
                except (ValueError, TypeError):
                    t = 0.65
                if abs(t - 0.65) < 0.001:
                    self._items.append(val)
                else:
                    self._items.append({"template": val, "threshold": round(t, 2)})

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
        self._load_last_preset()
        self.bot_thread = None
        self.bot_running = False
        self.bot_stop_event = threading.Event()
        self.log_queue = queue.Queue()
        self._setup_log_handler()
        self._build_ui()
        self._refresh_preset_list()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.after(200, self._poll_log)

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

    def _add_file_row(self, parent, label):
        f = ttk.Frame(parent)
        ttk.Label(f, text=label).pack(side="left")
        e = ttk.Entry(f, width=14)
        e.pack(side="left", padx=2)
        btn = ttk.Button(f, text="浏览", width=4)
        btn.configure(command=self._make_file_picker(e))
        btn.pack(side="left")
        parent.add(f)
        return e

    def _add_text_row(self, parent, label):
        f = ttk.Frame(parent)
        ttk.Label(f, text=label).pack(side="left")
        e = ttk.Entry(f, width=14)
        e.pack(side="left", padx=2)
        parent.add(f)
        return e

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
                        self.preset_data = json.load(f)
                    self.current_preset_path = path
                    logger.info("Loaded last preset: %s", last)
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
        if self.log_debug_var.get():
            self._log_handler.setLevel(logging.DEBUG)
            logging.getLogger().setLevel(logging.DEBUG)
        else:
            self._log_handler.setLevel(logging.INFO)
            logging.getLogger().setLevel(logging.INFO)

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
            ("recorder", "连招录制（待测试）"),
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

    def _build_main_area(self):
        container = ttk.Frame(self.root, padding=8)
        container.grid(row=0, column=1, sticky="nsew")
        container.columnconfigure(0, weight=1)
        container.rowconfigure(0, weight=1)
        self._pages = {}
        for key in ("dashboard", "characters", "devtools", "recorder", "screenshot", "settings"):
            frame = ttk.Frame(container)
            frame.grid(row=0, column=0, sticky="nsew")
            frame.columnconfigure(0, weight=1)
            frame.rowconfigure(0, weight=1)
            self._pages[key] = frame
        self._build_dashboard()
        self._build_characters()
        self._build_devtools()
        self._build_recorder()
        self._build_screenshot()
        self._build_settings()
        self._switch_page("dashboard")

    def _switch_page(self, key):
        self.root.unbind_all("<MouseWheel>")
        self._current_page.set(key)
        for k, f in self._pages.items():
            f.grid_remove()
        self._pages[key].grid()
        if key == "characters":
            self._refresh_char_table()
            self.root.bind_all("<MouseWheel>",
                lambda e: self._page_canvas.yview_scroll(int(-e.delta / 120), "units"), "+")
            self._page_canvas.update_idletasks()
            self._page_canvas.yview_moveto(0)

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

    # ========== DASHBOARD ==========
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
        ttk.Button(row, text="刷新", command=self._refresh_preset_list).pack(side="left")
        ttk.Button(row, text="确定", command=self._edit_selected_preset).pack(side="left", padx=4)
        ttk.Button(row, text="删除", command=self._delete_preset).pack(side="left")
        row2 = ttk.Frame(card)
        row2.pack(fill="x", pady=(4, 0))
        ttk.Label(row2, text="执行角色数:").pack(side="left")
        self.dash_char_count = tk.IntVar(value=1)
        self.dash_char_spin = ttk.Spinbox(row2, from_=1, to_=20, textvariable=self.dash_char_count, width=4)
        self.dash_char_spin.pack(side="left", padx=6)
        ttk.Label(row2, text=" 隐身模式:").pack(side="left")
        self.dash_stealth = tk.BooleanVar()
        ttk.Checkbutton(row2, variable=self.dash_stealth).pack(side="left")
        ttk.Label(row2, text=" 后台（待测试）:").pack(side="left")
        self.dash_background = tk.BooleanVar()
        ttk.Checkbutton(row2, variable=self.dash_background).pack(side="left")
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
                self.preset_data = data
                self.current_preset_path = path
                count = len(data.get("characters", []))
                cc = data.get("char_count", max(1, count))
                if cc > count:
                    cc = max(1, count)
                self.dash_char_count.set(cc)
                self.dash_char_spin.configure(to_=max(1, count))
                self.dash_stealth.set(data.get("stealth", False))
                self.dash_background.set(data.get("background", False))
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
                self.preset_data = json.load(f)
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
            logger.info("Deleted preset: %s", name)
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

    def _update_dash_status(self, text):
        self.dash_status_text.configure(state="normal")
        self.dash_status_text.insert("end", text + "\n")
        self._trim_log_lines()
        self.dash_status_text.see("end")
        self.dash_status_text.configure(state="disabled")

    def _edit_fallback_combo(self):
        FallbackComboDialog(self.root, self._fallback_combos_data, self._on_fallback_save)

    def _on_fallback_save(self, combos):
        self._fallback_combos_data = combos

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
        self._fallback_combos_data = []

        cfg_header(cfg_frame, "▎进入游戏")
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

        cfg_header(cfg_frame, "▎角色列表")
        table_frame = ttk.LabelFrame(cfg_frame, text="角色列表", padding=6)
        table_frame.pack(fill="x", pady=(2, 0))

        table_frame.columnconfigure(0, weight=1)
        table_frame.rowconfigure(0, weight=1)
        cols = ("#", "名称", "头像模板", "技能栏", "结算", "城镇头像", "次数", "连招数")
        self.char_tree = ttk.Treeview(table_frame, columns=cols, show="headings",
                                        selectmode="browse", height=6)
        for c in cols:
            self.char_tree.heading(c, text=c)
            self.char_tree.column(c, width=50 if c == "#" else 70)
        self.char_tree.column("名称", width=90)
        self.char_tree.column("连招数", width=60)
        scroll_tree = ttk.Scrollbar(table_frame, orient="vertical", command=self.char_tree.yview)
        self.char_tree.configure(yscrollcommand=scroll_tree.set)
        self.char_tree.grid(row=0, column=0, sticky="nsew")
        scroll_tree.grid(row=0, column=1, sticky="ns")
        self.char_tree.bind("<Double-1>", lambda e: self._edit_character())

        btn_frame = ttk.Frame(table_frame)
        btn_frame.grid(row=1, column=0, columnspan=2, pady=4, sticky="ew")
        ttk.Button(btn_frame, text="添加角色", command=self._add_character).pack(side="left", padx=2)
        ttk.Button(btn_frame, text="编辑角色", command=self._edit_character).pack(side="left", padx=2)
        ttk.Button(btn_frame, text="删除角色", command=self._delete_character).pack(side="left", padx=2)
        ttk.Button(btn_frame, text="上移", command=lambda: self._move_char(-1)).pack(side="left", padx=2)
        ttk.Button(btn_frame, text="下移", command=lambda: self._move_char(1)).pack(side="left", padx=2)
        ttk.Button(btn_frame, text="编辑兜底连招", command=self._edit_fallback_combo).pack(side="left", padx=8)

    @staticmethod
    def _default_fallback():
        return [
            {"keys": ["1"], "duration": 0.1, "delay_after": 0.6},
            {"keys": ["2"], "duration": 0.1, "delay_after": 0.6},
            {"keys": ["3"], "duration": 0.1, "delay_after": 0.6},
            {"keys": ["4"], "duration": 0.1, "delay_after": 0.6},
            {"keys": ["5"], "duration": 0.1, "delay_after": 0.6},
            {"keys": ["e"], "duration": 0.15, "delay_after": 1.0},
            {"keys": ["q"], "duration": 0.15, "delay_after": 2.0},
        ]

    def _new_preset(self):
        self.preset_data = self._load_default_preset()
        self.current_preset_path = None
        self._refresh_char_table()

    def _save_preset(self):
        self._sync_char_table_to_data()
        self._sync_global_config()
        if self.current_preset_path:
            self.current_preset_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.current_preset_path, "w", encoding="utf-8") as f:
                json.dump(self.preset_data, f, indent=2, ensure_ascii=False)
            messagebox.showinfo("保存成功", f"已保存到:\n{self.current_preset_path}")
            self._refresh_preset_list()
        else:
            self._save_preset_as()

    def _save_preset_as(self):
        self._sync_char_table_to_data()
        self._sync_global_config()
        path = filedialog.asksaveasfilename(
            defaultextension=".json",
            initialdir=self.presets_path,
            filetypes=[("JSON", "*.json")],
        )
        if path:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(self.preset_data, f, indent=2, ensure_ascii=False)
            self.current_preset_path = Path(path)
            messagebox.showinfo("保存成功", f"已保存到:\n{path}")
            self._refresh_preset_list()

    @staticmethod
    def _pack_tpl(name, thr_val):
        return _pack_tpl_value(name, thr_val)

    @staticmethod
    def _unpack_tpl(value):
        return _unpack_tpl_value(value)

    def _sync_global_config(self):
        p = self.preset_data
        p["description"] = self.char_desc.get()
        p["window_title"] = self.cfg_window_title.get()
        p["enter_game_template"] = self._pack_tpl(self.cfg_enter_game.get(), self.cfg_enter_game_thr.get())
        p["rechallenge_template"] = self._pack_tpl(self.cfg_rechallenge.get(), self.cfg_rechallenge_thr.get())
        p["exit_domain_template"] = self._pack_tpl(self.cfg_exit_domain.get(), self.cfg_exit_domain_thr.get())
        p["portal_template"] = self._pack_tpl(self.cfg_portal.get(), self.cfg_portal_thr.get())
        if self._fallback_combos_data:
            p["fallback_combos"] = list(self._fallback_combos_data)
        else:
            p.pop("fallback_combos", None)

        p.setdefault("town_nav", {})
        p["town_nav"]["domain_select_steps"] = self.domain_chain.get_items()
        p["town_nav"]["confirm_enter_template"] = self.confirm_enter_chain.get_items()
        p["town_nav"]["npc_marker_template"] = self._pack_tpl(self.cfg_npc_marker.get(), self.cfg_npc_marker_thr.get())
        p["town_nav"].pop("daily_button_template", None)
        p["town_nav"].pop("challenge_templates", None)
        p["town_nav"]["alt_for_mouse"] = self.cfg_town_alt.get()
        p.setdefault("town_exit", {})
        p["town_exit"]["settings_template"] = self._pack_tpl(self.cfg_exit_settings.get(), self.cfg_exit_settings_thr.get())
        p["town_exit"]["switch_character_template"] = self._pack_tpl(self.cfg_exit_switch.get(), self.cfg_exit_switch_thr.get())
        p["town_exit"]["exit_game_template"] = self._pack_tpl(self.cfg_exit_game.get(), self.cfg_exit_game_thr.get())
        p["town_exit"]["confirm_exit_template"] = self._pack_tpl(self.cfg_exit_confirm.get(), self.cfg_exit_confirm_thr.get())
        p["char_count"] = self.dash_char_count.get()
        p["stealth"] = self.dash_stealth.get()
        p["background"] = self.dash_background.get()

    def _sync_char_table_to_data(self):
        chars = []
        for item in self.char_tree.get_children(""):
            vals = self.char_tree.item(item, "values")
            idx = int(vals[0]) - 1
            if idx < len(self.preset_data.get("characters", [])):
                chars.append(self.preset_data["characters"][idx])
            else:
                chars.append({
                    "name": vals[1],
                    "portrait_template": vals[2],
                    "skill_bar_template": vals[3] or None,
                    "result_screen_template": vals[4] or None,
                    "avatar_template": vals[5] or None,
                    "runs": int(vals[6]),
                    "combos": [],
                    "fallback_combos": None,
                })
        self.preset_data["characters"] = chars

    def _refresh_char_table(self):
        self.char_tree.delete(*self.char_tree.get_children(""))
        data = self.preset_data
        self.char_desc.delete(0, "end")
        self.char_desc.insert(0, data.get("description", ""))

        n, t = self._unpack_tpl(data.get("enter_game_template", ""))
        self.cfg_enter_game.delete(0, "end"); self.cfg_enter_game.insert(0, n)
        self.cfg_enter_game_thr.set(str(t))

        n, t = self._unpack_tpl(data.get("rechallenge_template", ""))
        self.cfg_rechallenge.delete(0, "end"); self.cfg_rechallenge.insert(0, n)
        self.cfg_rechallenge_thr.set(str(t))

        fb = data.get("fallback_combos")
        if fb:
            self._fallback_combos_data = list(fb)
        else:
            self._fallback_combos_data = list(self._default_fallback())

        n, t = self._unpack_tpl(data.get("exit_domain_template", ""))
        self.cfg_exit_domain.delete(0, "end"); self.cfg_exit_domain.insert(0, n)
        self.cfg_exit_domain_thr.set(str(t))

        n, t = self._unpack_tpl(data.get("portal_template", ""))
        self.cfg_portal.delete(0, "end"); self.cfg_portal.insert(0, n)
        self.cfg_portal_thr.set(str(t))

        self.cfg_window_title.delete(0, "end")
        self.cfg_window_title.insert(0, data.get("window_title", ""))
        tn = data.get("town_nav", {})
        chain = tn.get("domain_select_steps") or tn.get("domain_template") or []
        if isinstance(chain, str):
            chain = [chain] if chain else []
        chain = list(chain)
        daily = tn.get("daily_button_template", "")
        if daily and daily not in chain:
            chain.insert(0, daily)
        ch = tn.get("challenge_templates", [])
        for c in (ch if isinstance(ch, list) else [ch]):
            if c and c not in chain:
                chain.append(c)
        self.domain_chain.set_items(chain)
        self.cfg_town_alt.set(tn.get("alt_for_mouse", tn.get("ctrl_for_mouse", True)))

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
        for i, ch in enumerate(data.get("characters", [])):
            self.char_tree.insert("", "end", values=(
                i + 1,
                ch.get("name", ""),
                ch.get("portrait_template", ""),
                ch.get("skill_bar_template", "") or "",
                ch.get("result_screen_template", "") or "",
                ch.get("avatar_template", "") or "",
                ch.get("runs", 1),
                len(ch.get("combos", [])),
            ))

    def _add_character(self):
        char = {"name": "新角色", "portrait_template": "",
                "skill_bar_template": None, "result_screen_template": None,
                "avatar_template": None,
                "runs": 4, "fallback_combos": None, "combos": []}
        self.preset_data.setdefault("characters", []).append(char)
        self._refresh_char_table()
        self.root.after(100, lambda: self._edit_character_at(len(self.preset_data["characters"]) - 1))

    def _edit_character(self):
        sel = self.char_tree.selection()
        if not sel:
            messagebox.showwarning("提示", "请先选择一个角色")
            return
        idx = int(self.char_tree.item(sel[0], "values")[0]) - 1
        self._edit_character_at(idx)

    def _edit_character_at(self, idx):
        chars = self.preset_data.get("characters", [])
        if idx < 0 or idx >= len(chars):
            return
        CharacterDialog(self.root, chars, idx, self.templates_path,
                        lambda: self._refresh_char_table())

    def _delete_character(self):
        sel = self.char_tree.selection()
        if not sel:
            return
        idx = int(self.char_tree.item(sel[0], "values")[0]) - 1
        chars = self.preset_data.get("characters", [])
        if 0 <= idx < len(chars):
            if messagebox.askyesno("确认", f"删除角色「{chars[idx]['name']}」?"):
                chars.pop(idx)
                self._refresh_char_table()

    def _move_char(self, direction):
        sel = self.char_tree.selection()
        if not sel:
            return
        idx = int(self.char_tree.item(sel[0], "values")[0]) - 1
        chars = self.preset_data.get("characters", [])
        new_idx = idx + direction
        if 0 <= new_idx < len(chars):
            chars[idx], chars[new_idx] = chars[new_idx], chars[idx]
            self._refresh_char_table()

    # ========== DEVTOOLS PAGE ==========
    def _build_devtools(self):
        f = self._pages["devtools"]
        ttk.Label(f, text="开发者工具", font=("", 14, "bold")).pack(anchor="w", pady=(0, 8))
        card = ttk.LabelFrame(f, text="调试选项", padding=10)
        card.pack(fill="x")
        self.dash_skip_combat = tk.BooleanVar()
        ttk.Checkbutton(card, text="跳过刷本，直接从 town_exit 开始（测试角色切换）",
                         variable=self.dash_skip_combat).pack(anchor="w")

    # ========== RECORDER PAGE ==========
    def _build_recorder(self):
        f = self._pages["recorder"]
        f.columnconfigure(0, weight=1)
        f.rowconfigure(2, weight=1)

        ttk.Label(f, text="连招录制（待测试）", font=("", 14, "bold")).grid(row=0, column=0, sticky="w", pady=(0, 4))
        instr = ttk.LabelFrame(f, text="使用说明", padding=8)
        instr.grid(row=1, column=0, sticky="ew", pady=(0, 4))
        ttk.Label(instr, text=(
            "F5: 开始/停止录制   |   F6: 停止录制\n"
            "3秒倒计时后开始录制，F5/F6 不会被录到连招中"
        ), justify="left").pack(anchor="w")

        row = ttk.Frame(f)
        row.grid(row=2, column=0, sticky="ew", pady=2)
        self.rec_btn = ttk.Button(row, text="▶ F5 开始录制", command=self._toggle_recording)
        self.rec_btn.pack(side="left", padx=(0, 6))
        self.rec_status = ttk.Label(row, text="就绪", foreground="gray")
        self.rec_status.pack(side="left", padx=6)
        ttk.Button(row, text="保存录制结果", command=self._save_recorded).pack(side="left", padx=4)

        self.rec_result_frame = ttk.LabelFrame(f, text="录制结果预览", padding=4)
        self.rec_result_frame.grid(row=3, column=0, sticky="nsew", pady=4)
        self.rec_result_frame.columnconfigure(0, weight=1)
        self.rec_result_frame.rowconfigure(0, weight=1)
        self.rec_result_text = tk.Text(self.rec_result_frame, height=6, wrap="word",
                                        state="disabled", font=("Consolas", 9))
        self.rec_result_text.grid(row=0, column=0, sticky="nsew")

        sep = ttk.Separator(f, orient="horizontal")
        sep.grid(row=4, column=0, sticky="ew", pady=6)

        ttk.Label(f, text="连招管理", font=("", 14, "bold")).grid(row=5, column=0, sticky="w", pady=(0, 4))
        self._build_combo_manager(f, row_start=6)

        self._recorded_actions = None
        self._recorder_instance = None
        self._setup_recorder_hotkeys()

    def _build_combo_manager(self, parent, row_start):
        mgr = ttk.LabelFrame(parent, text="已保存的连招文件", padding=4)
        mgr.grid(row=row_start, column=0, sticky="nsew", pady=2)
        parent.rowconfigure(row_start, weight=1)
        mgr.columnconfigure(0, weight=1)
        mgr.rowconfigure(0, weight=1)

        cols = ("名称", "时长(s)", "动作数", "录制时间")
        self.combo_tree = ttk.Treeview(mgr, columns=cols, show="headings", height=6)
        for c in cols:
            self.combo_tree.heading(c, text=c)
            self.combo_tree.column(c, width=60)
        self.combo_tree.column("名称", width=120)
        self.combo_tree.column("录制时间", width=120)
        scroll_c = ttk.Scrollbar(mgr, orient="vertical", command=self.combo_tree.yview)
        self.combo_tree.configure(yscrollcommand=scroll_c.set)
        self.combo_tree.grid(row=0, column=0, sticky="nsew")
        scroll_c.grid(row=0, column=1, sticky="ns")
        self.combo_tree.bind("<Double-1>", lambda e: self._preview_combo())

        btn_row = ttk.Frame(mgr)
        btn_row.grid(row=1, column=0, columnspan=2, pady=4, sticky="ew")
        ttk.Button(btn_row, text="刷新列表", command=self._refresh_combo_list).pack(side="left", padx=2)
        ttk.Button(btn_row, text="预览", command=self._preview_combo).pack(side="left", padx=2)
        ttk.Button(btn_row, text="删除", command=self._delete_combo_file).pack(side="left", padx=2)
        ttk.Button(btn_row, text="绑定到角色...", command=self._bind_combo_to_character).pack(side="left", padx=2)
        ttk.Button(btn_row, text="打开文件夹", command=self._open_combos_folder).pack(side="left", padx=2)
        self._combo_dir = self.script_dir / "combos"
        self._refresh_combo_list()

    def _refresh_combo_list(self):
        self.combo_tree.delete(*self.combo_tree.get_children(""))
        if not self._combo_dir.exists():
            return
        for f in sorted(self._combo_dir.glob("*.json")):
            try:
                with open(f, encoding="utf-8") as fp:
                    data = json.load(fp)
                name = data.get("name", f.stem)
                dur = data.get("duration_sec", 0)
                actions = len(data.get("actions", []))
                rec_time = data.get("recorded_at", "")
                self.combo_tree.insert("", "end", values=(name, dur, actions, rec_time),
                                       tags=(str(f),))
            except Exception:
                self.combo_tree.insert("", "end", values=(f.stem, "?", "?", ""),
                                       tags=(str(f),))

    def _preview_combo(self):
        sel = self.combo_tree.selection()
        if not sel:
            return
        path = Path(self.combo_tree.item(sel[0], "tags")[0])
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            messagebox.showerror("错误", f"读取失败:\n{e}")
            return
        text = json.dumps(data, indent=2, ensure_ascii=False)
        top = tk.Toplevel(self.root)
        top.title(f"连招预览 - {data.get('name', path.stem)}")
        _center_on_parent(top, self.root, 700, 500)
        txt = tk.Text(top, wrap="word", font=("Consolas", 9))
        txt.pack(fill="both", expand=True, padx=6, pady=6)
        txt.insert("1.0", text)
        txt.configure(state="disabled")

    def _delete_combo_file(self):
        sel = self.combo_tree.selection()
        if not sel:
            return
        path = Path(self.combo_tree.item(sel[0], "tags")[0])
        if messagebox.askyesno("确认删除", f"确定删除连招文件?\n{path.name}"):
            path.unlink()
            self._refresh_combo_list()

    def _open_combos_folder(self):
        self._combo_dir.mkdir(parents=True, exist_ok=True)
        os.startfile(str(self._combo_dir))

    def _bind_combo_to_character(self):
        sel = self.combo_tree.selection()
        if not sel:
            messagebox.showwarning("提示", "请先在连招列表中选择一个连招")
            return
        combo_path = Path(self.combo_tree.item(sel[0], "tags")[0])
        try:
            with open(combo_path, encoding="utf-8") as f:
                combo_data = json.load(f)
        except Exception as e:
            messagebox.showerror("错误", f"读取连招失败:\n{e}")
            return
        BindComboDialog(self.root, combo_data, self.presets_path, self.preset_data,
                        lambda: self._refresh_char_table())

    def _notify(self, title, message, timeout=3):
        try:
            from plyer import notification
            threading.Thread(target=lambda: notification.notify(
                title=title, message=message, timeout=timeout, app_name="GameBot"
            ), daemon=True).start()
        except Exception:
            pass

    def _setup_recorder_hotkeys(self):
        import ctypes
        import ctypes.wintypes
        self._hotkey_thread_running = True
        self._hotkeys_enabled = True
        user32 = ctypes.windll.user32
        user32.RegisterHotKey(None, 1, 0x4000, 0x74)  # F5, MOD_NOREPEAT
        user32.RegisterHotKey(None, 2, 0x4000, 0x75)  # F6, MOD_NOREPEAT
        def _hotkey_loop():
            msg = ctypes.wintypes.MSG()
            while self._hotkey_thread_running:
                ret = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
                if ret == 0:
                    break
                if msg.message == 0x0312:  # WM_HOTKEY
                    if not self._hotkeys_enabled:
                        continue
                    if msg.wParam == 1:
                        self.root.after(0, self._toggle_recording)
                    elif msg.wParam == 2:
                        self.root.after(0, self._stop_recording)
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
        t = threading.Thread(target=_hotkey_loop, daemon=True)
        t.start()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close_with_hotkey)
        self._hotkey_thread = t

    def _on_close_with_hotkey(self):
        self._hotkey_thread_running = False
        import ctypes
        ctypes.windll.user32.UnregisterHotKey(None, 1)
        ctypes.windll.user32.UnregisterHotKey(None, 2)
        self._on_close()

    def _toggle_recording(self):
        if not hasattr(self, "_recording_active") or not self._recording_active:
            self.rec_btn.configure(text="⏹ F5 停止录制")
            self.rec_status.configure(text="倒计时3s...", foreground="orange")
            self._recording_active = True
            self._recorded_actions = None
            self._recorder_instance = None
            self._notify("GameBot 连招录制", "3秒后开始录制，按 F5/F6 停止")
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
            dur = result["duration_sec"]
            self.rec_status.configure(text=f"录制完成 ({dur}s)", foreground="green")
            self._notify("GameBot 录制完成", f"录制 {dur}s，共 {len(result['actions'])} 个动作")
            self.rec_result_text.configure(state="normal")
            self.rec_result_text.delete("1.0", "end")
            self.rec_result_text.insert("end", json.dumps(result, indent=2, ensure_ascii=False))
            self.rec_result_text.configure(state="disabled")
            self.root.after(200, self._prompt_save_combo)
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
        if not messagebox.askyesno("保存连招", "是否保存本次录制的连招?"):
            self._recorded_actions = None
            self._cleanup_temp_combos()
            return
        name = filedialog.asksaveasfilename(
            title="保存连招文件",
            initialdir=self._combo_dir,
            defaultextension=".json",
            filetypes=[("JSON", "*.json")],
        )
        if not name:
            self._recorded_actions = None
            return
        name = Path(name)
        output = {
            "name": name.stem,
            "recorded_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "duration_sec": 0,
            "actions": self._recorded_actions,
        }
        if self.rec_result_text.get("1.0", "end-1c").strip():
            import re
            m = re.search(r'"duration_sec": ([\d.]+)', self.rec_result_text.get("1.0", "end-1c"))
            if m:
                output["duration_sec"] = float(m.group(1))
        name.parent.mkdir(parents=True, exist_ok=True)
        with open(str(name), "w", encoding="utf-8") as f:
            json.dump(output, f, indent=2, ensure_ascii=False)
        self._cleanup_temp_combos()
        self._refresh_combo_list()
        self.rec_status.configure(text=f"已保存: {name.name}", foreground="green")

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
            logger.error("Screenshot capture failed: %s", e)
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
        self._sync_char_table_to_data()
        self._sync_global_config()
        chars = self.preset_data.get("characters", [])
        if not chars:
            messagebox.showwarning("提示", "预设中没有配置角色，请先添加角色")
            return
        counts = self.dash_char_count.get()
        stealth = self.dash_stealth.get()
        bg = self.dash_background.get()
        skip_combat = self.dash_skip_combat.get()
        cfg = Settings()
        cfg.last_preset = name
        cfg.save()
        self._hotkeys_enabled = False
        self.bot_stop_event.clear()
        self.bot_running = True
        self.status_label.configure(text="● 运行中", foreground="green")
        self.start_btn.configure(text="⏹ 停止")

        if skip_combat:
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
                    logger.info("Pre-activated game window: %s", best.title)
            except Exception as e:
                logger.warning("Pre-activate window failed: %s", e)

        self.bot_thread = threading.Thread(
            target=self._run_bot,
            args=(name, counts, stealth, bg, skip_combat),
            daemon=True,
        )
        self.bot_thread.start()

    def _run_bot(self, preset_name, total_chars, stealth, bg, skip_combat=False):
        controller = None
        capture = None
        window_mgr = None
        try:
            from config.settings import Settings
            from core.blackboard import Blackboard
            from core.fsm import FSM
            from capture.screen import ScreenCapture
            from input.controller import Controller
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
            blackboard = Blackboard()
            self._blackboard = blackboard
            blackboard["preset_name"] = preset_name
            blackboard["preset"] = preset
            blackboard["total_characters"] = total_chars
            blackboard["current_character_index"] = 0
            blackboard["domain_run_count"] = 0

            capture = ScreenCapture()
            capture.start(method=cfg.capture_method, fps_limit=cfg.fps_limit)
            blackboard["_capture"] = capture

            controller = Controller(stealth=stealth,
                                    combo_randomness=cfg.combo_randomness,
                                    bezier_steps=cfg.mouse_bezier_steps,
                                    click_jitter=cfg.click_jitter_px)

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
            import ctypes as _ct
            _sw = _ct.windll.user32.GetSystemMetrics(0)
            _sh = _ct.windll.user32.GetSystemMetrics(1)
            title = preset.get("window_title", "")
            blackboard["_window_rect"] = None

            wm = WindowManager(title_keyword=title)
            blackboard["_window_mgr"] = wm
            if wm.find_window(retries=5, interval=0.5):
                _b = wm._window.box
                _rect = (_b.left, _b.top, _b.left + _b.width, _b.top + _b.height)
                if _rect[0] < 0 or _rect[1] < 0:
                    logger.info("Window minimized, activating...")
                    wm.activate()
                    time.sleep(0.5)
                    _b = wm._window.box
                    _rect = (_b.left, _b.top, _b.left + _b.width, _b.top + _b.height)
                blackboard["_window_rect"] = _rect
                logger.info("Game window: hwnd=%s rect=(%d,%d,%d,%d)", wm.hwnd, *_rect)

            if blackboard["_window_rect"] is None:
                best_rect = None
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
                if best_rect:
                    blackboard["_window_rect"] = best_rect[1:]
                    logger.info("Game window (fallback): rect=(%d,%d,%d,%d)", *best_rect[1:])

            if wm and wm._window:
                if bg:
                    wm.save_position()
                window_mgr = wm

            if skip_combat:
                logger.info("跳过刷本模式：直接从town_exit开始（测试角色切换）")
                fsm.transition("town_exit", blackboard)
            else:
                fsm.transition("character_select", blackboard)
            logger.info("Bot started via GUI. Preset=%s Characters=%d", preset_name, total_chars)

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
                if stealth and fsm.current != "domain_combat":
                    controller.occasional_look_around()
                time.sleep(1.0 / max(cfg.fps_limit, 1))

            logger.info("Bot stopped")
        except Exception as e:
            logger.exception("Bot thread crashed: %s", e)
        finally:
            if controller:
                controller.release_all()
            if capture:
                capture.stop()
            if window_mgr:
                window_mgr.restore_position()
            self.bot_running = False
            self.root.after(0, self._bot_stopped)
            self.log_queue.put("==== Bot stopped ====")

    def _stop_bot(self):
        self.bot_stop_event.set()
        try:
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


class CharacterDialog:
    def __init__(self, parent, chars, idx, templates_path, on_save):
        self.chars = chars
        self.idx = idx
        self.templates_path = templates_path
        self.on_save = on_save
        self.char = chars[idx]
        self.dialog = tk.Toplevel(parent)
        self.dialog.title(f"编辑角色 - {self.char.get('name', '')}")
        _center_on_parent(self.dialog, parent, 800, 600)
        self.dialog.transient(parent)
        self.dialog.grab_set()
        self._build()
        self.dialog.wait_window()

    def _pick_template(self, entry, title):
        global _last_browse_dir
        start = _last_browse_dir if _last_browse_dir else self.templates_path
        path = filedialog.askopenfilename(
            title=title,
            initialdir=start,
            filetypes=[("图片文件", "*.png *.jpg *.jpeg *.bmp"), ("所有文件", "*.*")],
        )
        if path:
            _last_browse_dir = Path(path).parent
            name = _import_template_file(path, self.templates_path)
            entry.delete(0, "end")
            entry.insert(0, name)

    def _build(self):
        main = ttk.Frame(self.dialog, padding=8)
        main.pack(fill="both", expand=True)

        top = ttk.LabelFrame(main, text="角色信息", padding=6)
        top.pack(fill="x")

        r = ttk.Frame(top); r.pack(fill="x", pady=1)
        ttk.Label(r, text="名称:").pack(side="left")
        self.entry_name = ttk.Entry(r, width=16)
        self.entry_name.pack(side="left", padx=1)
        self.entry_name.insert(0, self.char.get("name", ""))
        ttk.Label(r, text="次数:").pack(side="left")
        self.spin_runs = ttk.Spinbox(r, from_=1, to_=99, width=4)
        self.spin_runs.pack(side="left", padx=2)
        self.spin_runs.delete(0, "end")
        self.spin_runs.insert(0, str(self.char.get("runs", 4)))

        def _tpl_row(parent, label, field, width=10):
            f = ttk.Frame(parent); f.pack(fill="x", pady=1)
            ttk.Label(f, text=label).pack(side="left")
            e = ttk.Entry(f, width=width)
            e.pack(side="left", padx=1)
            thr = ttk.Spinbox(f, from_=0.30, to=0.99, increment=0.05, width=4)
            thr.set("0.65")
            thr.pack(side="left", padx=1)
            btn = ttk.Button(f, text="浏览", width=4,
                             command=lambda en=e: self._pick_template(en, label))
            btn.pack(side="left")
            n, t = _unpack_tpl_value(self.char.get(field, ""))
            e.insert(0, n)
            thr.set(str(t))
            return e, thr

        def _tpl_row_pair(parent, label1, field1, label2, field2):
            f = ttk.Frame(parent); f.pack(fill="x", pady=1)
            ttk.Label(f, text=label1).pack(side="left")
            e1 = ttk.Entry(f, width=10); e1.pack(side="left", padx=1)
            thr1 = ttk.Spinbox(f, from_=0.30, to=0.99, increment=0.05, width=4)
            thr1.set("0.65"); thr1.pack(side="left", padx=1)
            ttk.Button(f, text="浏览", width=4,
                       command=lambda en=e1: self._pick_template(en, label1)).pack(side="left")
            n1, t1 = _unpack_tpl_value(self.char.get(field1, ""))
            e1.insert(0, n1); thr1.set(str(t1))
            ttk.Label(f, text=label2).pack(side="left", padx=(8, 0))
            e2 = ttk.Entry(f, width=10); e2.pack(side="left", padx=1)
            thr2 = ttk.Spinbox(f, from_=0.30, to=0.99, increment=0.05, width=4)
            thr2.set("0.65"); thr2.pack(side="left", padx=1)
            ttk.Button(f, text="浏览", width=4,
                       command=lambda en=e2: self._pick_template(en, label2)).pack(side="left")
            n2, t2 = _unpack_tpl_value(self.char.get(field2, ""))
            e2.insert(0, n2); thr2.set(str(t2))
            return (e1, thr1), (e2, thr2)

        (self.entry_portrait, self.thr_portrait), (self.entry_skillbar, self.thr_skillbar) = \
            _tpl_row_pair(top, "选人界面头像:", "portrait_template", "技能栏:", "skill_bar_template")
        (self.entry_result, self.thr_result), (self.entry_avatar, self.thr_avatar) = \
            _tpl_row_pair(top, "结算模板:", "result_screen_template", "城镇头像:", "avatar_template")

        bottom = ttk.Frame(main)
        bottom.pack(fill="x", pady=4)
        ttk.Button(bottom, text="保存", command=self._save).pack(side="right", padx=4)
        ttk.Button(bottom, text="取消", command=self.dialog.destroy).pack(side="right", padx=4)

        combo_frame = ttk.LabelFrame(main, text="连招列表", padding=6)
        combo_frame.pack(fill="both", expand=True, pady=4)
        combo_frame.columnconfigure(0, weight=1)
        combo_frame.rowconfigure(0, weight=1)
        cols = ("#", "按键", "持续", "前延", "后延", "按住", "重复")
        self.tree = ttk.Treeview(combo_frame, columns=cols, show="headings", height=6)
        for c in cols:
            self.tree.heading(c, text=c)
            w = 40 if c == "#" else 60 if c in ("按住",) else 50
            self.tree.column(c, width=w)
        self.tree.column("按键", width=120)
        scroll_tree = ttk.Scrollbar(combo_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll_tree.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        scroll_tree.grid(row=0, column=1, sticky="ns")
        self.tree.bind("<Double-1>", lambda e: self._edit_action())
        btn_row = ttk.Frame(combo_frame)
        btn_row.grid(row=1, column=0, columnspan=2, pady=4, sticky="ew")
        ttk.Button(btn_row, text="添加", command=self._add_action).pack(side="left", padx=2)
        ttk.Button(btn_row, text="编辑", command=self._edit_action).pack(side="left", padx=2)
        ttk.Button(btn_row, text="删除", command=self._del_action).pack(side="left", padx=2)
        ttk.Button(btn_row, text="上移", command=lambda: self._move(-1)).pack(side="left", padx=2)
        ttk.Button(btn_row, text="下移", command=lambda: self._move(1)).pack(side="left", padx=2)
        self._refresh()

    def _refresh(self):
        self.tree.delete(*self.tree.get_children(""))
        for i, act in enumerate(self.char.get("combos", [])):
            self.tree.insert("", "end", values=(
                i + 1,
                "+".join(act.get("keys", [])),
                act.get("duration", 0.1),
                act.get("delay_before", 0.0),
                act.get("delay_after", 0.0),
                "是" if act.get("hold") else "否",
                act.get("repeat", 1),
            ))

    def _add_action(self):
        ActionDialog(self.dialog, self.char.setdefault("combos", []), -1, self._refresh)

    def _edit_action(self):
        sel = self.tree.selection()
        if not sel:
            return
        idx = int(self.tree.item(sel[0], "values")[0]) - 1
        ActionDialog(self.dialog, self.char["combos"], idx, self._refresh)

    def _del_action(self):
        sel = self.tree.selection()
        if not sel:
            return
        idx = int(self.tree.item(sel[0], "values")[0]) - 1
        combos = self.char.get("combos", [])
        if 0 <= idx < len(combos):
            del combos[idx]
            self._refresh()

    def _move(self, direction):
        sel = self.tree.selection()
        if not sel:
            return
        idx = int(self.tree.item(sel[0], "values")[0]) - 1
        combos = self.char.get("combos", [])
        new_idx = idx + direction
        if 0 <= new_idx < len(combos):
            combos[idx], combos[new_idx] = combos[new_idx], combos[idx]
            self._refresh()

    def _save(self):
        self.char["name"] = self.entry_name.get()
        self.char["portrait_template"] = _pack_tpl_value(self.entry_portrait.get(), self.thr_portrait.get())
        self.char["skill_bar_template"] = _pack_tpl_value(self.entry_skillbar.get(), self.thr_skillbar.get()) or None
        self.char["result_screen_template"] = _pack_tpl_value(self.entry_result.get(), self.thr_result.get()) or None
        self.char["avatar_template"] = _pack_tpl_value(self.entry_avatar.get(), self.thr_avatar.get()) or None
        try:
            self.char["runs"] = int(self.spin_runs.get())
        except ValueError:
            pass
        self.chars[self.idx] = self.char
        self.on_save()
        self.dialog.destroy()


AVAILABLE_KEYS = ["w", "a", "s", "d", "1", "2", "3", "4", "5",
                   "e", "q", "space", "left_shift", "left_ctrl", "left_alt",
                   "left_click", "right_click", "esc", "tab", "f"]


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

    def _add_param(self, parent, label, key, min_v, max_v):
        f = ttk.Frame(parent)
        f.pack(side="left", padx=4)
        ttk.Label(f, text=label).pack()
        var = tk.StringVar(value=str(self.action.get(key, 0.1)))
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
        keys = sorted(k for k, v in self.key_vars.items() if v.get())
        if not keys:
            messagebox.showwarning("提示", "请至少选择一个按键")
            return
        action = {
            "keys": keys,
            "duration": float(self.param_duration.get()),
            "delay_before": float(self.param_delay_before.get()),
            "delay_after": float(self.param_delay_after.get()),
            "hold": self.hold_var.get(),
            "repeat": int(self.param_repeat.get()),
        }
        if self.idx >= 0 and self.idx < len(self.combos):
            self.combos[self.idx] = action
        else:
            self.combos.append(action)
        self.on_save()
        self.dialog.destroy()


class FallbackComboDialog:
    def __init__(self, parent, combos, on_save):
        self.combos = list(combos)
        self.on_save = on_save
        self.dialog = tk.Toplevel(parent)
        self.dialog.title("编辑兜底连招")
        _center_on_parent(self.dialog, parent, 700, 400)
        self.dialog.transient(parent)
        self.dialog.grab_set()
        self._build()
        self.dialog.wait_window()

    def _build(self):
        main = ttk.Frame(self.dialog, padding=8)
        main.pack(fill="both", expand=True)
        cols = ("#", "按键", "持续", "前延", "后延", "按住", "重复")
        self.tree = ttk.Treeview(main, columns=cols, show="headings", height=8)
        for c in cols:
            self.tree.heading(c, text=c)
            w = 40 if c == "#" else 60 if c in ("按住",) else 50
            self.tree.column(c, width=w)
        self.tree.column("按键", width=120)
        scroll_tree = ttk.Scrollbar(main, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll_tree.set)
        self.tree.pack(fill="both", expand=True)
        scroll_tree.pack(side="right", fill="y")
        self.tree.bind("<Double-1>", lambda e: self._edit_action())
        btn_frame = ttk.Frame(main)
        btn_frame.pack(fill="x", pady=4)
        ttk.Button(btn_frame, text="添加", command=self._add_action).pack(side="left", padx=2)
        ttk.Button(btn_frame, text="编辑", command=self._edit_action).pack(side="left", padx=2)
        ttk.Button(btn_frame, text="删除", command=self._del_action).pack(side="left", padx=2)
        ttk.Button(btn_frame, text="上移", command=lambda: self._move(-1)).pack(side="left", padx=2)
        ttk.Button(btn_frame, text="下移", command=lambda: self._move(1)).pack(side="left", padx=2)
        bottom = ttk.Frame(main)
        bottom.pack(fill="x", pady=4)
        ttk.Button(bottom, text="确定", command=self._save).pack(side="right", padx=4)
        ttk.Button(bottom, text="取消", command=self.dialog.destroy).pack(side="right", padx=4)
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
                "是" if act.get("hold") else "否",
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
        self.on_save(list(self.combos))
        self.dialog.destroy()


class BindComboDialog:
    def __init__(self, parent, combo_data, presets_path, current_preset_data, on_save):
        self.combo_data = combo_data
        self.presets_path = Path(presets_path)
        self.current_preset_data = current_preset_data
        self.on_save = on_save
        self._selected_preset = None
        self._selected_char_idx = None

        self.dialog = tk.Toplevel(parent)
        self.dialog.title("绑定连招到角色")
        _center_on_parent(self.dialog, parent, 500, 400)
        self.dialog.minsize(400, 300)
        self.dialog.transient(parent)
        self.dialog.grab_set()
        self._build()
        self.dialog.wait_window()

    def _build(self):
        main = ttk.Frame(self.dialog, padding=8)
        main.pack(fill="both", expand=True)

        self.char_listbox = tk.Listbox(main, height=6)
        self.char_listbox.pack(fill="x", pady=(2, 4))
        self.char_listbox.bind("<<ListboxSelect>>", self._on_char_selected)

        ttk.Label(main, text="选择目标预设:", font=("", 9, "bold")).pack(anchor="w")
        self.preset_var = tk.StringVar()
        self.preset_combo = ttk.Combobox(main, textvariable=self.preset_var,
                                          state="readonly", width=40)
        self.preset_combo.pack(fill="x", pady=2)
        self.preset_combo.bind("<<ComboboxSelected>>", self._on_preset_selected)
        self._refresh_preset_list()

        info = ttk.LabelFrame(main, text="连招信息", padding=4)
        info.pack(fill="x", pady=6)
        actions = self.combo_data.get("actions", [])
        name = self.combo_data.get("name", "未知")
        dur = self.combo_data.get("duration_sec", 0)
        ttk.Label(info, text=f"连招: {name}  |  动作数: {len(actions)}  |  时长: {dur}s").pack()

        btn_frame = ttk.Frame(main)
        btn_frame.pack(fill="x", pady=6)
        ttk.Button(btn_frame, text="确认绑定", command=self._confirm).pack(side="right", padx=4)
        ttk.Button(btn_frame, text="取消", command=self.dialog.destroy).pack(side="right", padx=4)

    def _refresh_preset_list(self):
        presets = []
        if self.presets_path.exists():
            for p in sorted(self.presets_path.glob("*.json")):
                presets.append(p.stem)
        self.preset_combo["values"] = presets
        current_name = self.current_preset_data.get("description") or ""
        if current_name and current_name in presets:
            self.preset_var.set(current_name)
        elif presets:
            self.preset_var.set(presets[0])
        self._load_preset_chars()

    def _on_preset_selected(self, event=None):
        self._load_preset_chars()

    def _load_preset_chars(self):
        self.char_listbox.delete(0, "end")
        self._selected_char_idx = None
        name = self.preset_var.get()
        if not name:
            return
        path = self.presets_path / f"{name}.json"
        if not path.exists():
            return
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            self._selected_preset = data
            for i, ch in enumerate(data.get("characters", [])):
                ch_name = ch.get("name", f"角色{i+1}")
                runs = ch.get("runs", 1)
                combo_count = len(ch.get("combos", []))
                self.char_listbox.insert("end", f"{i+1}. {ch_name}  (次数:{runs}, 连招:{combo_count})")
        except Exception:
            pass

    def _on_char_selected(self, event=None):
        sel = self.char_listbox.curselection()
        self._selected_char_idx = sel[0] if sel else None

    def _confirm(self):
        if self._selected_char_idx is None:
            messagebox.showwarning("提示", "请选择一个角色")
            return
        if not self._selected_preset:
            messagebox.showwarning("提示", "请先选择预设")
            return
        chars = self._selected_preset.setdefault("characters", [])
        if self._selected_char_idx >= len(chars):
            messagebox.showwarning("提示", "角色索引无效")
            return
        import copy
        target = chars[self._selected_char_idx]
        target.setdefault("combos", []).extend(copy.deepcopy(self.combo_data.get("actions", [])))
        path = self.presets_path / f"{self.preset_var.get()}.json"
        with open(str(path), "w", encoding="utf-8") as f:
            json.dump(self._selected_preset, f, indent=2, ensure_ascii=False)
        if self.current_preset_data.get("description") == self._selected_preset.get("description"):
            self.current_preset_data["characters"] = self._selected_preset["characters"]
        self.on_save()
        messagebox.showinfo("绑定成功", f"已追加到角色「{target['name']}」的连招列表")
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
        logger.info("Template saved: %s (%dx%d)", name.name,
                     crop_x2 - crop_x1, crop_y2 - crop_y1)
        if self.on_save:
            self.on_save()
        self.dialog.destroy()
