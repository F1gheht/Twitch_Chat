# -*- coding: utf-8 -*-
"""
Twitch Chat Overlay
Чат Twitch поверх всех окон. Окно прозрачное, клики проходят сквозь него,
фокус у игры/другой программы не отбирается.

Управление — через значок в трее (правый клик / двойной клик).
Зависимости: pip install PyQt5
"""
import sys
import os
import json
import time
import random
import socket
import threading

from PyQt5 import QtCore, QtGui, QtWidgets
from PyQt5.QtCore import Qt

IS_WIN = sys.platform == "win32"
APP_NAME = "TwitchChatOverlay"
APP_VERSION = "1.0.0"

# ----------------------------------------------------------------------------
# Пути
# ----------------------------------------------------------------------------
# Ресурсы (иконка): рядом со скриптом или внутри exe, собранного PyInstaller
RES_DIR = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
ICON_PATH = os.path.join(RES_DIR, "assets", "icon.ico")

# Настройки: %APPDATA%\TwitchChatOverlay (в Program Files писать нельзя)
if IS_WIN and os.environ.get("APPDATA"):
    CONFIG_DIR = os.path.join(os.environ["APPDATA"], APP_NAME)
else:
    CONFIG_DIR = os.path.join(os.path.expanduser("~"), ".config", APP_NAME)
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.json")

DEFAULTS = {
    "channel": "",
    "lifetime": 15,          # сколько секунд сообщение видно (0 = не исчезает)
    "fade": 1.5,             # длительность плавного исчезновения, сек
    "font_family": "Segoe UI",
    "font_size": 16,
    "bold": True,
    "outline": 3,            # толщина обводки, px
    "shadow": True,
    "text_color": "#FFFFFF",
    "max_messages": 30,
    "spacing": 4,            # расстояние между сообщениями, px
    "x": 50, "y": 200, "w": 420, "h": 500,
}


def load_config():
    cfg = dict(DEFAULTS)
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            cfg.update(json.load(f))
    except Exception:
        pass
    return cfg


def save_config(cfg):
    try:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print("Не удалось сохранить настройки:", e)


# ----------------------------------------------------------------------------
# WinAPI: клики насквозь + поверх всех окон
# ----------------------------------------------------------------------------
if IS_WIN:
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    GWL_EXSTYLE = -20
    WS_EX_TRANSPARENT = 0x00000020
    WS_EX_TOOLWINDOW = 0x00000080
    WS_EX_LAYERED = 0x00080000
    WS_EX_NOACTIVATE = 0x08000000
    HWND_TOPMOST = wintypes.HWND(-1)
    SWP_NOSIZE, SWP_NOMOVE, SWP_NOACTIVATE = 0x0001, 0x0002, 0x0010

    user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.GetWindowLongW.restype = ctypes.c_long
    user32.SetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_long]
    user32.SetWindowLongW.restype = ctypes.c_long
    user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                    ctypes.c_int, ctypes.c_int, ctypes.c_uint]

    def win_set_click_through(hwnd, enabled):
        style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        style |= WS_EX_LAYERED | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE
        if enabled:
            style |= WS_EX_TRANSPARENT
        else:
            style &= ~WS_EX_TRANSPARENT
        user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style)

    def win_force_topmost(hwnd):
        user32.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0,
                            SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)


