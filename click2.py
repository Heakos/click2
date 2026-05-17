import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import pyautogui
import time
import threading
import os
import json
from PIL import ImageGrab, Image, ImageTk
import pygetwindow as gw
import datetime
import logging
from logging.handlers import RotatingFileHandler
import ctypes
import random
import winsound
from collections import deque

# ---------- Opcionális könyvtárak ----------
try:
    import keyboard
    HAS_HOTKEYS = True
except ImportError:
    HAS_HOTKEYS = False

try:
    import pystray
    from pystray import MenuItem as TrayItem
    HAS_TRAY = True
except ImportError:
    HAS_TRAY = False

# ---------- Windows ablakelőtérbe hozás ----------
user32 = ctypes.windll.user32
SW_RESTORE = 9

def ablak_eloterbe_hwnd(hwnd):
    try:
        user32.ShowWindow(hwnd, SW_RESTORE)
        user32.SetForegroundWindow(hwnd)
        return True
    except Exception:
        return False

def ablak_eloterbe_windows(wins):
    try:
        hwnd = wins[0]._hWnd
        return ablak_eloterbe_hwnd(hwnd)
    except Exception:
        return False

# ---------- Naplózás (fájl + UI) ----------
class UILogHandler(logging.Handler):
    def __init__(self, max_lines=500):
        super().__init__()
        self.max_lines = max_lines
        self.buffer = deque(maxlen=max_lines)
        self.widget = None

    def set_widget(self, text_widget):
        self.widget = text_widget

    def emit(self, record):
        msg = self.format(record)
        self.buffer.append(msg)
        if self.widget and self.widget.winfo_exists():
            self.widget.after(0, self._ui_append, msg)

    def _ui_append(self, msg):
        if not self.widget or not self.widget.winfo_exists():
            return
        self.widget.configure(state="normal")
        self.widget.insert("end", msg + "\n")
        line_count = int(self.widget.index("end-1c").split(".")[0])
        if line_count > self.max_lines:
            self.widget.delete("1.0", f"{line_count - self.max_lines + 1}.0")
        self.widget.see("end")
        self.widget.configure(state="disabled")

    def get_lines(self, count=50):
        lines = list(self.buffer)
        return lines[-count:]

# ---------- Konfigurációk ----------
running = False
pause = False
state_lock = threading.Lock()
test_mode = False
SETTINGS_FILE = "beallitasok.json"
LOGFILE = "kattinto_log.txt"
CYCLE_LIMIT = 0  # 0 = határtalan
sikeres_ciklusok = 0

pyautogui.FAILSAFE = True
pyautogui.PAUSE = 0.05

log_formatter = logging.Formatter('%(asctime)s %(levelname)s: %(message)s', datefmt='%Y-%m-%d %H:%M:%S')

file_handler = RotatingFileHandler(LOGFILE, maxBytes=1_048_576, backupCount=3, encoding='utf-8')
file_handler.setFormatter(log_formatter)

ui_handler = UILogHandler()
ui_handler.setFormatter(logging.Formatter('%(asctime)s %(message)s', datefmt='%H:%M:%S'))

root_logger = logging.getLogger()
root_logger.setLevel(logging.INFO)
root_logger.addHandler(file_handler)
root_logger.addHandler(ui_handler)

kattintas_szamlalo = 0
sikeres_kattintasok = 0
sikertelen_kattintasok = 0
thread_obj = None
tray_thread = None
tray_icon = None
hotkey_hooks = []

TARGET_DEFAULT = {
    "enabled": False,
    "path": "",
    "last_region": None,
    "delay_min": 0.3,
    "delay_max": 0.7,
    "jitter": 0,
    "confidence": 0.0,
    "click_type": "",
    "click_button": "",
}

click_targets = []

# ---------- Segédfüggvények: beállítások ----------
def beallitasok_mentese():
    data = {
        "ablaknev": ablaknev.get(),
        "ido": ido_entry.get(),
        "idopont": idopont_entry.get(),
        "idozites_tipus": idozites_tipus.get(),
        "global_click_type": global_click_type.get(),
        "global_click_button": global_click_button.get(),
        "match_mode": match_mode.get(),
        "match_pattern": match_pattern.get(),
        "global_confidence": global_confidence.get(),
        "test_mode": test_mode_var.get(),
        "cycle_limit": cycle_limit_var.get(),
        "click_targets": [
            {
                "enabled": t["enabled"],
                "path": t["path"],
                "delay_min": t["delay_min"],
                "delay_max": t["delay_max"],
                "jitter": t["jitter"],
                "confidence": t["confidence"],
                "click_type": t["click_type"],
                "click_button": t["click_button"],
            } for t in click_targets
        ]
    }
    try:
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logging.exception("Hiba beállítások mentésekor: %s", e)

def beallitasok_betoltese():
    global click_targets
    if os.path.exists(SETTINGS_FILE):
        try:
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if data.get("ablaknev") in ablakcimek:
                ablaknev.set(data["ablaknev"])

            ido_entry.delete(0, tk.END)
            ido_entry.insert(0, data.get("ido", "60"))
            idopont_entry.delete(0, tk.END)
            idopont_entry.insert(0, data.get("idopont", "15:00"))
            idozites_tipus.set(data.get("idozites_tipus", "idoe"))
            global_click_type.set(data.get("global_click_type", "Egyszeres kattintás"))
            global_click_button.set(data.get("global_click_button", "Bal"))
            match_mode.set(data.get("match_mode", "Pontos"))
            match_pattern.set(data.get("match_pattern", ""))
            global_confidence.set(data.get("global_confidence", 0.75))
            test_mode_var.set(data.get("test_mode", False))
            cycle_limit_var.set(data.get("cycle_limit", 0))

            saved_targets = data.get("click_targets", [])
            click_targets.clear()
            if saved_targets:
                for s in saved_targets:
                    t = dict(TARGET_DEFAULT)
                    t.update(s)
                    click_targets.append(t)
            else:
                click_targets.append(dict(TARGET_DEFAULT))
                click_targets[0]["enabled"] = True

            ujratolt_target_ui()
            idozites_mezok_frissit()
        except Exception as e:
            logging.exception("Hiba a beállítások betöltésekor: %s", e)

