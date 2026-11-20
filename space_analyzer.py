import os
import re
import subprocess
import threading
import tkinter as tk
from tkinter import messagebox, ttk

# Допоміжні функції форматування розміру
def format_size(size_bytes):
    if size_bytes is None or size_bytes < 0:
        return "0 Б"
    for unit in ["Б", "КБ", "МБ", "ГБ", "ТБ"]:
        if size_bytes < 1024.0:
            return f"{size_bytes:.1f} {unit}" if unit != "Б" else f"{int(size_bytes)} Б"
        size_bytes /= 1024.0
    return f"{size_bytes:.1f} ПБ"


def make_ascii_bar(pct, length=14):
    """Створює компактну візуальну діаграму-бар за допомогою блокових символів Unicode"""
    pct = max(0.0, min(100.0, pct))
    filled = int(round((pct / 100.0) * length))
    filled = max(0, min(length, filled))
    bar = "█" * filled + "░" * (length - filled)
    return f"[{bar}] {pct:5.1f}%"


def execute_adb_command(cmd_args, device_id=None, timeout=30):
    """Виконання ADB команд без миготіння чорних вікон консолі на Windows"""
    startupinfo = None
    if os.name == 'nt':
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW

    full_cmd = ["adb"]
    if device_id:
        full_cmd.extend(["-s", device_id])
    full_cmd.extend(cmd_args)

    try:
        res = subprocess.run(
            full_cmd,
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace',
            startupinfo=startupinfo,
            timeout=timeout
        )
        return res.returncode, res.stdout.strip(), res.stderr.strip()
    except subprocess.TimeoutExpired:
        return -1, "", "Timeout"
    except Exception as e:
        return -1, "", str(e)


