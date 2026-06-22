"""Точка входа: связывает хоткей → запись → ASR → авто-вставку + трей.

Запуск:  python -m ptt.main

Главный поток держит иконку трея (pystray блокирующий). Хоткей слушается в
отдельном потоке pynput. Распознавание выполняется в рабочем потоке, чтобы трей
и хоткей не подвисали во время инференса.
"""

from __future__ import annotations

import logging
import threading

from . import inject
from .asr import Transcriber
from .audio import Recorder
from .config import load_config, save_config
from .hotkey import HotkeyListener
from .overlay import Overlay
from .postprocess import clean_text
from .tray import Tray

logger = logging.getLogger("ptt.main")


class App:
    def __init__(self) -> None:
        self.cfg = load_config()
        problems = self.cfg.validate()
        if problems:
            raise SystemExit("Ошибки конфига:\n  - " + "\n  - ".join(problems))

        self._ui_state = "idle"  # idle | recording | processing (для трея и оверлея)
        self._level = 0.0  # текущий уровень микрофона 0..1 (для анимации)

        self.recorder = Recorder(
            sample_rate=self.cfg.sample_rate,
            device=self.cfg.input_device,
            level_callback=self._on_level,
        )
        self.transcriber = Transcriber(
            model_name=self.cfg.model_name,
            providers=self.cfg.execution_providers(),
            model_dir=self.cfg.model_dir,
            max_segment_sec=self.cfg.max_segment_sec,
            use_vad=self.cfg.vad_enabled,
        )
        self.tray = Tray(
            mode=self.cfg.mode,
            hotkey=self.cfg.hotkey,
            on_mode_change=self._on_mode_change,
            on_capture_hotkey=self._on_capture_hotkey,
            on_quit=self._on_quit,
        )
        self.hotkey = HotkeyListener(
            hotkey=self.cfg.hotkey,
            mode=self.cfg.mode,
            on_start=self._on_record_start,
            on_stop=self._on_record_stop,
        )
        self.overlay = Overlay(
            state_getter=lambda: self._ui_state,
            level_getter=lambda: self._level,
        )
        self._busy = False  # идёт распознавание — новые старты игнорируем
        self._capturing = False  # реально ли сейчас идёт запись (а не холостой стоп)
        self._lock = threading.Lock()

    def _set_state(self, state: str) -> None:
        self._ui_state = state
        self.tray.set_state(state)

    def _on_level(self, level: float) -> None:
        self._level = level

    # --- callbacks хоткея ---
    def _on_record_start(self) -> None:
        with self._lock:
            if self._busy:
                logger.info("Ещё распознаю прошлую фразу — подожди секунду")
                return
        self.recorder.start()
        self._capturing = True
        self._set_state("recording")

    def _on_record_stop(self) -> None:
        # если запись не стартовала (старт был заблокирован занятостью) — холостой
        # стоп игнорируем, иначе гоняли бы пустой буфер → «Пустой результат»
        if not self._capturing:
            return
        self._capturing = False
        audio = self.recorder.stop()
        self._set_state("processing")
        with self._lock:
            self._busy = True
        threading.Thread(target=self._process, args=(audio,), daemon=True).start()

    def _process(self, audio) -> None:
        try:
            text = self.transcriber.transcribe(audio)
            text = clean_text(
                text,
                fillers=self.cfg.filler_filter,
                it_terms=self.cfg.it_dictionary,
            )
            if text:
                # содержимое НЕ логируем (приватность) — только факт и длину
                logger.info("Распознано (%d символов), вставляю", len(text))
                inject.insert_text(
                    text,
                    method=self.cfg.paste_method,
                    auto_paste=self.cfg.auto_paste,
                    restore_delay=self.cfg.paste_restore_delay,
                    append_space=self.cfg.append_space,
                )
            else:
                logger.info("Пустой результат (тишина?)")
        except Exception:
            logger.exception("Ошибка распознавания")
        finally:
            with self._lock:
                self._busy = False
            self._set_state("idle")

    # --- callbacks трея ---
    def _on_mode_change(self, mode: str) -> None:
        self.hotkey.mode = mode
        self.cfg.mode = mode
        self._save()
        logger.info("Режим активации: %s", mode)

    def _on_capture_hotkey(self) -> None:
        """Открыть окно захвата произвольного хоткея (из меню трея)."""
        self.hotkey.pause()  # игнорировать клавиши на время захвата (слушатель жив)
        self.overlay.request_hotkey_capture(self._on_hotkey_captured)

    def _on_hotkey_captured(self, spec: str | None) -> None:
        # выполняется в Qt event loop (из окна захвата) — НЕ трогаем здесь pystray
        if spec:
            self.cfg.hotkey = spec
            self.hotkey.set_hotkey(spec)
            self._save()
            # обновление трея — в отдельном потоке (update_menu из Qt-потока вешает pystray)
            threading.Thread(
                target=self.tray.set_hotkey_display, args=(spec,), daemon=True
            ).start()
            logger.info("Назначен хоткей: %s", spec)
        self.hotkey.resume()  # возобновить реакцию (с новым или прежним хоткеем)

    def _save(self) -> None:
        try:
            save_config(self.cfg)
        except Exception:
            logger.exception("Не удалось сохранить конфиг")

    def _on_quit(self) -> None:
        logger.info("Выход")
        self.hotkey.stop()
        self.recorder.abort()
        self.overlay.request_stop()  # завершит mainloop в главном потоке
        self.tray.stop()

    # --- запуск ---
    def run(self) -> None:
        logger.info(
            "Загрузка модели %s (провайдеры: %s)…",
            self.cfg.model_name,
            self.cfg.execution_providers(),
        )
        self.transcriber.load()  # прогрев: первая диктовка без задержки
        logger.info("Готово. Хоткей: %s, режим: %s", self.cfg.hotkey, self.cfg.mode)
        self.hotkey.start()
        self.tray.run_detached()  # трей в фоновом потоке
        self.overlay.run()  # анимация в главном потоке, блокирует до выхода


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    App().run()


if __name__ == "__main__":
    main()
