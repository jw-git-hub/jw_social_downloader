import re
import shlex
from pathlib import Path

import pytest

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


# --- Регресс-барьер H-11: в Dockerfile должна быть ровно одна установка из
# каждого requirements-файла, и никакой другой pip install рядом. -----------
#
# Раунд 1 проверял это подстрокой `"--upgrade" not in dockerfile` — не ловил
# синоним `-U` и ложно падал от слова «--upgrade» в прозе комментария.
# Раунд 2 переписал на разбор построчных `pip install`-строк — но фильтром
# была подстрока `"pip install" in stripped`, а её обходит `pip3 install`
# (между `pip` и `install` стоит `3`, подстроки "pip install" там нет) и
# `pip  install` с двойным пробелом. Оба обхода ревью нашло эмпирически и
# подтвердило, что `pip3` реально есть в образе (`which pip3`).
#
# Ниже — разбор по КОМАНДЕ, а не по подстроке: для каждой RUN-инструкции
# (с склейкой `\`-переносов) команда бьётся на подкоманды по `&&`/`;`/`|`,
# каждая подкоманда токенизируется через shlex, и первый токен(ы) сверяются
# с формами `pip`/`pip3`/`pip3.12`/`python -m pip`. Найденная команда всё
# равно должна дословно совпасть с одним из двух ожидаемых вызовов — так
# ловятся не только альтернативные имена интерпретатора, но и `-U`/
# `--upgrade`/лишние пакеты рядом с легитимным вызовом.

_RUN_RE = re.compile(r"^RUN\s+(.*)$", re.IGNORECASE)
_COMMAND_SPLIT_RE = re.compile(r"&&|\|\||;|\|")
_PIP_NAME_RE = re.compile(r"^pip3?(?:\.\d+)?$")
_PYTHON_NAME_RE = re.compile(r"^python3?(?:\.\d+)?$")


def _looks_like_pip_install(tokens: list[str]) -> bool:
    """`pip`/`pip3`/`pip3.12 install ...` или `python[3[.NN]] -m pip install ...`."""
    if len(tokens) >= 2 and _PIP_NAME_RE.fullmatch(tokens[0]) and tokens[1] == "install":
        return True
    if (
        len(tokens) >= 4
        and _PYTHON_NAME_RE.fullmatch(tokens[0])
        and tokens[1] == "-m"
        and tokens[2] == "pip"
        and tokens[3] == "install"
    ):
        return True
    return False


def _logical_lines(dockerfile_text: str) -> list[str]:
    """Инструкции Dockerfile: комментарии выброшены, `\\`-переносы склеены в
    одну логическую строку (иначе вторая физическая строка переноса не
    видна как продолжение той же RUN-инструкции)."""
    logical: list[str] = []
    buffer = ""
    for raw_line in dockerfile_text.splitlines():
        stripped = raw_line.strip()
        if not buffer and stripped.startswith("#"):
            continue  # строка целиком комментарий — не инструкция
        if stripped.endswith("\\"):
            buffer += stripped[:-1] + " "
            continue
        logical.append((buffer + stripped).strip())
        buffer = ""
    if buffer:
        logical.append(buffer.strip())
    return logical


def _pip_install_commands_in(dockerfile_text: str) -> set[str]:
    """Все подкоманды из RUN-инструкций, распознанные как вызов pip install
    (дословный текст подкоманды, без нормализации — чтобы `-U`/`--upgrade`/
    лишние пакеты остались видны при сравнении с эталоном)."""
    found: set[str] = set()
    for line in _logical_lines(dockerfile_text):
        match = _RUN_RE.match(line)
        if not match:
            continue
        for sub_command in _COMMAND_SPLIT_RE.split(match.group(1)):
            sub_command = sub_command.strip()
            if not sub_command:
                continue
            try:
                tokens = shlex.split(sub_command)
            except ValueError:
                tokens = sub_command.split()
            if _looks_like_pip_install(tokens):
                found.add(sub_command)
    return found


def _pip_install_commands() -> set[str]:
    return _pip_install_commands_in(_dockerfile_text())


def test_dockerfile_has_exactly_the_two_expected_pip_installs():
    """H-11, ревью раундов 1 и 2: единственный автоматический барьер против
    возврата бага. Множество распознанных вызовов pip install должно
    состоять ровно из двух — по одному на каждый requirements-файл — и
    дословно совпадать с ожидаемым. Список форм, которые это обязано ловить
    и не ловить, задокументирован тестами ниже — это не полный shell-парсер,
    а достаточный барьер против честной попытки вернуть H-11."""
    assert _pip_install_commands() == {
        "pip install --no-cache-dir -r requirements.txt",
        "pip install --no-cache-dir -r requirements-dev.txt",
    }