# ---------- Target UI kezelés ----------
targets_inner = None

def ujratolt_target_ui():
    global targets_inner
    for w in target_frames:
        w.destroy()
    target_frames.clear()
    preview_labels.clear()
    target_ui_vars.clear()

    for w in targets_body.winfo_children():
        w.destroy()

    if not click_targets:
        targets_body.pack_propagate(True)
        ttk.Label(targets_body, text="Nincs célpont. Kattints a '+ Új célpont' gombra.",
            font=("Segoe UI", 9), foreground="#888").pack(pady=20)
        return

    targets_body.configure(height=300)
    targets_body.pack_propagate(False)
    canvas = tk.Canvas(targets_body, borderwidth=0, highlightthickness=0, bg=CARD_BG)
    scrollbar = ttk.Scrollbar(targets_body, orient="vertical", command=canvas.yview)
    canvas.configure(yscrollcommand=scrollbar.set)

    scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
    canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

    targets_inner = ttk.Frame(canvas)
    targets_inner.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
    inner_win = canvas.create_window((0, 0), window=targets_inner, anchor="nw")

    def _resize_inner(event):
        canvas.itemconfig(inner_win, width=event.width - 4)
    canvas.bind("<Configure>", _resize_inner)

    for i, t in enumerate(click_targets):
        hozzaad_target_ui(i)

def hozzaad_target_ui(index):
    container = ttk.LabelFrame(targets_inner, padding=4, style="Target.TLabelframe")
    container.pack(fill=tk.X, pady=1)
    target_frames.append(container)

    vars_dict = {
        "enabled": tk.BooleanVar(value=click_targets[index]["enabled"]),
        "confidence": tk.DoubleVar(value=click_targets[index]["confidence"] or global_confidence.get()),
        "delay_min": tk.DoubleVar(value=click_targets[index]["delay_min"]),
        "delay_max": tk.DoubleVar(value=click_targets[index]["delay_max"]),
        "jitter": tk.IntVar(value=click_targets[index]["jitter"]),
        "click_type": tk.StringVar(value=click_targets[index]["click_type"] or global_click_type.get()),
        "click_button": tk.StringVar(value=click_targets[index]["click_button"] or global_click_button.get()),
    }
    target_ui_vars.append(vars_dict)

    # --- Felül: checkbox, gombok, előnézet, rendezés ---
    top = ttk.Frame(container)
    top.pack(fill=tk.X)

    ttk.Checkbutton(top, text=f"#{index+1}", variable=vars_dict["enabled"],
        command=lambda i=index: target_enabled_changed(i)).pack(side=tk.LEFT, padx=(0, 3))

    ttk.Button(top, text="Kijelölés", style="Small.TButton",
        command=lambda i=index: screenshot_terulet(i), width=8).pack(side=tk.LEFT, padx=1)
    ttk.Button(top, text="Betöltés", style="Small.TButton",
        command=lambda i=index: kep_kivalasztas(i), width=8).pack(side=tk.LEFT, padx=1)

    preview_frame = ttk.Frame(top)
    preview_frame.pack(side=tk.LEFT)
    preview_label = tk.Label(preview_frame, relief="solid", borderwidth=1, bg=CARD_BG)
    preview_labels.append(preview_label)

    ttk.Button(top, text="▲", style="Small.TButton", width=2,
        command=lambda i=index: mozgat_target(i, -1)).pack(side=tk.LEFT, padx=(4, 1))
    ttk.Button(top, text="▼", style="Small.TButton", width=2,
        command=lambda i=index: mozgat_target(i, 1)).pack(side=tk.LEFT, padx=1)
    ttk.Button(top, text="✕", style="Small.TButton", width=2,
        command=lambda i=index: eltavolit_target(i)).pack(side=tk.LEFT, padx=1)

    # --- Alsó sor: paraméterek ---
    bot = ttk.Frame(container)
    bot.pack(fill=tk.X, pady=(2, 0))

    ttk.Label(bot, text="Késleltetés:", font=("Segoe UI", 8)).pack(side=tk.LEFT)
    min_spin = ttk.Spinbox(bot, from_=0.0, to=60.0, increment=0.1,
        textvariable=vars_dict["delay_min"], width=4)
    min_spin.pack(side=tk.LEFT, padx=(1, 0))
    ttk.Label(bot, text="–", font=("Segoe UI", 8)).pack(side=tk.LEFT)
    max_spin = ttk.Spinbox(bot, from_=0.0, to=60.0, increment=0.1,
        textvariable=vars_dict["delay_max"], width=4)
    max_spin.pack(side=tk.LEFT, padx=(1, 2))

    ttk.Label(bot, text="Szórás:", font=("Segoe UI", 8)).pack(side=tk.LEFT)
    ttk.Spinbox(bot, from_=0, to=50, increment=1,
        textvariable=vars_dict["jitter"], width=3).pack(side=tk.LEFT, padx=(1, 3))

    ttk.Label(bot, text="Típus:", font=("Segoe UI", 8)).pack(side=tk.LEFT)
    ttk.Combobox(bot, textvariable=vars_dict["click_type"],
        values=["Globális", "Egyszeres kattintás", "Dupla kattintás"],
        state="readonly", width=18).pack(side=tk.LEFT, padx=(1, 3))

    ttk.Label(bot, text="Gomb:", font=("Segoe UI", 8)).pack(side=tk.LEFT)
    ttk.Combobox(bot, textvariable=vars_dict["click_button"],
        values=["Globális", "Bal", "Jobb", "Középső"],
        state="readonly", width=7).pack(side=tk.LEFT, padx=(1, 3))

    ttk.Label(bot, text="Biztonság:", font=("Segoe UI", 8)).pack(side=tk.LEFT)
    s = ttk.Scale(bot, from_=0.5, to=1.0, variable=vars_dict["confidence"],
        orient=tk.HORIZONTAL, length=70)
    s.pack(side=tk.LEFT, padx=(1, 2))
    conf_txt = tk.StringVar(value=f"{vars_dict['confidence'].get():.2f}")
    def upd_conf(*a, i=index):
        conf_txt.set(f"{target_ui_vars[i]['confidence'].get():.2f}")
    vars_dict["confidence"].trace_add("write", upd_conf)
    ttk.Label(bot, textvariable=conf_txt, width=4, font=("Segoe UI", 8),
        anchor=tk.CENTER).pack(side=tk.LEFT)

    if click_targets[index]["path"] and os.path.isfile(click_targets[index]["path"]):
        mutat_kep_elonezet(index)

    min_spin.bind('<FocusOut>', lambda e, i=index: target_delay_changed(i))
    min_spin.bind('<Return>', lambda e, i=index: target_delay_changed(i))
    max_spin.bind('<FocusOut>', lambda e, i=index: target_delay_changed(i))
    max_spin.bind('<Return>', lambda e, i=index: target_delay_changed(i))

