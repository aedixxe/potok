"""ASR-обёртка над onnx-asr + GigaAM-v3 (e2e-rnnt, с пунктуацией).

Модель грузится один раз (ленивая инициализация), держится в памяти.
Провайдер onnxruntime берётся из конфига: CPU по умолчанию, CUDA опционально.
Длинная речь режется через vad.segment_audio и склеивается в единый текст.

onnx-asr принимает waveform как np.float32 16 kHz mono напрямую — временные WAV не нужны.
"""

from __future__ import annotations

import logging
import os
import sys

import numpy as np

from . import vad

logger = logging.getLogger("ptt.asr")

SAMPLE_RATE = 16000


def _bundled_models() -> tuple[str, str] | None:
    """Путь к вшитым int8-весам GigaAM, если они есть (dev `app/models/` или бандл).

    Возвращает (директория, quantization) или None — тогда грузим с HuggingFace.
    Тест-переключатель: при заданной env `PTT_FORCE_FP32` вшитые int8 игнорируются и
    грузится fp32 с HF (для сравнения качества).
    """
    if os.environ.get("PTT_FORCE_FP32"):
        return None
    base = sys._MEIPASS if getattr(sys, "frozen", False) else os.path.dirname(
        os.path.dirname(os.path.abspath(__file__))
    )
    d = os.path.join(base, "models")
    if os.path.isfile(os.path.join(d, "v3_e2e_rnnt_encoder.int8.onnx")):
        return d, "int8"
    return None


class Transcriber:
    def __init__(
        self,
        model_name: str = "gigaam-v3-e2e-rnnt",
        providers: list[str] | None = None,
        model_dir: str = "",
        max_segment_sec: float = 24.0,
        use_vad: bool = True,
    ) -> None:
        self.model_name = model_name
        self.providers = providers or ["CPUExecutionProvider"]
        self.model_dir = model_dir
        self.max_segment_sec = max_segment_sec
        self.use_vad = use_vad
        self._model: object | None = None

    def load(self) -> None:
        """Грузит модель (скачивает с HF при первом запуске, если model_dir пуст)."""
        if self._model is not None:
            return
        import onnx_asr

        kwargs: dict = {}
        bundled = _bundled_models()
        if bundled:
            kwargs["path"], kwargs["quantization"] = bundled
            logger.info("Использую вшитые int8-веса: %s", bundled[0])
        elif self.model_dir:
            kwargs["path"] = self.model_dir

        # onnx-asr пробрасывает providers в onnxruntime. На старых версиях
        # параметра может не быть — тогда грузим без него (CPU).
        try:
            self._model = onnx_asr.load_model(self.model_name, providers=self.providers, **kwargs)
        except TypeError:
            logger.warning("onnx_asr.load_model не принимает providers — грузим на CPU")
            self._model = onnx_asr.load_model(self.model_name, **kwargs)

        active = getattr(self._model, "providers", None) or self.providers
        logger.info("Модель %s загружена, провайдеры: %s", self.model_name, active)

    def _recognize_one(self, audio: np.ndarray) -> str:
        assert self._model is not None
        result = self._model.recognize(audio, sample_rate=SAMPLE_RATE)
        # recognize может вернуть строку или объект с .text — нормализуем
        if isinstance(result, str):
            return result.strip()
        text = getattr(result, "text", None)
        return (text or str(result)).strip()

    def transcribe(self, audio: np.ndarray) -> str:
        """Главный метод: numpy float32 16 kHz → готовый текст с пунктуацией."""
        if self._model is None:
            self.load()
        if audio is None or len(audio) == 0:
            return ""

        segments = vad.segment_audio(
            audio,
            max_segment_sec=self.max_segment_sec,
            sample_rate=SAMPLE_RATE,
            use_vad=self.use_vad,
        )
        if not segments:
            return ""

        parts = [self._recognize_one(seg) for seg in segments]
        return " ".join(p for p in parts if p).strip()


def _cli(path: str, provider: str = "cpu") -> None:
    """Проверка п.1 плана: распознать готовый wav и напечатать текст."""
    import soundfile as sf

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    audio, sr = sf.read(path, dtype="float32", always_2d=False)
    if audio.ndim > 1:
        audio = audio[:, 0]  # моно
    if sr != SAMPLE_RATE:
        raise SystemExit(
            f"Ожидается {SAMPLE_RATE} Hz, в файле {sr} Hz. Переконвертируй wav в 16 kHz mono."
        )

    providers = (
        ["CUDAExecutionProvider", "CPUExecutionProvider"]
        if provider == "cuda"
        else ["CPUExecutionProvider"]
    )
    tr = Transcriber(providers=providers)
    tr.load()
    print(tr.transcribe(audio))


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        raise SystemExit("Использование: python -m ptt.asr <audio.wav> [cpu|cuda]")
    _cli(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else "cpu")