# --- Явный список форм, которые детектор обязан распознавать как вызов pip
# install — раунд 2 попросил не выводить границы защиты из реализации. -----


@pytest.mark.parametrize(
    "command_line",
    [
        pytest.param("pip install --no-cache-dir -r requirements.txt", id="pip"),
        pytest.param("pip3 install --no-cache-dir -r requirements.txt", id="pip3"),
        pytest.param("pip3.12 install --no-cache-dir -r requirements.txt", id="pip3.12"),
        pytest.param("python -m pip install --no-cache-dir -r requirements.txt", id="python -m pip"),
        pytest.param("python3 -m pip install --no-cache-dir -r requirements.txt", id="python3 -m pip"),
        pytest.param("pip  install --no-cache-dir -r requirements.txt", id="двойной пробел"),
    ],
)
def test_pip_install_detector_recognizes_every_known_form(command_line):
    """Ревью раунда 2: `pip3 install` и `pip  install` (двойной пробел)
    прошли мимо фильтра раунда 2 незамеченными — это была проверка
    подстроки `"pip install" in stripped`, а не команды."""
    found = _pip_install_commands_in(f"RUN {command_line}\n")
    assert found, f"{command_line!r} должен быть распознан как вызов pip install"


@pytest.mark.parametrize(
    "bad_run_instruction",
    [
        pytest.param(
            'RUN pip install --no-cache-dir -r requirements.txt '
            '&& pip install --no-cache-dir -U "yt-dlp[default,curl-cffi]" gallery-dl',
            id="-U через &&",
        ),
        pytest.param(
            'RUN pip install --no-cache-dir -r requirements.txt '
            '&& pip install --no-cache-dir --upgrade "yt-dlp[default,curl-cffi]" gallery-dl',
            id="--upgrade через &&",
        ),
        pytest.param(
            'RUN pip install --no-cache-dir -r requirements.txt\n'
            'RUN pip install --no-cache-dir -U gallery-dl',
            id="лишний отдельный RUN pip install",
        ),
        pytest.param(
            'RUN pip install --no-cache-dir -r requirements.txt\n'
            'RUN pip3 install --no-cache-dir --upgrade "yt-dlp[default,curl-cffi]" gallery-dl',
            id="лишний отдельный RUN pip3 install (найдено ревью раунда 2)",
        ),
        pytest.param(
            'RUN pip install --no-cache-dir -r requirements.txt \\\n'
            '    && pip install --no-cache-dir --upgrade "yt-dlp[default,curl-cffi]" gallery-dl',
            id="перенос строки через \\",
        ),
        pytest.param(
            'RUN pip install --no-cache-dir -r requirements.txt\n'
            'RUN python -m pip install --no-cache-dir -U gallery-dl',
            id="лишний отдельный RUN python -m pip install",
        ),
    ],
)
def test_regression_barrier_catches_every_known_bypass(bad_run_instruction):
    """Каждая форма здесь — конкретный обход, а не гипотеза: первые две и
    перенос строки воспроизводят исходный H-11, следующие две — ровно то,
    что нашло ревью раундов 1 и 2 (в т.ч. `pip3 install`, прошедший мимо
    предыдущей версии фильтра). Каждая обязана сломать инвариант «в файле
    ровно два вызова pip install, дословно»."""
    dockerfile = (
        bad_run_instruction
        + "\n\nFROM base AS testdeps\n"
        + "COPY requirements-dev.txt .\n"
        + "RUN pip install --no-cache-dir -r requirements-dev.txt\n"
    )
    assert _pip_install_commands_in(dockerfile) != {
        "pip install --no-cache-dir -r requirements.txt",
        "pip install --no-cache-dir -r requirements-dev.txt",
    }


def test_regression_barrier_does_not_false_positive_on_comment_prose():
    """Раунд 1: комментарий, упоминающий `pip install`/`--upgrade` в прозе,
    не должен ломать барьер — именно так падал предыдущий вариант теста."""
    dockerfile = (
        "COPY requirements.txt .\n"
        "# Не делай здесь отдельный pip install --upgrade, это был баг H-11\n"
        "RUN pip install --no-cache-dir -r requirements.txt\n"
        "\n"
        "FROM base AS testdeps\n"
        "COPY requirements-dev.txt .\n"
        "RUN pip install --no-cache-dir -r requirements-dev.txt\n"
    )
    assert _pip_install_commands_in(dockerfile) == {
        "pip install --no-cache-dir -r requirements.txt",
        "pip install --no-cache-dir -r requirements-dev.txt",
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
