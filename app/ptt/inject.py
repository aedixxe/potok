"""Авто-вставка распознанного текста в активное окно.

Два метода:
- "type"  — печать символами через pynput (KEYEVENTF_UNICODE на Windows). Идёт как
  обычный ввод в сфокусированное поле, поэтому работает в webview/Electron (VS Code,
  Slack, браузер), где синтетический Ctrl+V часто перехватывается приложением.
  Кириллица печатается юникодом, минуя раскладку. Дефолт.
- "paste" — буфер обмена + Ctrl/Cmd+V со snapshot и восстановлением (концепция RuFlow).
  Быстрее для длинного текста, но в webview-полях может не доходить.
"""

from __future__ import annotations

import contextlib
import sys
import threading
import time

import pyperclip
from pynput.keyboard import Controller, Key

_IS_MAC = sys.platform == "darwin"
_PASTE_MODIFIER = Key.cmd if _IS_MAC else Key.ctrl

_kbd = Controller()


class ClipboardSnapshot:
    """Снимок текстового содержимого буфера обмена с восстановлением.

    Примечание: pyperclip работает только с текстом, поэтому не-текстовый буфер
    (картинки/файлы) восстановить не получится — для диктовщика это приемлемо.
    """

    def __init__(self) -> None:
        self._text: str | None = None

    @classmethod
    def capture(cls) -> ClipboardSnapshot:
        snap = cls()
        try:
            snap._text = pyperclip.paste()
        except Exception:
            snap._text = None
        return snap

    def restore(self) -> None:
        if self._text is not None:
            with contextlib.suppress(Exception):
                pyperclip.copy(self._text)


def _press_paste() -> None:
    with _kbd.pressed(_PASTE_MODIFIER):
        _kbd.press("v")
        _kbd.release("v")


def _type_text(text: str) -> None:
    """Печать символами — надёжно для webview/Electron и кириллицы."""
    _kbd.type(text)


def _paste_via_clipboard(text: str, restore_delay: float) -> None:
    snapshot = ClipboardSnapshot.capture()
    pyperclip.copy(text)
    time.sleep(0.02)  # дать буферу обновиться до paste
    _press_paste()

    def _restore_later() -> None:
        time.sleep(restore_delay)
        snapshot.restore()

    threading.Thread(target=_restore_later, daemon=True).start()


def insert_text(
    text: str,
    method: str = "type",
    auto_paste: bool = True,
    restore_delay: float = 0.25,
    append_space: bool = True,
) -> None:
    """Вставляет распознанный текст в активное окно.

    method="type"  — печать символами (дефолт, работает в webview/VS Code).
    method="paste" — буфер обмена + Ctrl/Cmd+V.
    append_space=True → добавляет хвостовой пробел, чтобы соседние фразы не слипались.
    auto_paste=False → только кладём текст в буфер, вставляет пользователь сам.
    """
    if not text:
        return

    payload = text + " " if append_space else text

    if not auto_paste:
        pyperclip.copy(payload)
        return

    if method == "type":
        _type_text(payload)
    else:
        _paste_via_clipboard(payload, restore_delay)
