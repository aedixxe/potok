"""Плавающий оверлей записи на PySide6: acrylic-блюр + барная волна.

Безрамочное окно поверх всех окон у нижнего края экрана: матовое стекло (Windows
acrylic) и ряд синих скруглённых капсул-баров, реагирующих на уровень микрофона
(выше в центре, мельче к краям). Появляется при записи, гаснет в простое.

Под Windows:
- acrylic blur-behind + нативное скругление Win11 (DWM) — вид как у системных флайаутов;
- `WS_EX_NOACTIVATE` — окно НЕ ворует фокус, поэтому вставка текста не ломается.

Уровень и состояние читаются из приложения через колбэки. QApplication.exec()
держит главный поток (трей при этом работает в фоновом, см. main.py).
"""

from __future__ import annotations

import contextlib
import ctypes
import logging
import math
import sys
from collections.abc import Callable
from ctypes import POINTER, Structure, byref, c_int, c_size_t, sizeof

from PySide6.QtCore import QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QApplication, QLabel, QVBoxLayout, QWidget

logger = logging.getLogger("ptt.overlay")

# --- внешний вид (подобрано под системную громкость) ---
N_BARS = 21
BAR_W = 4
BAR_GAP = 4
PAD = 12
EQ_H = 28
BAR_COLOR = QColor(95, 140, 255)  # яркий синий
TINT = QColor(28, 28, 32, 224)  # плотная тёмная подложка (читаемо на любом фоне)
MARGIN_BOTTOM = 100

_EQ_W = N_BARS * BAR_W + (N_BARS - 1) * BAR_GAP
_W = _EQ_W + 2 * PAD
_H = EQ_H + 2 * PAD


# --- Windows acrylic + no-activate + нативное скругление ---
class _ACCENT_POLICY(Structure):
    _fields_ = [
        ("AccentState", c_int),
        ("AccentFlags", c_int),
        ("GradientColor", c_int),
        ("AnimationId", c_int),
    ]


class _WINCOMPATTRDATA(Structure):
    _fields_ = [("Attribute", c_int), ("Data", POINTER(_ACCENT_POLICY)), ("SizeOfData", c_size_t)]


def _apply_native(hwnd: int, no_activate: bool = True) -> None:
    if sys.platform != "win32":
        return
    user32 = ctypes.windll.user32
    accent = _ACCENT_POLICY()
    accent.AccentState = 4  # ACCENT_ENABLE_ACRYLICBLURBEHIND
    accent.GradientColor = 0x01000000
    data = _WINCOMPATTRDATA()
    data.Attribute = 19  # WCA_ACCENT_POLICY
    data.Data = ctypes.pointer(accent)
    data.SizeOfData = sizeof(accent)
    user32.SetWindowCompositionAttribute(hwnd, byref(data))

    # no_activate=False для окна захвата хоткея — ему нужен фокус, чтобы ловить клавиши
    if no_activate:
        GWL_EXSTYLE, WS_EX_NOACTIVATE, WS_EX_TOOLWINDOW = -20, 0x08000000, 0x00000080
        ex = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        user32.SetWindowLongW(hwnd, GWL_EXSTYLE, ex | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW)

    dwmapi = ctypes.windll.dwmapi
    # нативное скругление Windows 11 (гладкое, как флайауты)
    DWMWA_WINDOW_CORNER_PREFERENCE, DWMWCP_ROUND = 33, 2
    pref = c_int(DWMWCP_ROUND)
    dwmapi.DwmSetWindowAttribute(hwnd, DWMWA_WINDOW_CORNER_PREFERENCE, byref(pref), sizeof(pref))
    # убрать системную тень окна (non-client rendering off)
    DWMWA_NCRENDERING_POLICY, DWMNCRP_DISABLED = 2, 1
    ncrp = c_int(DWMNCRP_DISABLED)
    dwmapi.DwmSetWindowAttribute(hwnd, DWMWA_NCRENDERING_POLICY, byref(ncrp), sizeof(ncrp))