def target_enabled_changed(index):
    click_targets[index]["enabled"] = target_ui_vars[index]["enabled"].get()

def target_delay_changed(index):
    try:
        click_targets[index]["delay_min"] = float(target_ui_vars[index]["delay_min"].get())
        click_targets[index]["delay_max"] = float(target_ui_vars[index]["delay_max"].get())
    except ValueError:
        pass

def target_jitter_changed(index):
    try:
        click_targets[index]["jitter"] = int(target_ui_vars[index]["jitter"].get())
    except ValueError:
        pass

def hozzaad_uj_target():
    click_targets.append(dict(TARGET_DEFAULT))
    hozzaad_target_ui(len(click_targets) - 1)
    beallitasok_mentese()

def eltavolit_target(index):
    if len(click_targets) <= 1:
        messagebox.showwarning("Figyelmeztetés", "Legalább egy célpontnak maradnia kell!")
        return
    click_targets.pop(index)
    ujratolt_target_ui()
    beallitasok_mentese()

def mozgat_target(index, irany):
    new_index = index + irany
    if new_index < 0 or new_index >= len(click_targets):
        return
    click_targets[index], click_targets[new_index] = click_targets[new_index], click_targets[index]
    ujratolt_target_ui()
    beallitasok_mentese()

# ---------- Ablak előtérbe hozatala ----------
def _chars_in_order(pattern, text):
    pat = pattern.lower()
    txt = text.lower()
    i = 0
    for ch in txt:
        if i < len(pat) and ch == pat[i]:
            i += 1
    return i == len(pat)

def ablak_eloterbe_hozasa(ablak_cim):
    try:
        wins = gw.getWindowsWithTitle(ablak_cim)
        if wins:
            ablak_eloterbe_windows(wins)
            logging.info("Ablak előtérbe hozva (pontos): %s", ablak_cim)
            return True

        pattern = match_pattern.get().strip()
        mode = match_mode.get()
        if pattern:
            all_windows = [w for w in gw.getAllWindows() if w.title and w.title.strip()]
            for w in all_windows:
                title = w.title
                title_lower = title.lower()
                pat_lower = pattern.lower()
                matched = False
                if mode == "Tartalmaz":
                    if pat_lower in title_lower:
                        matched = True
                elif mode == "Karakterek sorrendben":
                    if _chars_in_order(pat_lower, title_lower):
                        matched = True
                elif mode == "Pontos":
                    if title == ablak_cim:
                        matched = True
                if matched:
                    ablak_eloterbe_windows([w])
                    ablaknev.set(title)
                    logging.info("Ablak előtérbe hozva (pattern match, mode=%s): %s", mode, title)
                    return True

        logging.warning("Nem található illeszkedő ablak: %s (pattern='%s', mode=%s)", ablak_cim, pattern, mode)
        return False
    except Exception as e:
        logging.exception("Hiba ablak előtérbe hozása közben: %s", e)
        return False

# ---------- GUI frissítés ----------
def frissit_status(szoveg):
    root.after(0, lambda: status_label.config(text=szoveg))

def frissit_statisztika():
    arany = (sikeres_kattintasok / kattintas_szamlalo * 100) if kattintas_szamlalo > 0 else 0
    szoveg = f"Összes: {kattintas_szamlalo}  |  Sikeres: {sikeres_kattintasok}  |  Sikertelen: {sikertelen_kattintasok}  |  Sikeresség: {arany:.1f}%"
    root.after(0, lambda: stat_label.config(text=szoveg))

def mutat_kep_elonezet(index):
    try:
        path = click_targets[index]["path"]
        label = preview_labels[index]
        if os.path.isfile(path):
            img = Image.open(path)
            img.thumbnail((40, 35), Image.Resampling.LANCZOS)
            photo = ImageTk.PhotoImage(img)
            label.config(width=40, height=35, image=photo)
            label.image = photo
            label.pack(padx=(4, 2))
        else:
            label.pack_forget()
            label.config(image='', width=0, height=0)
    except Exception as e:
        logging.warning("Nem sikerült betölteni a kép előnézetet [%d]: %s", index, e)

# ---------- Teszt mód: sárga téglalap a találat helyén ----------
def mutat_teszt_keret(x, y, meret=40):
    try:
        teszt_overlay = tk.Toplevel(root)
        teszt_overlay.attributes("-fullscreen", True)
        teszt_overlay.attributes("-alpha", 0.4)
        teszt_overlay.attributes("-topmost", True)
        teszt_overlay.configure(bg='black')
        teszt_overlay.transient(root)
        c = tk.Canvas(teszt_overlay, bg='black', highlightthickness=0)
        c.pack(fill="both", expand=True)
        c.create_rectangle(
            x - meret, y - meret, x + meret, y + meret,
            outline='yellow', width=4
        )
        c.create_line(x - 15, y, x + 15, y, fill='yellow', width=2)
        c.create_line(x, y - 15, x, y + 15, fill='yellow', width=2)
        root.after(1200, teszt_overlay.destroy)
    except Exception as e:
        logging.warning("Teszt keret megjelenítése nem sikerült: %s", e)

# ---------- Kattintás logika ----------
def valogat_kattintas_tipus(tipus_str):
    if tipus_str in ("Egyszeres kattintás", "Egyszeres"):
        return "Egyszeres"
    elif tipus_str in ("Dupla kattintás", "Dupla"):
        return "Dupla"
    return "Egyszeres"

