"""Быстрая проверка всей ASR-цепочки с микрофона, без хоткея и трея.

Записывает N секунд с микрофона и сразу распознаёт через GigaAM-v3.
Использование:
    python -m ptt.mic_test            # 5 сек, CPU
    python -m ptt.mic_test 8          # 8 сек
    python -m ptt.mic_test 8 cuda     # 8 сек на GPU
"""

from __future__ import annotations

import logging
import sys
import time

from .asr import Transcriber
from .audio import Recorder
from .config import load_config


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    seconds = float(sys.argv[1]) if len(sys.argv) > 1 else 5.0
    provider = sys.argv[2] if len(sys.argv) > 2 else None

    cfg = load_config()
    providers = (
        ["CUDAExecutionProvider", "CPUExecutionProvider"]
        if provider == "cuda"
        else cfg.execution_providers()
    )

    print("Загружаю модель (первый раз — скачивание весов с HuggingFace)…")
    tr = Transcriber(
        model_name=cfg.model_name,
        providers=providers,
        model_dir=cfg.model_dir,
        max_segment_sec=cfg.max_segment_sec,
        use_vad=cfg.vad_enabled,
    )
    tr.load()

    rec = Recorder(sample_rate=cfg.sample_rate, device=cfg.input_device)
    print(f"\n>>> ГОВОРИ! Запись {seconds:.0f} сек…")
    rec.start()
    time.sleep(seconds)
    audio = rec.stop()
    print(f"<<< Записано {len(audio) / cfg.sample_rate:.1f} сек. Распознаю…\n")

    text = tr.transcribe(audio)
    print("=" * 60)
    print("РАСПОЗНАНО:", text or "(пусто — тишина или микрофон не пишет?)")
    print("=" * 60)


if __name__ == "__main__":
    main()
