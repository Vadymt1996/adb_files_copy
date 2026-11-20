import os
import re
import shutil
import string
import subprocess
import threading
import time
import tkinter as tk
from collections import deque
from datetime import datetime
from tkinter import filedialog, messagebox, simpledialog, ttk

import ctypes
from ctypes import wintypes

# Win32 Drag & Drop API константи та функції
WM_DROPFILES = 0x0233
GWLP_WNDPROC = -4

shell32 = ctypes.windll.shell32
user32 = ctypes.windll.user32

WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_longlong, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)

SetWindowLongPtr = user32.SetWindowLongPtrW
SetWindowLongPtr.restype = ctypes.c_longlong
SetWindowLongPtr.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_void_p]

CallWindowProc = user32.CallWindowProcW
CallWindowProc.restype = ctypes.c_longlong
CallWindowProc.argtypes = [ctypes.c_void_p, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]

shell32.DragQueryFileW.argtypes = [ctypes.c_void_p, wintypes.UINT, wintypes.LPWSTR, wintypes.UINT]
shell32.DragQueryFileW.restype = wintypes.UINT

shell32.DragFinish.argtypes = [ctypes.c_void_p]
shell32.DragFinish.restype = None

shell32.DragQueryPoint.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.POINT)]
shell32.DragQueryPoint.restype = wintypes.BOOL


class DummyLocalPane:
    """Тимчасовий об'єкт джерела для файлів, перетягнутих із зовнішнього Провідника Windows"""

    def __init__(self, current_path):
        self.mode = "pc"
        self.current_path = current_path
        self.files_cache = []

    def refresh(self):
        pass

# Регулярний вираз для парсингу стандартного виводу adb shell ls -la
LS_REGEX = re.compile(
    r'^([bcdlps-])([rwxstST-]{9}\+?)\s+'          # 1: тип, 2: права
    r'(?:\d+\s+)?'                                # необов'язкова к-ть посилань
    r'(\S+)\s+'                                   # 3: власник
    r'(\S+)\s+'                                   # 4: група
    r'(\d+)\s+'                                   # 5: розмір у байтах
    r'([A-Za-z]{3}\s+\d+|\d{4}-\d{2}-\d{2})\s+'  # 6: дата ч.1
    r'(\d{2}:\d{2}|\d{4})\s+'                     # 7: час або рік
    r'(.+)$'                                      # 8: назва
)

# Категорії файлів для швидкої фільтрації
FILE_CATEGORIES = {
    "🌐 Всі типи": None,
    "🖼️ Зображення": ['.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp', '.heic', '.raw', '.svg', '.tiff'],
    "🎬 Відео": ['.mp4', '.mkv', '.avi', '.mov', '.3gp', '.webm', '.wmv', '.flv', '.ts', '.m4v'],
    "🎵 Музика / Аудіо": ['.mp3', '.m4a', '.wav', '.flac', '.ogg', '.aac', '.wma', '.opus', '.mid'],
    "📄 Документи": ['.pdf', '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx', '.txt', '.csv', '.log', '.md', '.rtf', '.html', '.htm', '.xml', '.json'],
    "📦 Архіви": ['.zip', '.rar', '.7z', '.tar', '.gz', '.bz2', '.xz', '.tgz'],
    "📱 Додатки (APK)": ['.apk', '.xapk', '.apks']
}


def format_size(bytes_val):
    """Форматування розміру у читабельний вигляд (Б, КБ, МБ, ГБ)"""
    if bytes_val is None:
        return ""
    if bytes_val < 1024:
        return f"{bytes_val} Б"
    elif bytes_val < 1024 * 1024:
        return f"{bytes_val / 1024:.1f} КБ"
    elif bytes_val < 1024 * 1024 * 1024:
        return f"{bytes_val / (1024 * 1024):.1f} МБ"
    else:
        return f"{bytes_val / (1024 * 1024 * 1024):.2f} ГБ"


def get_file_icon(name, is_dir, is_link=False):
    """Повертає відповідну піктограму за типом або розширенням"""
    if is_dir:
        return "📁 "
    if is_link:
        return "🔗 "

    ext = os.path.splitext(name)[1].lower()
    if ext in ['.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp', '.heic', '.raw', '.svg', '.tiff']:
        return "🖼️ "
    elif ext in ['.mp4', '.mkv', '.avi', '.mov', '.3gp', '.webm', '.wmv', '.flv', '.ts', '.m4v']:
        return "🎬 "
    elif ext in ['.mp3', '.m4a', '.wav', '.flac', '.ogg', '.aac', '.wma', '.opus', '.mid']:
        return "🎵 "
    elif ext in ['.zip', '.rar', '.7z', '.tar', '.gz', '.bz2', '.xz', '.tgz']:
        return "📦 "
    elif ext in ['.apk', '.xapk', '.apks']:
        return "📱 "
    elif ext in ['.pdf', '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx', '.txt', '.log', '.csv', '.md', '.rtf']:
        return "📄 "
    return "📎 "


def get_windows_drives():
    """Повертає список доступних локальних дисків на Windows"""
    if os.name != 'nt':
        return ["/"]
    return [f"{letter}:\\" for letter in string.ascii_uppercase if os.path.exists(f"{letter}:\\")]