# ----------------------------------------------------------------------------
# Подключение к чату Twitch (анонимно, только чтение)
# ----------------------------------------------------------------------------
class TwitchClient(QtCore.QObject):
    message = QtCore.pyqtSignal(str, str, str)   # ник, цвет, текст
    status = QtCore.pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self._stop = threading.Event()
        self.channel = ""

    def start(self, channel):
        self.stop()
        self.channel = channel.strip().lstrip("#").lower()
        if not self.channel:
            self.status.emit("Канал не указан")
            return
        self._stop = threading.Event()
        threading.Thread(target=self._run, args=(self.channel, self._stop), daemon=True).start()

    def stop(self):
        self._stop.set()

    def _run(self, channel, stop):
        while not stop.is_set():
            try:
                self.status.emit(f"Подключение к #{channel}…")
                with socket.create_connection(("irc.chat.twitch.tv", 6667), timeout=10) as s:
                    s.settimeout(1.0)
                    nick = f"justinfan{random.randint(10000, 99999)}"
                    s.sendall(b"CAP REQ :twitch.tv/tags twitch.tv/commands\r\n")
                    s.sendall(f"NICK {nick}\r\nJOIN #{channel}\r\n".encode())
                    buf = b""
                    last_data = time.time()
                    pinged = False
                    while not stop.is_set():
                        try:
                            data = s.recv(4096)
                        except socket.timeout:
                            idle = time.time() - last_data
                            if idle > 240 and not pinged:
                                s.sendall(b"PING :tmi.twitch.tv\r\n")
                                pinged = True
                            elif idle > 330:
                                raise ConnectionError("сервер не отвечает")
                            continue
                        if not data:
                            raise ConnectionError("соединение закрыто")
                        last_data = time.time()
                        pinged = False
                        buf += data
                        while b"\r\n" in buf:
                            line, buf = buf.split(b"\r\n", 1)
                            self._handle(s, line.decode("utf-8", "replace"), channel)
            except Exception as e:
                if stop.is_set():
                    break
                self.status.emit(f"Нет связи ({e}). Повтор через 5 с…")
                stop.wait(5)

    def _handle(self, s, line, channel):
        if line.startswith("PING"):
            s.sendall(("PONG" + line[4:] + "\r\n").encode())
            return
        tags = {}
        if line.startswith("@"):
            raw, _, line = line[1:].partition(" ")
            for kv in raw.split(";"):
                k, _, v = kv.partition("=")
                tags[k] = v
        parts = line.split(" ", 3)
        if len(parts) < 2:
            return
        cmd = parts[1]
        if cmd == "PRIVMSG" and len(parts) >= 4:
            nick = parts[0][1:].split("!")[0]
            text = parts[3][1:] if parts[3].startswith(":") else parts[3]
            if text.startswith("\x01ACTION ") and text.endswith("\x01"):
                text = text[8:-1]  # /me
            name = (tags.get("display-name") or nick).replace("\\s", " ")
            color = tags.get("color") or ""
            self.message.emit(name, color, text)
        elif cmd == "ROOMSTATE":
            self.status.emit(f"Подключено к #{channel}")
        elif cmd == "NOTICE" and len(parts) >= 4:
            self.status.emit("Twitch: " + parts[3].lstrip(":"))


# ----------------------------------------------------------------------------
# Оверлей
# ----------------------------------------------------------------------------
NAME_FALLBACK_COLORS = ["#FF4A4A", "#4AA8FF", "#4AFF7A", "#FF9F40", "#C77DFF",
                        "#FF6EC7", "#40E0D0", "#FFD84A", "#9ACD32", "#FF7F50"]


def readable_name_color(hex_color, name):
    """Цвет ника, который хорошо видно с чёрной обводкой."""
    c = QtGui.QColor(hex_color) if hex_color else QtGui.QColor()
    if not c.isValid():
        c = QtGui.QColor(NAME_FALLBACK_COLORS[sum(map(ord, name)) % len(NAME_FALLBACK_COLORS)])
    if c.lightness() < 130:   # тёмные ники (синий, тёмно-зелёный и т.п.) осветляем
        c = QtGui.QColor.fromHsl(max(c.hslHue(), 0), c.hslSaturation(), 150)
    return c


