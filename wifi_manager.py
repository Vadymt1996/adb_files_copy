import json
import os
import re
import subprocess
import threading
import tkinter as tk
from tkinter import messagebox, ttk

CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "wifi_history.json")


def load_wifi_history():
    """Завантаження історії раніше використаних IP-адрес"""
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data.get("history", [])
        except Exception:
            pass
    return []


def save_wifi_ip(ip):
    """Збереження IP-адреси в історію"""
    if not ip or not ip.strip():
        return
    ip = ip.strip()
    history = load_wifi_history()
    if ip in history:
        history.remove(ip)
    history.insert(0, ip)
    history = history[:10]  # зберігаємо до 10 останніх IP
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump({"history": history}, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def execute_adb_command(cmd_args, device_id=None):
    """Універсальне виконання команди ADB з приховуванням вікна консолі"""
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
            timeout=15
        )
        return res.returncode, res.stdout.strip(), res.stderr.strip()
    except subprocess.TimeoutExpired:
        return -1, "", "Час очікування відповіді ADB вичерпано (Timeout)"
    except FileNotFoundError:
        return -1, "", "ADB не знайдено в системі PATH"
    except Exception as e:
        return -1, "", str(e)


def get_phone_wifi_ip(device_id):
    """Автоматичне визначення локальної Wi-Fi IP-адреси підключеного смартфона"""
    # Спосіб 1: ip route
    ret, out, _ = execute_adb_command(["shell", "ip route 2>/dev/null"], device_id)
    if ret == 0 and out:
        m = re.search(r'src\s+(\d+\.\d+\.\d+\.\d+)', out)
        if m:
            return m.group(1)

    # Спосіб 2: ip addr show wlan0
    ret, out, _ = execute_adb_command(["shell", "ip -f inet addr show wlan0 2>/dev/null"], device_id)
    if ret == 0 and out:
        m = re.search(r'inet\s+(\d+\.\d+\.\d+\.\d+)', out)
        if m:
            return m.group(1)

    # Спосіб 3: getprop dhcp.wlan0.ipaddress
    ret, out, _ = execute_adb_command(["shell", "getprop dhcp.wlan0.ipaddress 2>/dev/null"], device_id)
    if ret == 0 and out.strip() and re.match(r'^\d+\.\d+\.\d+\.\d+$', out.strip()):
        return out.strip()

    # Спосіб 4: ifconfig wlan0
    ret, out, _ = execute_adb_command(["shell", "ifconfig wlan0 2>/dev/null"], device_id)
    if ret == 0 and out:
        m = re.search(r'inet\s+(?:addr:)?(\d+\.\d+\.\d+\.\d+)', out)
        if m:
            return m.group(1)

    return None


