"""Лаунчер «Поток» для автозапуска (без консольного окна).

Добавляет папку app в sys.path и стартует приложение. На него ссылается запись
автозапуска в реестре — так запуск не зависит от рабочей директории.
"""

import os
import sys

_APP_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _APP_DIR)
os.chdir(_APP_DIR)  # чтобы подхватился config.toml рядом с приложением

from ptt.main import main  # noqa: E402

if __name__ == "__main__":
    main()
