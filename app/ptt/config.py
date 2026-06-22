"""Конфигурация приложения.

Читает `config.toml` рядом с приложением (или из пути PTT_CONFIG).
Для собранного exe — рядом с Potok.exe; в dev-режиме — в текущей директории.
Если файла нет — берёт значения по умолчанию и создаёт файл при первом запуске.
"""

from __future__ import annotations

import dataclasses
import os
import sys
import tomllib
from dataclasses import asdict, dataclass
from pathlib import Path

DEFAULT_CONFIG_NAME = "config.toml"


@dataclass
class Config:
    # --- Хоткей и режим активации ---
    hotkey: str = "ctrl+space"  # одиночная клавиша ("ctrl_r") или комбо ("ctrl+space")
    mode: str = "hold"  # "hold" | "toggle"

    # --- ASR ---
    model_name: str = "gigaam-v3-e2e-rnnt"
    provider: str = "cpu"  # "cpu" | "cuda"
    model_dir: str = ""  # пустая строка → onnx-asr hub качает с HuggingFace

    # --- Аудио ---
    sample_rate: int = 16000
    input_device: int | None = None  # None → системное устройство по умолчанию

    # --- Вставка текста ---
    paste_method: str = "type"  # "type" (печать символами, надёжно для webview/VS Code)
    # или "paste" (буфер + Ctrl/Cmd+V, быстрее для длинного текста)
    paste_restore_delay: float = 0.25  # сек до восстановления буфера (только для method=paste)
    auto_paste: bool = True  # False → только положить в буфер обмена
    append_space: bool = True  # добавлять пробел в конце, чтобы фразы не слипались

    # --- Постобработка ---
    filler_filter: bool = True  # вырезать заминки-паразиты («э-э-э», «ммм», «эм»)
    it_dictionary: bool = True  # канонизировать IT-термины (FastAPI, GitLab, Docker…)

    # --- VAD ---
    vad_enabled: bool = True
    max_segment_sec: float = 24.0  # < 25 сек лимита GigaAM на один вызов

    def validate(self) -> list[str]:
        """Возвращает список проблем конфига (пустой список = всё ок)."""
        problems: list[str] = []
        if self.mode not in ("hold", "toggle"):
            problems.append(f"mode должен быть 'hold' или 'toggle', а не {self.mode!r}")
        if self.provider not in ("cpu", "cuda"):
            problems.append(f"provider должен быть 'cpu' или 'cuda', а не {self.provider!r}")
        if self.paste_method not in ("type", "paste"):
            problems.append(
                f"paste_method должен быть 'type' или 'paste', а не {self.paste_method!r}"
            )
        if self.sample_rate != 16000:
            problems.append("sample_rate должен быть 16000 (требование GigaAM-v3)")
        if self.max_segment_sec >= 25:
            problems.append("max_segment_sec должен быть < 25 (лимит одного вызова GigaAM)")
        return problems

    def execution_providers(self) -> list[str]:
        """Список onnxruntime-провайдеров в порядке приоритета."""
        if self.provider == "cuda":
            return ["CUDAExecutionProvider", "CPUExecutionProvider"]
        return ["CPUExecutionProvider"]


def _config_path() -> Path:
    env = os.environ.get("PTT_CONFIG")
    if env:
        return Path(env)
    # собранный exe: конфиг рядом с Potok.exe (не зависит от рабочей директории,
    # иначе автозапуск из реестра писал бы конфиг в system32)
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent / DEFAULT_CONFIG_NAME
    return Path.cwd() / DEFAULT_CONFIG_NAME


def load_config() -> Config:
    """Загружает конфиг из TOML; при отсутствии — дефолты."""
    path = _config_path()
    if not path.exists():
        return Config()
    with path.open("rb") as fh:
        data = tomllib.load(fh)
    known = {f.name for f in dataclasses.fields(Config)}
    filtered = {k: v for k, v in data.items() if k in known}
    return Config(**filtered)


def _serialize(cfg: Config) -> str:
    lines = ["# Конфигурация ptt — push-to-talk диктовщик (GigaAM-v3)", ""]
    for key, value in asdict(cfg).items():
        if value is None:
            lines.append(f"# {key} = ")  # None → закомментировано (системный дефолт)
        elif isinstance(value, bool):
            lines.append(f"{key} = {str(value).lower()}")
        elif isinstance(value, str):
            lines.append(f'{key} = "{value}"')
        else:
            lines.append(f"{key} = {value}")
    return "\n".join(lines) + "\n"


def save_config(cfg: Config, path: Path | None = None) -> Path:
    """Сохраняет текущий конфиг в TOML (чтобы выбор из меню запоминался)."""
    path = path or _config_path()
    path.write_text(_serialize(cfg), encoding="utf-8")
    return path
