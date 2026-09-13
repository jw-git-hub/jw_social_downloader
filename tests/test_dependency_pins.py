import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _requirements() -> str:
    return (ROOT / "requirements.txt").read_text(encoding="utf-8")


def _dockerfile_text() -> str:
    return (ROOT / "Dockerfile").read_text(encoding="utf-8")


def test_gallery_dl_is_pinned_to_the_version_that_fixes_instagram():
    # 1.32.11 чинит падение TikTok, 1.32.12 — Instagram posts/reels.
    assert "gallery-dl==1.32.12" in _requirements()


def test_aiogram_is_pinned_exactly():
    assert "aiogram==3.31.0" in _requirements()


def test_ytdlp_keeps_the_curl_cffi_extra():
    # curl_cffi нужен для TikTok и Instagram; без extras он не приедет.
    assert re.search(r"yt-dlp\[[^\]]*curl-cffi[^\]]*\]==", _requirements())


def test_transitively_safe_deps_are_pinned_exactly_too():
    """Ревью раунда 1: баг с кэшем случайно защищал эти четыре пакета от
    дрейфа — слой не пересобирался с августа, поэтому диапазоны `>=` не
    переразрешались. После починки кэш-бастера (см. тест ниже) любая правка
    requirements.txt по совершенно постороннему поводу заставила бы pip
    переразрешить их заново на «последнее подходящее на тот момент». loguru
    отдельно завязан на Task 2: маскирование секретов проверялось именно
    под 0.7.3 (порядок "сообщение → патчер → трейсбек", `configure(patcher=)`).
    """
    requirements = _requirements()
    assert "SQLAlchemy[asyncio]==2.0.52" in requirements
    assert "aiosqlite==0.22.1" in requirements
    assert "pydantic-settings==2.15.0" in requirements
    assert "loguru==0.7.3" in requirements


def _pip_install_lines() -> set[str]:
    """Множество некомментарийных строк Dockerfile, содержащих `pip install`.

    Разбор построчный, без склейки `\\`-переносов — и это осознанно, а не
    недосмотр: если кто-то вернёт исходный баг в виде переноса

        RUN pip install --no-cache-dir -r requirements.txt \\
            && pip install --no-cache-dir --upgrade "yt-dlp[...]" gallery-dl

    то первая физическая строка получит лишний хвост (` \\`) и перестанет
    дословно совпадать с эталоном сама по себе — склеивать перенос, чтобы
    это заметить, не требуется. А что вторая, добавленная команда при этом
    тоже останется в множестве лишней строкой — просто дополнительная
    подстраховка.
    """
    lines = set()
    for raw_line in _dockerfile_text().splitlines():
        stripped = raw_line.strip()
        if stripped.startswith("#"):
            continue  # строка целиком комментарий — не инструкция
        if "pip install" in stripped:
            lines.add(stripped)
    return lines


def test_dockerfile_has_exactly_the_two_expected_pip_installs():
    """H-11, ревью раунда 1: старая версия этого теста делала
    `assert "--upgrade" not in dockerfile` по сырому тексту всего файла.
    Это не ловило синоним `-U` (`pip install -U` эквивалентен `--upgrade`),
    а вторая закэшированная команда pip install в обход `-r requirements.txt`
    воспроизвела бы исходный баг при полностью зелёном тесте — единственный
    автоматический барьер оказался дырявым. Заодно та же грубая проверка
    ложно падала от слова «--upgrade» в прозе комментария (см. round 1).

    Теперь сверяем МНОЖЕСТВО строк с `pip install` в Dockerfile с ровно
    двумя ожидаемыми — дословно. Не проходит: `-U`, `--upgrade`, любой
    добавленный `&& pip install ...` в той же инструкции, лишний третий
    `RUN pip install`, любой другой пакет или флаг рядом с `-r ...txt`.
    Комментарии в проверку не попадают в принципе (строка исключается по
    первому непробельному символу `#`), поэтому упоминание `pip install`
    или `--upgrade` в прозе больше не ломает тест.
    """
    assert _pip_install_lines() == {
        "RUN pip install --no-cache-dir -r requirements.txt",
        "RUN pip install --no-cache-dir -r requirements-dev.txt",
    }


def test_installed_versions_match_the_pins():
    """Тест гоняется внутри образа, поэтому проверяет фактически
    установленное, а не только текст файла — это и есть настоящий критерий
    приёмки всей задачи (см. Task 6: `docker history` показывал слой
    `pip install` датированным августом при пересборке в сентябре).

    Проверены версии всех шести пакетов, зафиксированных `==` в
    requirements.txt, и дополнительно — yt-dlp и curl_cffi (ревью раунда 1):
    yt-dlp пинится в файле, но именно расхождение «текст/образ» и было
    исходным багом, так что его тоже стоит проверять на уровне образа, а не
    только текста; curl_cffi нарочно не пинится в requirements.txt (решение
    отложено до Task 35), но его фактическая версия здесь зафиксирована,
    чтобы будущий дрейф транзитивной версии не прошёл незамеченным мимо CI.
    """
    from importlib.metadata import version

    assert version("gallery-dl") == "1.32.12"
    assert version("aiogram") == "3.31.0"
    assert version("SQLAlchemy") == "2.0.52"
    assert version("aiosqlite") == "0.22.1"
    assert version("pydantic-settings") == "2.15.0"
    assert version("loguru") == "0.7.3"
    assert version("yt-dlp") == "2026.8.19"
    assert version("curl_cffi") == "0.16.3"