def _qt_key_name(key: int) -> str | None:
    """Qt-код клавиши → имя в формате нашего hotkey.parse_hotkey (совместимо с pynput)."""
    if Qt.Key_A <= key <= Qt.Key_Z:
        return chr(key).lower()
    if Qt.Key_0 <= key <= Qt.Key_9:
        return chr(key)
    if Qt.Key_F1 <= key <= Qt.Key_F35:
        return f"f{key - Qt.Key_F1 + 1}"
    specials = {
        Qt.Key_Space: "space",
        Qt.Key_Return: "enter",
        Qt.Key_Enter: "enter",
        Qt.Key_Tab: "tab",
        Qt.Key_Backspace: "backspace",
        Qt.Key_Insert: "insert",
        Qt.Key_Delete: "delete",
        Qt.Key_Home: "home",
        Qt.Key_End: "end",
        Qt.Key_PageUp: "page_up",
        Qt.Key_PageDown: "page_down",
        Qt.Key_Up: "up",
        Qt.Key_Down: "down",
        Qt.Key_Left: "left",
        Qt.Key_Right: "right",
        Qt.Key_Pause: "pause",
    }
    return specials.get(key)


def _mods_list(modifiers) -> list[str]:
    out = []
    if modifiers & Qt.ControlModifier:
        out.append("ctrl")
    if modifiers & Qt.AltModifier:
        out.append("alt")
    if modifiers & Qt.ShiftModifier:
        out.append("shift")
    if modifiers & Qt.MetaModifier:
        out.append("cmd")
    return out


