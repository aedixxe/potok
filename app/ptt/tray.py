"""Иконка в системном трее (pystray) с индикатором состояния и меню.

Знак — фирменный «Поток»: эквалайзер из 3 капсул (упрощение знака под 16 px,
по бренд-борду в `brand/`). Состояния:
    idle       — серый контур  (ждём хоткей)
    recording  — красный заливка (идёт запись)
    processing — синий, пониженные капсулы (распознаём)

Иконка генерируется Pillow на лету по координатам знака — внешних файлов не нужно.
"""

from __future__ import annotations

from collections.abc import Callable

import pystray
from PIL import Image, ImageDraw

from . import autostart

_SIZE = 64

# Капсулы знака в системе координат 100×100 (как в brand/tray-*.svg): (x, y, w, h).
_CAPS_FULL = [(18, 30, 14, 40), (43, 14, 14, 72), (68, 24, 14, 52)]
_CAPS_PROC = [(18, 38, 14, 24), (43, 22, 14, 56), (68, 32, 14, 36)]

_IDLE = (139, 144, 164, 255)  # #8B90A4
_RECORDING = (232, 69, 69, 255)  # #E84545
_PROCESSING = (95, 140, 255, 255)  # #5F8CFF

# Дружелюбные имена numpad-клавиш (хранятся как vkNNN) для отображения.
_NUMPAD = {
    96: "Num0",
    97: "Num1",
    98: "Num2",
    99: "Num3",
    100: "Num4",
    101: "Num5",
    102: "Num6",
    103: "Num7",
    104: "Num8",
    105: "Num9",
    106: "Num*",
    107: "Num+",
    109: "Num-",
    110: "Num.",
    111: "Num/",
}


def _pretty_hotkey(spec: str) -> str:
    """Спек хоткея → читаемая подпись (vk104 → Num8 и т.п.)."""
    out = []
    for tok in spec.split("+"):
        if tok.startswith("vk") and tok[2:].isdigit():
            out.append(_NUMPAD.get(int(tok[2:]), tok))
        else:
            out.append(tok)
    return "+".join(out)


def _make_image(state: str) -> Image.Image:
    s = _SIZE / 100.0
    img = Image.new("RGBA", (_SIZE, _SIZE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    if state == "recording":
        caps, color, outline = _CAPS_FULL, _RECORDING, False
    elif state == "processing":
        caps, color, outline = _CAPS_PROC, _PROCESSING, False
    else:  # idle — контурный знак (как в дизайне)
        caps, color, outline = _CAPS_FULL, _IDLE, True

    for x, y, w, h in caps:
        box = [x * s, y * s, (x + w) * s, (y + h) * s]
        r = (w * s) / 2
        if outline:
            draw.rounded_rectangle(box, radius=r, outline=color, width=max(2, round(5 * s)))
        else:
            draw.rounded_rectangle(box, radius=r, fill=color)
    return img


class Tray:
    def __init__(
        self,
        mode: str,
        hotkey: str,
        on_mode_change: Callable[[str], None],
        on_capture_hotkey: Callable[[], None],
        on_quit: Callable[[], None],
    ) -> None:
        self._mode = mode
        self._hotkey = hotkey
        self._on_mode_change = on_mode_change
        self._on_capture_hotkey = on_capture_hotkey
        self._on_quit = on_quit
        self._state = "idle"
        # кешируем состояние автозапуска, чтобы не лезть в реестр при каждой перерисовке меню
        self._autostart = autostart.is_enabled()
        self._icon = pystray.Icon(
            "ptt",
            icon=_make_image("idle"),
            title=self._title(),
            menu=self._build_menu(),
        )

    def _title(self) -> str:
        return f"Поток — {self._state} [{self._mode} · {_pretty_hotkey(self._hotkey)}]"

    def _build_menu(self) -> pystray.Menu:
        def make_mode_item(mode_value: str, label: str):
            return pystray.MenuItem(
                label,
                lambda icon, item: self._select_mode(mode_value),
                checked=lambda item, mv=mode_value: self._mode == mv,
                radio=True,
            )

        hotkey_menu = pystray.Menu(
            pystray.MenuItem(
                lambda item: f"Сейчас: {_pretty_hotkey(self._hotkey)}",
                lambda icon, item: None,  # информационная строка (нормальный цвет)
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                "Задать свой хоткей…",
                lambda icon, item: self._on_capture_hotkey(),
            ),
        )

        return pystray.Menu(
            make_mode_item("hold", "Режим: hold (зажать)"),
            make_mode_item("toggle", "Режим: toggle (старт/стоп)"),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Хоткей", hotkey_menu),
            pystray.MenuItem(
                "Запускать с Windows",
                lambda icon, item: self._toggle_autostart(),
                checked=lambda item: self._autostart,
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Выход", lambda icon, item: self._quit()),
        )

    def _toggle_autostart(self) -> None:
        self._autostart = autostart.toggle()
        self._icon.update_menu()

    def _select_mode(self, mode: str) -> None:
        if mode == self._mode:
            return
        self._mode = mode
        self._icon.title = self._title()
        self._on_mode_change(mode)

    def set_hotkey_display(self, spec: str) -> None:
        """Обновить отображаемый хоткей в трее (тултип + пункт «Сейчас: …»).

        ВАЖНО: вызывать НЕ из Qt event loop, а из обычного потока (как `set_state`) —
        `update_menu()` из Qt-потока вешает pystray и весь event loop. Из daemon-потока
        (см. main `_on_hotkey_captured`) — безопасно.
        """
        self._hotkey = spec
        self._icon.title = self._title()
        self._icon.update_menu()

    def _quit(self) -> None:
        self._on_quit()
        self._icon.stop()

    # --- публичный API (вызывается из main, в т.ч. из других потоков) ---
    def set_state(self, state: str) -> None:
        self._state = state
        self._icon.icon = _make_image(state)
        self._icon.title = self._title()

    def run(self) -> None:
        """Блокирующий вызов — держит трей в главном потоке."""
        self._icon.run()

    def run_detached(self) -> None:
        """Запустить трей в фоновом потоке (главный поток освобождается под GUI)."""
        self._icon.run_detached()

    def stop(self) -> None:
        self._icon.stop()