class WiFiManagerDialog(tk.Toplevel):
    """Діалогове вікно бездротового підключення смартфона через Wi-Fi"""

    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
        self.title("📶 Бездротове підключення ADB (Wi-Fi)")
        self.geometry("620x480")
        self.minsize(560, 420)
        self.transient(parent)
        self.grab_set()

        # Центрування вікна
        self.update_idletasks()
        try:
            px = parent.winfo_rootx() + (parent.winfo_width() // 2) - 310
            py = parent.winfo_rooty() + (parent.winfo_height() // 2) - 240
            self.geometry(f"+{max(50, px)}+{max(50, py)}")
        except Exception:
            pass

        self.history = load_wifi_history()
        self.last_detected_ip = ""

        self._build_ui()

    def _build_ui(self):
        # Шапка діалогу
        header = tk.Frame(self, bg="#E8F5E9", padx=12, pady=8, bd=1, relief="groove")
        header.pack(fill="x", padx=10, pady=(10, 6))

        tk.Label(
            header,
            text="📶 Бездротове підключення смартфона через Wi-Fi",
            font=("Arial", 11, "bold"),
            bg="#E8F5E9",
            fg="#1B5E20"
        ).pack(anchor="w")

        tk.Label(
            header,
            text="Дозволяє переглядати та копіювати файли без USB-кабелю в одній мережі Wi-Fi.",
            font=("Arial", 9),
            bg="#E8F5E9",
            fg="#37474F"
        ).pack(anchor="w")

        # Вкладки режимів підключення
        self.nb = ttk.Notebook(self)
        self.nb.pack(fill="both", expand=True, padx=10, pady=4)

        self.tab_switch = ttk.Frame(self.nb, padding=10)
        self.tab_direct = ttk.Frame(self.nb, padding=10)
        self.tab_pair = ttk.Frame(self.nb, padding=10)

        self.nb.add(self.tab_switch, text="⚡ Швидке перемикання з USB")
        self.nb.add(self.tab_direct, text="🔌 Пряме підключення за IP")
        self.nb.add(self.tab_pair, text="🔒 Бездротове сполучення (Android 11+)")

        self._build_tab_switch()
        self._build_tab_direct()
        self._build_tab_pair()

        # Нижній статус та кнопка закриття
        bottom_bar = tk.Frame(self, padx=10, pady=6)
        bottom_bar.pack(fill="x")

        self.lbl_status = tk.Label(
            bottom_bar,
            text="Оберіть режим та виконайте підключення.",
            font=("Arial", 9),
            fg="#555",
            anchor="w"
        )
        self.lbl_status.pack(side="left", fill="x", expand=True)

        tk.Button(
            bottom_bar,
            text="Закрити",
            font=("Arial", 9),
            width=10,
            command=self.destroy
        ).pack(side="right")

    # --- Вкладка 1: Швидке перемикання з USB на Wi-Fi ---

    def _build_tab_switch(self):
        f = self.tab_switch

        info_box = tk.LabelFrame(f, text="Поточний USB-пристрій", font=("Arial", 9, "bold"), padx=10, pady=6)
        info_box.pack(fill="x", pady=(0, 10))

        curr_id = getattr(self.app, "current_device_id", None)
        model_name = getattr(self.app, "device_model", "")
        is_usb = curr_id and ":" not in curr_id

        if is_usb:
            dev_text = f"🟢 Знайдено: {model_name or 'Смартфон'} (ID: {curr_id})"
            dev_fg = "#1B5E20"
        elif curr_id and ":" in curr_id:
            dev_text = f"📶 Пристрій уже підключений через Wi-Fi ({curr_id})"
            dev_fg = "#0D47A1"
        else:
            dev_text = "🔴 USB-смартфон не виявлено. Під'єднайте кабель з увімкненим USB-налагодженням."
            dev_fg = "#C62828"

        self.lbl_usb_device = tk.Label(info_box, text=dev_text, font=("Arial", 9, "bold"), fg=dev_fg, anchor="w")
        self.lbl_usb_device.pack(fill="x")

        # Крок 1: Увімкнення tcpip 5555 та зчитування IP
        step1_box = tk.Frame(f)
        step1_box.pack(fill="x", pady=4)

        self.btn_step1 = tk.Button(
            step1_box,
            text="1️⃣ Увімкнути Wi-Fi на телефоні (adb tcpip 5555)",
            font=("Arial", 9, "bold"),
            bg="#1976D2",
            fg="white",
            padx=10,
            pady=4,
            command=self.action_enable_tcpip
        )
        self.btn_step1.pack(side="left", fill="x", expand=True)

        # Результат визначення IP
        ip_frame = tk.Frame(f, pady=6)
        ip_frame.pack(fill="x")

        tk.Label(ip_frame, text="Визначена IP-адреса телефона:", font=("Arial", 9, "bold")).pack(side="left", padx=(0, 6))
        self.ent_switch_ip = tk.Entry(ip_frame, font=("Consolas", 10), width=18)
        self.ent_switch_ip.pack(side="left")

        # Підказка про кабель
        self.lbl_cable_tip = tk.Label(
            f,
            text="💡 Після успішного виконання кроку 1 ви можете ВІД'ЄДНАТИ USB-кабель від комп'ютера.",
            font=("Arial", 8, "italic"),
            fg="#555",
            anchor="w"
        )
        self.lbl_cable_tip.pack(fill="x", pady=(2, 10))

        # Крок 2: Підключення по Wi-Fi
        step2_box = tk.Frame(f)
        step2_box.pack(fill="x", pady=4)

        self.btn_step2 = tk.Button(
            step2_box,
            text="2️⃣ Підключитися без кабелю (adb connect)",
            font=("Arial", 9, "bold"),
            bg="#2E7D32",
            fg="white",
            padx=10,
            pady=4,
            command=self.action_connect_switch
        )
        self.btn_step2.pack(side="left", fill="x", expand=True)

    # --- Вкладка 2: Пряме підключення за IP ---

    def _build_tab_direct(self):
        f = self.tab_direct

        tk.Label(
            f,
            text="Введіть локальну IP-адресу телефона (телефон має бути в тій самій мережі Wi-Fi):",
            font=("Arial", 9),
            anchor="w"
        ).pack(fill="x", pady=(0, 6))

        row1 = tk.Frame(f)
        row1.pack(fill="x", pady=4)

        tk.Label(row1, text="IP-адреса:", font=("Arial", 9, "bold"), width=12, anchor="w").pack(side="left")

        default_ip = self.history[0] if self.history else "192.168.1."
        self.cb_direct_ip = ttk.Combobox(row1, values=self.history, font=("Consolas", 10), width=22)
        self.cb_direct_ip.set(default_ip)
        self.cb_direct_ip.pack(side="left", padx=(0, 8))

        tk.Label(row1, text="Порт:", font=("Arial", 9, "bold")).pack(side="left", padx=(0, 4))
        self.ent_direct_port = tk.Entry(row1, font=("Consolas", 10), width=7)
        self.ent_direct_port.insert(0, "5555")
        self.ent_direct_port.pack(side="left")

        # Кнопки дій
        btn_box = tk.Frame(f, pady=10)
        btn_box.pack(fill="x")

        self.btn_direct_connect = tk.Button(
            btn_box,
            text="📶 Підключитися",
            font=("Arial", 9, "bold"),
            bg="#2E7D32",
            fg="white",
            padx=12,
            pady=4,
            command=self.action_direct_connect
        )
        self.btn_direct_connect.pack(side="left", padx=(0, 8))

        self.btn_disconnect = tk.Button(
            btn_box,
            text="🔌 Відключити всі Wi-Fi з'єднання",
            font=("Arial", 9),
            padx=10,
            pady=4,
            command=self.action_disconnect_all
        )
        self.btn_disconnect.pack(side="left")

        # Довідка
        help_frame = tk.LabelFrame(f, text="Як дізнатися IP-адресу на телефоні", font=("Arial", 8, "bold"), padx=8, pady=6)
        help_frame.pack(fill="x", pady=(12, 0))

        help_text = (
            "1. Відкрийте Налаштування телефону ➔ Wi-Fi ➔ натисніть на поточну мережу (або шестерню).\n"
            "2. У властивостях мережі знайдіть пункт «IP-адреса» (зазвичай 192.168.x.x).\n"
            "3. Переконайтеся, що комп'ютер та телефон підключені до одного роутера."
        )
        tk.Label(help_frame, text=help_text, font=("Arial", 8), fg="#555", justify="left").pack(anchor="w")

    # --- Вкладка 3: Бездротове сполучення (Android 11+) ---

    def _build_tab_pair(self):
        f = self.tab_pair

        tk.Label(
            f,
            text="Підключення без USB через меню «Бездротове налагодження» (Wireless Debugging):",
            font=("Arial", 9),
            anchor="w"
        ).pack(fill="x", pady=(0, 4))

        # Покроковий опис
        guide = (
            "1. На телефоні: Налаштування ➔ Для розробників ➔ «Бездротове налагодження» (увімкнути).\n"
            "2. Натисніть «Підключити пристрій за допомогою коду підключення».\n"
            "3. Введіть IP, порт та 6-значний код із віконця на телефоні:"
        )
        tk.Label(f, text=guide, font=("Arial", 8), fg="#333", justify="left").pack(anchor="w", pady=(0, 6))

        # Поля введення для Pair
        pair_box = tk.LabelFrame(f, text="Крок 1: Сполучення (Pairing)", font=("Arial", 8, "bold"), padx=8, pady=6)
        pair_box.pack(fill="x", pady=(0, 8))

        r1 = tk.Frame(pair_box)
        r1.pack(fill="x", pady=2)
        tk.Label(r1, text="IP:Порт сполучення:", font=("Arial", 8, "bold"), width=18, anchor="w").pack(side="left")
        self.ent_pair_host = tk.Entry(r1, font=("Consolas", 9), width=22)
        self.ent_pair_host.insert(0, "192.168.1.100:37851")
        self.ent_pair_host.pack(side="left", padx=(0, 8))

        tk.Label(r1, text="Код (6 цифр):", font=("Arial", 8, "bold")).pack(side="left", padx=(0, 4))
        self.ent_pair_code = tk.Entry(r1, font=("Consolas", 9), width=8)
        self.ent_pair_code.pack(side="left")

        self.btn_pair = tk.Button(
            pair_box,
            text="🔒 1. Сполучити пристрій (adb pair)",
            font=("Arial", 8, "bold"),
            bg="#1976D2",
            fg="white",
            command=self.action_pair_device
        )
        self.btn_pair.pack(anchor="e", pady=(4, 0))

        # Крок 2: Підключення до основного порту
        conn_box = tk.LabelFrame(f, text="Крок 2: Підключення (Connect)", font=("Arial", 8, "bold"), padx=8, pady=6)
        conn_box.pack(fill="x")

        r2 = tk.Frame(conn_box)
        r2.pack(fill="x", pady=2)
        tk.Label(r2, text="Основний IP:Порт:", font=("Arial", 8, "bold"), width=18, anchor="w").pack(side="left")
        self.ent_connect_host = tk.Entry(r2, font=("Consolas", 9), width=22)
        self.ent_connect_host.insert(0, "192.168.1.100:41253")
        self.ent_connect_host.pack(side="left", padx=(0, 8))

        tk.Label(r2, text="(вказаний на екрані бездротового налагодження)", font=("Arial", 8, "italic"), fg="#666").pack(side="left")

        self.btn_pair_connect = tk.Button(
            conn_box,
            text="📶 2. Підключитися (adb connect)",
            font=("Arial", 8, "bold"),
            bg="#2E7D32",
            fg="white",
            command=self.action_connect_after_pair
        )
        self.btn_pair_connect.pack(anchor="e", pady=(4, 0))

    # --- Логіка дій ---

    def set_status(self, text, is_error=False, is_success=False):
        color = "#C62828" if is_error else ("#1B5E20" if is_success else "#444")
        self.lbl_status.config(text=text, fg=color)

    def action_enable_tcpip(self):
        """Крок 1: adb tcpip 5555 та зчитування IP-адреси смартфона"""
        curr_id = getattr(self.app, "current_device_id", None)
        if not curr_id:
            messagebox.showwarning("Пристрій не знайдено", "Під'єднайте телефон по USB та увімкніть USB-налагодження!")
            return

        self.btn_step1.config(state="disabled")
        self.set_status("🔄 Переведення смартфона в TCP/IP режим (порт 5555)...")

        def task():
            ret, out, err = execute_adb_command(["tcpip", "5555"], curr_id)
            if ret != 0:
                self.after(0, lambda: (
                    self.btn_step1.config(state="normal"),
                    self.set_status(f"Помилка adb tcpip: {err or out}", is_error=True),
                    messagebox.showerror("Помилка", f"Не вдалося перевести телефон у режим tcpip:\n{err or out}")
                ))
                return

            # Дізнаємося IP адресу
            ip = get_phone_wifi_ip(curr_id)
            self.last_detected_ip = ip or ""

            def finish():
                self.btn_step1.config(state="normal")
                if ip:
                    self.ent_switch_ip.delete(0, tk.END)
                    self.ent_switch_ip.insert(0, ip)
                    save_wifi_ip(ip)
                    self.set_status(f"✅ Режим Wi-Fi увімкнено! IP: {ip}. Тепер можна від'єднати кабель.", is_success=True)
                    messagebox.showinfo(
                        "Успіх",
                        f"Режим Wi-Fi (порт 5555) успішно увімкнено!\n\n"
                        f"Визначена IP-адреса: {ip}\n\n"
                        f"Тепер ви можете від'єднати USB-кабель від комп'ютера та натиснути кнопку «2. Підключитися»."
                    )
                else:
                    self.set_status("⚠️ Wi-Fi режим активовано, але не вдалося авто-визначити IP. Введіть IP вручну.", is_error=True)
                    messagebox.showwarning(
                        "Увага",
                        "Порт 5555 активовано, але телефон не повідомив свій IP.\n"
                        "Подивіться IP-адресу у властивостях Wi-Fi на телефоні та введіть її у поле."
                    )

            self.after(0, finish)

        threading.Thread(target=task, daemon=True).start()

    def action_connect_switch(self):
        """Крок 2: adb connect <IP>:5555"""
        ip = self.ent_switch_ip.get().strip()
        if not ip:
            messagebox.showwarning("Введіть IP", "Вкажіть або визначте IP-адресу телефона!")
            return
        self._connect_to_target(f"{ip}:5555", ip)

    def action_direct_connect(self):
        """Пряме підключення за IP та портом"""
        ip = self.cb_direct_ip.get().strip()
        port = self.ent_direct_port.get().strip() or "5555"

        if not ip:
            messagebox.showwarning("Введіть IP", "Вкажіть IP-адресу телефона!")
            return

        target = f"{ip}:{port}" if ":" not in ip else ip
        self._connect_to_target(target, ip)

    def _connect_to_target(self, target, save_ip_val=None):
        """Спільна функція виклику adb connect"""
        self.set_status(f"🔄 Підключення до {target}...")

        def task():
            ret, out, err = execute_adb_command(["connect", target])
            output = (out + " " + err).strip()

            def finish():
                if "connected to" in output.lower() or "already connected" in output.lower():
                    if save_ip_val:
                        save_wifi_ip(save_ip_val)
                    self.set_status(f"✅ Успішно підключено до {target}!", is_success=True)
                    messagebox.showinfo("Успіх", f"Смартфон успішно підключено по Wi-Fi:\n{target}")
                    # Оновлюємо стан пристроїв у головному вікні
                    self.app.manual_device_check()
                    self.destroy()
                else:
                    err_msg = output or "Не вдалося з'єднатися з пристроєм"
                    self.set_status(f"❌ Помилка підключення: {err_msg}", is_error=True)
                    messagebox.showerror(
                        "Помилка підключення",
                        f"Не вдалося підключитися до {target}:\n\n{err_msg}\n\n"
                        f"Поради:\n"
                        f"1. Переконайтеся, що телефон та ПК підключені до ОДНІЄЇ мережі Wi-Fi.\n"
                        f"2. Якщо це перше підключення — спочатку виконайте крок 1 (adb tcpip 5555) з підключеним кабелем."
                    )

            self.after(0, finish)

        threading.Thread(target=task, daemon=True).start()

    def action_disconnect_all(self):
        """Відключення всіх бездротових ADB з'єднань"""
        def task():
            ret, out, err = execute_adb_command(["disconnect"])
            self.after(0, lambda: (
                self.set_status("🔌 Всі Wi-Fi з'єднання відключено."),
                messagebox.showinfo("Відключено", f"Результат:\n{out or err or 'Відключено'}"),
                self.app.manual_device_check()
            ))
        threading.Thread(target=task, daemon=True).start()

    def action_pair_device(self):
        """Сполучення за кодом adb pair"""
        host = self.ent_pair_host.get().strip()
        code = self.ent_pair_code.get().strip()

        if not host or not code:
            messagebox.showwarning("Заповніть поля", "Вкажіть IP:Порт сполучення та 6-значний код!")
            return

        self.btn_pair.config(state="disabled")
        self.set_status(f"🔄 Сполучення з {host}...")

        def task():
            ret, out, err = execute_adb_command(["pair", host, code])
            output = (out + " " + err).strip()

            def finish():
                self.btn_pair.config(state="normal")
                if ret == 0 and ("successfully paired" in output.lower() or "paired" in output.lower()):
                    self.set_status(f"✅ Сполучення успішне! Тепер натисніть крок 2 (Підключитися).", is_success=True)
                    messagebox.showinfo("Успіх", f"Пристрій успішно сполучено!\n\nТепер введіть основний порт та натисніть «Підключитися».")
                else:
                    self.set_status(f"❌ Помилка сполучення: {output}", is_error=True)
                    messagebox.showerror("Помилка сполучення", f"Не вдалося виконати сполучення:\n{output}")

            self.after(0, finish)

        threading.Thread(target=task, daemon=True).start()

    def action_connect_after_pair(self):
        """Підключення після сполучення adb connect"""
        host = self.ent_connect_host.get().strip()
        if not host:
            messagebox.showwarning("Введіть адресу", "Вкажіть основний IP:Порт підключення!")
            return
        ip = host.split(":")[0] if ":" in host else host
        self._connect_to_target(host, ip)