def valogat_kattintas_gomb(gomb_str):
    m = {"Bal": "left", "Jobb": "right", "Középső": "middle"}
    return m.get(gomb_str, "left")

def vegrehajt_kattintas(pos, katt_tipus, gomb="left"):
    try:
        pyautogui.moveTo(pos[0], pos[1], duration=0.15)
        time.sleep(0.08)
        if katt_tipus == "Dupla":
            pyautogui.doubleClick(pos[0], pos[1], button=gomb)
        else:
            pyautogui.click(pos[0], pos[1], button=gomb)
        return True
    except Exception as e:
        logging.exception("Hiba kattintás végrehajtásakor: %s", e)
        return False

def keres_es_kattint(target_index):
    target = click_targets[target_index]
    path = target["path"]

    if not os.path.isfile(path):
        logging.error("Template fájl nem található [%d]: %s", target_index, path)
        return False

    # Target-specific or global confidence
    target_conf = target_ui_vars[target_index]["confidence"].get()
    conf = global_confidence.get()
    if target["confidence"] != 0.0:
        conf = target_conf
    if conf < 0.5:
        conf = 0.5

    pos = None
    try:
        if target["last_region"]:
            try:
                pos = pyautogui.locateCenterOnScreen(path, region=target["last_region"],
                    confidence=conf, grayscale=True)
            except Exception:
                target["last_region"] = None

        if not pos:
            pos = pyautogui.locateCenterOnScreen(path, confidence=conf, grayscale=True)
            if pos:
                x, y = pos
                target["last_region"] = (max(0, x-100), max(0, y-100), 200, 200)
                logging.info("Új régió mentve [%d]: %s", target_index, target["last_region"])
    except Exception as e:
        logging.exception("Hiba locateCenterOnScreen közben [%d]: %s", target_index, e)
        return False

    if not pos:
        logging.warning("Célpont nem található a képernyőn [%d]: %s", target_index, path)
        return False

    logging.info("Célpont megtalálva [%d] pozíció: %s", target_index, pos)

    # Jitter: random pixel offset
    jitter = target.get("jitter", 0)
    if jitter > 0:
        jx = random.randint(-jitter, jitter)
        jy = random.randint(-jitter, jitter)
        pos = (pos[0] + jx, pos[1] + jy)
        logging.info("Jitter alkalmazva [%d]: (%+d, %+d) → %s", target_index, jx, jy, pos)

    # Test mode: just show marker + move mouse, don't click
    if test_mode:
        pyautogui.moveTo(pos[0], pos[1], duration=0.15)
        root.after(0, lambda: mutat_teszt_keret(int(pos[0]), int(pos[1])))
        logging.info("TESZT: célpont [%d] megtalálva %s (kattintás nélkül)", target_index, pos)
        time.sleep(0.3)
        return True

    # Resolve click type
    katt_tipus = valogat_kattintas_tipus(global_click_type.get())
    target_click_type = target.get("click_type", "")
    if target_click_type and target_click_type != "Globális":
        katt_tipus = valogat_kattintas_tipus(target_click_type)

    # Resolve mouse button
    gomb = valogat_kattintas_gomb(global_click_button.get())
    target_click_button = target.get("click_button", "")
    if target_click_button and target_click_button != "Globális":
        gomb = valogat_kattintas_gomb(target_click_button)

    if vegrehajt_kattintas(pos, katt_tipus, gomb):
        time.sleep(0.2)
        return True
    return False

# ---------- Szekvencia ----------
def paused():
    with state_lock:
        return pause

def vegrehajt_katt_szekvencia():
    global kattintas_szamlalo, sikeres_kattintasok, sikertelen_kattintasok, sikeres_ciklusok

    if paused():
        logging.info("Szekvencia szüneteltetve")
        return

    ablak_cim = ablaknev.get()
    if not ablak_eloterbe_hozasa(ablak_cim):
        logging.warning("Ablak nem hozható előtérbe: %s", ablak_cim)
        sikertelen_kattintasok += 1
        kattintas_szamlalo += 1
        frissit_statisztika()
        root.after(0, lambda: winsound.MessageBeep(winsound.MB_ICONHAND))
        return

    time.sleep(1.2)
    szekvencia_sikeres = True
    enabled_targets = [(i, t) for i, t in enumerate(click_targets) if t["enabled"]]

    for idx, (i, target) in enumerate(enabled_targets):
        if paused():
            return
        frissit_status(f"Célpont {i+1} keresése...")
        if not keres_es_kattint(i):
            szekvencia_sikeres = False
            break
        if idx < len(enabled_targets) - 1:
            delay_min = target.get("delay_min", 0.3)
            delay_max = target.get("delay_max", 0.7)
            if delay_max < delay_min:
                delay_min, delay_max = delay_max, delay_min
            delay = random.uniform(delay_min, delay_max)
            logging.info("Várakozás %.2f mp a következő célpontig...", delay)
            for _ in range(int(delay * 10)):
                if paused():
                    return
                time.sleep(0.1)

    kattintas_szamlalo += 1
    if szekvencia_sikeres:
        sikeres_kattintasok += 1
        sikeres_ciklusok += 1
        logging.info("Teljes szekvencia sikeresen végrehajtva (%d/%d)", sikeres_ciklusok, CYCLE_LIMIT)
        frissit_status("Szekvencia sikeres! Várakozás...")
        root.after(0, lambda: winsound.MessageBeep(winsound.MB_OK))
    else:
        sikertelen_kattintasok += 1
        logging.warning("Szekvencia nem teljesült")
        frissit_status("Szekvencia sikertelen. Várakozás...")
        root.after(0, lambda: winsound.MessageBeep(winsound.MB_ICONHAND))

    frissit_statisztika()

    # Check cycle limit
    if CYCLE_LIMIT > 0 and sikeres_ciklusok >= CYCLE_LIMIT:
        logging.info("Ciklus limit elérve (%d), automatikus leállítás", CYCLE_LIMIT)
        root.after(0, leallitas)

