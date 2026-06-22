"""Захват аудио с микрофона через sounddevice.

Пишем непрерывный поток в буфер (16 kHz mono, float32) пока идёт запись.
По остановке отдаём весь буфер одним numpy-массивом для ASR.
Параллельно считаем сглаженный уровень сигнала для индикатора в трее.
"""

from __future__ import annotations

import threading
from collections.abc import Callable

import numpy as np

try:
    import sounddevice as sd
except OSError as exc:  # PortAudio не найден
    raise RuntimeError(
        "Не удалось загрузить PortAudio (sounddevice). Установи зависимости "
        "из requirements.txt и системный PortAudio."
    ) from exc


class Recorder:
    """Пишет микрофон в буфер пока активна запись.

    Использование:
        rec = Recorder(sample_rate=16000)
        rec.start()
        ...
        audio = rec.stop()   # np.float32 моно, [-1, 1]
    """

    def __init__(
        self,
        sample_rate: int = 16000,
        device: int | None = None,
        level_callback: Callable[[float], None] | None = None,
        blocksize: int = 1600,  # 0.1 сек при 16 kHz
    ) -> None:
        self.sample_rate = sample_rate
        self.device = device
        self.level_callback = level_callback
        self.blocksize = blocksize

        self._frames: list[np.ndarray] = []
        self._stream: sd.InputStream | None = None
        self._lock = threading.Lock()
        self._recording = False

    def _on_audio(self, indata, frames, time_info, status) -> None:
        # status (переполнение/недогрузка буфера) не критичен — кадр всё равно берём
        chunk = indata[:, 0].copy()  # моно: берём первый канал
        with self._lock:
            self._frames.append(chunk)
        if self.level_callback is not None:
            # RMS → грубый уровень 0..1 (gated шумодав, идея из RuFlow)
            rms = float(np.sqrt(np.mean(np.square(chunk))) + 1e-9)
            level = 0.0 if rms < 0.005 else min(1.0, rms * 12.0)
            self.level_callback(level)

    def start(self) -> None:
        if self._recording:
            return
        with self._lock:
            self._frames = []
        self._stream = sd.InputStream(
            samplerate=self.sample_rate,
            channels=1,
            dtype="float32",
            blocksize=self.blocksize,
            device=self.device,
            callback=self._on_audio,
        )
        self._stream.start()
        self._recording = True

    def stop(self) -> np.ndarray:
        """Останавливает запись и возвращает накопленный звук (float32 mono)."""
        if not self._recording:
            return np.zeros(0, dtype=np.float32)
        self._recording = False
        assert self._stream is not None
        self._stream.stop()
        self._stream.close()
        self._stream = None
        with self._lock:
            if not self._frames:
                return np.zeros(0, dtype=np.float32)
            audio = np.concatenate(self._frames).astype(np.float32)
            self._frames = []
        return audio

    def abort(self) -> None:
        """Сброс записи без возврата данных (на случай отмены)."""
        if self._stream is not None:
            self._stream.abort()
            self._stream.close()
            self._stream = None
        self._recording = False
        with self._lock:
            self._frames = []


def list_input_devices() -> list[tuple[int, str]]:
    """Список доступных устройств ввода (index, name) — для настройки."""
    devices = sd.query_devices()
    out: list[tuple[int, str]] = []
    for idx, dev in enumerate(devices):
        if dev.get("max_input_channels", 0) > 0:
            out.append((idx, dev["name"]))
    return out
