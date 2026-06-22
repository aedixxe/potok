"""Нарезка речи любой длины на сегменты через silero-VAD (ONNX, без torch).

GigaAM берёт до ~25 сек за один вызов. Длинную диктовку режем по паузам: VAD находит
речевые интервалы, мы группируем их в куски не длиннее max_segment_sec. Если VAD
недоступен или выключен — fallback на грубую нарезку по фиксированному окну.

Модель `assets/silero_vad.onnx` (silero v5, 16 kHz) гоняется через onnxruntime —
тот же движок, что и для распознавания. Никакого PyTorch.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

VAD_SAMPLE_RATE = 16000
_WINDOW = 512   # размер окна silero для 16 kHz
_CONTEXT = 64   # хвост предыдущего окна, который силеро дописывает перед текущим
_MODEL_PATH = Path(__file__).parent / "assets" / "silero_vad.onnx"

# Параметры детекции речи (как в silero get_speech_timestamps).
_THRESHOLD = 0.5
_NEG_THRESHOLD = 0.35
_MIN_SILENCE = int(0.1 * VAD_SAMPLE_RATE)   # 100 мс тишины закрывают речь
_SPEECH_PAD = int(0.03 * VAD_SAMPLE_RATE)   # 30 мс паддинг вокруг речи


class _SileroOnnxVAD:
    """Ленивая обёртка над silero-VAD в ONNX (onnxruntime, CPU)."""

    def __init__(self) -> None:
        self._session = None

    def _ensure_loaded(self) -> None:
        if self._session is not None:
            return
        import onnxruntime as ort

        opts = ort.SessionOptions()
        opts.inter_op_num_threads = 1
        opts.intra_op_num_threads = 1
        self._session = ort.InferenceSession(
            str(_MODEL_PATH), sess_options=opts, providers=["CPUExecutionProvider"]
        )

    def _probs(self, audio: np.ndarray) -> list[float]:
        """Вероятность речи для каждого окна (512 сэмплов).

        Перед каждым окном силеро дописывает контекст — хвост (64 сэмпла)
        предыдущего окна, поэтому модель получает 64+512 = 576 сэмплов.
        """
        self._ensure_loaded()
        assert self._session is not None
        state = np.zeros((2, 1, 128), dtype=np.float32)
        context = np.zeros((1, _CONTEXT), dtype=np.float32)
        sr = np.array(VAD_SAMPLE_RATE, dtype=np.int64)
        probs: list[float] = []
        for start in range(0, len(audio), _WINDOW):
            chunk = audio[start : start + _WINDOW].astype(np.float32)
            if len(chunk) < _WINDOW:
                chunk = np.pad(chunk, (0, _WINDOW - len(chunk)))
            x = np.concatenate([context, chunk.reshape(1, _WINDOW)], axis=1)
            out, state = self._session.run(
                None, {"input": x, "state": state, "sr": sr}
            )
            context = x[:, -_CONTEXT:].astype(np.float32)
            probs.append(float(out[0][0]))
        return probs

    def speech_timestamps(self, audio: np.ndarray) -> list[dict]:
        """Список речевых отрезков {'start': sample, 'end': sample}."""
        probs = self._probs(audio)
        n = len(audio)
        speeches: list[dict] = []
        triggered = False
        cur_start = 0
        temp_end = 0

        for i, p in enumerate(probs):
            sample = i * _WINDOW
            if p >= _THRESHOLD:
                temp_end = 0
                if not triggered:
                    triggered = True
                    cur_start = sample
            elif p < _NEG_THRESHOLD and triggered:
                if not temp_end:
                    temp_end = sample
                if sample - temp_end >= _MIN_SILENCE:
                    speeches.append({"start": cur_start, "end": temp_end})
                    triggered = False
                    temp_end = 0

        if triggered:
            speeches.append({"start": cur_start, "end": n})

        for s in speeches:
            s["start"] = max(0, s["start"] - _SPEECH_PAD)
            s["end"] = min(n, s["end"] + _SPEECH_PAD)
        return speeches


_vad_singleton: _SileroOnnxVAD | None = None


def _get_vad() -> _SileroOnnxVAD:
    global _vad_singleton
    if _vad_singleton is None:
        _vad_singleton = _SileroOnnxVAD()
    return _vad_singleton


def _chunk_by_window(audio: np.ndarray, max_samples: int) -> list[np.ndarray]:
    """Грубая нарезка по фиксированному окну (fallback без VAD)."""
    if len(audio) <= max_samples:
        return [audio]
    return [audio[i : i + max_samples] for i in range(0, len(audio), max_samples)]


def segment_audio(
    audio: np.ndarray,
    max_segment_sec: float = 24.0,
    sample_rate: int = VAD_SAMPLE_RATE,
    use_vad: bool = True,
) -> list[np.ndarray]:
    """Режет звук на куски ≤ max_segment_sec, по возможности по паузам речи.

    Возвращает список numpy-массивов float32. Короткий звук → один элемент.
    """
    if len(audio) == 0:
        return []

    max_samples = int(max_segment_sec * sample_rate)
    if not use_vad:
        return _chunk_by_window(audio, max_samples)

    try:
        speech = _get_vad().speech_timestamps(audio)
    except Exception:
        # VAD не загрузился / упал — не падаем, грубо режем по окну
        return _chunk_by_window(audio, max_samples)

    if not speech:
        return _chunk_by_window(audio, max_samples)

    # Группируем речевые интервалы в сегменты ≤ max_samples, режа по паузам.
    segments: list[np.ndarray] = []
    seg_start = speech[0]["start"]
    seg_end = speech[0]["end"]
    for interval in speech[1:]:
        if interval["end"] - seg_start > max_samples:
            segments.append(audio[seg_start:seg_end])
            seg_start = interval["start"]
        seg_end = interval["end"]
    segments.append(audio[seg_start:seg_end])

    # Подстраховка: сплошная речь без пауз длиннее лимита — дорежем по окну.
    out: list[np.ndarray] = []
    for seg in segments:
        if len(seg) > max_samples:
            out.extend(_chunk_by_window(seg, max_samples))
        else:
            out.append(seg)
    return out