def kattintas_loop(ido_ertek=None):
    idozit_tipus = idozites_tipus.get()
    utolso_katt_datum = None

    if idozit_tipus == "idoe":
        try:
            ido_masodpercben = float(ido_ertek) * 60
        except (ValueError, TypeError):
            logging.error("Hibás időformátum, alapértelmezett 60 perc")
            ido_masodpercben = 60.0 * 60
        kovetkezo_ido = time.time() + ido_masodpercben
        frissit_status("Futás... Várakozás az első ciklusra.")
    elif idozit_tipus == "idop":
        try:
            idopont_str = idopont_entry.get()
            ora, perc = map(int, idopont_str.strip().split(":"))
        except:
            logging.error("Hibás időpont, alapértelmezett 15:00")
            ora, perc = 15, 0
        today = datetime.date.today()
        kovetkezo_alkalom = datetime.datetime.combine(today, datetime.time(hour=ora, minute=perc))
        if datetime.datetime.now() >= kovetkezo_alkalom:
            kovetkezo_alkalom += datetime.timedelta(days=1)
        frissit_status("Futás... Várakozás a megadott időpontra.")
    else:
        return

    while True:
        with state_lock:
            if not running:
                break
            pauselve = pause
        try:
            if pauselve:
                time.sleep(0.3)
                continue
            if idozit_tipus == "idoe":
                most = time.time()
                if most >= kovetkezo_ido:
                    kovetkezo_ido = most + ido_masodpercben
                    vegrehajt_katt_szekvencia()
            else:
                now = datetime.datetime.now()
                if now.hour == ora and now.minute == perc:
                    if utolso_katt_datum != now.date():
                        utolso_katt_datum = now.date()
                        vegrehajt_katt_szekvencia()
            time.sleep(1)
        except Exception as e:
            logging.exception("Hiba a fő loop-ban: %s", e)
            time.sleep(5)

    logging.info("Kattintó leállítva")
    frissit_status("Leállítva")

# ---------- Vezérlés ----------
def inditas():
    global running, thread_obj, CYCLE_LIMIT, sikeres_ciklusok
    with state_lock:
        if running:
            messagebox.showwarning("Figyelem", "A program már fut!")
            return

    enabled_count = sum(1 for t in click_targets if t["enabled"] and t["path"])
    if enabled_count == 0:
        messagebox.showerror("Hiba", "Legalább egy célpontot engedélyezned kell és be kell töltened egy képet!")
        return

    ablak_cim = ablaknev.get()
    if not ablak_cim:
        messagebox.showerror("Hiba", "Válassz ki egy ablakot!")
        return

    idoz_tipus = idozites_tipus.get()
    if idoz_tipus == "idoe":
        try:
            ido_perc = float(ido_entry.get())
            if ido_perc <= 0:
                raise ValueError()
        except ValueError:
            messagebox.showerror("Hiba", "Az időnek pozitív számnak kell lennie!")
            return
        ido_ertek = ido_perc
    else:
        idopont_str = idopont_entry.get()
        try:
            ora, perc = map(int, idopont_str.strip().split(":"))
            assert 0 <= ora < 24 and 0 <= perc < 60
        except:
            messagebox.showerror("Hiba", "Az időpont formátuma hh:mm (pl. 15:34)")
            return
        ido_ertek = None

    try:
        CYCLE_LIMIT = int(cycle_limit_var.get())
    except ValueError:
        CYCLE_LIMIT = 0
    if CYCLE_LIMIT < 0:
        CYCLE_LIMIT = 0
    sikeres_ciklusok = 0

    global test_mode
    test_mode = test_mode_var.get()

    with state_lock:
        running = True
    start_button.config(state="disabled")
    stop_button.config(state="normal")
    pause_button.config(state="normal")
    reset_button.config(state="disabled")
    enable_target_ui(False)

    thread_obj = threading.Thread(target=kattintas_loop, args=(ido_ertek,), daemon=True)
    thread_obj.start()

    if test_mode:
        status_txt = f"TESZT: {enabled_count} célpont minden {ido_entry.get()} percben"
    elif idoz_tipus == "idoe":
        status_txt = f"Kattintás {enabled_count} célpontra minden {ido_entry.get()} percben..."
    else:
        status_txt = f"Kattintás {enabled_count} célpontra minden nap {idopont_entry.get()}-kor..."
    frissit_status(status_txt)
    if test_mode:
        logging.info("Program elindítva. Célpontok: %d, TESZT MÓD", enabled_count)
    else:
        logging.info("Program elindítva. Célpontok: %d", enabled_count)

def leallitas():
    global running
    with state_lock:
        running = False
    start_button.config(state="normal")
    stop_button.config(state="disabled")
    pause_button.config(state="disabled")
    reset_button.config(state="normal")
    enable_target_ui(True)
    frissit_status("Leállítva")
    logging.info("Program leállítva")

def valtas_szunet():
    global pause
    with state_lock:
        pause = not pause
    if pause:
        pause_button.config(text="Folytatás")
        frissit_status("Szüneteltetve")
        logging.info("Program szüneteltetve")
    else:
        pause_button.config(text="Szüneteltetés")
        frissit_status("Folytatva")
        logging.info("Program folytatva")

def enable_target_ui(enabled):
    state = "normal" if enabled else "disabled"
    for container in target_frames:
        for child in container.winfo_children():
            for sub in child.winfo_children():
                if isinstance(sub, (ttk.Button, ttk.Checkbutton, ttk.Combobox, ttk.Spinbox, ttk.Scale)):
                    sub.config(state=state)
    add_target_button.config(state=state)

def kilepes():
    global running
    with state_lock:
        running = False
    beallitasok_mentese()
    if HAS_TRAY and tray_icon:
        tray_icon.stop()
    if HAS_HOTKEYS:
        for h in hotkey_hooks:
            keyboard.remove_hotkey(h)
    root.quit()

def reset_statisztika():
    global kattintas_szamlalo, sikeres_kattintasok, sikertelen_kattintasok, sikeres_ciklusok
    with state_lock:
        kattintas_szamlalo = 0
        sikeres_kattintasok = 0
        sikertelen_kattintasok = 0
        sikeres_ciklusok = 0
    for target in click_targets:
        target["last_region"] = None
    frissit_statisztika()
    logging.info("Statisztikák nullázva")