class Overlay(QtWidgets.QWidget):
    geometry_saved = QtCore.pyqtSignal()

    def __init__(self, cfg):
        flags = (Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
                 | Qt.WindowDoesNotAcceptFocus)
        if not IS_WIN:
            flags |= Qt.WindowTransparentForInput
        super().__init__(None, flags)
        self.cfg = cfg
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WA_NoSystemBackground)
        self.setGeometry(cfg["x"], cfg["y"], cfg["w"], cfg["h"])

        self.messages = []
        self.edit_mode = False
        self._drag = None
        self._style_key = None
        self._topmost_counter = 0

        self.timer = QtCore.QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.start(33)

    # --- служебное -------------------------------------------------------
    def showEvent(self, e):
        super().showEvent(e)
        self.set_click_through(not self.edit_mode)

    def set_click_through(self, enabled):
        if IS_WIN:
            win_set_click_through(int(self.winId()), enabled)
            win_force_topmost(int(self.winId()))
        else:
            self.setWindowFlag(Qt.WindowTransparentForInput, enabled)
            self.show()

    def set_edit_mode(self, on):
        self.edit_mode = on
        self.set_click_through(not on)
        if not on:
            g = self.geometry()
            self.cfg.update(x=g.x(), y=g.y(), w=g.width(), h=g.height())
            self.geometry_saved.emit()
            self._style_key = None
        self.update()

    def apply_style(self):
        self._style_key = None   # сбросить кэш раскладки
        while len(self.messages) > self.cfg["max_messages"]:
            self.messages.pop(0)
        self.update()

    def add_message(self, name, color, text):
        self.messages.append({"name": name, "color": color, "text": text,
                              "t": time.time(), "cache": None})
        while len(self.messages) > self.cfg["max_messages"]:
            self.messages.pop(0)
        self.update()

    def _alpha(self, msg, now):
        life = float(self.cfg["lifetime"])
        if life <= 0:
            return 1.0
        age = now - msg["t"]
        if age <= life:
            return 1.0
        fade = max(float(self.cfg["fade"]), 0.01)
        return max(0.0, 1.0 - (age - life) / fade)

    def _tick(self):
        now = time.time()
        before = len(self.messages)
        self.messages = [m for m in self.messages if self._alpha(m, now) > 0]
        fading = any(self._alpha(m, now) < 1 for m in self.messages)
        if fading or before != len(self.messages):
            self.update()
        # некоторые игры перехватывают «поверх всех» — возвращаем каждые ~2 с
        self._topmost_counter += 1
        if IS_WIN and self._topmost_counter >= 60:
            self._topmost_counter = 0
            win_force_topmost(int(self.winId()))

    # --- раскладка текста ------------------------------------------------
    def _font(self):
        f = QtGui.QFont(self.cfg["font_family"])
        f.setPixelSize(int(self.cfg["font_size"]))
        f.setBold(bool(self.cfg["bold"]))
        f.setHintingPreference(QtGui.QFont.PreferNoHinting)
        return f

    def _layout(self, msg, font, fm, max_w):
        """Переносит сообщение по строкам. Возвращает список строк,
        строка = список (QPainterPath, QColor) с координатами от базовой линии 0."""
        name_color = readable_name_color(msg["color"], msg["name"])
        text_color = QtGui.QColor(self.cfg["text_color"])

        words = [(msg["name"], name_color), (": ", text_color)]
        for i, w in enumerate(msg["text"].split(" ")):
            words.append(((" " if i else "") + w, text_color))

        lines, cur, x = [], [], 0.0
        for word, color in words:
            w = fm.horizontalAdvance(word)
            if x + w > max_w and cur:
                lines.append(cur)
                cur, x = [], 0.0
                word = word.lstrip(" ")
                w = fm.horizontalAdvance(word)
            # слишком длинное слово — режем по символам
            while w > max_w and len(word) > 1:
                cut = len(word)
                while cut > 1 and fm.horizontalAdvance(word[:cut]) > max_w - x:
                    cut -= 1
                cur.append((x, word[:cut], color))
                lines.append(cur)
                cur, x = [], 0.0
                word = word[cut:]
                w = fm.horizontalAdvance(word)
            if word:
                cur.append((x, word, color))
                x += w
        if cur:
            lines.append(cur)

        result = []
        for line in lines:
            segs = []
            for sx, s, color in line:
                path = QtGui.QPainterPath()
                path.addText(QtCore.QPointF(sx, 0), font, s)
                segs.append((path, color))
            result.append(segs)
        return result

    # --- отрисовка -------------------------------------------------------
    def paintEvent(self, e):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        p.setRenderHint(QtGui.QPainter.TextAntialiasing)
        p.setCompositionMode(QtGui.QPainter.CompositionMode_Source)
        p.fillRect(self.rect(), Qt.transparent)
        p.setCompositionMode(QtGui.QPainter.CompositionMode_SourceOver)

        if self.edit_mode:
            p.fillRect(self.rect(), QtGui.QColor(20, 20, 30, 150))
            pen = QtGui.QPen(QtGui.QColor(145, 70, 255), 2, Qt.DashLine)
            p.setPen(pen)
            p.drawRect(self.rect().adjusted(1, 1, -2, -2))
            p.setPen(Qt.white)
            p.drawText(self.rect().adjusted(10, 10, -10, -10), Qt.AlignTop | Qt.AlignHCenter | Qt.TextWordWrap,
                       "Перетащите окно мышью.\nУгол справа внизу — изменить размер.\n"
                       "Двойной клик — готово.")
            g = 18
            r = self.rect()
            p.fillRect(r.right() - g, r.bottom() - g, g, g, QtGui.QColor(145, 70, 255))

        font = self._font()
        fm = QtGui.QFontMetricsF(font)
        pad = 8
        max_w = max(50, self.width() - 2 * pad - self.cfg["outline"] * 2)
        line_h = fm.lineSpacing()
        key = (font.key(), max_w, self.cfg["text_color"])
        if key != self._style_key:
            self._style_key = key
            for m in self.messages:
                m["cache"] = None

        ow = float(self.cfg["outline"])
        outline_pen = QtGui.QPen(QtGui.QColor(0, 0, 0, 235), ow * 2,
                                 Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
        shadow_color = QtGui.QColor(0, 0, 0, 150)
        shadow_off = max(2.0, ow * 0.8)

        now = time.time()
        y = self.height() - pad - fm.descent()  # базовая линия последней строки
        for msg in reversed(self.messages):
            a = self._alpha(msg, now)
            if a <= 0:
                continue
            if msg["cache"] is None:
                msg["cache"] = self._layout(msg, font, fm, max_w)
            lines = msg["cache"]
            top_baseline = y - (len(lines) - 1) * line_h
            if top_baseline - fm.ascent() < 0:
                break
            p.setOpacity(a)
            for i, segs in enumerate(lines):
                p.save()
                p.translate(pad + ow, top_baseline + i * line_h)
                if self.cfg["shadow"]:
                    p.save()
                    p.translate(shadow_off, shadow_off)
                    for path, _ in segs:
                        if ow > 0:
                            p.strokePath(path, QtGui.QPen(shadow_color, ow * 2, Qt.SolidLine,
                                                          Qt.RoundCap, Qt.RoundJoin))
                        p.fillPath(path, shadow_color)
                    p.restore()
                if ow > 0:
                    for path, _ in segs:
                        p.strokePath(path, outline_pen)
                for path, color in segs:
                    p.fillPath(path, color)
                p.restore()
            y = top_baseline - line_h - self.cfg["spacing"]
        p.end()

    # --- перемещение / размер в режиме настройки ---------------------------
    def mousePressEvent(self, e):
        if not self.edit_mode or e.button() != Qt.LeftButton:
            return
        corner = e.pos().x() > self.width() - 24 and e.pos().y() > self.height() - 24
        self._drag = ("resize" if corner else "move", e.globalPos(), self.geometry())

    def mouseMoveEvent(self, e):
        if not self._drag:
            return
        mode, start, geo = self._drag
        d = e.globalPos() - start
        if mode == "move":
            self.move(geo.topLeft() + d)
        else:
            self.resize(max(200, geo.width() + d.x()), max(120, geo.height() + d.y()))

    def mouseReleaseEvent(self, e):
        self._drag = None

    def mouseDoubleClickEvent(self, e):
        if self.edit_mode:
            self.set_edit_mode(False)


# ----------------------------------------------------------------------------
# Окно настроек
# ----------------------------------------------------------------------------
class SettingsDialog(QtWidgets.QWidget):
    applied = QtCore.pyqtSignal()
    edit_toggled = QtCore.pyqtSignal(bool)
    test_requested = QtCore.pyqtSignal()

    def __init__(self, cfg):
        super().__init__(None, Qt.Window)
        self.cfg = cfg
        self.setWindowTitle(f"Twitch Chat Overlay {APP_VERSION} — настройки")
        self.setMinimumWidth(380)

        form = QtWidgets.QFormLayout()

        self.channel = QtWidgets.QLineEdit(cfg["channel"])
        self.channel.setPlaceholderText("например: f1gheht")
        form.addRow("Канал Twitch:", self.channel)

        self.lifetime = QtWidgets.QDoubleSpinBox()
        self.lifetime.setRange(0, 3600); self.lifetime.setDecimals(1); self.lifetime.setSuffix(" с")
        self.lifetime.setSpecialValueText("не исчезать")
        self.lifetime.setValue(cfg["lifetime"])
        form.addRow("Время показа сообщения:", self.lifetime)

        self.fade = QtWidgets.QDoubleSpinBox()
        self.fade.setRange(0, 10); self.fade.setDecimals(1); self.fade.setSuffix(" с")
        self.fade.setValue(cfg["fade"])
        form.addRow("Плавное исчезновение:", self.fade)

        self.font_family = QtWidgets.QFontComboBox()
        self.font_family.setCurrentFont(QtGui.QFont(cfg["font_family"]))
        form.addRow("Шрифт:", self.font_family)

        self.font_size = QtWidgets.QSpinBox()
        self.font_size.setRange(8, 72); self.font_size.setSuffix(" px")
        self.font_size.setValue(cfg["font_size"])
        form.addRow("Размер шрифта:", self.font_size)

        self.bold = QtWidgets.QCheckBox("Жирный")
        self.bold.setChecked(cfg["bold"])
        form.addRow("", self.bold)

        self.outline = QtWidgets.QSpinBox()
        self.outline.setRange(0, 10); self.outline.setSuffix(" px")
        self.outline.setValue(cfg["outline"])
        form.addRow("Обводка текста:", self.outline)

        self.shadow = QtWidgets.QCheckBox("Тень")
        self.shadow.setChecked(cfg["shadow"])
        form.addRow("", self.shadow)

        self.color_btn = QtWidgets.QPushButton()
        self._text_color = cfg["text_color"]
        self._update_color_btn()
        self.color_btn.clicked.connect(self._pick_color)
        form.addRow("Цвет текста:", self.color_btn)

        self.max_messages = QtWidgets.QSpinBox()
        self.max_messages.setRange(1, 200)
        self.max_messages.setValue(cfg["max_messages"])
        form.addRow("Макс. сообщений:", self.max_messages)

        self.spacing = QtWidgets.QSpinBox()
        self.spacing.setRange(0, 40); self.spacing.setSuffix(" px")
        self.spacing.setValue(cfg["spacing"])
        form.addRow("Отступ между сообщениями:", self.spacing)

        self.status = QtWidgets.QLabel("—")
        self.status.setWordWrap(True)
        form.addRow("Статус:", self.status)

        self.edit_btn = QtWidgets.QPushButton("Переместить / изменить размер чата")
        self.edit_btn.setCheckable(True)
        self.edit_btn.toggled.connect(self.edit_toggled.emit)

        test_btn = QtWidgets.QPushButton("Тестовое сообщение")
        test_btn.clicked.connect(self.test_requested.emit)

        apply_btn = QtWidgets.QPushButton("Применить и сохранить")
        apply_btn.setDefault(True)
        apply_btn.clicked.connect(self._apply)

        lay = QtWidgets.QVBoxLayout(self)
        lay.addLayout(form)
        lay.addWidget(self.edit_btn)
        row = QtWidgets.QHBoxLayout()
        row.addWidget(test_btn)
        row.addWidget(apply_btn)
        lay.addLayout(row)
        hint = QtWidgets.QLabel("Окно можно закрыть — оверлей продолжит работать.\n"
                                "Настройки и выход — через значок в трее.")
        hint.setStyleSheet("color: gray;")
        lay.addWidget(hint)

    def _update_color_btn(self):
        self.color_btn.setText(self._text_color)
        self.color_btn.setStyleSheet(f"background:{self._text_color}; color:black;")

    def _pick_color(self):
        c = QtWidgets.QColorDialog.getColor(QtGui.QColor(self._text_color), self, "Цвет текста")
        if c.isValid():
            self._text_color = c.name()
            self._update_color_btn()

    def _apply(self):
        self.cfg.update(
            channel=self.channel.text().strip().lstrip("#"),
            lifetime=self.lifetime.value(),
            fade=self.fade.value(),
            font_family=self.font_family.currentFont().family(),
            font_size=self.font_size.value(),
            bold=self.bold.isChecked(),
            outline=self.outline.value(),
            shadow=self.shadow.isChecked(),
            text_color=self._text_color,
            max_messages=self.max_messages.value(),
            spacing=self.spacing.value(),
        )
        self.applied.emit()

    def set_status(self, text):
        self.status.setText(text)

    def set_edit_checked(self, on):
        self.edit_btn.blockSignals(True)
        self.edit_btn.setChecked(on)
        self.edit_btn.blockSignals(False)


# ----------------------------------------------------------------------------
# Приложение
# ----------------------------------------------------------------------------
def make_icon():
    if os.path.exists(ICON_PATH):
        return QtGui.QIcon(ICON_PATH)
    pm = QtGui.QPixmap(64, 64)
    pm.fill(Qt.transparent)
    p = QtGui.QPainter(pm)
    p.setRenderHint(QtGui.QPainter.Antialiasing)
    p.setBrush(QtGui.QColor(145, 70, 255))
    p.setPen(Qt.NoPen)
    p.drawRoundedRect(4, 8, 56, 40, 10, 10)
    p.drawPolygon(QtGui.QPolygon([QtCore.QPoint(16, 46), QtCore.QPoint(30, 46), QtCore.QPoint(16, 60)]))
    p.setBrush(Qt.white)
    for x in (18, 32, 46):
        p.drawEllipse(QtCore.QPoint(x, 28), 4, 4)
    p.end()
    return QtGui.QIcon(pm)


class App(QtCore.QObject):
    def __init__(self, qapp):
        super().__init__()
        self.qapp = qapp
        self.cfg = load_config()

        self.overlay = Overlay(self.cfg)
        self.settings = SettingsDialog(self.cfg)
        self.client = TwitchClient()

        icon = make_icon()
        qapp.setWindowIcon(icon)

        self.client.message.connect(self.overlay.add_message)
        self.client.status.connect(self._on_status)
        self.settings.applied.connect(self._apply)
        self.settings.edit_toggled.connect(self._set_edit)
        self.settings.test_requested.connect(self._test)
        self.overlay.geometry_saved.connect(lambda: save_config(self.cfg))
        self.overlay.geometry_saved.connect(lambda: self.settings.set_edit_checked(False))

        # трей
        self.tray = QtWidgets.QSystemTrayIcon(icon)
        self.tray.setToolTip("Twitch Chat Overlay")
        menu = QtWidgets.QMenu()
        menu.addAction("Настройки", self._show_settings)
        self.act_edit = menu.addAction("Переместить чат")
        self.act_edit.setCheckable(True)
        self.act_edit.toggled.connect(self._set_edit)
        self.act_visible = menu.addAction("Показывать чат")
        self.act_visible.setCheckable(True)
        self.act_visible.setChecked(True)
        self.act_visible.toggled.connect(self.overlay.setVisible)
        menu.addAction("Очистить чат", self._clear)
        menu.addSeparator()
        menu.addAction("Выход", self._quit)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(
            lambda r: self._show_settings() if r == QtWidgets.QSystemTrayIcon.DoubleClick else None)
        self.menu = menu
        self.tray.show()

        self.overlay.show()
        if self.cfg["channel"]:
            self.client.start(self.cfg["channel"])
        else:
            self._show_settings()

    def _on_status(self, text):
        self.settings.set_status(text)
        self.tray.setToolTip("Twitch Chat Overlay — " + text)

    def _apply(self):
        save_config(self.cfg)
        self.overlay.apply_style()
        if self.cfg["channel"].lower() != self.client.channel:
            self.overlay.messages.clear()
            self.client.start(self.cfg["channel"])

    def _set_edit(self, on):
        if on and not self.overlay.isVisible():
            self.act_visible.setChecked(True)
        self.overlay.set_edit_mode(on)
        self.settings.set_edit_checked(on)
        self.act_edit.blockSignals(True)
        self.act_edit.setChecked(on)
        self.act_edit.blockSignals(False)

    def _test(self):
        self.overlay.add_message(random.choice(["Тестер", "Viewer_42", "Гость"]), "",
                                 "Это тестовое сообщение, чтобы проверить, как выглядит чат поверх окна 👋")

    def _clear(self):
        self.overlay.messages.clear()
        self.overlay.update()

    def _show_settings(self):
        self.settings.show()
        self.settings.raise_()
        self.settings.activateWindow()

    def _quit(self):
        g = self.overlay.geometry()
        self.cfg.update(x=g.x(), y=g.y(), w=g.width(), h=g.height())
        save_config(self.cfg)
        self.client.stop()
        self.tray.hide()
        self.qapp.quit()


def main():
    if IS_WIN:
        # своя иконка на панели задач вместо значка Python
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_NAME)
        except Exception:
            pass
    QtWidgets.QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QtWidgets.QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
    qapp = QtWidgets.QApplication(sys.argv)
    qapp.setQuitOnLastWindowClosed(False)
    _app = App(qapp)  # noqa: F841
    sys.exit(qapp.exec_())


if __name__ == "__main__":
    main()
