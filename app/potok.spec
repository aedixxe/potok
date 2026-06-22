# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller-спека «Поток» (onedir, всё-в-одном, без консоли).

Сборка:  .venv\\Scripts\\pyinstaller potok.spec --noconfirm
Результат: dist/Potok/  (папка с Potok.exe и всем содержимым).
"""

from PyInstaller.utils.hooks import collect_all

# onnx-asr и onnxruntime подгружают данные/плагины динамически — собираем целиком.
oa_datas, oa_bins, oa_hidden = collect_all("onnx_asr")
ort_datas, ort_bins, ort_hidden = collect_all("onnxruntime")

datas = [
    ("ptt/assets/silero_vad.onnx", "ptt/assets"),
    ("ptt/assets/icon.ico", "ptt/assets"),
    ("models", "models"),                 # вшитые int8-веса GigaAM
    ("config.example.toml", "."),
] + oa_datas + ort_datas

hiddenimports = ["onnx_asr", "onnxruntime"] + oa_hidden + ort_hidden

a = Analysis(
    ["potok.py"],
    pathex=[],
    binaries=oa_bins + ort_bins,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    excludes=[
        "torch", "torchaudio", "silero_vad",  # VAD теперь на onnxruntime
        "tkinter", "matplotlib",  # не нужны в рантайме
        "pytest", "ruff", "mypy",
    ],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Potok",
    debug=False,
    strip=False,
    upx=False,
    console=False,                      # GUI-приложение, без чёрного окна
    icon="ptt/assets/icon.ico",
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="Potok",
)