def kep_kivalasztas(index):
    file = filedialog.askopenfilename(
        filetypes=[("PNG files", "*.png"), ("Minden kép", "*.png;*.jpg;*.jpeg")])
    if file:
        click_targets[index]["path"] = file
        frissit_status(f"Célpont {index+1}: {os.path.basename(file)}")
        mutat_kep_elonezet(index)

def screenshot_terulet(index):
    root.withdraw()
    time.sleep(0.5)
    try:
        screenshot = ImageGrab.grab(all_screens=True)
    except TypeError:
        screenshot = ImageGrab.grab()
    overlay = tk.Toplevel(root)
    overlay.attributes("-fullscreen", True)
    overlay.attributes("-alpha", 0.3)
    overlay.configure(bg='black')

    start_x = start_y = end_x = end_y = 0
    rect_id = None
    canvas = tk.Canvas(overlay, cursor="cross", bg='black', highlightthickness=0)
    canvas.pack(fill="both", expand=True)

    canvas.create_text(overlay.winfo_screenwidth() // 2, 50,
        text=f"Célpont {index+1} - Húzd az egeret a gomb fölé | ESC = Mégse",
        fill="white", font=("Segoe UI", 14, "bold"))

    def on_mouse_down(event):
        nonlocal start_x, start_y, rect_id
        start_x, start_y = event.x, event.y
        rect_id = canvas.create_rectangle(start_x, start_y, start_x, start_y,
            outline='red', width=3)
    def on_mouse_move(event):
        if rect_id:
            canvas.coords(rect_id, start_x, start_y, event.x, event.y)
    def on_mouse_up(event):
        nonlocal end_x, end_y
        end_x, end_y = event.x, event.y
        x1, y1 = min(start_x, end_x), min(start_y, end_y)
        x2, y2 = max(start_x, end_x), max(start_y, end_y)
        if abs(x2 - x1) < 10 or abs(y2 - y1) < 10:
            messagebox.showwarning("Figyelmeztetés", "Túl kicsi terület!")
            overlay.destroy()
            root.deiconify()
            return
        overlay.destroy()
        bbox = (x1, y1, x2, y2)
        cropped = screenshot.crop(bbox)
        filename = f"target_{index + 1}_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
        cropped.save(filename)
        click_targets[index]["path"] = filename
        frissit_status(f"Célpont {index+1} mentve: {filename} ({x2 - x1}x{y2 - y1}px)")
        mutat_kep_elonezet(index)
        root.deiconify()
    def on_escape(event):
        overlay.destroy()
        root.deiconify()

    canvas.bind("<ButtonPress-1>", on_mouse_down)
    canvas.bind("<B1-Motion>", on_mouse_move)
    canvas.bind("<ButtonRelease-1>", on_mouse_up)
    overlay.bind("<Escape>", on_escape)
    root.wait_window(overlay)

def listaz_ablakokat():
    return sorted(set(w.title for w in gw.getAllWindows() if w.title and w.title.strip()))

def frissit_ablaklistat():
    global ablakcimek
    ablakcimek = listaz_ablakokat()
    ablak_combo['values'] = ablakcimek
    if ablakcimek and ablaknev.get() not in ablakcimek:
        ablaknev.set(ablakcimek[0])

def idozites_mezok_frissit():
    if idozites_tipus.get() == "idoe":
        ido_entry.config(state="normal")
        idopont_entry.config(state="disabled")
    else:
        ido_entry.config(state="disabled")
        idopont_entry.config(state="normal")

# ---------- Globális gyorsbillentyűk ----------
def init_hotkeys():
    if not HAS_HOTKEYS:
        return
    try:
        h = keyboard.add_hotkey('ctrl+shift+s',
            lambda: root.after(0, inditas) if not running else None)
        hotkey_hooks.append(h)
        h = keyboard.add_hotkey('ctrl+shift+x',
            lambda: root.after(0, leallitas) if running else None)
        hotkey_hooks.append(h)
        h = keyboard.add_hotkey('ctrl+shift+p',
            lambda: root.after(0, valtas_szunet) if running else None)
        hotkey_hooks.append(h)
        logging.info("Globális gyorsbillentyűk: Ctrl+Shift+S indítás, Ctrl+Shift+X leállítás, Ctrl+Shift+P szünet")
    except Exception as e:
        logging.warning("Gyorsbillentyűk regisztrálása nem sikerült: %s", e)

# ---------- Rendszertálca ----------
def init_tray():
    global tray_icon, tray_thread
    if not HAS_TRAY:
        return

    def on_show():
        root.after(0, lambda: root.deiconify())
    def on_start():
        root.after(0, inditas)
    def on_stop():
        root.after(0, leallitas)
    def on_exit():
        root.after(0, kilepes)

    try:
        from PIL import ImageDraw
        img = Image.new('RGB', (64, 64), color='darkblue')
        draw = ImageDraw.Draw(img)
        draw.ellipse((12, 12, 52, 52), fill='yellow')
        draw.rectangle((28, 20, 36, 44), fill='darkblue')
    except Exception:
        img = Image.new('RGB', (64, 64), color='darkblue')

    menu = (
        TrayItem('Megjelenítés', on_show, default=True),
        TrayItem('Indítás', on_start),
        TrayItem('Leállítás', on_stop),
        TrayItem('Kilépés', on_exit),
    )
    tray_icon = pystray.Icon("click2", img, "Automatikus Gombkattintó", menu)
    tray_thread = threading.Thread(target=tray_icon.run, daemon=True)
    tray_thread.start()
    logging.info("Rendszertálca ikon elindítva")

# ---------- GUI felépítés ----------
root = tk.Tk()
try:
    root.iconbitmap("click_icon.ico")
except Exception:
    pass
root.title("Automatikus Gombkattintó")
root.geometry("800x950+200+50")
root.minsize(820, 880)

