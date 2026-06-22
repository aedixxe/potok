"""Автозапуск «Поток» с Windows через реестр (HKCU\\…\\Run).

Включение добавляет запись в Run — приложение стартует при входе пользователя.
В dev-режиме запускается через `run.pyw` (pythonw, без консоли); в упакованном
виде (`sys.frozen`) — сам exe. На не-Windows функции — безопасные заглушки.
"""

from __future__ import annotations

import os
import sys

_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_APP_NAME = "Potok"  # имя записи в реестре


def _command() -> str:
    """Команда запуска для записи в реестр."""
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}"'
    pyw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    if not os.path.exists(pyw):
        pyw = sys.executable
    # app/ = на уровень выше пакета ptt; рядом лежит run.pyw
    app_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    launcher = os.path.join(app_dir, "run.pyw")
    return f'"{pyw}" "{launcher}"'


def is_enabled() -> bool:
    if sys.platform != "win32":
        return False
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as key:
            winreg.QueryValueEx(key, _APP_NAME)
            return True
    except FileNotFoundError:
        return False


def set_enabled(enabled: bool) -> None:
    if sys.platform != "win32":
        return
    import winreg

    if enabled:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, _APP_NAME, 0, winreg.REG_SZ, _command())
    else:
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
                winreg.DeleteValue(key, _APP_NAME)
        except FileNotFoundError:
            pass


def toggle() -> bool:
    """Переключить автозапуск, вернуть новое состояние."""
    new_state = not is_enabled()
    set_enabled(new_state)
    return new_state