def format_eta(seconds):
    """Форматує залишок часу у вигляді ММ:СС або Г:ММ:СС"""
    if seconds is None or seconds < 0:
        return "--:--"
    seconds = int(round(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h > 0:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def get_local_path_size(path):
    """Повертає розмір локального файлу або сумарний розмір усіх файлів у папці"""
    if not os.path.exists(path):
        return 0
    if os.path.isfile(path) or os.path.islink(path):
        try:
            return os.path.getsize(path)
        except OSError:
            return 0
    total = 0
    try:
        for root_d, _, files in os.walk(path):
            for fn in files:
                try:
                    total += os.path.getsize(os.path.join(root_d, fn))
                except OSError:
                    pass
    except OSError:
        pass
    return total


class SpeedTracker:
    """Розраховує згладжену швидкість передачі (байт/с) та орієнтовний час до кінця (ETA)."""

    def __init__(self, total_bytes, window_sec=3.0):
        self.total_bytes = max(0, total_bytes)
        self.start_time = time.monotonic()
        self.window_sec = window_sec
        self.samples = deque([(self.start_time, 0)])

    def update(self, bytes_done):
        now = time.monotonic()
        self.samples.append((now, bytes_done))
        while len(self.samples) > 2 and (now - self.samples[0][0]) > self.window_sec:
            self.samples.popleft()

        dt = now - self.samples[0][0]
        db = bytes_done - self.samples[0][1]

        speed_bps = 0.0
        if dt >= 0.25 and db > 0:
            speed_bps = db / dt
        else:
            total_dt = now - self.start_time
            if total_dt >= 0.25 and bytes_done > 0:
                speed_bps = bytes_done / total_dt

        eta_sec = None
        if speed_bps > 0 and self.total_bytes > bytes_done:
            eta_sec = (self.total_bytes - bytes_done) / speed_bps
        elif self.total_bytes > 0 and bytes_done >= self.total_bytes:
            eta_sec = 0

        return speed_bps, eta_sec


class FilePane:
    """Одна з двох файлових панелей (як у Total Commander), може працювати з Телефоном або ПК"""

    def __init__(self, parent, app, side, initial_mode="phone", initial_path="/sdcard"):
        self.app = app
        self.side = side  # "left" або "right"
        self.mode = initial_mode  # "phone" або "pc"
        self.current_path = initial_path
        self.files_cache = []
        self.iid_to_name = {}
        self.sort_column = "name"
        self.sort_reverse = False
        self.is_active = False

        # Змінні інтерфейсу панелі
        self.path_var = tk.StringVar(value=initial_path)
        self.filter_var = tk.StringVar(value="")
        self.filter_var.trace_add("write", lambda *args: self.render_file_tree())
        self.type_filter_var = tk.StringVar(value="🌐 Всі типи")
        self.show_folders_var = tk.BooleanVar(value=True)

        # Стан внутрішнього перетягування (Drag & Drop)
        self._drag_data = {
            "start_x": 0,
            "start_y": 0,
            "is_dragging": False,
            "items": [],
            "tip_window": None,
            "press_iid": None
        }

        # Зовнішній контейнер панелі з рамкою для підсвітки активної панелі
        self.outer_frame = tk.Frame(parent, bd=2, relief="groove", bg="#CFD8DC")
        self.frame = tk.Frame(self.outer_frame, bg="#FFFFFF")
        self.frame.pack(fill="both", expand=True, padx=1, pady=1)

        self._build_ui()

    def _build_ui(self):
        side_title = "ЛІВА ПАНЕЛЬ" if self.side == "left" else "ПРАВА ПАНЕЛЬ"

        # 1. Заголовок панелі: режим (Телефон / ПК) + індикатор активності
        self.pane_header = tk.Frame(self.frame, bg="#ECEFF1", pady=3, padx=6)
        self.pane_header.pack(fill="x")

        self.active_badge = tk.Label(
            self.pane_header,
            text=f"  {side_title}",
            font=("Arial", 9, "bold"),
            bg="#ECEFF1",
            fg="#37474F"
        )
        self.active_badge.pack(side="left")

        mode_frame = tk.Frame(self.pane_header, bg="#ECEFF1")
        mode_frame.pack(side="right")

        self.btn_mode_phone = tk.Button(
            mode_frame,
            text="📱 Телефон",
            font=("Arial", 8, "bold"),
            relief="raised",
            padx=6,
            command=lambda: self.set_mode("phone")
        )
        self.btn_mode_phone.pack(side="left", padx=2)

        self.btn_mode_pc = tk.Button(
            mode_frame,
            text="💻 Комп'ютер",
            font=("Arial", 8, "bold"),
            relief="raised",
            padx=6,
            command=lambda: self.set_mode("pc")
        )
        self.btn_mode_pc.pack(side="left", padx=2)

        # 2. Панель швидких закладок / дисків (динамічна залежно від режиму)
        self.bookmarks_frame = tk.Frame(self.frame, pady=2, padx=4)
        self.bookmarks_frame.pack(fill="x")
        self._rebuild_bookmarks()

        # 3. Навігаційна панель (Вгору + Шлях + Перейти + Оновити)
        nav_frame = tk.Frame(self.frame, padx=4, pady=2)
        nav_frame.pack(fill="x")

        self.btn_up = tk.Button(nav_frame, text="⬆️ ..", font=("Arial", 8, "bold"), width=5, command=self.go_up)
        self.btn_up.pack(side="left", padx=(0, 3))

        self.path_entry = tk.Entry(nav_frame, textvariable=self.path_var, font=("Consolas", 9))
        self.path_entry.pack(side="left", fill="x", expand=True, padx=(0, 3))
        self.path_entry.bind("<Return>", lambda e: self.navigate_to(self.path_var.get()))
        self.path_entry.bind("<FocusIn>", lambda e: self.activate())

        self.btn_browse_pc = tk.Button(nav_frame, text="📁", font=("Arial", 8), width=3, command=self.browse_pc_folder)
        self.btn_browse_pc.pack(side="left", padx=(0, 3))

        tk.Button(nav_frame, text="Перейти", font=("Arial", 8), command=lambda: self.navigate_to(self.path_var.get())).pack(side="left", padx=(0, 3))
        tk.Button(nav_frame, text="🔄", font=("Arial", 8), width=3, command=self.refresh).pack(side="left")

        # 4. Панель фільтрів (Тип файлів + Пошук + Папки + Скинути)
        filter_bar = tk.Frame(self.frame, bg="#F5F5F5", bd=1, relief="groove")
        filter_bar.pack(fill="x", padx=4, pady=2)

        tk.Label(filter_bar, text="Тип:", font=("Arial", 8, "bold"), bg="#F5F5F5").pack(side="left", padx=(4, 2), pady=2)
        self.type_combobox = ttk.Combobox(
            filter_bar,
            textvariable=self.type_filter_var,
            values=list(FILE_CATEGORIES.keys()),
            state="readonly",
            width=14,
            font=("Arial", 8)
        )
        self.type_combobox.pack(side="left", padx=(0, 6), pady=2)
        self.type_combobox.bind("<<ComboboxSelected>>", lambda e: (self.activate(), self.render_file_tree()))

        tk.Label(filter_bar, text="🔍", font=("Arial", 8), bg="#F5F5F5").pack(side="left", padx=(0, 2), pady=2)
        self.filter_entry = tk.Entry(filter_bar, textvariable=self.filter_var, font=("Arial", 8), width=12)
        self.filter_entry.pack(side="left", fill="x", expand=True, padx=(0, 4), pady=2)
        self.filter_entry.bind("<FocusIn>", lambda e: self.activate())

        self.check_show_folders = tk.Checkbutton(
            filter_bar,
            text="Папки",
            variable=self.show_folders_var,
            command=lambda: (self.activate(), self.render_file_tree()),
            bg="#F5F5F5",
            font=("Arial", 8)
        )
        self.check_show_folders.pack(side="left", padx=(0, 2), pady=2)

        tk.Button(filter_bar, text="✖", font=("Arial", 8), padx=3, pady=0, command=self.reset_filters).pack(side="right", padx=3, pady=2)

        # 5. Вкладки (Всі / Не скопійовані / До видалення)
        self.notebook = ttk.Notebook(self.frame)
        self.notebook.pack(fill="both", expand=True, padx=4, pady=(2, 2))

        self.tab_all = ttk.Frame(self.notebook)
        self.tab_new = ttk.Frame(self.notebook)
        self.tab_copied = ttk.Frame(self.notebook)

        self.notebook.add(self.tab_all, text="📋 Всі (0)")
        self.notebook.add(self.tab_new, text="🆕 Не скопійовані (0)")
        self.notebook.add(self.tab_copied, text="🗑️ Вже в цілі (0)")
        self.notebook.bind("<<NotebookTabChanged>>", self.on_tab_changed)

        # 6. Таблиця файлів (Treeview)
        self.tree_frame = tk.Frame(self.notebook)
        self.tree_frame.pack(in_=self.tab_all, fill="both", expand=True)

        columns = ("num", "name", "size", "date", "status")
        self.tree = ttk.Treeview(self.tree_frame, columns=columns, show="headings", selectmode="extended")

        self.tree.heading("num", text="№", command=lambda: self.sort_by("num"))
        self.tree.heading("name", text="Назва", command=lambda: self.sort_by("name"))
        self.tree.heading("size", text="Розмір", command=lambda: self.sort_by("size"))
        self.tree.heading("date", text="Дата", command=lambda: self.sort_by("date"))
        self.tree.heading("status", text="Статус", command=lambda: self.sort_by("status"))

        self.tree.column("num", width=36, minwidth=30, anchor="center", stretch=False)
        self.tree.column("name", width=220, minwidth=120, anchor="w", stretch=True)
        self.tree.column("size", width=78, minwidth=60, anchor="e", stretch=False)
        self.tree.column("date", width=112, minwidth=85, anchor="center", stretch=False)
        self.tree.column("status", width=92, minwidth=75, anchor="center", stretch=False)

        scrollbar_y = ttk.Scrollbar(self.tree_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar_y.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scrollbar_y.pack(side="right", fill="y")

        # Події Treeview
        self.tree.bind("<Button-1>", lambda e: self.activate())
        self.tree.bind("<FocusIn>", lambda e: self.activate())
        self.tree.bind("<Double-1>", self.on_double_click)
        self.tree.bind("<Return>", self.on_double_click)
        self.tree.bind("<BackSpace>", lambda e: (self.go_up(), "break"))
        self.tree.bind("<Delete>", lambda e: (self.activate(), self.app.confirm_delete_selected(), "break"))
        self.tree.bind("<Control-a>", lambda e: (self.select_all_items(), "break"))
        self.tree.bind("<Control-A>", lambda e: (self.select_all_items(), "break"))
        self.tree.bind("<Button-3>", self.show_context_menu)
        self.tree.bind("<<TreeviewSelect>>", self.on_selection_changed)

        # Drag and Drop події (перетягування файлів)
        self.tree.bind("<ButtonPress-1>", self._on_drag_press, add="+")
        self.tree.bind("<B1-Motion>", self._on_drag_motion)
        self.tree.bind("<ButtonRelease-1>", self._on_drag_release)
        self.tree.bind("<Escape>", self._on_drag_cancel)

        # Контекстне меню панелі
        self.context_menu = tk.Menu(self.frame, tearoff=0)
        self.context_menu.add_command(label="📋 Копіювати в іншу панель (F5)", command=lambda: (self.activate(), self.app.start_transfer(move=False)))
        self.context_menu.add_command(label="✂️ Перемістити в іншу панель (F6)", command=lambda: (self.activate(), self.app.start_transfer(move=True)))
        self.context_menu.add_separator()
        self.context_menu.add_command(label="📂 Відкрити у системі / Провіднику", command=self.open_in_system)
        self.context_menu.add_command(label="📋 Копіювати шлях", command=self.copy_path_to_clipboard)
        self.context_menu.add_separator()
        self.context_menu.add_command(label="📁 Нова папка (F7)", command=lambda: (self.activate(), self.app.create_new_folder()))
        self.context_menu.add_command(label="✏️ Перейменувати (F2)", command=lambda: (self.activate(), self.app.rename_selected()))
        self.context_menu.add_command(label="🗑️ Видалити (F8 / Del)", command=lambda: (self.activate(), self.app.confirm_delete_selected()))
        self.context_menu.add_separator()
        self.context_menu.add_command(label="☑ Вибрати всі", command=self.select_all_items)
        self.context_menu.add_command(label="✨ Вибрати тільки нові", command=self.select_only_new_items)
        self.context_menu.add_command(label="🔄 Оновити", command=self.refresh)

        # 7. Панель мульти-вибору
        select_bar = tk.Frame(self.frame, padx=4, pady=2)
        select_bar.pack(fill="x")

        tk.Button(select_bar, text="☑ Всі", font=("Arial", 8), command=self.select_all_items).pack(side="left", padx=(0, 2))
        tk.Button(select_bar, text="✨ Тільки нові", font=("Arial", 8, "bold"), fg="#1B5E20", command=self.select_only_new_items).pack(side="left", padx=(0, 2))
        tk.Button(select_bar, text="☐ Скинути", font=("Arial", 8), command=self.deselect_all_items).pack(side="left", padx=(0, 2))
        tk.Button(select_bar, text="🔄 Інв.", font=("Arial", 8), command=self.invert_selection).pack(side="left", padx=(0, 2))

        self.selection_label = tk.Label(select_bar, text="Вибрано: 0 (0 Б)", font=("Arial", 8, "bold"), fg="#333")
        self.selection_label.pack(side="right")

        # 8. Інформаційний рядок панелі
        self.info_status_label = tk.Label(self.frame, text="Очікування...", font=("Arial", 8), fg="#555", anchor="w", padx=6)
        self.info_status_label.pack(fill="x", pady=(0, 3))

        self._update_mode_buttons()

    def activate(self):
        self.app.set_active_pane(self)

    def set_active(self, active):
        self.is_active = active
        side_title = "ЛІВА" if self.side == "left" else "ПРАВА"
        mode_str = "📱 ТЕЛЕФОН" if self.mode == "phone" else "💻 ПК"
        if active:
            self.outer_frame.config(bg="#1565C0")
            self.pane_header.config(bg="#BBDEFB")
            self.active_badge.config(text=f"★ {side_title}: {mode_str} (АКТИВНА)", bg="#BBDEFB", fg="#0D47A1")
        else:
            self.outer_frame.config(bg="#CFD8DC")
            self.pane_header.config(bg="#ECEFF1")
            self.active_badge.config(text=f"  {side_title}: {mode_str}", bg="#ECEFF1", fg="#546E7A")

    def _update_mode_buttons(self):
        if self.mode == "phone":
            self.btn_mode_phone.config(relief="sunken", bg="#C8E6C9", fg="#1B5E20")
            self.btn_mode_pc.config(relief="raised", bg="#F5F5F5", fg="#333333")
            self.btn_browse_pc.config(state="disabled")
        else:
            self.btn_mode_phone.config(relief="raised", bg="#F5F5F5", fg="#333333")
            self.btn_mode_pc.config(relief="sunken", bg="#BBDEFB", fg="#0D47A1")
            self.btn_browse_pc.config(state="normal")
        self.set_active(self.is_active)

    def _rebuild_bookmarks(self):
        for w in self.bookmarks_frame.winfo_children():
            w.destroy()

        if self.mode == "phone":
            bookmarks = [
                ("💾 /sdcard", "/sdcard"),
                ("📷 DCIM", "/sdcard/DCIM"),
                ("📥 Download", "/sdcard/Download"),
                ("🖼️ Pictures", "/sdcard/Pictures"),
                ("📄 Docs", "/sdcard/Documents"),
                ("🎵 Music", "/sdcard/Music"),
                ("🌐 /", "/"),
            ]
        else:
            drives = [(f"💽 {d}", d) for d in get_windows_drives()]
            home = os.path.expanduser("~")
            bookmarks = drives + [
                ("📥 Завантаж.", os.path.join(home, "Downloads")),
                ("🖥️ Стіл", os.path.join(home, "Desktop")),
                ("📄 Документи", os.path.join(home, "Documents")),
            ]

        for title, path in bookmarks:
            btn = tk.Button(
                self.bookmarks_frame,
                text=title,
                font=("Arial", 8),
                relief="groove",
                bg="#FAFAFA",
                padx=4,
                pady=1,
                command=lambda p=path: (self.activate(), self.navigate_to(p))
            )
            btn.pack(side="left", padx=1)

    def set_mode(self, new_mode, target_path=None):
        self.activate()
        if new_mode == self.mode and target_path is None:
            return
        self.mode = new_mode
        self._update_mode_buttons()
        self._rebuild_bookmarks()

        if target_path:
            self.current_path = target_path
        elif new_mode == "phone":
            self.current_path = "/sdcard"
        else:
            dl = os.path.join(os.path.expanduser("~"), "Downloads")
            self.current_path = dl if os.path.exists(dl) else os.path.expanduser("~")

        self.path_var.set(self.current_path)
        self.app.update_transfer_buttons()
        self.refresh()

    def browse_pc_folder(self):
        self.activate()
        if self.mode != "pc":
            return
        folder = filedialog.askdirectory(title="Виберіть папку на комп'ютері", initialdir=self.current_path)
        if folder:
            self.navigate_to(folder)

    def get_other_pane(self):
        return self.app.right_pane if self.side == "left" else self.app.left_pane

    def refresh(self):
        self.navigate_to(self.current_path)

    def go_up(self):
        self.activate()
        if self.mode == "phone":
            if self.current_path in ["", "/"]:
                return
            parent = os.path.dirname(self.current_path) or "/"
            self.navigate_to(parent)
        else:
            norm = os.path.normpath(self.current_path)
            parent = os.path.dirname(norm)
            if parent and parent != norm:
                self.navigate_to(parent)

    def navigate_to(self, new_path):
        clean_path = new_path.strip()
        if self.mode == "phone":
            if not self.app.current_device_id:
                self.clear_file_list("Смартфон не підключено")
                return
            if not clean_path:
                clean_path = "/sdcard"
            if not clean_path.startswith("/"):
                clean_path = "/" + clean_path
            clean_path = re.sub(r'/+', '/', clean_path)
            if len(clean_path) > 1 and clean_path.endswith("/"):
                clean_path = clean_path[:-1]

            self.info_status_label.config(text=f"Завантаження {clean_path}...")
            threading.Thread(target=self._fetch_phone_directory, args=(clean_path,), daemon=True).start()
        else:
            if not clean_path:
                clean_path = os.path.expanduser("~")
            clean_path = os.path.normpath(clean_path)
            if not os.path.exists(clean_path) or not os.path.isdir(clean_path):
                messagebox.showerror("Помилка", f"Папка на ПК не існує:\n{clean_path}")
                return
            self._fetch_pc_directory(clean_path)

    def _fetch_pc_directory(self, path):
        entries = []
        try:
            with os.scandir(path) as it:
                for entry in it:
                    try:
                        is_dir = entry.is_dir(follow_symlinks=False)
                        is_link = entry.is_symlink()
                        stat_info = entry.stat(follow_symlinks=False)
                        size = None if is_dir else stat_info.st_size
                        mtime = datetime.fromtimestamp(stat_info.st_mtime).strftime("%Y-%m-%d %H:%M")
                        entries.append({
                            "name": entry.name,
                            "is_dir": is_dir,
                            "is_link": is_link,
                            "link_target": "",
                            "size": size,
                            "date": mtime,
                            "raw_type": "d" if is_dir else "-"
                        })
                    except OSError:
                        continue
        except PermissionError:
            messagebox.showerror("Помилка доступу", f"Немає прав доступу до папки:\n{path}")
            return
        except Exception as e:
            messagebox.showerror("Помилка", f"Не вдалося прочитати папку {path}:\n{e}")
            return

        self._apply_directory(path, entries)

    def _fetch_phone_directory(self, path):
        safe_path = path.replace("'", "'\\''")
        ret, stdout, stderr = self.app.execute_adb(["shell", f"cd '{safe_path}' && ls -la"])

        if ret != 0 or "No such file or directory" in stderr or "Permission denied" in stderr:
            err_msg = stderr.strip() or stdout.strip() or "Помилка доступу до каталогу"
            self.app.safe_after(0, lambda: messagebox.showerror("Помилка", f"Не вдалося відкрити {path}:\n{err_msg}"))
            self.app.safe_after(0, lambda: self.info_status_label.config(text=f"Помилка відкриття {path}"))
            return

        entries = []
        for line in stdout.splitlines():
            line_str = line.strip()
            if not line_str or line_str.startswith("total ") or "??" in line_str:
                continue

            match = LS_REGEX.match(line_str)
            if match:
                ftype = match.group(1)
                size = int(match.group(5))
                date_str = f"{match.group(6)} {match.group(7)}"
                full_name = match.group(8)

                is_dir = (ftype == 'd')
                is_link = (ftype == 'l')
                name = full_name
                link_target = ""

                if is_link and " -> " in full_name:
                    name, link_target = full_name.split(" -> ", 1)

                name = os.path.basename(name.strip().rstrip("/"))
                if not name or name in [".", ".."]:
                    continue

                entries.append({
                    "name": name,
                    "is_dir": is_dir,
                    "is_link": is_link,
                    "link_target": link_target.strip(),
                    "size": size if not is_dir else None,
                    "date": date_str,
                    "raw_type": ftype
                })
            else:
                parts = line_str.split()
                if len(parts) >= 8:
                    ftype = parts[0][0] if parts[0] else '-'
                    is_dir = (ftype == 'd')
                    is_link = (ftype == 'l')
                    name = " ".join(parts[7:])
                    if is_link and " -> " in name:
                        name, _ = name.split(" -> ", 1)
                    name = os.path.basename(name.strip().rstrip("/"))
                    if name and name not in [".", ".."]:
                        entries.append({
                            "name": name,
                            "is_dir": is_dir,
                            "is_link": is_link,
                            "link_target": "",
                            "size": None,
                            "date": f"{parts[5]} {parts[6]}",
                            "raw_type": ftype
                        })

        self.app.safe_after(0, lambda: self._apply_directory(path, entries))

    def _apply_directory(self, path, entries):
        self.current_path = path
        self.path_var.set(path)
        self.files_cache = entries
        self.render_file_tree()
        # Оновлюємо статуси в сусідній панелі, оскільки цільова папка змінилась
        other = self.get_other_pane()
        if other:
            other.render_file_tree()
        self.app.update_transfer_buttons()

    def clear_file_list(self, status_msg="Смартфон відключено"):
        for row in self.tree.get_children():
            self.tree.delete(row)
        self.files_cache = []
        self.iid_to_name = {}
        try:
            self.notebook.tab(0, text="📋 Всі (0)")
            self.notebook.tab(1, text="🆕 Не скопійовані (0)")
            self.notebook.tab(2, text="🗑️ Вже в цілі (0)")
        except Exception:
            pass
        self.info_status_label.config(text=status_msg)
        self.on_selection_changed()

    def reset_filters(self):
        self.activate()
        self.filter_var.set("")
        self.type_filter_var.set("🌐 Всі типи")
        self.show_folders_var.set(True)
        self.render_file_tree()

    def on_tab_changed(self, event=None):
        self.activate()
        try:
            current_tab = self.notebook.index(self.notebook.select())
            tabs = [self.tab_all, self.tab_new, self.tab_copied]
            if 0 <= current_tab < len(tabs):
                self.tree_frame.pack(in_=tabs[current_tab], fill="both", expand=True)
            self.render_file_tree()
        except Exception:
            pass

    def render_file_tree(self):
        """Відображення файлів, сортування, фільтрація та порівняння із сусідньою панеллю"""
        filter_query = self.filter_var.get().strip().lower()
        selected_category = self.type_filter_var.get()
        allowed_exts = FILE_CATEGORIES.get(selected_category, None)
        show_folders = self.show_folders_var.get()

        try:
            current_tab = self.notebook.index(self.notebook.select())
        except Exception:
            current_tab = 0

        for row in self.tree.get_children():
            self.tree.delete(row)
        self.iid_to_name = {}

        # Мапа файлів протилежної панелі для перевірки дублікатів
        other = self.get_other_pane()
        other_map = {}
        other_ready = False
        if other and (other.mode == "pc" or self.app.current_device_id):
            other_ready = True
            other_map = {item["name"]: item for item in other.files_cache}

        same_location = False
        if other and other.mode == self.mode:
            if self.mode == "phone" and other.current_path == self.current_path:
                same_location = True
            elif self.mode == "pc" and os.path.normcase(other.current_path) == os.path.normcase(self.current_path):
                same_location = True

        total_files = 0
        already_in_other = 0
        exists_label = "✔️ Є на ПК" if (other and other.mode == "pc") else "✔️ Є там"

        for item in self.files_cache:
            name = item["name"]
            is_folder = item["is_dir"] or item["is_link"]
            size = item["size"]

            if is_folder or not other_ready or same_location:
                item["sync_status"] = "—"
            else:
                total_files += 1
                other_item = other_map.get(name)
                if not other_item:
                    item["sync_status"] = "🆕 Новий"
                else:
                    other_size = other_item.get("size")
                    if size is not None and other_size is not None and size == other_size:
                        item["sync_status"] = exists_label
                        already_in_other += 1
                    elif other_item.get("is_dir"):
                        item["sync_status"] = "⚠️ Папка"
                    else:
                        item["sync_status"] = "⚠️ Інш. розмір"

        not_copied_count = total_files - already_in_other

        # Оновлення заголовків вкладок
        try:
            tab2_title = f"🗑️ Вже на ПК ({already_in_other})" if (other and other.mode == "pc") else f"🗑️ Вже в цілі ({already_in_other})"
            self.notebook.tab(0, text=f"📋 Всі ({total_files})")
            self.notebook.tab(1, text=f"🆕 Не скопійовані ({not_copied_count})")
            self.notebook.tab(2, text=tab2_title)
        except Exception:
            pass

        def sort_key(item):
            is_folder = item["is_dir"] or item["is_link"]
            folder_weight = 0 if is_folder else 1
            if self.sort_column == "name":
                val = item["name"].lower()
            elif self.sort_column == "size":
                val = item["size"] if item["size"] is not None else -1
            elif self.sort_column == "date":
                val = item["date"]
            elif self.sort_column == "status":
                val = item.get("sync_status", "")
            else:
                val = item["name"].lower()
            return (folder_weight, val)

        sorted_files = sorted(self.files_cache, key=sort_key, reverse=self.sort_reverse)

        displayed_count = 0
        total_folders = 0
        total_bytes = 0
        row_num = 1

        for item in sorted_files:
            name = item["name"]
            is_dir = item["is_dir"]
            is_link = item["is_link"]
            size = item["size"]
            date = item["date"]
            sync_status = item.get("sync_status", "—")

            if is_dir:
                total_folders += 1
            elif size:
                total_bytes += size

            # Фільтрація за активною вкладкою
            if current_tab == 2 and (is_dir or is_link):
                continue
            if not is_dir and not is_link:
                if current_tab == 1 and sync_status == exists_label:
                    continue
                elif current_tab == 2 and sync_status != exists_label:
                    continue

            # Фільтрація за типом
            if is_dir or is_link:
                if not show_folders and allowed_exts is not None:
                    continue
            else:
                if allowed_exts is not None:
                    ext = os.path.splitext(name)[1].lower()
                    if ext not in allowed_exts:
                        continue

            # Фільтрація за текстом
            if filter_query and filter_query not in name.lower():
                continue

            icon = get_file_icon(name, is_dir, is_link)
            display_name = f"{icon}{name}"
            size_str = "<Папка>" if is_dir else ("<Посилання>" if is_link else format_size(size))

            iid = f"r_{row_num}"
            self.iid_to_name[iid] = name
            self.tree.insert("", "end", iid=iid, values=(str(row_num), display_name, size_str, date, sync_status))
            row_num += 1
            displayed_count += 1

        if total_files > 0 and not same_location and other_ready:
            pct = (already_in_other / total_files * 100)
            info_text = f"📊 В іншій панелі: {already_in_other}/{total_files} ({pct:.0f}%) | Нових: {not_copied_count} | Папок: {total_folders} | {format_size(total_bytes)}"
        else:
            info_text = f"Папок: {total_folders} | Файлів: {len(self.files_cache) - total_folders} ({format_size(total_bytes)})"

        if filter_query or selected_category != "🌐 Всі типи" or current_tab != 0:
            info_text += f" | Показано: {displayed_count}"

        self.info_status_label.config(text=info_text)
        self.on_selection_changed()

    def sort_by(self, column):
        self.activate()
        if self.sort_column == column:
            self.sort_reverse = not self.sort_reverse
        else:
            self.sort_column = column
            self.sort_reverse = False
        self.render_file_tree()

    def get_selected_items(self):
        items = []
        for iid in self.tree.selection():
            name = self.iid_to_name.get(iid)
            if name:
                item = next((i for i in self.files_cache if i["name"] == name), None)
                if item:
                    items.append(item)
        return items

    def on_selection_changed(self, event=None):
        selected_items = self.get_selected_items()
        if not selected_items:
            self.selection_label.config(text="Вибрано: 0 (0 Б)")
            return

        total_bytes = 0
        files_count = 0
        folders_count = 0
        for item in selected_items:
            if item["is_dir"]:
                folders_count += 1
            else:
                files_count += 1
                if item["size"]:
                    total_bytes += item["size"]

        parts = []
        if files_count:
            parts.append(f"{files_count} ф.")
        if folders_count:
            parts.append(f"{folders_count} пап.")
        detail_text = ", ".join(parts) if parts else f"{len(selected_items)} ел."
        size_text = f" ({format_size(total_bytes)})" if total_bytes > 0 else ""
        self.selection_label.config(text=f"Вибрано: {detail_text}{size_text}")

    def select_all_items(self):
        self.activate()
        self.tree.selection_set(self.tree.get_children())
        self.on_selection_changed()

    def select_only_new_items(self):
        self.activate()
        new_iids = []
        for iid in self.tree.get_children():
            vals = self.tree.item(iid, "values")
            if len(vals) >= 5 and vals[4] == "🆕 Новий":
                new_iids.append(iid)
        self.tree.selection_set(new_iids)
        self.on_selection_changed()
        if not new_iids:
            messagebox.showinfo("Вибір нових", "Усі відображені файли вже є в цільовій панелі!")

    def deselect_all_items(self):
        self.activate()
        self.tree.selection_remove(self.tree.selection())
        self.on_selection_changed()

    def invert_selection(self):
        self.activate()
        curr = set(self.tree.selection())
        self.tree.selection_set([i for i in self.tree.get_children() if i not in curr])
        self.on_selection_changed()

    def on_double_click(self, event=None):
        self.activate()
        selected_items = self.get_selected_items()
        if not selected_items:
            return
        item = selected_items[0]
        name = item["name"]

        if item["is_dir"] or item["is_link"]:
            if self.mode == "phone":
                new_path = f"{self.current_path.rstrip('/')}/{name}"
            else:
                new_path = os.path.join(self.current_path, name)
            self.navigate_to(new_path)
        else:
            other = self.get_other_pane()
            target_desc = "телефон" if other.mode == "phone" else "ПК"
            msg = f"Скопіювати файл «{name}» у протилежну панель ({target_desc}):\n{other.current_path}?"
            if messagebox.askyesno("Швидке копіювання", msg):
                self.app.start_transfer(move=False)

    def show_context_menu(self, event):
        self.activate()
        iid = self.tree.identify_row(event.y)
        if iid and iid not in self.tree.selection():
            self.tree.selection_set(iid)
        self.context_menu.post(event.x_root, event.y_root)

    def copy_path_to_clipboard(self):
        selected = self.get_selected_items()
        if selected:
            name = selected[0]["name"]
            full_path = f"{self.current_path.rstrip('/')}/{name}" if self.mode == "phone" else os.path.join(self.current_path, name)
        else:
            full_path = self.current_path
        self.app.root.clipboard_clear()
        self.app.root.clipboard_append(full_path)
        messagebox.showinfo("Скопійовано", f"Шлях скопійовано в буфер:\n{full_path}")

    def open_in_system(self):
        if self.mode != "pc":
            messagebox.showinfo("Інформація", "Відкриття напряму доступне для файлів та папок на ПК.")
            return
        selected = self.get_selected_items()
        target = os.path.join(self.current_path, selected[0]["name"]) if selected else self.current_path
        if os.path.exists(target):
            if hasattr(os, 'startfile'):
                os.startfile(target)
            else:
                subprocess.Popen(["explorer", target])

    def side_title(self):
        return "Ліву панель" if self.side == "left" else "Праву панель"

    # --- Обробники внутрішнього Drag & Drop ---

    def _on_drag_press(self, event):
        self._drag_data["start_x"] = event.x
        self._drag_data["start_y"] = event.y
        self._drag_data["is_dragging"] = False
        self._drag_data["press_iid"] = self.tree.identify_row(event.y)

    def _on_drag_motion(self, event):
        if not self._drag_data["is_dragging"]:
            dx = abs(event.x - self._drag_data["start_x"])
            dy = abs(event.y - self._drag_data["start_y"])
            if dx > 6 or dy > 6:
                press_iid = self._drag_data.get("press_iid")
                if press_iid and press_iid not in self.tree.selection():
                    self.tree.selection_set(press_iid)

                items = self.get_selected_items()
                if not items:
                    return

                self._drag_data["is_dragging"] = True
                self._drag_data["items"] = items

                try:
                    tip = tk.Toplevel(self.app.root)
                    tip.overrideredirect(True)
                    tip.attributes("-topmost", True)
                    try:
                        tip.attributes("-alpha", 0.90)
                    except Exception:
                        pass
                    tip_frame = tk.Frame(tip, bg="#FFF9C4", bd=1, relief="solid")
                    tip_frame.pack(fill="both", expand=True)

                    count = len(items)
                    first_name = items[0]["name"]
                    display_text = f"📄 {first_name}" if count == 1 else f"📁 {count} елемент(ів)"
                    tk.Label(
                        tip_frame,
                        text=f"Перетягування: {display_text}",
                        font=("Arial", 9, "bold"),
                        bg="#FFF9C4",
                        fg="#333",
                        padx=8,
                        pady=4
                    ).pack()
                    self._drag_data["tip_window"] = tip
                except Exception:
                    pass

                self.app.root.config(cursor="hand2")

        if self._drag_data["is_dragging"]:
            tip = self._drag_data.get("tip_window")
            if tip:
                try:
                    tip.geometry(f"+{event.x_root + 15}+{event.y_root + 12}")
                except Exception:
                    pass

    def _on_drag_release(self, event):
        if not self._drag_data["is_dragging"]:
            return

        items = self._drag_data["items"]
        self._cleanup_drag()

        if not items:
            return

        target_pane = self.app.get_pane_at(event.x_root, event.y_root)
        if not target_pane:
            return

        target_dir, target_folder_name = self.app.get_target_dir_from_pane(target_pane, event.x_root, event.y_root)

        # Якщо кинули в ту саму папку тієї ж панелі — дія не потрібна
        if target_pane == self and target_dir == self.current_path:
            return

        # Меню вибору дії (Скопіювати / Перемістити / Скасувати)
        menu = tk.Menu(self.app.root, tearoff=0)
        target_desc = f"у папку «{target_folder_name}»" if target_folder_name else f"у {target_pane.side_title()}"

        menu.add_command(
            label=f"📋 Скопіювати {target_desc}",
            command=lambda: self.app.start_transfer_items(items, src_pane=self, dst_pane=target_pane, dst_dir=target_dir, move=False)
        )
        menu.add_command(
            label=f"✂️ Перемістити {target_desc}",
            command=lambda: self.app.start_transfer_items(items, src_pane=self, dst_pane=target_pane, dst_dir=target_dir, move=True)
        )
        menu.add_separator()
        menu.add_command(label="❌ Скасувати", command=lambda: None)

        try:
            menu.tk_popup(event.x_root, event.y_root)
        except Exception:
            pass

    def _on_drag_cancel(self, event=None):
        self._cleanup_drag()

    def _cleanup_drag(self):
        self._drag_data["is_dragging"] = False
        self._drag_data["items"] = []
        tip = self._drag_data.get("tip_window")
        if tip:
            try:
                tip.destroy()
            except Exception:
                pass
            self._drag_data["tip_window"] = None
        try:
            self.app.root.config(cursor="")
        except Exception:
            pass


class ADBFileBrowserApp:
    """Головне вікно двопанельного менеджера файлів (Total Commander style)"""

    def safe_after(self, delay, callback, *args):
        if not self.running:
            return
        try:
            self.root.after(delay, callback, *args)
        except Exception:
            pass

    def __init__(self, root):
        self.root = root
        self.root.title("ADB Двопанельний Менеджер (Total Commander Style)")
        self.root.geometry("1280x760")
        self.root.minsize(980, 600)

        self.running = True
        self.current_device_id = None
        self.device_model = ""
        self.transfer_process = None
        self.abort_transfer = False
        self.is_busy = False

        self.skip_duplicates_var = tk.BooleanVar(value=True)
        self.auto_delete_var = tk.BooleanVar(value=False)

        # Посилання на Win32 Drag & Drop ресурси
        self._wndproc_ref = None
        self._old_wndproc = None

        self._init_ui()
        self.set_active_pane(self.left_pane)

        # Завантажуємо початковий вміст правої ПК-панелі одразу
        self.right_pane.refresh()

        # Підключаємо зовнішній Windows Drag & Drop через Win32 API
        self.root.after(150, self._setup_windows_drop)

        # Запускаємо фоновий моніторинг ADB
        threading.Thread(target=self.adb_monitor_loop, daemon=True).start()

    def _init_ui(self):
        # Верхній статус підключення ADB
        self.header_frame = tk.Frame(self.root, bg="#ECEFF1", bd=1, relief="solid")
        self.header_frame.pack(fill="x", padx=10, pady=(8, 4))

        self.status_icon = tk.Label(self.header_frame, text="🔄", font=("Segoe UI Emoji", 13), bg="#ECEFF1")
        self.status_icon.pack(side="left", padx=(8, 4), pady=4)

        self.status_label = tk.Label(
            self.header_frame,
            text="Перевірка підключення смартфона через ADB...",
            font=("Arial", 10, "bold"),
            bg="#ECEFF1",
            fg="#455A64"
        )
        self.status_label.pack(side="left", padx=4, pady=4)

        tk.Button(
            self.header_frame,
            text="🔄 Перепідключити ADB",
            command=self.manual_device_check,
            font=("Arial", 8),
            padx=8
        ).pack(side="right", padx=8, pady=4)

        tk.Button(
            self.header_frame,
            text="⇄ Поміняти панелі місцями",
            command=self.swap_panes,
            font=("Arial", 8, "bold"),
            padx=8
        ).pack(side="right", padx=4, pady=4)

        # Основна двопанельна область (з можливістю змінювати пропорції повзунком)
        self.paned = tk.PanedWindow(self.root, orient=tk.HORIZONTAL, sashwidth=6, sashrelief=tk.RAISED, bg="#B0BEC5")
        self.paned.pack(fill="both", expand=True, padx=10, pady=4)

        default_pc_dir = os.path.join(os.path.expanduser("~"), "Downloads")
        if not os.path.exists(default_pc_dir):
            default_pc_dir = os.path.expanduser("~")

        self.left_pane = FilePane(self.paned, self, side="left", initial_mode="phone", initial_path="/sdcard")
        self.right_pane = FilePane(self.paned, self, side="right", initial_mode="pc", initial_path=default_pc_dir)

        self.paned.add(self.left_pane.outer_frame, minsize=400, stretch="always")
        self.paned.add(self.right_pane.outer_frame, minsize=400, stretch="always")

        self.active_pane = self.left_pane

        # Нижня панель опцій, прогресу та F-кнопок
        bottom_frame = tk.Frame(self.root)
        bottom_frame.pack(fill="x", padx=10, pady=(2, 8))

        # Опції перенесення
        options_frame = tk.Frame(bottom_frame)
        options_frame.pack(fill="x", pady=(0, 4))

        tk.Checkbutton(
            options_frame,
            text="✔️ Пропускати дублікати (якщо збігається розмір у цільовій панелі)",
            variable=self.skip_duplicates_var,
            font=("Arial", 9)
        ).pack(side="left", padx=(0, 16))

        tk.Checkbutton(
            options_frame,
            text="🗑️ Автовидалення оригіналу після F5 Копіювання (якщо перевірено розмір)",
            variable=self.auto_delete_var,
            fg="#B71C1C",
            font=("Arial", 9)
        ).pack(side="left")

        # Прогрес-бари (Поточний файл + Загальний прогрес) і статус операції
        prog_frame = tk.Frame(bottom_frame)
        prog_frame.pack(fill="x", pady=(0, 6))

        cur_prog_row = tk.Frame(prog_frame)
        cur_prog_row.pack(fill="x", pady=(0, 2))
        tk.Label(cur_prog_row, text="Файл:", font=("Arial", 8, "bold"), width=7, anchor="w").pack(side="left")
        self.file_progress_bar = ttk.Progressbar(cur_prog_row, orient="horizontal", mode="determinate", maximum=100)
        self.file_progress_bar.pack(side="left", fill="x", expand=True, padx=(0, 6))
        self.file_progress_pct_label = tk.Label(cur_prog_row, text="0%", font=("Consolas", 8, "bold"), width=6, anchor="e")
        self.file_progress_pct_label.pack(side="left")

        tot_prog_row = tk.Frame(prog_frame)
        tot_prog_row.pack(fill="x", pady=(0, 3))
        tk.Label(tot_prog_row, text="Всього:", font=("Arial", 8, "bold"), width=7, anchor="w").pack(side="left")
        self.progress_bar = ttk.Progressbar(tot_prog_row, orient="horizontal", mode="determinate", maximum=100)
        self.progress_bar.pack(side="left", fill="x", expand=True, padx=(0, 6))
        self.total_progress_pct_label = tk.Label(tot_prog_row, text="0%", font=("Consolas", 8, "bold"), width=6, anchor="e")
        self.total_progress_pct_label.pack(side="left")

        status_row = tk.Frame(prog_frame)
        status_row.pack(fill="x")

        self.progress_status_label = tk.Label(
            status_row,
            text="Готово до роботи. 🖱️ Drag & Drop між панелями або з Windows | Tab — змінити панель | F5 — Копіювати | F6 — Перемістити | F8/Del — Видалити",
            font=("Arial", 9),
            fg="#444",
            anchor="w"
        )
        self.progress_status_label.pack(side="left", fill="x", expand=True)

        self.btn_abort = tk.Button(
            status_row,
            text="🛑 Скасувати",
            command=self.abort_transfer_action,
            state="disabled",
            width=12
        )
        self.btn_abort.pack(side="right", padx=(6, 0))

        # Кнопки дій (стиль Total Commander F2 - F8)
        fbar = tk.Frame(bottom_frame)
        fbar.pack(fill="x")

        self.btn_f2 = tk.Button(fbar, text="F2 Перейменувати", font=("Arial", 9, "bold"), command=self.rename_selected, pady=5)
        self.btn_f2.pack(side="left", fill="x", expand=True, padx=(0, 4))

        self.btn_f5 = tk.Button(fbar, text="F5 Копіювати ➔", font=("Arial", 9, "bold"), bg="#2E7D32", fg="white", command=lambda: self.start_transfer(move=False), pady=5)
        self.btn_f5.pack(side="left", fill="x", expand=True, padx=4)

        self.btn_f6 = tk.Button(fbar, text="F6 Перемістити ➔", font=("Arial", 9, "bold"), bg="#1565C0", fg="white", command=lambda: self.start_transfer(move=True), pady=5)
        self.btn_f6.pack(side="left", fill="x", expand=True, padx=4)

        self.btn_f7 = tk.Button(fbar, text="F7 Нова папка", font=("Arial", 9, "bold"), command=self.create_new_folder, pady=5)
        self.btn_f7.pack(side="left", fill="x", expand=True, padx=4)

        self.btn_f8 = tk.Button(fbar, text="F8 / Del Видалити", font=("Arial", 9, "bold"), fg="#C62828", command=self.confirm_delete_selected, pady=5)
        self.btn_f8.pack(side="left", fill="x", expand=True, padx=(4, 0))

        # Глобальні гарячі клавіші Total Commander
        self.root.bind("<Tab>", self._on_tab_key)
        self.root.bind("<F2>", lambda e: self.rename_selected())
        self.root.bind("<F5>", lambda e: self.start_transfer(move=False))
        self.root.bind("<F6>", lambda e: self.start_transfer(move=True))
        self.root.bind("<F7>", lambda e: self.create_new_folder())
        self.root.bind("<F8>", lambda e: self.confirm_delete_selected())
        self.root.bind("<Control-r>", lambda e: self.active_pane.refresh())
        self.root.bind("<Control-R>", lambda e: self.active_pane.refresh())

    def _on_tab_key(self, event):
        target = self.right_pane if self.active_pane == self.left_pane else self.left_pane
        self.set_active_pane(target)
        target.tree.focus_set()
        return "break"

    def set_active_pane(self, pane):
        self.active_pane = pane
        self.left_pane.set_active(pane == self.left_pane)
        self.right_pane.set_active(pane == self.right_pane)
        self.update_transfer_buttons()

    def update_transfer_buttons(self):
        src = self.active_pane
        dst = src.get_other_pane()
        arrow = "➔" if src.side == "left" else "⬅"
        src_name = "Телефон" if src.mode == "phone" else "ПК"
        dst_name = "Телефон" if dst.mode == "phone" else "ПК"

        self.btn_f5.config(text=f"F5 Копіювати ({src_name} {arrow} {dst_name})")
        self.btn_f6.config(text=f"F6 Перемістити ({src_name} {arrow} {dst_name})")

    def swap_panes(self):
        """Міняє місцями вміст та режими лівої і правої панелей"""
        l_mode, l_path = self.left_pane.mode, self.left_pane.current_path
        r_mode, r_path = self.right_pane.mode, self.right_pane.current_path

        self.left_pane.set_mode(r_mode, r_path)
        self.right_pane.set_mode(l_mode, l_path)
        self.set_active_pane(self.left_pane if self.active_pane == self.right_pane else self.right_pane)

    # --- ADB інфраструктура та моніторинг ---

    def execute_adb(self, args):
        startupinfo = None
        if os.name == 'nt':
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW

        full_cmd = ["adb"]
        if self.current_device_id and args[0] not in ["devices", "start-server", "kill-server"]:
            full_cmd.extend(["-s", self.current_device_id])
        full_cmd.extend(args)

        try:
            res = subprocess.run(
                full_cmd,
                capture_output=True,
                text=True,
                encoding='utf-8',
                errors='replace',
                startupinfo=startupinfo
            )
            return res.returncode, res.stdout, res.stderr
        except FileNotFoundError:
            return -1, "", "ADB не знайдено в системі PATH"
        except Exception as e:
            return -1, "", str(e)

    def manual_device_check(self):
        threading.Thread(target=self._check_device_state, daemon=True).start()

    def adb_monitor_loop(self):
        while self.running:
            self._check_device_state()
            time.sleep(3)

    def _check_device_state(self):
        ret, stdout, stderr = self.execute_adb(["devices"])

        if ret == -1:
            self.safe_after(0, self.update_device_ui, "error", "⚠️ ADB не знайдено в системі! Встановіть Android SDK Platform-Tools.")
            return

        lines = [l.strip() for l in stdout.strip().split("\n")[1:] if l.strip()]
        connected_devices = []
        for line in lines:
            parts = line.split()
            if len(parts) >= 2:
                connected_devices.append((parts[0], parts[1]))

        if not connected_devices:
            was_connected = self.current_device_id is not None
            self.current_device_id = None
            self.device_model = ""
            self.safe_after(0, self.update_device_ui, "disconnected", "🔴 Смартфон не підключено (увімкніть USB-налагодження)")
            if was_connected:
                self.safe_after(0, self._on_phone_disconnected)
            return

        active = [d for d in connected_devices if d[1] == "device"]
        unauthorized = [d for d in connected_devices if d[1] == "unauthorized"]

        if active:
            dev_id = active[0][0]
            if dev_id != self.current_device_id:
                self.current_device_id = dev_id
                _, model_out, _ = self.execute_adb(["shell", "getprop", "ro.product.model"])
                _, brand_out, _ = self.execute_adb(["shell", "getprop", "ro.product.brand"])
                model_name = f"{brand_out.strip()} {model_out.strip()}".strip() or dev_id
                self.device_model = model_name
                self.safe_after(0, self.update_device_ui, "connected", f"🟢 Підключено: {model_name} (ID: {dev_id})")
                self.safe_after(0, self._on_phone_connected)
            else:
                self.safe_after(0, self.update_device_ui, "connected", f"🟢 Підключено: {self.device_model} (ID: {dev_id})")
        elif unauthorized:
            dev_id = unauthorized[0][0]
            self.current_device_id = None
            self.safe_after(0, self.update_device_ui, "unauthorized", f"🟡 Телефон {dev_id} не авторизовано. Натисніть «Дозволити» на екрані телефона!")
        else:
            self.current_device_id = None
            self.safe_after(0, self.update_device_ui, "other", f"⚪ Пристрій у стані: {connected_devices[0][1]}")

    def _on_phone_connected(self):
        for pane in (self.left_pane, self.right_pane):
            if pane.mode == "phone":
                pane.refresh()

    def _on_phone_disconnected(self):
        for pane in (self.left_pane, self.right_pane):
            if pane.mode == "phone":
                pane.clear_file_list("Смартфон відключено")
            else:
                pane.render_file_tree()

    def update_device_ui(self, state, text):
        colors = {
            "connected": ("#1B5E20", "#E8F5E9", "🟢"),
            "disconnected": ("#B71C1C", "#FFEBEE", "🔴"),
            "unauthorized": ("#E65100", "#FFF3E0", "🟡"),
            "error": ("#B71C1C", "#FFCDD2", "⚠️"),
            "other": ("#37474F", "#ECEFF1", "⚪")
        }
        fg, bg, icon = colors.get(state, ("#37474F", "#ECEFF1", "⚪"))
        self.header_frame.config(bg=bg)
        self.status_icon.config(text=icon, bg=bg)
        self.status_label.config(text=text, fg=fg, bg=bg)

    # --- Блокування / розблокування дій під час передачі ---

    def _set_busy(self, busy):
        self.is_busy = busy
        state_normal = "disabled" if busy else "normal"
        self.btn_f2.config(state=state_normal)
        self.btn_f5.config(state=state_normal)
        self.btn_f6.config(state=state_normal)
        self.btn_f7.config(state=state_normal)
        self.btn_f8.config(state=state_normal)
        self.btn_abort.config(state="normal" if busy else "disabled")

    def abort_transfer_action(self):
        self.abort_transfer = True
        if self.transfer_process and self.transfer_process.poll() is None:
            try:
                self.transfer_process.terminate()
            except Exception:
                pass
        self.progress_status_label.config(text="Зупинка операції користувачем...")

    # --- Копіювання (F5) та Переміщення (F6) між панелями ---

    def start_transfer(self, move=False):
        if self.is_busy:
            return

        src_pane = self.active_pane
        dst_pane = src_pane.get_other_pane()

        selected = src_pane.get_selected_items()
        if not selected:
            messagebox.showwarning("Виберіть файли", "Будь ласка, виберіть файли або папки в активній панелі!")
            return

        if (src_pane.mode == "phone" or dst_pane.mode == "phone") and not self.current_device_id:
            messagebox.showwarning("Пристрій не підключено", "Підключіть смартфон через ADB для операцій з телефоном!")
            return

        # Перевірка на однакову папку
        if src_pane.mode == dst_pane.mode:
            if src_pane.mode == "phone" and src_pane.current_path == dst_pane.current_path:
                messagebox.showwarning("Однакова папка", "Вихідна та цільова папки на телефоні однакові!")
                return
            if src_pane.mode == "pc" and os.path.normcase(src_pane.current_path) == os.path.normcase(dst_pane.current_path):
                messagebox.showwarning("Однакова папка", "Вихідна та цільова папки на ПК однакові!")
                return

        if dst_pane.mode == "pc" and not os.path.exists(dst_pane.current_path):
            try:
                os.makedirs(dst_pane.current_path, exist_ok=True)
            except Exception as e:
                messagebox.showerror("Помилка", f"Не вдалося створити папку на ПК:\n{e}")
                return

        delete_source = move or self.auto_delete_var.get()

        if move:
            total_size = sum(i["size"] for i in selected if i["size"])
            size_info = f" ({format_size(total_size)})" if total_size > 0 else ""
            src_lbl = "Телефон" if src_pane.mode == "phone" else "ПК"
            dst_lbl = "Телефон" if dst_pane.mode == "phone" else "ПК"
            confirm = messagebox.askyesno(
                "Підтвердження переміщення (F6)",
                f"Перемістити {len(selected)} елемент(ів){size_info}\n"
                f"з [{src_lbl}]: {src_pane.current_path}\n"
                f"у [{dst_lbl}]: {dst_pane.current_path}?"
            )
            if not confirm:
                return

        skip_dupes = self.skip_duplicates_var.get()

        self._set_busy(True)
        self.abort_transfer = False

        threading.Thread(
            target=self._transfer_worker,
            args=(selected, src_pane, dst_pane, move, delete_source, skip_dupes),
            daemon=True
        ).start()

    def start_transfer_items(self, items, src_pane, dst_pane, dst_dir=None, move=False):
        """Запуск копіювання або переміщення списку items у цільову папку (Drag & Drop)"""
        if self.is_busy:
            messagebox.showinfo("Зайнято", "Зачекайте завершення поточної операції передачі!")
            return

        if not items:
            return

        if (src_pane.mode == "phone" or dst_pane.mode == "phone") and not self.current_device_id:
            messagebox.showwarning("Пристрій не підключено", "Підключіть смартфон через ADB для операцій з телефоном!")
            return

        target_dir = dst_dir if dst_dir else dst_pane.current_path

        # Перевірка на однакову папку
        if src_pane.mode == dst_pane.mode:
            if src_pane.mode == "phone" and src_pane.current_path == target_dir:
                messagebox.showwarning("Однакова папка", "Вихідна та цільова папки на телефоні однакові!")
                return
            if src_pane.mode == "pc" and os.path.normcase(src_pane.current_path) == os.path.normcase(target_dir):
                messagebox.showwarning("Однакова папка", "Вихідна та цільова папки на ПК однакові!")
                return

        if dst_pane.mode == "pc" and not os.path.exists(target_dir):
            try:
                os.makedirs(target_dir, exist_ok=True)
            except Exception as e:
                messagebox.showerror("Помилка", f"Не вдалося створити папку на ПК:\n{e}")
                return

        delete_source = move
        skip_dupes = self.skip_duplicates_var.get()

        self._set_busy(True)
        self.abort_transfer = False

        threading.Thread(
            target=self._transfer_worker,
            args=(items, src_pane, dst_pane, move, delete_source, skip_dupes, target_dir),
            daemon=True
        ).start()

    def get_pane_at(self, root_x, root_y):
        """Повертає панель (left_pane або right_pane), над якою знаходяться екранні координати"""
        try:
            left_box = (
                self.left_pane.outer_frame.winfo_rootx(),
                self.left_pane.outer_frame.winfo_rooty(),
                self.left_pane.outer_frame.winfo_width(),
                self.left_pane.outer_frame.winfo_height(),
            )
            if (left_box[0] <= root_x <= left_box[0] + left_box[2] and
                left_box[1] <= root_y <= left_box[1] + left_box[3]):
                return self.left_pane

            right_box = (
                self.right_pane.outer_frame.winfo_rootx(),
                self.right_pane.outer_frame.winfo_rooty(),
                self.right_pane.outer_frame.winfo_width(),
                self.right_pane.outer_frame.winfo_height(),
            )
            if (right_box[0] <= root_x <= right_box[0] + right_box[2] and
                right_box[1] <= root_y <= right_box[1] + right_box[3]):
                return self.right_pane
        except Exception:
            pass
        return None

    def get_target_dir_from_pane(self, pane, root_x, root_y):
        """Повертає (target_dir, folder_name або None) для панелі та екранних координат"""
        default_dir = pane.current_path
        try:
            tree = pane.tree
            t_x = root_x - tree.winfo_rootx()
            t_y = root_y - tree.winfo_rooty()
            if 0 <= t_x <= tree.winfo_width() and 0 <= t_y <= tree.winfo_height():
                iid = tree.identify_row(t_y)
                if iid:
                    name = pane.iid_to_name.get(iid)
                    item = next((i for i in pane.files_cache if i["name"] == name), None)
                    if item and item["is_dir"]:
                        if pane.mode == "phone":
                            sub_dir = f"{pane.current_path.rstrip('/')}/{name}"
                        else:
                            sub_dir = os.path.join(pane.current_path, name)
                        return sub_dir, name
        except Exception:
            pass
        return default_dir, None

    def _setup_windows_drop(self):
        """Реєстрація вікна як Drop Target для перетягування файлів з Windows Explorer"""
        try:
            self.root.update_idletasks()
            hwnd = self.root.winfo_id()
            shell32.DragAcceptFiles(hwnd, True)

            def py_wndproc(h, msg, wp, lp):
                if msg == WM_DROPFILES:
                    try:
                        hDrop = ctypes.c_void_p(wp)
                        count = shell32.DragQueryFileW(hDrop, 0xFFFFFFFF, None, 0)
                        dropped_paths = []
                        for i in range(count):
                            size = shell32.DragQueryFileW(hDrop, i, None, 0)
                            buf = ctypes.create_unicode_buffer(size + 1)
                            shell32.DragQueryFileW(hDrop, i, buf, size + 1)
                            dropped_paths.append(buf.value)

                        pt = wintypes.POINT()
                        shell32.DragQueryPoint(hDrop, ctypes.byref(pt))
                        shell32.DragFinish(hDrop)

                        self.safe_after(0, self.handle_external_dropped_files, dropped_paths, pt.x, pt.y)
                        return 0
                    except Exception:
                        pass
                return CallWindowProc(self._old_wndproc, h, msg, wp, lp)

            self._wndproc_ref = WNDPROC(py_wndproc)
            self._old_wndproc = SetWindowLongPtr(hwnd, GWLP_WNDPROC, ctypes.cast(self._wndproc_ref, ctypes.c_void_p))
        except Exception:
            pass

    def handle_external_dropped_files(self, dropped_paths, client_x, client_y):
        """Обробка файлів, перетягнутих з Провідника Windows у вікно програми"""
        if self.is_busy:
            messagebox.showinfo("Зайнято", "Зачекайте завершення поточної операції передачі!")
            return

        if not dropped_paths:
            return

        root_x = self.root.winfo_rootx() + client_x
        root_y = self.root.winfo_rooty() + client_y

        target_pane = self.get_pane_at(root_x, root_y)
        if not target_pane:
            target_pane = self.active_pane

        target_dir, target_folder_name = self.get_target_dir_from_pane(target_pane, root_x, root_y)

        if target_pane.mode == "phone" and not self.current_device_id:
            messagebox.showwarning("Телефон не підключено", "Підключіть смартфон з увімкненим USB-налагодженням для завантаження файлів!")
            return

        items = []
        for p in dropped_paths:
            if not os.path.exists(p):
                continue
            is_dir = os.path.isdir(p)
            name = os.path.basename(p.rstrip(r"\/"))
            sz = get_local_path_size(p) if is_dir else os.path.getsize(p)
            items.append({
                "name": name,
                "is_dir": is_dir,
                "size": sz,
                "date": "",
                "local_full_path": p
            })

        if not items:
            return

        total_size = sum(i["size"] for i in items if i["size"])
        size_info = f" ({format_size(total_size)})" if total_size > 0 else ""
        dst_lbl = "папку на телефоні" if target_pane.mode == "phone" else "папку на ПК"

        confirm = messagebox.askyesno(
            "Drag & Drop завантаження",
            f"Завантажити {len(items)} елемент(ів){size_info}\n"
            f"у {dst_lbl}:\n{target_dir}?"
        )
        if not confirm:
            return

        dummy_src_pane = DummyLocalPane(os.path.dirname(dropped_paths[0]))

        self._set_busy(True)
        self.abort_transfer = False
        skip_dupes = self.skip_duplicates_var.get()

        threading.Thread(
            target=self._transfer_worker,
            args=(items, dummy_src_pane, target_pane, False, False, skip_dupes, target_dir),
            daemon=True
        ).start()

    def _verify_phone_item(self, remote_path, is_dir, expected_size):
        """Перевірка наявності та розміру файлу/папки на телефоні після adb push"""
        safe_remote = remote_path.replace("'", "'\\''")
        if is_dir:
            ret, out, _ = self.execute_adb(["shell", f"test -d '{safe_remote}' && echo OK"])
            return ret == 0 and "OK" in out
        ret, out, _ = self.execute_adb(["shell", f"ls -l '{safe_remote}'"])
        if ret != 0:
            return False
        for line in out.splitlines():
            m = LS_REGEX.match(line.strip())
            if m:
                remote_size = int(m.group(5))
                return expected_size is None or expected_size == 0 or remote_size == expected_size
        return False

    def _get_phone_path_size(self, remote_path, is_dir):
        """Отримує розмір файлу або папки на телефоні через ADB"""
        safe_remote = remote_path.replace("'", "'\\''")
        if is_dir:
            ret, out, _ = self.execute_adb(["shell", f"du -s -k '{safe_remote}'"])
            if ret == 0 and out.strip():
                first_tok = out.strip().split()[0]
                if first_tok.isdigit():
                    return int(first_tok) * 1024
            return 0

        ret, out, _ = self.execute_adb(["shell", f"stat -c %s '{safe_remote}' 2>/dev/null || ls -l '{safe_remote}'"])
        if ret == 0 and out.strip():
            first_line = out.strip().splitlines()[0].strip()
            if first_line.isdigit():
                return int(first_line)
            m = LS_REGEX.match(first_line)
            if m:
                return int(m.group(5))
        return 0

    def _is_subpath_conflict(self, mode, src_path, dst_dir):
        """Перевіряє, чи не намагається користувач скопіювати/перемістити папку саму в себе"""
        if mode == "phone":
            s_norm = src_path.rstrip("/") or "/"
            d_norm = dst_dir.rstrip("/") or "/"
            return d_norm == s_norm or d_norm.startswith(s_norm + "/")
        s_abs = os.path.normcase(os.path.abspath(src_path))
        d_abs = os.path.normcase(os.path.abspath(dst_dir))
        return d_abs == s_abs or d_abs.startswith(s_abs + os.sep)

    def _collect_pc_folder_tree(self, local_dir):
        """Повертає (rel_dirs, rel_files) з відносними POSIX-шляхами для локальної папки"""
        rel_dirs = [""]
        rel_files = []
        for root_d, dirs, files in os.walk(local_dir):
            rel_root = os.path.relpath(root_d, local_dir)
            posix_root = "" if rel_root == "." else rel_root.replace("\\", "/")
            if posix_root and posix_root not in rel_dirs:
                rel_dirs.append(posix_root)
            for d in dirs:
                sub_rel = f"{posix_root}/{d}" if posix_root else d
                if sub_rel not in rel_dirs:
                    rel_dirs.append(sub_rel)
            for fn in files:
                full_f = os.path.join(root_d, fn)
                rel_f = f"{posix_root}/{fn}" if posix_root else fn
                try:
                    f_size = os.path.getsize(full_f)
                except OSError:
                    f_size = 0
                rel_files.append((rel_f, f_size))
        return rel_dirs, rel_files

    def _collect_phone_folder_tree(self, remote_dir):
        """Повертає (rel_dirs, rel_files) з відносними POSIX-шляхами для папки на телефоні"""
        base = remote_dir.rstrip("/")
        prefix = f"{base}/"
        safe_base = base.replace("'", "'\\''")

        ret, out, _ = self.execute_adb(["shell", f"find '{safe_base}/' -exec stat -c '%F|%s|%n' {{}} +"])
        rel_dirs = [""]
        rel_files = []

        if ret == 0 and out.strip():
            seen_dirs = {""}
            for line in out.splitlines():
                line = line.rstrip("\r")
                parts = line.split("|", 2)
                if len(parts) != 3:
                    continue
                ftype, size_str, full_path = parts
                if full_path == base or full_path == prefix:
                    rel_path = ""
                elif full_path.startswith(prefix):
                    rel_path = full_path[len(prefix):].rstrip("/")
                else:
                    continue
                if "directory" in ftype:
                    if rel_path and rel_path not in seen_dirs:
                        seen_dirs.add(rel_path)
                        rel_dirs.append(rel_path)
                elif "regular" in ftype:
                    rel_files.append((rel_path, int(size_str) if size_str.isdigit() else 0))
            return rel_dirs, rel_files

        # Резервний парсер через ls -laR, якщо stat недоступний
        ret2, out2, _ = self.execute_adb(["shell", f"ls -laR '{safe_base}/'"])
        if ret2 != 0 and not out2.strip():
            return rel_dirs, rel_files

        seen_dirs = {""}
        cur_rel_dir = ""
        for raw_line in out2.splitlines():
            line = raw_line.rstrip("\r")
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.endswith(":") and (stripped.startswith(base) or stripped.startswith("./")):
                header_path = stripped[:-1].rstrip("/")
                if header_path == base or header_path == prefix.rstrip("/"):
                    cur_rel_dir = ""
                elif header_path.startswith(prefix):
                    cur_rel_dir = header_path[len(prefix):].rstrip("/")
                if cur_rel_dir and cur_rel_dir not in seen_dirs:
                    seen_dirs.add(cur_rel_dir)
                    rel_dirs.append(cur_rel_dir)
                continue
            if stripped.startswith("total "):
                continue
            m = LS_REGEX.match(stripped)
            if not m:
                continue
            ftype_char = m.group(1)
            fsize = int(m.group(5))
            fname = m.group(8)
            if fname in (".", ".."):
                continue
            if ftype_char == "l" and " -> " in fname:
                fname = fname.split(" -> ", 1)[0]
            rel_item = f"{cur_rel_dir}/{fname}" if cur_rel_dir else fname
            if ftype_char == "d":
                if rel_item not in seen_dirs:
                    seen_dirs.add(rel_item)
                    rel_dirs.append(rel_item)
            elif ftype_char == "-":
                rel_files.append((rel_item, fsize))

        return rel_dirs, rel_files

    def _ensure_phone_dirs(self, remote_dirs):
        """Створює список папок на телефоні пакетами через mkdir -p"""
        unique_dirs = []
        seen = set()
        for d in remote_dirs:
            norm_d = d.rstrip("/") or "/"
            if norm_d not in seen:
                seen.add(norm_d)
                unique_dirs.append(norm_d)

        chunk = []
        chunk_len = 0
        for d in unique_dirs:
            quoted = "'" + d.replace("'", "'\\''") + "'"
            if chunk and chunk_len + len(quoted) + 1 > 5000:
                ret, out, err = self.execute_adb(["shell", "mkdir -p " + " ".join(chunk)])
                if ret != 0:
                    raise RuntimeError(err.strip() or out.strip() or "Не вдалося створити папки на телефоні")
                chunk = []
                chunk_len = 0
            chunk.append(quoted)
            chunk_len += len(quoted) + 1

        if chunk:
            ret, out, err = self.execute_adb(["shell", "mkdir -p " + " ".join(chunk)])
            if ret != 0:
                raise RuntimeError(err.strip() or out.strip() or "Не вдалося створити папки на телефоні")

    def _run_adb_popen_with_progress(self, cmd, poll_size_fn, progress_cb, poll_interval=0.12, cwd=None, startupinfo=None):
        """Запускає ADB-команду з безпечним UTF-8 читанням потоків та відстеженням прогресу"""
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=cwd,
            startupinfo=startupinfo,
            text=True,
            encoding='utf-8',
            errors='replace'
        )
        self.transfer_process = proc
        out_parts = []
        err_parts = []

        def drain(stream, buf):
            try:
                buf.append(stream.read())
            except Exception:
                pass

        t_out = threading.Thread(target=drain, args=(proc.stdout, out_parts), daemon=True)
        t_err = threading.Thread(target=drain, args=(proc.stderr, err_parts), daemon=True)
        t_out.start()
        t_err.start()

        try:
            while proc.poll() is None:
                if self.abort_transfer:
                    proc.terminate()
                    break
                if poll_size_fn is not None:
                    progress_cb(poll_size_fn())
                time.sleep(poll_interval)
            proc.wait()
            t_out.join(timeout=2.0)
            t_err.join(timeout=2.0)
            return proc.returncode, "".join(out_parts), "".join(err_parts)
        finally:
            self.transfer_process = None

    def _pull_phone_file(self, remote_file, local_file, progress_cb, startupinfo):
        """Завантажує один файл з телефону через тимчасове ASCII-ім'я у цільовій папці (обхід багу кирилиці в adb.exe)"""
        parent_dir = os.path.dirname(os.path.abspath(local_file))
        os.makedirs(parent_dir, exist_ok=True)
        tmp_name = f".__adb_pull_{os.getpid()}_{int(time.monotonic() * 1000000) % 100000000}.part"
        tmp_full = os.path.join(parent_dir, tmp_name)

        max_seen = 0

        def poll_tmp():
            nonlocal max_seen
            try:
                sz = os.path.getsize(tmp_full) if os.path.exists(tmp_full) else 0
            except OSError:
                sz = 0
            if sz > max_seen:
                max_seen = sz
            return max_seen

        cmd = ["adb", "-s", self.current_device_id, "pull", remote_file, tmp_name]
        try:
            rc, out, err = self._run_adb_popen_with_progress(
                cmd, poll_tmp, progress_cb, poll_interval=0.12, cwd=parent_dir, startupinfo=startupinfo
            )
            if self.abort_transfer:
                raise InterruptedError("Операцію скасовано")
            if rc == 0 and os.path.exists(tmp_full):
                os.replace(tmp_full, local_file)
                return True, ""
            return False, err.strip() or out.strip() or "Помилка adb pull"
        finally:
            if os.path.exists(tmp_full):
                try:
                    os.remove(tmp_full)
                except OSError:
                    pass

    def _push_pc_file(self, local_file, remote_file, progress_cb, startupinfo):
        """Надсилає один файл із ПК на телефон точним шляхом призначення"""
        max_seen = 0

        def poll_remote():
            nonlocal max_seen
            polled = self._get_phone_path_size(remote_file, is_dir=False)
            if polled > max_seen:
                max_seen = polled
            return max_seen

        cmd = ["adb", "-s", self.current_device_id, "push", local_file, remote_file]
        rc, out, err = self._run_adb_popen_with_progress(
            cmd, poll_remote, progress_cb, poll_interval=0.25, startupinfo=startupinfo
        )
        if self.abort_transfer:
            raise InterruptedError("Операцію скасовано")
        if rc == 0:
            return True, ""
        return False, err.strip() or out.strip() or "Помилка adb push"

    def _copy_pc_file_chunked(self, src_file, dst_file, progress_cb):
        """Копіювання файлу на ПК блоками по 1 МБ для точного прогресу, швидкості та скасування"""
        chunk_size = 1024 * 1024
        with open(src_file, 'rb') as fsrc, open(dst_file, 'wb') as fdst:
            while True:
                if self.abort_transfer:
                    fdst.close()
                    try:
                        os.remove(dst_file)
                    except OSError:
                        pass
                    raise InterruptedError("Операцію скасовано")
                chunk = fsrc.read(chunk_size)
                if not chunk:
                    break
                fdst.write(chunk)
                progress_cb(len(chunk))
        try:
            shutil.copystat(src_file, dst_file)
        except OSError:
            pass

    def _copy_pc_item_with_progress(self, src_path, dst_path, is_folder, progress_cb):
        """Рекурсивне копіювання файлу або папки на ПК з підрахунком переданих байтів"""
        if not is_folder:
            self._copy_pc_file_chunked(src_path, dst_path, progress_cb)
            return

        os.makedirs(dst_path, exist_ok=True)
        for root_d, dirs, files in os.walk(src_path):
            rel = os.path.relpath(root_d, src_path)
            target_root = dst_path if rel == "." else os.path.join(dst_path, rel)
            os.makedirs(target_root, exist_ok=True)
            for d in dirs:
                os.makedirs(os.path.join(target_root, d), exist_ok=True)
            for fn in files:
                if self.abort_transfer:
                    raise InterruptedError("Операцію скасовано")
                self._copy_pc_file_chunked(
                    os.path.join(root_d, fn),
                    os.path.join(target_root, fn),
                    progress_cb
                )

    def _update_transfer_progress_ui(self, file_pct, total_pct, status_text):
        self.file_progress_bar.configure(mode="determinate", value=file_pct)
        self.file_progress_pct_label.config(text=f"{file_pct:.0f}%")
        self.progress_bar.configure(mode="determinate", value=total_pct)
        self.total_progress_pct_label.config(text=f"{total_pct:.0f}%")
        self.progress_status_label.config(text=status_text)

    def _transfer_worker(self, items, src_pane, dst_pane, is_move, delete_source, skip_dupes=False, dst_target_dir=None):
        src_mode = src_pane.mode
        dst_mode = dst_pane.mode
        src_dir = src_pane.current_path
        dst_dir = dst_target_dir if dst_target_dir else dst_pane.current_path

        total_selected = len(items)
        success_count = 0
        skipped_count = 0
        deleted_count = 0

        dst_cache_map = {i["name"]: i for i in dst_pane.files_cache} if (not dst_target_dir or dst_target_dir == dst_pane.current_path) else {}

        startupinfo = None
        if os.name == 'nt':
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW

        self.safe_after(0, lambda: self._update_transfer_progress_ui(0, 0, "Підрахунок розміру вибраних файлів..."))

        # 1. Формуємо чергу (відсіюємо дублікати, розгортаємо папки) та визначаємо обсяг у байтах
        work_queue = []
        for item in items:
            if self.abort_transfer:
                break

            name = item["name"]
            is_folder = item["is_dir"]
            src_path = item.get("local_full_path") or (f"{src_dir.rstrip('/')}/{name}" if src_mode == "phone" else os.path.join(src_dir, name))
            dst_path = f"{dst_dir.rstrip('/')}/{name}" if dst_mode == "phone" else os.path.join(dst_dir, name)

            if is_folder and src_mode == dst_mode and self._is_subpath_conflict(src_mode, src_path, dst_dir):
                work_queue.append({
                    "item": item,
                    "name": name,
                    "is_folder": True,
                    "src_path": src_path,
                    "dst_path": dst_path,
                    "expected_size": 0,
                    "error": "Неможливо скопіювати або перемістити папку саму в себе!"
                })
                continue

            item_size = item["size"] or 0
            if skip_dupes and not is_folder:
                is_dupe = False
                if dst_mode == "pc" and os.path.exists(dst_path):
                    try:
                        if os.path.getsize(dst_path) == item_size:
                            is_dupe = True
                    except OSError:
                        pass
                elif dst_mode == "phone":
                    existing = dst_cache_map.get(name)
                    if existing and not existing["is_dir"] and existing["size"] == item_size:
                        is_dupe = True

                if is_dupe:
                    skipped_count += 1
                    continue

            rel_dirs = []
            rel_files = []
            if is_folder and not (src_mode == "phone" and dst_mode == "phone"):
                if src_mode == "pc":
                    rel_dirs, all_files = self._collect_pc_folder_tree(src_path)
                else:
                    rel_dirs, all_files = self._collect_phone_folder_tree(src_path)

                if skip_dupes and all_files:
                    existing_phone_files = {}
                    if dst_mode == "phone":
                        existing_dst = dst_cache_map.get(name)
                        if (existing_dst and existing_dst["is_dir"]) or self._verify_phone_item(dst_path, True, 0):
                            _, dst_existing_list = self._collect_phone_folder_tree(dst_path)
                            existing_phone_files = dict(dst_existing_list)

                    filtered_files = []
                    for rel_f, f_sz in all_files:
                        is_nested_dupe = False
                        if dst_mode == "pc":
                            l_dst = os.path.join(dst_path, *rel_f.split("/"))
                            try:
                                if os.path.isfile(l_dst) and os.path.getsize(l_dst) == f_sz:
                                    is_nested_dupe = True
                            except OSError:
                                pass
                        elif dst_mode == "phone":
                            if rel_f in existing_phone_files and existing_phone_files[rel_f] == f_sz:
                                is_nested_dupe = True
                        if not is_nested_dupe:
                            filtered_files.append((rel_f, f_sz))
                    rel_files = filtered_files
                else:
                    rel_files = all_files

                # Якщо всі вкладені файли і папки вже є в цілі та пропуск дублікатів увімкнений
                if skip_dupes and all_files and not rel_files:
                    dst_dir_exists = os.path.isdir(dst_path) if dst_mode == "pc" else (
                        (dst_cache_map.get(name, {}).get("is_dir")) or self._verify_phone_item(dst_path, True, 0)
                    )
                    if dst_dir_exists:
                        skipped_count += 1
                        continue

                expected_size = sum(sz for _, sz in rel_files)
            elif is_folder:
                expected_size = self._get_phone_path_size(src_path, is_dir=True)
            else:
                if src_mode == "pc":
                    expected_size = get_local_path_size(src_path)
                else:
                    expected_size = item["size"] if item["size"] is not None else self._get_phone_path_size(src_path, is_dir=False)

            work_queue.append({
                "item": item,
                "name": name,
                "is_folder": is_folder,
                "src_path": src_path,
                "dst_path": dst_path,
                "rel_dirs": rel_dirs,
                "rel_files": rel_files,
                "expected_size": max(0, expected_size or 0)
            })

        total_to_transfer = len(work_queue)
        total_batch_bytes = sum(entry["expected_size"] for entry in work_queue)
        tracker = SpeedTracker(total_batch_bytes)
        bytes_done_before = 0
        last_ui_time = 0.0
        action_word = "Переміщення" if is_move else "Копіювання"

        def report_progress(idx, name, cur_bytes, expected_size, force=False):
            nonlocal last_ui_time
            now = time.monotonic()
            if not force and (now - last_ui_time) < 0.09:
                return
            last_ui_time = now

            cur_clamped = min(cur_bytes, expected_size) if expected_size > 0 else cur_bytes
            total_done = bytes_done_before + cur_clamped

            if expected_size > 0:
                file_pct = min(100.0, (cur_clamped / expected_size) * 100.0)
            else:
                file_pct = 100.0 if force else 0.0

            if total_batch_bytes > 0:
                total_pct = min(100.0, (total_done / total_batch_bytes) * 100.0)
            else:
                total_pct = min(100.0, ((idx - 1 + file_pct / 100.0) / max(1, total_to_transfer)) * 100.0)

            speed_bps, eta_sec = tracker.update(total_done)
            speed_str = f"{format_size(speed_bps)}/с" if speed_bps > 0 else "розрахунок..."
            eta_str = f"~{format_eta(eta_sec)}" if eta_sec is not None else "--:--"

            size_part = f"({format_size(cur_clamped)} / {format_size(expected_size)})" if expected_size > 0 else ""
            total_part = f" | Всього: {format_size(total_done)} / {format_size(total_batch_bytes)}" if total_batch_bytes > 0 else ""
            status_txt = f"[{idx}/{total_to_transfer}] {action_word}: {name} {size_part}{total_part} | 🚀 {speed_str} | ⏱️ Залишилось: {eta_str}"

            self.safe_after(0, self._update_transfer_progress_ui, file_pct, total_pct, status_txt)

        # 2. Виконуємо перенесення кожного елемента
        for idx, entry in enumerate(work_queue, start=1):
            if self.abort_transfer:
                break

            item = entry["item"]
            name = entry["name"]
            is_folder = entry["is_folder"]
            src_path = entry["src_path"]
            dst_path = entry["dst_path"]
            expected_size = entry["expected_size"]

            if entry.get("error"):
                self.safe_after(0, lambda n=name, e=entry["error"]: messagebox.showerror("Помилка операції", f"Не вдалося обробити «{n}»:\n{e}"))
                continue

            report_progress(idx, name, 0, expected_size, force=True)

            ok = False
            err_msg = ""
            bytes_done_in_entry = 0

            try:
                # Сценарій 1: Телефон -> ПК (adb pull)
                if src_mode == "phone" and dst_mode == "pc":
                    if not is_folder:
                        ok, err_msg = self._pull_phone_file(
                            src_path, dst_path,
                            lambda cur: report_progress(idx, name, cur, expected_size),
                            startupinfo
                        )
                        if ok and delete_source:
                            verified = os.path.exists(dst_path) and (expected_size == 0 or os.path.getsize(dst_path) == expected_size)
                            if verified:
                                safe_src = src_path.replace("'", "'\\''")
                                r_del, _, _ = self.execute_adb(["shell", f"rm -f '{safe_src}'"])
                                if r_del == 0:
                                    deleted_count += 1
                    else:
                        os.makedirs(dst_path, exist_ok=True)
                        for rel_d in entry["rel_dirs"]:
                            if rel_d:
                                os.makedirs(os.path.join(dst_path, *rel_d.split("/")), exist_ok=True)

                        all_verified = True
                        for rel_f, f_size in entry["rel_files"]:
                            if self.abort_transfer:
                                raise InterruptedError("Операцію скасовано")
                            r_file = f"{src_path.rstrip('/')}/{rel_f}"
                            l_file = os.path.join(dst_path, *rel_f.split("/"))
                            disp_name = f"{name}/{rel_f}"
                            report_progress(idx, disp_name, 0, f_size, force=True)
                            f_ok, f_err = self._pull_phone_file(
                                r_file, l_file,
                                lambda cur, dn=disp_name, fs=f_size: report_progress(idx, dn, cur, fs),
                                startupinfo
                            )
                            if not f_ok:
                                all_verified = False
                                raise RuntimeError(f"Файл «{rel_f}»: {f_err}")
                            report_progress(idx, disp_name, f_size, f_size, force=True)
                            bytes_done_before += f_size
                            bytes_done_in_entry += f_size
                            if not (os.path.exists(l_file) and (f_size == 0 or os.path.getsize(l_file) == f_size)):
                                all_verified = False

                        ok = True
                        if delete_source and all_verified and os.path.isdir(dst_path):
                            safe_src = src_path.replace("'", "'\\''")
                            r_del, _, _ = self.execute_adb(["shell", f"rm -rf '{safe_src}'"])
                            if r_del == 0:
                                deleted_count += 1

                # Сценарій 2: ПК -> Телефон (adb push)
                elif src_mode == "pc" and dst_mode == "phone":
                    if not is_folder:
                        ok, err_msg = self._push_pc_file(
                            src_path, dst_path,
                            lambda cur: report_progress(idx, name, cur, expected_size),
                            startupinfo
                        )
                        if ok and delete_source and self._verify_phone_item(dst_path, False, expected_size):
                            try:
                                os.remove(src_path)
                                deleted_count += 1
                            except Exception:
                                pass
                    else:
                        remote_dirs = [dst_path] + [f"{dst_path.rstrip('/')}/{rd}" for rd in entry["rel_dirs"] if rd]
                        self._ensure_phone_dirs(remote_dirs)

                        for rel_f, f_size in entry["rel_files"]:
                            if self.abort_transfer:
                                raise InterruptedError("Операцію скасовано")
                            l_file = os.path.join(src_path, *rel_f.split("/"))
                            r_file = f"{dst_path.rstrip('/')}/{rel_f}"
                            disp_name = f"{name}/{rel_f}"
                            report_progress(idx, disp_name, 0, f_size, force=True)
                            f_ok, f_err = self._push_pc_file(
                                l_file, r_file,
                                lambda cur, dn=disp_name, fs=f_size: report_progress(idx, dn, cur, fs),
                                startupinfo
                            )
                            if not f_ok:
                                raise RuntimeError(f"Файл «{rel_f}»: {f_err}")
                            report_progress(idx, disp_name, f_size, f_size, force=True)
                            bytes_done_before += f_size
                            bytes_done_in_entry += f_size

                        ok = True
                        if delete_source:
                            verified = self._verify_phone_item(dst_path, True, 0)
                            if verified and entry["rel_files"]:
                                _, dst_files_after = self._collect_phone_folder_tree(dst_path)
                                dst_map_after = dict(dst_files_after)
                                for rel_f, f_size in entry["rel_files"]:
                                    if rel_f not in dst_map_after or (f_size > 0 and dst_map_after[rel_f] != f_size):
                                        verified = False
                                        break
                            if verified:
                                try:
                                    shutil.rmtree(src_path)
                                    deleted_count += 1
                                except Exception:
                                    pass

                # Сценарій 3: Телефон -> Телефон (adb shell cp / mv)
                elif src_mode == "phone" and dst_mode == "phone":
                    safe_src = src_path.replace("'", "'\\''")
                    safe_dst = f"{dst_dir.rstrip('/')}/".replace("'", "'\\''")
                    if delete_source:
                        cmd_str = f"mv -f '{safe_src}' '{safe_dst}'"
                        ret, out, err = self.execute_adb(["shell", cmd_str])
                        if ret == 0:
                            ok = True
                            deleted_count += 1
                        elif is_folder:
                            # Якщо цільова папка на телефоні вже існує і не порожня — копіюємо з злиттям та видаляємо джерело
                            rc_cp, out_cp, err_cp = self._run_adb_popen_with_progress(
                                ["adb", "-s", self.current_device_id, "shell", f"cp -rf '{safe_src}' '{safe_dst}'"],
                                lambda: self._get_phone_path_size(dst_path, True),
                                lambda cur: report_progress(idx, name, cur, expected_size),
                                poll_interval=0.25,
                                startupinfo=startupinfo
                            )
                            if rc_cp == 0 and not self.abort_transfer:
                                r_del, _, err_del = self.execute_adb(["shell", f"rm -rf '{safe_src}'"])
                                ok = True
                                if r_del == 0:
                                    deleted_count += 1
                                elif err_del.strip():
                                    err_msg = err_del.strip()
                            else:
                                err_msg = err_cp.strip() or out_cp.strip() or err.strip() or out.strip() or "Помилка переміщення на телефоні"
                        else:
                            err_msg = err.strip() or out.strip() or "Помилка переміщення на телефоні"
                    else:
                        flag = "-rf" if is_folder else "-f"
                        cmd_str = f"cp {flag} '{safe_src}' '{safe_dst}'"
                        cmd = ["adb", "-s", self.current_device_id, "shell", cmd_str]
                        max_seen = 0

                        def poll_cp():
                            nonlocal max_seen
                            polled = self._get_phone_path_size(dst_path, is_folder)
                            if polled > max_seen:
                                max_seen = polled
                            return max_seen

                        rc, out, err = self._run_adb_popen_with_progress(
                            cmd, poll_cp,
                            lambda cur: report_progress(idx, name, cur, expected_size),
                            poll_interval=0.25,
                            startupinfo=startupinfo
                        )
                        if rc == 0:
                            ok = True
                        else:
                            err_msg = err.strip() or out.strip() or "Помилка копіювання на телефоні"

                # Сценарій 4: ПК -> ПК (локальне копіювання / переміщення з реальним прогресом)
                elif src_mode == "pc" and dst_mode == "pc":
                    same_drive = os.path.splitdrive(os.path.abspath(src_path))[0].lower() == os.path.splitdrive(os.path.abspath(dst_path))[0].lower()
                    if delete_source and same_drive and (not is_folder or not os.path.exists(dst_path)):
                        if os.path.exists(dst_path):
                            if os.path.isdir(dst_path) and not is_folder:
                                raise RuntimeError("У цільовій папці вже є каталог з таким ім'ям")
                            if not os.path.isdir(dst_path):
                                os.remove(dst_path)
                        shutil.move(src_path, dst_path)
                        deleted_count += 1
                        ok = True
                    elif not is_folder:
                        cur_pc_bytes = 0

                        def on_chunk(delta):
                            nonlocal cur_pc_bytes
                            cur_pc_bytes += delta
                            report_progress(idx, name, cur_pc_bytes, expected_size)

                        self._copy_pc_file_chunked(src_path, dst_path, on_chunk)
                        ok = True
                        if delete_source:
                            os.remove(src_path)
                            deleted_count += 1
                    else:
                        os.makedirs(dst_path, exist_ok=True)
                        for rel_d in entry["rel_dirs"]:
                            if rel_d:
                                os.makedirs(os.path.join(dst_path, *rel_d.split("/")), exist_ok=True)

                        for rel_f, f_size in entry["rel_files"]:
                            if self.abort_transfer:
                                raise InterruptedError("Операцію скасовано")
                            l_src = os.path.join(src_path, *rel_f.split("/"))
                            l_dst = os.path.join(dst_path, *rel_f.split("/"))
                            disp_name = f"{name}/{rel_f}"
                            cur_f_bytes = 0
                            report_progress(idx, disp_name, 0, f_size, force=True)

                            def on_file_chunk(delta, fs=f_size, dn=disp_name):
                                nonlocal cur_f_bytes
                                cur_f_bytes += delta
                                report_progress(idx, dn, cur_f_bytes, fs)

                            self._copy_pc_file_chunked(l_src, l_dst, on_file_chunk)
                            report_progress(idx, disp_name, f_size, f_size, force=True)
                            bytes_done_before += f_size
                            bytes_done_in_entry += f_size

                        ok = True
                        if delete_source:
                            if not item["is_link"]:
                                shutil.rmtree(src_path)
                            else:
                                os.remove(src_path)
                            deleted_count += 1

            except InterruptedError:
                pass
            except Exception as e:
                err_msg = str(e)

            if ok:
                success_count += 1
                remaining = max(0, expected_size - bytes_done_in_entry)
                if remaining > 0 or not is_folder:
                    report_progress(idx, name, expected_size, expected_size, force=True)
                    bytes_done_before += remaining
            elif not self.abort_transfer and err_msg:
                self.safe_after(0, lambda n=name, e=err_msg: messagebox.showerror("Помилка операції", f"Не вдалося обробити «{n}»:\n{e}"))

        self.safe_after(0, lambda: self._on_transfer_finished(src_pane, dst_pane, is_move, success_count, skipped_count, deleted_count, total_selected))

    def _on_transfer_finished(self, src_pane, dst_pane, is_move, success, skipped, deleted, total):
        self._set_busy(False)
        self.file_progress_bar.stop()
        self.progress_bar.stop()

        if not self.abort_transfer:
            self.file_progress_bar.configure(mode="determinate", value=100)
            self.file_progress_pct_label.config(text="100%")
            self.progress_bar.configure(mode="determinate", value=100)
            self.total_progress_pct_label.config(text="100%")

        dst_pane.refresh()
        if deleted > 0:
            src_pane.refresh()

        op_name = "Переміщення" if is_move else "Копіювання"
        if self.abort_transfer:
            status = f"{op_name} зупинено. Оброблено: {success}, пропущено: {skipped} з {total}."
            self.progress_status_label.config(text=status)
        else:
            status = f"✅ {op_name} завершено! Оброблено: {success}"
            if skipped > 0:
                status += f", пропущено (дублікати): {skipped}"
            if deleted > 0:
                status += f", видалено оригіналів: {deleted}"
            self.progress_status_label.config(text=status)

            details = f"{op_name} завершено!\n\n• Вибрано: {total}\n• Успішно оброблено: {success}\n• Пропущено дублікатів: {skipped}"
            if deleted > 0:
                details += f"\n• Видалено з вихідної папки: {deleted}"
            messagebox.showinfo("Готово", details)

    # --- Нова папка (F7), Перейменування (F2), Видалення (F8/Del) ---

    def create_new_folder(self):
        if self.is_busy:
            return
        pane = self.active_pane
        if pane.mode == "phone" and not self.current_device_id:
            messagebox.showwarning("Помилка", "Смартфон не підключено!")
            return

        folder_name = simpledialog.askstring("Нова папка (F7)", f"Введіть назву нової папки у:\n{pane.current_path}", parent=self.root)
        if not folder_name or not folder_name.strip():
            return
        folder_name = folder_name.strip()

        if pane.mode == "phone":
            new_path = f"{pane.current_path.rstrip('/')}/{folder_name}"
            safe_path = new_path.replace("'", "'\\''")
            ret, out, err = self.execute_adb(["shell", f"mkdir -p '{safe_path}'"])
            if ret == 0:
                self.progress_status_label.config(text=f"📁 Створено папку на телефоні: {folder_name}")
                pane.refresh()
            else:
                messagebox.showerror("Помилка", f"Не вдалося створити папку:\n{err or out}")
        else:
            new_path = os.path.join(pane.current_path, folder_name)
            try:
                os.makedirs(new_path, exist_ok=True)
                self.progress_status_label.config(text=f"📁 Створено папку на ПК: {folder_name}")
                pane.refresh()
            except Exception as e:
                messagebox.showerror("Помилка", f"Не вдалося створити папку:\n{e}")

    def rename_selected(self):
        if self.is_busy:
            return
        pane = self.active_pane
        selected = pane.get_selected_items()
        if len(selected) != 1:
            messagebox.showwarning("Перейменування (F2)", "Виберіть рівно один файл або папку для перейменування!")
            return

        old_name = selected[0]["name"]
        new_name = simpledialog.askstring("Перейменувати (F2)", "Нова назва:", initialvalue=old_name, parent=self.root)
        if not new_name or not new_name.strip() or new_name.strip() == old_name:
            return
        new_name = new_name.strip()

        if pane.mode == "phone":
            if not self.current_device_id:
                messagebox.showwarning("Помилка", "Смартфон не підключено!")
                return
            old_path = f"{pane.current_path.rstrip('/')}/{old_name}".replace("'", "'\\''")
            new_path = f"{pane.current_path.rstrip('/')}/{new_name}".replace("'", "'\\''")
            ret, out, err = self.execute_adb(["shell", f"mv '{old_path}' '{new_path}'"])
            if ret == 0:
                self.progress_status_label.config(text=f"✏️ Перейменовано: {old_name} ➔ {new_name}")
                pane.refresh()
            else:
                messagebox.showerror("Помилка", f"Не вдалося перейменувати:\n{err or out}")
        else:
            old_path = os.path.join(pane.current_path, old_name)
            new_path = os.path.join(pane.current_path, new_name)
            try:
                os.rename(old_path, new_path)
                self.progress_status_label.config(text=f"✏️ Перейменовано: {old_name} ➔ {new_name}")
                pane.refresh()
            except Exception as e:
                messagebox.showerror("Помилка", f"Не вдалося перейменувати:\n{e}")

    def confirm_delete_selected(self):
        if self.is_busy:
            return
        pane = self.active_pane
        if pane.mode == "phone" and not self.current_device_id:
            messagebox.showwarning("Помилка", "Телефон не підключено!")
            return

        selected = pane.get_selected_items()
        if not selected:
            messagebox.showwarning("Виберіть файли", "Виберіть файли або папки для видалення в активній панелі!")
            return

        total_size = sum(i["size"] for i in selected if i["size"])
        size_str = f" ({format_size(total_size)})" if total_size > 0 else ""
        where = "з ТЕЛЕФОНА" if pane.mode == "phone" else "з КОМП'ЮТЕРА"

        msg = (
            f"⚠️ УВАГА! Ви дійсно бажаєте безповоротно видалити {where}:\n"
            f"• {len(selected)} елемент(ів){size_str}?\n\n"
            f"Папка: {pane.current_path}"
        )
        if not messagebox.askyesno("Підтвердження видалення (F8)", msg, icon="warning"):
            return

        self._set_busy(True)
        threading.Thread(target=self._delete_worker, args=(pane, selected), daemon=True).start()

    def _delete_worker(self, pane, items):
        total = len(items)
        deleted = 0

        for idx, item in enumerate(items, start=1):
            name = item["name"]
            is_dir = item["is_dir"]
            self.safe_after(0, lambda i=idx, t=total, n=name: self.progress_status_label.config(
                text=f"[{i}/{t}] Видалення: {n}..."
            ))

            if pane.mode == "phone":
                remote_path = f"{pane.current_path.rstrip('/')}/{name}"
                flag = "-rf" if is_dir else "-f"
                safe_remote = remote_path.replace("'", "'\\''")
                ret, _, _ = self.execute_adb(["shell", f"rm {flag} '{safe_remote}'"])
                if ret == 0:
                    deleted += 1
            else:
                local_path = os.path.join(pane.current_path, name)
                try:
                    if is_dir and not item["is_link"]:
                        shutil.rmtree(local_path)
                    else:
                        os.remove(local_path)
                    deleted += 1
                except Exception:
                    pass

        def finish():
            self._set_busy(False)
            self.progress_status_label.config(text=f"🗑️ Успішно видалено {deleted} з {total} елементів.")
            messagebox.showinfo("Видалення завершено", f"Видалено {deleted} з {total} елементів.")
            pane.refresh()

        self.safe_after(0, finish)

    def on_closing(self):
        self.running = False
        if hasattr(self, "_old_wndproc") and self._old_wndproc:
            try:
                hwnd = self.root.winfo_id()
                user32.SetWindowLongPtrW(hwnd, GWLP_WNDPROC, self._old_wndproc)
                shell32.DragAcceptFiles(hwnd, False)
            except Exception:
                pass
        if self.transfer_process and self.transfer_process.poll() is None:
            try:
                self.transfer_process.terminate()
            except Exception:
                pass
        self.root.destroy()


if __name__ == "__main__":
    root = tk.Tk()
    app = ADBFileBrowserApp(root)
    root.protocol("WM_DELETE_WINDOW", app.on_closing)
    root.mainloop()