# ---------- Modern stílus ----------
STYLE = ttk.Style()
available_themes = STYLE.theme_names()
for t in ("clam", "vista", "alt", "default"):
    if t in available_themes:
        STYLE.theme_use(t)
        break

BG = "#f5f5f5"
FG = "#1a1a2e"
ACCENT = "#005a9e"
ACCENT_LIGHT = "#e6f2fb"
CARD_BG = "#ffffff"
CARD_BORDER = "#d0d0d0"
SUCCESS = "#2e7d32"
DANGER = "#c62828"
WARNING_ = "#f9a825"

root.configure(bg=BG)

STYLE.configure("TLabelFrame", background=BG, foreground=FG, font=("Segoe UI", 9, "bold"))
STYLE.configure("TLabelFrame.Label", background=BG, foreground=FG, font=("Segoe UI", 9, "bold"))
STYLE.configure("TLabel", background=BG, foreground=FG, font=("Segoe UI", 9))
STYLE.configure("TFrame", background=BG)
STYLE.configure("TButton", font=("Segoe UI", 9), padding=(6, 3))
STYLE.configure("Accent.TButton", font=("Segoe UI", 9, "bold"), foreground="#ffffff",
    background=ACCENT, padding=(10, 5))
STYLE.map("Accent.TButton",
    background=[("active", "#004578"), ("disabled", "#a0a0a0")])
STYLE.configure("Stop.TButton", font=("Segoe UI", 9, "bold"), foreground="#ffffff",
    background=DANGER, padding=(10, 5))
STYLE.map("Stop.TButton",
    background=[("active", "#9e0000"), ("disabled", "#a0a0a0")])
STYLE.configure("Pause.TButton", font=("Segoe UI", 9, "bold"), padding=(10, 5))
STYLE.configure("Small.TButton", font=("Segoe UI", 8), padding=(2, 1))
STYLE.configure("Target.TLabelframe", background=CARD_BG, foreground=FG,
    font=("Segoe UI", 8), relief="solid", borderwidth=1)
STYLE.configure("Target.TLabelframe.Label", background=CARD_BG, foreground=FG,
    font=("Segoe UI", 7))
STYLE.configure("Status.TLabel", background=BG, foreground=FG, font=("Segoe UI", 10))
STYLE.configure("Stat.TLabel", background=BG, foreground=FG, font=("Segoe UI", 9, "bold"))
STYLE.configure("Log.TFrame", background=CARD_BG)
STYLE.configure("Header.TLabel", background=BG, foreground=FG, font=("Segoe UI", 9, "bold"))
STYLE.configure("TCheckbutton", background=BG, font=("Segoe UI", 9))
STYLE.configure("TRadiobutton", background=BG, font=("Segoe UI", 9))
STYLE.configure("TScale", background=BG)
STYLE.configure("TSpinbox", font=("Segoe UI", 9))
STYLE.configure("TCombobox", font=("Segoe UI", 9))
STYLE.configure("Card.TFrame", background=CARD_BG, relief="solid", borderwidth=1)

ablaknev = tk.StringVar()
ablakcimek = listaz_ablakokat()
if ablakcimek:
    ablaknev.set(ablakcimek[0])

idozites_tipus = tk.StringVar(value="idoe")
global_click_type = tk.StringVar(value="Egyszeres kattintás")
global_click_button = tk.StringVar(value="Bal")
match_mode = tk.StringVar(value="Pontos")
match_pattern = tk.StringVar()
global_confidence = tk.DoubleVar(value=0.75)
test_mode_var = tk.BooleanVar(value=False)
cycle_limit_var = tk.StringVar(value="0")

target_frames = []
preview_labels = []
target_ui_vars = []

# ========== CÉLALAK ==========
ablak_group = ttk.LabelFrame(root, text=" Célablak ", padding=8)
ablak_group.pack(fill=tk.X, padx=10, pady=(6, 2))

ablak_frame = ttk.Frame(ablak_group)
ablak_frame.pack(fill=tk.X, pady=(0, 4))
ablak_combo = ttk.Combobox(ablak_frame, textvariable=ablaknev, values=ablakcimek, state="readonly")
ablak_combo.pack(side=tk.LEFT, fill=tk.X, expand=True)
ttk.Button(ablak_frame, text="Frissítés", command=frissit_ablaklistat, width=9).pack(side=tk.LEFT, padx=(5, 0))

match_row = ttk.Frame(ablak_group)
match_row.pack(fill=tk.X)
ttk.Label(match_row, text="Mód:", font=("Segoe UI", 8)).pack(side=tk.LEFT)
match_combo = ttk.Combobox(match_row, textvariable=match_mode,
    values=["Pontos", "Tartalmaz", "Karakterek sorrendben"],
    state="readonly", width=16)
match_combo.pack(side=tk.LEFT, padx=(3, 8))
ttk.Label(match_row, text="Minta:", font=("Segoe UI", 8)).pack(side=tk.LEFT)
match_entry = ttk.Entry(match_row, textvariable=match_pattern)
match_entry.pack(side=tk.LEFT, fill=tk.X, expand=True)

# ========== BEÁLLÍTÁSOK ==========
settings_group = ttk.LabelFrame(root, text=" Kattintás beállítások ", padding=8)
settings_group.pack(fill=tk.X, padx=10, pady=2)

idoz_group = ttk.Frame(settings_group)
idoz_group.pack(fill=tk.X, pady=(0, 4))
tk.Radiobutton(idoz_group, text="Időközönként", variable=idozites_tipus,
    value="idoe", command=idozites_mezok_frissit).pack(side=tk.LEFT)
tk.Radiobutton(idoz_group, text="Megadott időpontban", variable=idozites_tipus,
    value="idop", command=idozites_mezok_frissit).pack(side=tk.LEFT, padx=(12, 0))

row1 = ttk.Frame(settings_group)
row1.pack(fill=tk.X, pady=1)
ttk.Label(row1, text="Időköz:").pack(side=tk.LEFT)
ido_entry = ttk.Entry(row1, width=7)
ido_entry.insert(0, "60")
ido_entry.pack(side=tk.LEFT, padx=(3, 6))
ttk.Label(row1, text="Időpont:").pack(side=tk.LEFT)
idopont_entry = ttk.Entry(row1, width=7)
idopont_entry.insert(0, "15:00")
idopont_entry.pack(side=tk.LEFT, padx=(3, 6))
idopont_entry.config(state="disabled")

