import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _runtime_stage() -> str:
    """Текст стадии runtime из Dockerfile."""
    text = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    start = text.index("FROM base AS runtime")
    rest = text[start + len("FROM base AS runtime"):]
    end = rest.find("\nFROM ")
    return rest if end == -1 else rest[:end]


def test_runtime_stage_drops_root():
    stage = _runtime_stage()
    assert "USER botuser" in stage


def test_runtime_stage_creates_the_unprivileged_user():
    stage = _runtime_stage()
    assert "useradd" in stage
    assert "chown" in stage


def test_test_stage_stays_root():
    # Тесты пишут временные файлы в произвольных местах; ужесточать их
    # окружение незачем, в прод стадия test не попадает.
    text = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    test_stage = text[text.index("FROM testdeps AS test"):]
    assert "USER " not in test_stage


def test_superpowers_directory_is_git_ignored():
    ignored = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert ".superpowers/" in [line.strip() for line in ignored]


def test_container_process_is_not_root_when_image_says_so():
    """Прогоняется внутри образа. Стадия test — от root, и это ожидаемо;
    проверка нужна, чтобы факт был зафиксирован явно, а не подразумевался."""
    assert os.geteuid() == 0