# Визначення категорії файлу за розширенням
EXT_CATEGORIES = {
    "Відео": {".mp4", ".mkv", ".avi", ".mov", ".ts", ".webm", ".flv", ".3gp", ".m4v", ".wmv"},
    "Аудіо": {".mp3", ".flac", ".ape", ".wav", ".m4a", ".aac", ".ogg", ".wv", ".wma", ".opus"},
    "Фото": {".jpg", ".jpeg", ".png", ".gif", ".webp", ".heic", ".raw", ".dng", ".bmp", ".tiff"},
    "Архіви": {".zip", ".rar", ".7z", ".tar", ".gz", ".bz2", ".xz", ".tgz"},
    "APK/Додатки": {".apk", ".obb", ".xapk", ".apks"},
    "Документи": {".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".txt", ".rtf"}
}

def get_file_category(filename):
    _, ext = os.path.splitext(filename.lower())
    for cat, exts in EXT_CATEGORIES.items():
        if ext in exts:
            return cat
    return "Інше"


class SpaceAnalyzerDialog(tk.Toplevel):
    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
        self.device_id = getattr(app, "current_device_id", None)
        self.device_model = getattr(app, "device_model", "Смартфон")

        self.title("📊 Розумний аналізатор пам'яті смартфона (Space Analyzer)")
        self.geometry("980x700")
        self.minsize(800, 550)

        # Центрування вікна
        try:
            self.transient(parent)
            pw = parent.winfo_width()
            ph = parent.winfo_height()
            px = parent.winfo_rootx()
            py = parent.winfo_rooty()
            if pw > 100 and ph > 100:
                self.geometry(f"980x700+{px + max(0, (pw - 980) // 2)}+{py + max(0, (ph - 700) // 2)}")
        except Exception:
            pass

        self.current_folder_path = "/sdcard"
        self.folder_history = []
        self.is_scanning = False
        self.abort_scan = False

        self._build_ui()
        self.protocol("WM_DELETE_WINDOW", self.on_close)

        # Первинне оновлення інформації про диск
        self.refresh_storage_overview()
        self.scan_current_folder()

    def on_close(self):
        self.abort_scan = True
        self.destroy()

    def _build_ui(self):
        # 1. Верхня панель (Загальна статистика пам'яті)
        self.header_frame = tk.Frame(self, bg="#ECEFF1", padx=12, pady=10)
        self.header_frame.pack(fill="x", side="top")

        dev_line = tk.Frame(self.header_frame, bg="#ECEFF1")
        dev_line.pack(fill="x")

        self.lbl_device_title = tk.Label(
            dev_line,
            text=f"📱 Пристрій: {self.device_model} ({self.device_id or 'Невідомо'})",
            font=("Arial", 11, "bold"),
            bg="#ECEFF1",
            fg="#263238"
        )
        self.lbl_device_title.pack(side="left")

        self.btn_refresh_all = tk.Button(
            dev_line,
            text="🔄 Оновити дані",
            command=self.refresh_all,
            font=("Arial", 9),
            padx=8
        )
        self.btn_refresh_all.pack(side="right")

        self.lbl_storage_stats = tk.Label(
            self.header_frame,
            text="Отримання даних про зайняту пам'ять...",
            font=("Arial", 10),
            bg="#ECEFF1",
            fg="#455A64"
        )
        self.lbl_storage_stats.pack(anchor="w", pady=(6, 4))

        self.overall_progress = ttk.Progressbar(self.header_frame, orient="horizontal", mode="determinate")
        self.overall_progress.pack(fill="x")

        # 2. Вкладки аналізатора (Notebook)
        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill="both", expand=True, padx=8, pady=(8, 4))

        self.tab_folders = tk.Frame(self.notebook, bg="#F9F9F9")
        self.tab_heavy = tk.Frame(self.notebook, bg="#F9F9F9")
        self.tab_presets = tk.Frame(self.notebook, bg="#F9F9F9")

        self.notebook.add(self.tab_folders, text="📁 Розподіл за папками")
        self.notebook.add(self.tab_heavy, text="🐘 Топ важких файлів")
        self.notebook.add(self.tab_presets, text="🧹 Швидке очищення (Пресети)")

        self._build_tab_folders()
        self._build_tab_heavy()
        self._build_tab_presets()

        # 3. Нижня панель дій
        self.bottom_bar = tk.Frame(self, padx=10, pady=8, bg="#ECEFF1")
        self.bottom_bar.pack(fill="x", side="bottom")

        self.lbl_action_status = tk.Label(
            self.bottom_bar,
            text="Готово",
            font=("Arial", 9),
            bg="#ECEFF1",
            fg="#37474F"
        )
        self.lbl_action_status.pack(side="left", padx=4)

        tk.Button(
            self.bottom_bar,
            text="Закрити",
            command=self.on_close,
            font=("Arial", 9),
            padx=10
        ).pack(side="right", padx=4)

        self.btn_delete = tk.Button(
            self.bottom_bar,
            text="🗑️ Видалити з телефона",
            command=self.action_delete_selected,
            font=("Arial", 9, "bold"),
            bg="#FFEBEE",
            fg="#C62828",
            padx=10
        )
        self.btn_delete.pack(side="right", padx=4)

        self.btn_copy_pc = tk.Button(
            self.bottom_bar,
            text="💾 Скопіювати на ПК",
            command=self.action_copy_to_pc,
            font=("Arial", 9),
            padx=10
        )
        self.btn_copy_pc.pack(side="right", padx=4)

        self.btn_open_in_mgr = tk.Button(
            self.bottom_bar,
            text="📂 Відкрити у менеджері",
            command=self.action_open_in_manager,
            font=("Arial", 9, "bold"),
            bg="#E8F5E9",
            fg="#1B5E20",
            padx=10
        )
        self.btn_open_in_mgr.pack(side="right", padx=4)

    # ---------------- Вкладка 1: Розподіл за папками ----------------
    def _build_tab_folders(self):
        f = self.tab_folders

        # Панель навігації
        nav = tk.Frame(f, padx=6, pady=6)
        nav.pack(fill="x")

        tk.Button(nav, text="⬆️ Вгору", command=self.nav_up_folder, font=("Arial", 9)).pack(side="left", padx=(0, 6))
        tk.Label(nav, text="Папка:", font=("Arial", 9, "bold")).pack(side="left", padx=(0, 4))

        self.ent_folder_path = tk.Entry(nav, font=("Consolas", 10))
        self.ent_folder_path.pack(side="left", fill="x", expand=True, padx=4)
        self.ent_folder_path.insert(0, self.current_folder_path)
        self.ent_folder_path.bind("<Return>", lambda e: self.nav_to_entry())

        tk.Button(nav, text="Перейти", command=self.nav_to_entry, font=("Arial", 9)).pack(side="left", padx=4)
        tk.Button(nav, text="🔄 Оновити", command=self.scan_current_folder, font=("Arial", 9)).pack(side="left", padx=4)

        # Таблиця Treeview
        tree_frame = tk.Frame(f)
        tree_frame.pack(fill="both", expand=True, padx=6, pady=4)

        cols = ("name", "size", "bar", "path")
        self.tree_folders = ttk.Treeview(tree_frame, columns=cols, show="headings", selectmode="extended")
        self.tree_folders.heading("name", text="Назва папки")
        self.tree_folders.heading("size", text="Розмір")
        self.tree_folders.heading("bar", text="Частка від батьківської папки")
        self.tree_folders.heading("path", text="Повний шлях")

        self.tree_folders.column("name", width=260, anchor="w")
        self.tree_folders.column("size", width=110, anchor="e")
        self.tree_folders.column("bar", width=220, anchor="w")
        self.tree_folders.column("path", width=340, anchor="w")

        sb = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree_folders.yview)
        self.tree_folders.configure(yscrollcommand=sb.set)

        self.tree_folders.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        self.tree_folders.bind("<Double-1>", self.on_folder_double_click)

        # Підказка
        tk.Label(
            f,
            text="💡 Подвійний клік на папці відкриває її вміст для детальнішого аналізу.",
            font=("Arial", 8, "italic"),
            fg="#666"
        ).pack(anchor="w", padx=8, pady=(0, 4))

    # ---------------- Вкладка 2: Топ важких файлів ----------------
    def _build_tab_heavy(self):
        f = self.tab_heavy

        ctrl = tk.Frame(f, padx=6, pady=6)
        ctrl.pack(fill="x")

        tk.Label(ctrl, text="Мінімальний розмір файлу:", font=("Arial", 9, "bold")).pack(side="left", padx=(0, 4))
        self.cmb_heavy_size = ttk.Combobox(ctrl, values=["25 МБ", "50 МБ", "100 МБ", "250 МБ", "500 МБ", "1 ГБ"], width=10, state="readonly")
        self.cmb_heavy_size.set("50 МБ")
        self.cmb_heavy_size.pack(side="left", padx=4)

        tk.Label(ctrl, text="Ліміт результатів:", font=("Arial", 9, "bold")).pack(side="left", padx=(12, 4))
        self.cmb_heavy_limit = ttk.Combobox(ctrl, values=["50", "100", "200", "500"], width=8, state="readonly")
        self.cmb_heavy_limit.set("100")
        self.cmb_heavy_limit.pack(side="left", padx=4)

        self.btn_scan_heavy = tk.Button(
            ctrl,
            text="🔍 Знайти важкі файли",
            command=self.scan_heavy_files,
            font=("Arial", 9, "bold"),
            bg="#E3F2FD",
            fg="#0D47A1",
            padx=10
        )
        self.btn_scan_heavy.pack(side="left", padx=(12, 4))

        tree_frame = tk.Frame(f)
        tree_frame.pack(fill="both", expand=True, padx=6, pady=4)

        cols = ("name", "size", "type", "bar", "path")
        self.tree_heavy = ttk.Treeview(tree_frame, columns=cols, show="headings", selectmode="extended")
        self.tree_heavy.heading("name", text="Назва файлу")
        self.tree_heavy.heading("size", text="Розмір")
        self.tree_heavy.heading("type", text="Тип")
        self.tree_heavy.heading("bar", text="Візуальний розмір")
        self.tree_heavy.heading("path", text="Шлях на телефоні")

        self.tree_heavy.column("name", width=280, anchor="w")
        self.tree_heavy.column("size", width=110, anchor="e")
        self.tree_heavy.column("type", width=90, anchor="center")
        self.tree_heavy.column("bar", width=200, anchor="w")
        self.tree_heavy.column("path", width=260, anchor="w")

        sb = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree_heavy.yview)
        self.tree_heavy.configure(yscrollcommand=sb.set)

        self.tree_heavy.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

    # ---------------- Вкладка 3: Швидке очищення (Пресети) ----------------
    def _build_tab_presets(self):
        f = self.tab_presets

        preset_bar = tk.LabelFrame(f, text="Виберіть категорію для швидкого сканування", font=("Arial", 9, "bold"), padx=8, pady=6)
        preset_bar.pack(fill="x", padx=6, pady=6)

        presets = [
            ("🗑️ Ескізи (.thumbnails)", "thumbnails"),
            ("✈️ Медіа Telegram", "telegram"),
            ("💬 Медіа WhatsApp", "whatsapp"),
            ("📸 Скріншоти", "screenshots"),
            ("📦 Файли APK", "apks"),
            ("📥 Завантаження (Download)", "downloads"),
            ("📂 Порожні папки", "empty_dirs")
        ]

        self.preset_buttons = {}
        grid_frame = tk.Frame(preset_bar)
        grid_frame.pack(fill="x", pady=2)

        cols_count = 4
        for c in range(cols_count):
            grid_frame.columnconfigure(c, weight=1)

        for idx, (text, key) in enumerate(presets):
            btn = tk.Button(
                grid_frame,
                text=text,
                command=lambda k=key: self.scan_preset(k),
                font=("Arial", 9),
                padx=8,
                pady=4
            )
            btn.grid(row=idx // cols_count, column=idx % cols_count, padx=3, pady=3, sticky="ew")
            self.preset_buttons[key] = btn

        # Інформаційний рядок підсумку пресету
        self.lbl_preset_summary = tk.Label(
            f,
            text="Оберіть один із пресетів вище для запуску перевірки.",
            font=("Arial", 9, "bold"),
            fg="#1565C0",
            anchor="w"
        )
        self.lbl_preset_summary.pack(fill="x", padx=10, pady=(2, 6))

        tree_frame = tk.Frame(f)
        tree_frame.pack(fill="both", expand=True, padx=6, pady=4)

        cols = ("name", "size", "type", "path")
        self.tree_presets = ttk.Treeview(tree_frame, columns=cols, show="headings", selectmode="extended")
        self.tree_presets.heading("name", text="Об'єкт")
        self.tree_presets.heading("size", text="Розмір")
        self.tree_presets.heading("type", text="Категорія")
        self.tree_presets.heading("path", text="Шлях")

        self.tree_presets.column("name", width=280, anchor="w")
        self.tree_presets.column("size", width=120, anchor="e")
        self.tree_presets.column("type", width=130, anchor="center")
        self.tree_presets.column("path", width=390, anchor="w")

        sb = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree_presets.yview)
        self.tree_presets.configure(yscrollcommand=sb.set)

        self.tree_presets.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

    # ---------------- Логіка даних ----------------

    def refresh_all(self):
        self.refresh_storage_overview()
        self.scan_current_folder()

    def refresh_storage_overview(self):
        """Отримує загальний розмір і зайняте місце через adb shell df -k /sdcard"""
        def task():
            ret, out, err = execute_adb_command(["shell", "df -k /sdcard 2>/dev/null"], self.device_id)
            if ret == 0 and out:
                lines = out.strip().split("\n")
                if len(lines) >= 2:
                    parts = lines[-1].split()
                    if len(parts) >= 5:
                        try:
                            # 1K-blocks, Used, Available, Use%
                            total_kb = int(parts[1])
                            used_kb = int(parts[2])
                            avail_kb = int(parts[3])
                            pct_str = parts[4].replace("%", "")
                            pct = float(pct_str)

                            total_bytes = total_kb * 1024
                            used_bytes = used_kb * 1024
                            avail_bytes = avail_kb * 1024

                            text = (
                                f"💾 Сховище: Зайнято {format_size(used_bytes)} з {format_size(total_bytes)} "
                                f"({pct:.1f}%) | Вільно: {format_size(avail_bytes)}"
                            )
                            self.after(0, lambda: self._apply_storage_overview(text, pct))
                            return
                        except Exception:
                            pass
            self.after(0, lambda: self.lbl_storage_stats.config(text="Не вдалося отримати статистику сховища"))

        threading.Thread(target=task, daemon=True).start()

    def _apply_storage_overview(self, text, pct):
        self.lbl_storage_stats.config(text=text)
        self.overall_progress["value"] = pct

    # ---------------- Сканування папок ----------------

    def nav_up_folder(self):
        p = self.current_folder_path.rstrip("/")
        parent = os.path.dirname(p)
        if not parent or parent == "/":
            parent = "/sdcard"
        if parent != self.current_folder_path:
            self.current_folder_path = parent
            self.ent_folder_path.delete(0, tk.END)
            self.ent_folder_path.insert(0, self.current_folder_path)
            self.scan_current_folder()

    def nav_to_entry(self):
        target = self.ent_folder_path.get().strip()
        if target:
            self.current_folder_path = target
            self.scan_current_folder()

    def on_folder_double_click(self, event):
        sel = self.tree_folders.selection()
        if not sel:
            return
        item = self.tree_folders.item(sel[0])
        path = item["values"][3]
        if path and path != self.current_folder_path:
            self.current_folder_path = path
            self.ent_folder_path.delete(0, tk.END)
            self.ent_folder_path.insert(0, self.current_folder_path)
            self.scan_current_folder()

    def scan_current_folder(self):
        if self.is_scanning:
            return
        self.is_scanning = True
        self.lbl_action_status.config(text=f"⏳ Сканування папки {self.current_folder_path}...")
        self.tree_folders.delete(*self.tree_folders.get_children())

        path = self.current_folder_path.rstrip("/")

        def task():
            # du -d 1 -k '/path/'
            cmd = f"du -d 1 -k '{path}/' 2>/dev/null"
            ret, out, err = execute_adb_command(["shell", cmd], self.device_id, timeout=45)

            items = []
            parent_size_bytes = 0

            if ret == 0 and out:
                lines = out.strip().split("\n")
                raw_entries = []
                for line in lines:
                    line = line.strip()
                    if not line:
                        continue
                    parts = line.split("\t", 1)
                    if len(parts) == 2:
                        try:
                            kb = int(parts[0])
                            item_path = parts[1].strip()
                            raw_entries.append((kb, item_path))
                        except ValueError:
                            pass

                # Визначаємо розмір батьківської папки (зазвичай останній рядок)
                for kb, item_path in raw_entries:
                    norm_item = item_path.rstrip("/")
                    if norm_item == path:
                        parent_size_bytes = kb * 1024
                        break

                # Якщо не знайдено точного збігу, беремо суму підпапок
                sub_entries = [e for e in raw_entries if e[1].rstrip("/") != path]
                if parent_size_bytes == 0 and sub_entries:
                    parent_size_bytes = sum(e[0] for e in sub_entries) * 1024

                for kb, item_path in sub_entries:
                    size_bytes = kb * 1024
                    name = os.path.basename(item_path.rstrip("/"))
                    pct = (size_bytes / parent_size_bytes * 100.0) if parent_size_bytes > 0 else 0.0
                    items.append({
                        "name": f"📁 {name}",
                        "size": size_bytes,
                        "size_str": format_size(size_bytes),
                        "pct": pct,
                        "bar": make_ascii_bar(pct),
                        "path": item_path
                    })

                # Сортуємо від найбільшого до найменшого
                items.sort(key=lambda x: x["size"], reverse=True)

            self.after(0, lambda: self._apply_folder_items(items, parent_size_bytes))

        threading.Thread(target=task, daemon=True).start()

    def _apply_folder_items(self, items, parent_size_bytes):
        self.is_scanning = False
        self.tree_folders.delete(*self.tree_folders.get_children())
        for it in items:
            self.tree_folders.insert(
                "",
                "end",
                values=(it["name"], it["size_str"], it["bar"], it["path"])
            )
        total_str = format_size(parent_size_bytes) if parent_size_bytes > 0 else "0 Б"
        self.lbl_action_status.config(text=f"Папка {self.current_folder_path}: знайдено {len(items)} підпапок загальним обсягом {total_str}")

    # ---------------- Сканування важких файлів ----------------

    def scan_heavy_files(self):
        if self.is_scanning:
            return
        self.is_scanning = True
        self.btn_scan_heavy.config(state="disabled")

        size_raw = self.cmb_heavy_size.get().replace("МБ", "").replace("ГБ", "").strip()
        is_gb = "ГБ" in self.cmb_heavy_size.get()
        try:
            val = int(size_raw)
            min_mb = val * 1024 if is_gb else val
        except ValueError:
            min_mb = 50

        limit_raw = self.cmb_heavy_limit.get().strip()
        try:
            limit = int(limit_raw)
        except ValueError:
            limit = 100

        self.lbl_action_status.config(text=f"⏳ Пошук файлів понад {min_mb} МБ на пристрої...")
        self.tree_heavy.delete(*self.tree_heavy.get_children())

        def task():
            cmd = f"find /sdcard/ -type f -size +{min_mb}M -exec stat -c '%s %n' {{}} + 2>/dev/null"
            ret, out, err = execute_adb_command(["shell", cmd], self.device_id, timeout=60)

            items = []
            if ret == 0 and out:
                lines = out.strip().split("\n")
                for line in lines:
                    line = line.strip()
                    if not line:
                        continue
                    parts = line.split(" ", 1)
                    if len(parts) == 2:
                        try:
                            size_bytes = int(parts[0])
                            full_path = parts[1].strip()
                            name = os.path.basename(full_path)
                            cat = get_file_category(name)
                            items.append({
                                "name": name,
                                "size": size_bytes,
                                "size_str": format_size(size_bytes),
                                "type": cat,
                                "path": full_path
                            })
                        except ValueError:
                            pass

                # Сортування від найбільших
                items.sort(key=lambda x: x["size"], reverse=True)
                if limit and len(items) > limit:
                    items = items[:limit]

                # Обчислюємо відсоток відносно найбільшого файлу в списку
                max_size = items[0]["size"] if items else 1
                for it in items:
                    pct = (it["size"] / max_size) * 100.0
                    it["bar"] = make_ascii_bar(pct)

            self.after(0, lambda: self._apply_heavy_items(items))

        threading.Thread(target=task, daemon=True).start()

    def _apply_heavy_items(self, items):
        self.is_scanning = False
        self.btn_scan_heavy.config(state="normal")
        self.tree_heavy.delete(*self.tree_heavy.get_children())
        total_bytes = sum(it["size"] for it in items)
        for it in items:
            self.tree_heavy.insert(
                "",
                "end",
                values=(it["name"], it["size_str"], it["type"], it["bar"], it["path"])
            )
        self.lbl_action_status.config(
            text=f"Знайдено {len(items)} важких файлів загальним обсягом {format_size(total_bytes)}"
        )

    # ---------------- Сканування пресетів ----------------

    def scan_preset(self, preset_key):
        if self.is_scanning:
            return
        self.is_scanning = True
        self.tree_presets.delete(*self.tree_presets.get_children())
        self.lbl_preset_summary.config(text=f"⏳ Виконується сканування обраного пресету...")
        self.lbl_action_status.config(text=f"⏳ Сканування пресету {preset_key}...")

        def task():
            items = []
            summary_text = ""

            if preset_key == "thumbnails":
                summary_text = "Ескізи галереї (.thumbnails)"
                # Знаходимо всі папки .thumbnails
                cmd = "find /sdcard/ -maxdepth 4 -type d -name '.thumbnails' -exec du -sk {} + 2>/dev/null"
                ret, out, err = execute_adb_command(["shell", cmd], self.device_id, timeout=40)
                if ret == 0 and out:
                    for line in out.strip().split("\n"):
                        p = line.strip().split("\t", 1)
                        if len(p) == 2:
                            try:
                                sz = int(p[0]) * 1024
                                path = p[1].strip()
                                items.append({
                                    "name": f"🗑️ {os.path.basename(path)} ({os.path.basename(os.path.dirname(path))})",
                                    "size": sz,
                                    "size_str": format_size(sz),
                                    "type": "Кеш ескізів",
                                    "path": path
                                })
                            except ValueError:
                                pass

            elif preset_key == "telegram":
                summary_text = "Медіа та завантаження Telegram"
                paths = [
                    "/sdcard/Android/media/org.telegram.messenger",
                    "/sdcard/Pictures/Telegram",
                    "/sdcard/Movies/Telegram",
                    "/sdcard/Download/Telegram"
                ]
                cmd = f"du -sk {' '.join(paths)} 2>/dev/null"
                ret, out, err = execute_adb_command(["shell", cmd], self.device_id, timeout=30)
                if ret == 0 and out:
                    for line in out.strip().split("\n"):
                        p = line.strip().split("\t", 1)
                        if len(p) == 2:
                            try:
                                sz = int(p[0]) * 1024
                                path = p[1].strip()
                                items.append({
                                    "name": f"✈️ {os.path.basename(path)}",
                                    "size": sz,
                                    "size_str": format_size(sz),
                                    "type": "Telegram медіа",
                                    "path": path
                                })
                            except ValueError:
                                pass

            elif preset_key == "whatsapp":
                summary_text = "Медіа та голосові WhatsApp"
                paths = [
                    "/sdcard/Android/media/com.whatsapp",
                    "/sdcard/WhatsApp"
                ]
                cmd = f"du -sk {' '.join(paths)} 2>/dev/null"
                ret, out, err = execute_adb_command(["shell", cmd], self.device_id, timeout=30)
                if ret == 0 and out:
                    for line in out.strip().split("\n"):
                        p = line.strip().split("\t", 1)
                        if len(p) == 2:
                            try:
                                sz = int(p[0]) * 1024
                                path = p[1].strip()
                                items.append({
                                    "name": f"💬 {os.path.basename(path)}",
                                    "size": sz,
                                    "size_str": format_size(sz),
                                    "type": "WhatsApp медіа",
                                    "path": path
                                })
                            except ValueError:
                                pass

            elif preset_key == "screenshots":
                summary_text = "Скріншоти екрана"
                ss_paths = [
                    "/sdcard/Pictures/Screenshots",
                    "/sdcard/DCIM/Screenshots",
                    "/sdcard/Screenshots"
                ]
                cmd = f"find {' '.join(ss_paths)} -type f -exec stat -c '%s %n' {{}} + 2>/dev/null"
                ret, out, err = execute_adb_command(["shell", cmd], self.device_id, timeout=40)
                if out:
                    for line in out.strip().split("\n"):
                        p = line.strip().split(" ", 1)
                        if len(p) == 2:
                            try:
                                sz = int(p[0])
                                path = p[1].strip()
                                items.append({
                                    "name": f"📸 {os.path.basename(path)}",
                                    "size": sz,
                                    "size_str": format_size(sz),
                                    "type": "Скріншот",
                                    "path": path
                                })
                            except ValueError:
                                pass

            elif preset_key == "apks":
                summary_text = "Завантажені інсталяційні пакети APK"
                cmd = "find /sdcard/ -type f -iname '*.apk' -exec stat -c '%s %n' {} + 2>/dev/null"
                ret, out, err = execute_adb_command(["shell", cmd], self.device_id, timeout=40)
                if ret == 0 and out:
                    for line in out.strip().split("\n"):
                        p = line.strip().split(" ", 1)
                        if len(p) == 2:
                            try:
                                sz = int(p[0])
                                path = p[1].strip()
                                items.append({
                                    "name": f"📦 {os.path.basename(path)}",
                                    "size": sz,
                                    "size_str": format_size(sz),
                                    "type": "APK пакет",
                                    "path": path
                                })
                            except ValueError:
                                pass

            elif preset_key == "downloads":
                summary_text = "Вміст папки Завантаження"
                cmd = "du -d 1 -k /sdcard/Download/ /sdcard/ADM/ 2>/dev/null"
                ret, out, err = execute_adb_command(["shell", cmd], self.device_id, timeout=35)
                if ret == 0 and out:
                    for line in out.strip().split("\n"):
                        p = line.strip().split("\t", 1)
                        if len(p) == 2:
                            try:
                                sz = int(p[0]) * 1024
                                path = p[1].strip()
                                if path.rstrip("/") in ["/sdcard/Download", "/sdcard/ADM"]:
                                    continue
                                items.append({
                                    "name": f"📥 {os.path.basename(path)}",
                                    "size": sz,
                                    "size_str": format_size(sz),
                                    "type": "Завантаження",
                                    "path": path
                                })
                            except ValueError:
                                pass

            elif preset_key == "empty_dirs":
                summary_text = "Порожні директорії (поза системою Android)"
                cmd = "find /sdcard/ -path /sdcard/Android -prune -o -mindepth 1 -type d -empty -print 2>/dev/null"
                ret, out, err = execute_adb_command(["shell", cmd], self.device_id, timeout=30)
                if ret == 0 and out:
                    for line in out.strip().split("\n"):
                        path = line.strip()
                        if path and path != "/sdcard/Android":
                            items.append({
                                "name": f"📂 {os.path.basename(path)}",
                                "size": 0,
                                "size_str": "0 Б (порожня)",
                                "type": "Порожня папка",
                                "path": path
                            })

            items.sort(key=lambda x: x["size"], reverse=True)
            self.after(0, lambda: self._apply_preset_items(items, summary_text))

        threading.Thread(target=task, daemon=True).start()

    def _apply_preset_items(self, items, summary_text):
        self.is_scanning = False
        self.tree_presets.delete(*self.tree_presets.get_children())
        total_sz = sum(it["size"] for it in items)
        for it in items:
            self.tree_presets.insert(
                "",
                "end",
                values=(it["name"], it["size_str"], it["type"], it["path"])
            )
        res_info = f"Знайдено: {len(items)} об'єктів ({summary_text}) | Загальний обсяг: {format_size(total_sz)}"
        self.lbl_preset_summary.config(text=res_info)
        self.lbl_action_status.config(text=res_info)

    # ---------------- Дії над вибраними об'єктами ----------------

    def _get_active_selection(self):
        """Отримує список вибраних шляхів із поточної активної вкладки"""
        current_tab_idx = self.notebook.index(self.notebook.select())
        if current_tab_idx == 0:
            tree = self.tree_folders
            path_idx = 3
        elif current_tab_idx == 1:
            tree = self.tree_heavy
            path_idx = 4
        else:
            tree = self.tree_presets
            path_idx = 3

        selected = tree.selection()
        paths = []
        for s in selected:
            val = tree.item(s)["values"]
            if val and len(val) > path_idx:
                paths.append(str(val[path_idx]))
        return paths, tree

    def action_open_in_manager(self):
        """Відкриває вибрану папку в активній панелі основного вікна"""
        paths, _ = self._get_active_selection()
        if not paths:
            messagebox.showinfo("Вибір", "Виберіть об'єкт для переходу в менеджері.")
            return

        target_path = paths[0]
        # Якщо це файл, переходимо в його батьківську папку
        parent_dir = os.path.dirname(target_path) if "." in os.path.basename(target_path) else target_path

        if hasattr(self.app, "navigate_phone_pane"):
            self.app.navigate_phone_pane(parent_dir)
            self.lbl_action_status.config(text=f"Відкрито в головному вікні: {parent_dir}")
        else:
            messagebox.showinfo("Перехід", f"Шлях для переходу: {parent_dir}")

    def action_copy_to_pc(self):
        """Копіює вибрані файли або папки на ПК у цільову папку активної ПК-панелі"""
        paths, _ = self._get_active_selection()
        if not paths:
            messagebox.showinfo("Вибір", "Оберіть один або декілька об'єктів для копіювання на ПК.")
            return

        # Визначаємо цільову папку на ПК
        dst_folder = None
        for pane in (self.app.left_pane, self.app.right_pane):
            if pane.mode == "pc":
                dst_folder = pane.current_path
                break

        if not dst_folder or not os.path.exists(dst_folder):
            dst_folder = os.path.expanduser("~\\Downloads")

        if not messagebox.askyesno(
            "Копіювання на ПК",
            f"Скопіювати {len(paths)} обраних об'єктів у теку на комп'ютері?\n\nЦільова папка:\n{dst_folder}"
        ):
            return

        self.lbl_action_status.config(text=f"⏳ Копіювання {len(paths)} об'єктів на ПК...")

        def task():
            success = 0
            for p in paths:
                ret, _, err = execute_adb_command(["pull", p, dst_folder], self.device_id, timeout=300)
                if ret == 0:
                    success += 1

            self.after(0, lambda: (
                self.lbl_action_status.config(text=f"Успішно скопійовано: {success} з {len(paths)} об'єктів на ПК"),
                messagebox.showinfo("Копіювання завершено", f"Успішно скопійовано {success} з {len(paths)} об'єктів у:\n{dst_folder}")
            ))

        threading.Thread(target=task, daemon=True).start()

    def action_delete_selected(self):
        """Безпечне видалення вибраних файлів або папок на телефоні"""
        paths, tree = self._get_active_selection()
        if not paths:
            messagebox.showinfo("Вибір", "Оберіть об'єкти для видалення.")
            return

        # Захист від випадкового видалення кореня
        for p in paths:
            if p.rstrip("/") in ["", "/sdcard", "/storage", "/storage/emulated", "/storage/emulated/0"]:
                messagebox.showerror("Заборонено", "Видалення кореневої папки сховища неможливе з міркувань безпеки!")
                return

        count = len(paths)
        preview_text = "\n".join(paths[:5]) + ("\n..." if count > 5 else "")
        msg = f"Ви дійсно бажаєте НАЗАВЖДИ ВИДАЛИТИ з телефона {count} об'єктів?\n\n{preview_text}\n\nЦю дію неможливо скасувати!"
        if not messagebox.askyesno("Підтвердження видалення", msg, icon="warning"):
            return

        self.lbl_action_status.config(text=f"⏳ Видалення {count} об'єктів з телефона...")

        def task():
            deleted_count = 0
            for p in paths:
                # rm -rf
                safe_p = p.replace("'", "'\\''")
                ret, _, err = execute_adb_command(["shell", f"rm -rf '{safe_p}' 2>/dev/null"], self.device_id, timeout=30)
                if ret == 0:
                    deleted_count += 1

            self.after(0, lambda: self._on_delete_finished(deleted_count, count))

        threading.Thread(target=task, daemon=True).start()

    def _on_delete_finished(self, deleted, total):
        self.lbl_action_status.config(text=f"Видалено {deleted} з {total} об'єктів.")
        messagebox.showinfo("Видалення завершено", f"Успішно видалено {deleted} з {total} об'єктів.")
        # Оновлюємо поточну вкладку та загальне сховище
        self.refresh_storage_overview()
        current_tab_idx = self.notebook.index(self.notebook.select())
        if current_tab_idx == 0:
            self.scan_current_folder()
        elif current_tab_idx == 1:
            self.scan_heavy_files()