click_row = ttk.Frame(settings_group)
click_row.pack(fill=tk.X, pady=1)
ttk.Label(click_row, text="Típus:").pack(side=tk.LEFT)
ttk.Combobox(click_row, textvariable=global_click_type,
    values=["Egyszeres kattintás", "Dupla kattintás"], state="readonly", width=18).pack(side=tk.LEFT, padx=(3, 10))
ttk.Label(click_row, text="Gomb:").pack(side=tk.LEFT)
ttk.Combobox(click_row, textvariable=global_click_button,
    values=["Bal", "Jobb", "Középső"], state="readonly", width=10).pack(side=tk.LEFT, padx=(3, 0))

row2 = ttk.Frame(settings_group)
row2.pack(fill=tk.X, pady=1)
ttk.Label(row2, text="Biztosság:").pack(side=tk.LEFT)
ttk.Scale(row2, from_=0.5, to=1.0, variable=global_confidence,
    orient=tk.HORIZONTAL, length=100).pack(side=tk.LEFT, padx=(3, 2))
conf_label_val = tk.StringVar(value="0.75")
def frissit_glob_conf(*a):
    conf_label_val.set(f"{global_confidence.get():.2f}")
global_confidence.trace_add("write", frissit_glob_conf)
ttk.Label(row2, textvariable=conf_label_val, width=4).pack(side=tk.LEFT)
ttk.Checkbutton(row2, text="Teszt mód", variable=test_mode_var).pack(side=tk.LEFT, padx=(12, 0))
ttk.Label(row2, text="Limit:").pack(side=tk.LEFT, padx=(12, 3))
ttk.Spinbox(row2, from_=0, to=99999, textvariable=cycle_limit_var, width=5).pack(side=tk.LEFT)

# ========== CÉLPONTOK ==========
targets_container = ttk.LabelFrame(root, text=" Kattintási célpontok (sorrend szerint) ", padding=4)
targets_container.pack(fill=tk.X, pady=2, padx=10)

targets_body = ttk.Frame(targets_container)
targets_body.pack(fill=tk.X, pady=2)

# ========== VEZÉRLÉS ==========
control_group = ttk.LabelFrame(root, text=" Vezérlés ", padding=10)
control_group.pack(fill=tk.X, padx=10, pady=2)

btn_row = ttk.Frame(control_group)
btn_row.pack(fill=tk.X, pady=(0, 4))

start_button = ttk.Button(btn_row, text="  Indítás  ", command=inditas, style="Accent.TButton")
start_button.pack(side=tk.LEFT, expand=True, fill=tk.X, padx=(0, 3))

pause_button = ttk.Button(btn_row, text="  Szüneteltetés  ", command=valtas_szunet, state="disabled", style="Pause.TButton")
pause_button.pack(side=tk.LEFT, expand=True, fill=tk.X, padx=(3, 0))

stop_button = ttk.Button(btn_row, text="  Leállítás  ", command=leallitas, state="disabled", style="Stop.TButton")
stop_button.pack(side=tk.LEFT, expand=True, fill=tk.X, padx=(3, 0))

btn_row2 = ttk.Frame(control_group)
btn_row2.pack(fill=tk.X)
add_target_button = ttk.Button(btn_row2, text="+ Új célpont", command=hozzaad_uj_target)
add_target_button.pack(side=tk.LEFT, padx=(0, 5))
reset_button = ttk.Button(btn_row2, text="Statisztika nullázása", command=reset_statisztika)
reset_button.pack(side=tk.LEFT)

# ========== NAPLÓ ==========
log_group = ttk.LabelFrame(root, text=" Napló ", padding=5)
log_group.pack(fill=tk.X, padx=10, pady=2)

log_frame = ttk.Frame(log_group)
log_frame.pack(fill=tk.X, expand=True)

log_text = tk.Text(log_frame, height=4, state="disabled", font=("Consolas", 9),
    wrap=tk.WORD, bg=CARD_BG, fg=FG, insertbackground=FG,
    relief="solid", borderwidth=1, padx=4, pady=2)
log_scroll = ttk.Scrollbar(log_frame, orient="vertical", command=log_text.yview)
log_text.configure(yscrollcommand=log_scroll.set)
log_text.pack(side=tk.LEFT, fill=tk.X, expand=True)
log_scroll.pack(side=tk.RIGHT, fill=tk.Y)
ui_handler.set_widget(log_text)

# ========== STÁTUSZ ==========
status_group = ttk.LabelFrame(root, text=" Státusz ", padding=6)
status_group.pack(fill=tk.X, padx=10, pady=2)

status_label = ttk.Label(status_group, text="Készen áll", style="Status.TLabel", wraplength=740)
status_label.pack(pady=(0, 3))
stat_label = ttk.Label(status_group,
    text="Összes: 0  |  Sikeres: 0  |  Sikertelen: 0  |  Sikeresség: 0%",
    style="Stat.TLabel")
stat_label.pack()

# ========== FOOTER ==========
footer_frame = ttk.Frame(root)
footer_frame.pack(fill=tk.X, padx=10, pady=(2, 6))
ttk.Label(footer_frame, text="Ctrl+Shift+S Indítás | Ctrl+Shift+X Leállítás | Ctrl+Shift+P Szünet | Egér bal felső = Vészleállítás",
    foreground="#888888", font=("Segoe UI", 8)).pack()
tk.Button(footer_frame, text="Kilépés", command=kilepes, width=12,
    bg="#e0e0e0", activebackground="#d0d0d0", relief="flat", cursor="hand2").pack(pady=(4, 0))

# ========== BETOLTES / INDITAS ==========
ujratolt_target_ui()
root.after(100, idozites_mezok_frissit)
beallitasok_betoltese()

# Initialize optional features
root.after(500, lambda: threading.Thread(target=init_hotkeys, daemon=True).start())
root.after(1000, lambda: threading.Thread(target=init_tray, daemon=True).start())

root.mainloop()