class _HotkeyCaptureDialog(QWidget):
    """Окошко захвата хоткея. Создаётся ОДИН раз и переиспользуется через arm()/hide()
    — без постоянного create/destroy, который вешал Qt event loop при acrylic-окне.
    """

    def __init__(self) -> None:
        super().__init__()
        self._on_done: Callable[[str | None], None] | None = None
        self._done = True  # пока не armed — события игнорируем
        self._native_done = False
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Dialog)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.resize(380, 130)

        self._label = QLabel("Нажмите нужную комбинацию клавиш\n\nEsc — отмена", self)
        self._label.setAlignment(Qt.AlignCenter)
        self._label.setStyleSheet("color:#E6E8F0; font-family:'Segoe UI'; font-size:14px;")
        lay = QVBoxLayout(self)
        lay.addWidget(self._label)

    def arm(self, on_done: Callable[[str | None], None]) -> None:
        """Подготовить и показать окно для нового захвата (переиспользуем виджет)."""
        self._on_done = on_done
        self._done = False
        self._label.setText("Нажмите нужную комбинацию клавиш\n\nEsc — отмена")
        scr = QApplication.primaryScreen().geometry()
        self.move((scr.width() - 380) // 2, (scr.height() - 130) // 2)
        self.show()
        self.activateWindow()
        self.raise_()
        self.setFocus()

    def showEvent(self, e) -> None:
        super().showEvent(e)
        if not self._native_done:
            with contextlib.suppress(Exception):
                _apply_native(int(self.winId()), no_activate=False)
            self._native_done = True

    def paintEvent(self, e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setPen(Qt.NoPen)
        p.setBrush(TINT)
        p.drawRect(QRectF(0, 0, self.width(), self.height()))

    def keyPressEvent(self, e) -> None:
        if self._done:
            return
        key = e.key()
        if key == Qt.Key_Escape:
            self._finish(None)
            return
        if key in (Qt.Key_Control, Qt.Key_Shift, Qt.Key_Alt, Qt.Key_Meta):
            mods = _mods_list(e.modifiers())
            self._label.setText((" + ".join(mods) + " + …") if mods else "…")
            return
        # numpad-клавиши кодируем по виртуальному коду (отличить от обычных цифр)
        if e.modifiers() & Qt.KeypadModifier:
            name = f"vk{e.nativeVirtualKey()}"
        else:
            name = _qt_key_name(key)
        if not name:
            return
        spec = "+".join([*_mods_list(e.modifiers()), name])
        self._finish(spec)

    def _finish(self, spec: str | None) -> None:
        if self._done:
            return
        self._done = True
        cb = self._on_done
        self._on_done = None
        self.hide()  # переиспользуем окно — прячем, не уничтожаем
        if cb is not None:
            cb(spec)


class _Bars(QWidget):
    def __init__(
        self,
        state_getter: Callable[[], str],
        level_getter: Callable[[], float],
        stop_flag: Callable[[], bool],
    ) -> None:
        super().__init__()
        self._state_getter = state_getter
        self._level_getter = level_getter
        self._stop_flag = stop_flag
        self._level_smooth = 0.0
        self._t = 0.0
        self._native_done = False
        self._capture_requested = False
        self._capture_cb: Callable[[str | None], None] | None = None
        self._capture_dialog = _HotkeyCaptureDialog()  # создаём один раз, переиспользуем

        self.setWindowFlags(
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.Tool
            | Qt.WindowDoesNotAcceptFocus
            | Qt.NoDropShadowWindowHint
        )
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.resize(_W, _H)
        scr = QApplication.primaryScreen().geometry()
        self.move((scr.width() - _W) // 2, scr.height() - _H - MARGIN_BOTTOM)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(33)

    def showEvent(self, e) -> None:
        super().showEvent(e)
        if not self._native_done:
            with contextlib.suppress(Exception):
                _apply_native(int(self.winId()))
            self._native_done = True

    def _open_capture(self) -> None:
        cb = self._capture_cb

        def on_done(spec: str | None) -> None:
            if cb is not None:
                cb(spec)

        self._capture_dialog.arm(on_done)

    def _tick(self) -> None:
        # любое исключение НЕ должно убивать таймер — иначе встанет весь оверлей
        try:
            self._tick_body()
        except Exception:
            logger.exception("ошибка в _tick (таймер выжил)")

    def _tick_body(self) -> None:
        if self._stop_flag():
            app = QApplication.instance()
            if app is not None:
                app.quit()
            return

        if self._capture_requested:
            self._capture_requested = False
            self._open_capture()

        state = self._state_getter()
        if state in ("recording", "processing"):
            if not self.isVisible():
                self.show()
            self._t += 0.033
            # усиление + быстрый отклик → эквалайзер резко реагирует на голос
            target = max(0.0, min(1.0, self._level_getter() * 2.6))
            self._level_smooth += (target - self._level_smooth) * 0.6
            self.update()
        elif self.isVisible():
            self.hide()

    def paintEvent(self, e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setPen(Qt.NoPen)
        p.setBrush(TINT)
        p.drawRect(QRectF(0, 0, _W, _H))

        state = self._state_getter()
        # processing — ровная «думающая» волна; иначе пляшет от уровня микрофона
        energy = 0.35 if state == "processing" else self._level_smooth

        center = (N_BARS - 1) / 2
        cy = _H / 2
        min_h = BAR_W
        p.setBrush(BAR_COLOR)
        for i in range(N_BARS):
            env = 1.0 - (abs(i - center) / center) ** 1.5
            # две гармоники, ОБЕ бегут вправо ("- i*k") → движение строго слева направо;
            # разные пространственные частоты дают разрозненность соседей
            wob = 0.5 + 0.5 * (
                0.6 * math.sin(self._t * 5.0 - i * 0.8) + 0.4 * math.sin(self._t * 8.5 - i * 1.5)
            )
            amp = env * (0.25 + 0.75 * wob) * energy
            h = min_h + amp * (EQ_H - min_h)
            x = PAD + i * (BAR_W + BAR_GAP)
            p.drawRoundedRect(QRectF(x, cy - h / 2, BAR_W, h), BAR_W / 2, BAR_W / 2)


class Overlay:
    def __init__(
        self,
        state_getter: Callable[[], str],  # "idle" | "recording" | "processing"
        level_getter: Callable[[], float],  # 0..1 уровень микрофона
    ) -> None:
        self._state_getter = state_getter
        self._level_getter = level_getter
        self._stop = False
        self._widget: _Bars | None = None

    def run(self) -> None:
        """Блокирующий запуск Qt в главном потоке (трей — в фоновом)."""
        app = QApplication.instance() or QApplication(sys.argv)
        self._widget = _Bars(self._state_getter, self._level_getter, lambda: self._stop)
        # окно создаётся скрытым — показывается в _tick при записи
        app.exec()

    def request_stop(self) -> None:
        self._stop = True

    def request_hotkey_capture(self, on_done: Callable[[str | None], None]) -> None:
        """Запросить окно захвата хоткея (потокобезопасно — подхватит Qt-таймер)."""
        if self._widget is not None:
            self._widget._capture_cb = on_done
            self._widget._capture_requested = True
