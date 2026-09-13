"""Поддельный gallery-dl для тестов без сети.

Кладёт исполняемый скрипт с именем `gallery-dl` в указанный каталог; тест
подставляет этот каталог в PATH. Скрипт разбирает `-D` и `-f` из argv,
создаёт запрошенное число файлов и завершается заданным кодом.
"""

from __future__ import annotations

import sys
from pathlib import Path

_TEMPLATE = '''#!{python_executable}
import pathlib
import sys

argv = sys.argv[1:]
dest = pathlib.Path(argv[argv.index("-D") + 1])
name_format = argv[argv.index("-f") + 1]
prefix = name_format.split("_{{", 1)[0]

dest.mkdir(parents=True, exist_ok=True)
for i in range(1, {files} + 1):
    # Имитируем новый шаблон: <prefix>_<num>_<уникальное имя>.<ext>
    (dest / f"{{prefix}}_1_item{{i}}.jpg").write_bytes(b"\\xff\\xd8\\xff" + b"x" * 200)

sys.stderr.write({stderr_text!r})
sys.exit({exit_code})
'''


def write_fake_gallery_dl(
    bin_dir: Path, *, files: int = 2, exit_code: int = 0, stderr_text: str = ""
) -> None:
    bin_dir.mkdir(parents=True, exist_ok=True)
    script = bin_dir / "gallery-dl"
    script.write_text(
        _TEMPLATE.format(
            # Абсолютный путь интерпретатора, а не `#!/usr/bin/env python3`:
            # тест, который запускает этот скрипт, сознательно подменяет PATH
            # на один bin_dir (чтобы найти ТОЛЬКО наш фейковый gallery-dl) —
            # при таком PATH `env` не находит `python3` и падает с кодом 127
            # раньше, чем скрипт вообще начнёт выполняться.
            python_executable=sys.executable,
            files=files, exit_code=exit_code, stderr_text=stderr_text,
        )
    )
    script.chmod(0o755)
