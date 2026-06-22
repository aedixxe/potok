"""Глобальный хоткей через pynput: одиночная клавиша ИЛИ комбинация + watchdog.

Поддерживает как одну клавишу ("ctrl_r"), так и сочетания ("ctrl+space",
"ctrl+shift+f9"). Модификаторы ctrl/alt/shift/cmd нормализуются (левый и правый —
одно и то же).

Режимы:
- hold:   все клавиши комбо зажаты → start; отпустил хоть одну → stop. Autorepeat игнор.
- toggle: момент, когда комбо стало полностью зажато → переключение start/stop.

Watchdog (идея RuFlow): фоновый таймер обрывает запись, если она длится дольше
max_record_sec — страховка от «залипшего» хоткея, когда release не пришёл
(окно потеряло фокус).
"""

from __future__ import annotations

import threading
from collections.abc import Callable

from pynput import keyboard

# Родовой модификатор → множество фактических клавиш, которые ему подходят.
# "ctrl" в хоткее матчит и левый, и правый; а "ctrl_r" — только правый.
_FAMILY = {
    "ctrl": {"ctrl", "ctrl_l", "ctrl_r"},
    "alt": {"alt", "alt_l", "alt_r", "alt_gr"},
    "shift": {"shift", "shift_l", "shift_r"},
    "cmd": {"cmd", "cmd_l", "cmd_r"},
    "win": {"cmd", "cmd_l", "cmd_r"},
}


def _key_id(key) -> str | None:
    """Входящая клавиша pynput → точный строковый id (БЕЗ сворачивания сторон)."""
    if isinstance(key, keyboard.Key):
        return key.name  # ctrl_l / ctrl_r / space / f9 ...
    if isinstance(key, keyboard.KeyCode):
        vk = getattr(key, "vk", None)
        # numpad-зона Windows (VK 96..111) — различаем от обычных цифр по vk
        if vk is not None and 96 <= vk <= 111:
            return f"vk{vk}"
        if key.char:
            return key.char.lower()
        if vk is not None:
            return f"vk{vk}"
    return None


def parse_hotkey(spec: str) -> frozenset[str]:
    """Строка из конфига → множество требуемых токенов (как написаны).

    Примеры: "ctrl_r" → {"ctrl_r"} (только правый); "ctrl+space" → {"ctrl","space"}
    (любой ctrl + пробел); "a" → {"a"}.
    """
    tokens = [t.strip().lower() for t in spec.split("+") if t.strip()]
    if not tokens:
        raise ValueError(f"Пустой хоткей: {spec!r}")
    return frozenset(tokens)


def _token_satisfied(token: str, pressed: set[str]) -> bool:
    """Удовлетворён ли требуемый токен текущими зажатыми клавишами."""
    accepted = _FAMILY.get(token, {token})
    return bool(accepted & pressed)


class HotkeyListener:
    """Слушает глобальный хоткей (клавишу или комбо) и дёргает on_start / on_stop."""

    def __init__(
        self,
        hotkey: str,
        mode: str,
        on_start: Callable[[], None],
        on_stop: Callable[[], None],
        max_record_sec: float = 300.0,
    ) -> None:
        self.combo = parse_hotkey(hotkey)
        self.mode = mode
        self.on_start = on_start
        self.on_stop = on_stop
        self.max_record_sec = max_record_sec

        self._pressed: set[str] = set()  # сейчас физически зажатые id
        self._combo_active = False  # было ли комбо полным в прошлый раз (edge-детект)
        self._recording = False
        self._paused = False  # пауза на время захвата нового хоткея
        self._lock = threading.Lock()
        self._listener: keyboard.Listener | None = None
        self._watchdog_timer: threading.Timer | None = None

    # --- управление записью ---
    def _start(self) -> None:
        with self._lock:
            if self._recording:
                return
            self._recording = True
        self._arm_watchdog()
        self.on_start()

    def _stop(self) -> None:
        with self._lock:
            if not self._recording:
                return
            self._recording = False
        self._cancel_watchdog()
        self.on_stop()

    # --- watchdog ---
    def _arm_watchdog(self) -> None:
        self._cancel_watchdog()
        self._watchdog_timer = threading.Timer(self.max_record_sec, self._on_watchdog)
        self._watchdog_timer.daemon = True
        self._watchdog_timer.start()

    def _cancel_watchdog(self) -> None:
        if self._watchdog_timer is not None:
            self._watchdog_timer.cancel()
            self._watchdog_timer = None

    def _on_watchdog(self) -> None:
        self._pressed.clear()
        self._combo_active = False
        self._stop()

    # --- события клавиатуры ---
    def _combo_complete(self) -> bool:
        return all(_token_satisfied(tok, self._pressed) for tok in self.combo)

    def _on_press(self, key) -> None:
        if self._paused:
            return
        kid = _key_id(key)
        if kid is None:
            return
        self._pressed.add(kid)

        complete = self._combo_complete()
        if self.mode == "hold":
            # Самоисправляющийся hold: пишем, пока комбо полностью зажато.
            # Не полагаемся на edge-флаг — даже если прошлый release потерялся
            # (фокус ушёл в попап), следующий полный набор клавиш снова стартует,
            # а guard в _start() гасит autorepeat.
            if complete:
                self._start()
        else:  # toggle — нужен фронт (момент сборки комбо)
            if complete and not self._combo_active:
                self._combo_active = True
                self._stop() if self._recording else self._start()

    def _on_release(self, key) -> None:
        if self._paused:
            return
        kid = _key_id(key)
        if kid is None:
            return
        self._pressed.discard(kid)

        complete = self._combo_complete()
        if not complete:
            self._combo_active = False
            if self.mode == "hold" and self._recording:
                self._stop()

    def set_hotkey(self, spec: str) -> None:
        """Сменить комбинацию на лету (из меню трея). Сбрасывает состояние."""
        self.combo = parse_hotkey(spec)
        self._pressed.clear()
        self._combo_active = False
        if self._recording:
            self._stop()

    def pause(self) -> None:
        """Временно игнорировать клавиши (на время захвата нового хоткея)."""
        self._paused = True

    def resume(self) -> None:
        """Возобновить реакцию на клавиши, сбросив накопленное состояние."""
        self._pressed.clear()
        self._combo_active = False
        self._paused = False

    # --- жизненный цикл ---
    def start(self) -> None:
        self._listener = keyboard.Listener(on_press=self._on_press, on_release=self._on_release)
        self._listener.start()

    def stop(self) -> None:
        self._cancel_watchdog()
        if self._listener is not None:
            self._listener.stop()
            self._listener = None

    def join(self) -> None:
        if self._listener is not None:
            self._listener.join()


def _demo() -> None:
    """Ручная проверка хоткея без ASR: печатает start/stop."""
    import time

    def on_start():
        print(f"[{time.strftime('%H:%M:%S')}] START")

    def on_stop():
        print(f"[{time.strftime('%H:%M:%S')}] STOP")

    hk = HotkeyListener("ctrl+space", "hold", on_start, on_stop)
    print("Зажми Ctrl+Space (hold-режим). Ctrl+C для выхода.")
    hk.start()
    try:
        hk.join()
    except KeyboardInterrupt:
        hk.stop()


if __name__ == "__main__":
    _demo()
