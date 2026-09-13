import logging
import os
import sys

import pytest
from loguru import logger

from bot.utils.log_guard import mask_secrets, scrub_log_file, setup_logging

# Заведомо ненастоящий секрет: цифровая часть и 35 символов «X».
# В публичном репозитории реальных значений быть не может.
FAKE_SECRET = "424242:" + "X" * 35

# Второй фейковый секрет — живёт ТОЛЬКО в локальной переменной кадра, в текст
# исключения не попадает. Ловит именно дамп локалей (diagnose), а не
# маскировку текста сообщения: если бы кто-то вернул диагностический
# форматтер loguru, этот тест поймал бы утечку, а первый (с секретом в
# тексте) — не обязательно.
FAKE_LOCAL_SECRET = "555555:" + "W" * 35


@pytest.fixture
def isolated_logger():
    """Loguru глобален — снимаем все синки до теста и после него.

    setup_logging() с этого раунда фиксов также трогает КОРНЕВОЙ stdlib-
    логгер (мост _InterceptHandler, находка ревью #2) — это тоже глобальное
    состояние процесса. Сохраняем и восстанавливаем его вокруг теста, чтобы
    тесты не текли друг в друга через logging.root.
    """
    root_logger = logging.getLogger()
    prev_handlers = list(root_logger.handlers)
    prev_level = root_logger.level
    logger.remove()
    yield logger
    logger.remove()
    logger.configure(patcher=None)
    root_logger.handlers = prev_handlers
    root_logger.setLevel(prev_level)


def test_bot_secret_is_masked_but_numeric_id_survives():
    text = f"POST https://api.telegram.org/bot{FAKE_SECRET}/sendMediaGroup"
    masked = mask_secrets(text)
    assert "X" * 35 not in masked
    assert "424242:<redacted>" in masked
    assert "sendMediaGroup" in masked


def test_share_tokens_in_query_string_are_masked():
    text = "Starting download | url=https://www.instagram.com/reel/AbC/?igsh=MXY5eg%3D%3D&utm_source=ig"
    masked = mask_secrets(text)
    assert "MXY5eg" not in masked
    assert "igsh=<redacted>" in masked
    # Не-секретные параметры не трогаем.
    assert "utm_source=ig" in masked


def test_cookie_header_secrets_are_masked_outside_query_string():
    """Important-находка ревью: _QUERY_SECRET_RE был жёстко привязан к
    `[?&]` — имена вроде sessionid/csrftoken вне query-строки (например,
    заголовок Cookie) проходили мимо. Актуально: downloader.py сливает
    полный stderr yt-dlp на уровне ERROR."""
    text = "Cookie: sessionid=abc123def456; csrftoken=xyz789uvw000; theme=dark"
    masked = mask_secrets(text)
    assert "abc123def456" not in masked
    assert "xyz789uvw000" not in masked
    assert "sessionid=<redacted>" in masked
    assert "csrftoken=<redacted>" in masked
    # Разделители и обычный (не секретный) cookie не калечим.
    assert "; csrftoken" in masked
    assert "theme=dark" in masked
    assert mask_secrets(masked) == masked


def test_netscape_cookie_jar_line_is_masked():
    """Вторая форма из той же находки: netscape-формат банки кук (yt-dlp/
    gallery-dl) — поля разделены табом, у имени и значения нет «=» вообще."""
    line = ".instagram.com\tTRUE\t/\tTRUE\t1999999999\tsessionid\tabc123def456ghi789\n"
    masked = mask_secrets(line)
    assert "abc123def456ghi789" not in masked
    assert "\tsessionid\t<redacted>" in masked
    # Структура строки (домен, флаги, путь) не ломается.
    assert masked.startswith(".instagram.com\tTRUE\t/\tTRUE\t1999999999\t")
    assert mask_secrets(masked) == masked


def test_masking_is_idempotent():
    once = mask_secrets(f"token={FAKE_SECRET} url=https://x/y?stkn=abcdef")
    assert mask_secrets(once) == once


def test_masking_never_raises_on_odd_input():
    assert mask_secrets("") == ""
    assert mask_secrets("нет тут секретов") == "нет тут секретов"
    assert isinstance(mask_secrets(12345), str)


def test_setup_logging_masks_every_sink(tmp_path, isolated_logger):
    log_file = tmp_path / "bot.log"
    setup_logging(str(log_file))
    logger.error("Failed to send file: POST https://api.telegram.org/bot{}/sendVideo", FAKE_SECRET)
    logger.remove()  # закрываем файловый синк, чтобы содержимое точно дошло до диска

    written = log_file.read_text(encoding="utf-8")
    assert "X" * 35 not in written
    assert "424242:<redacted>" in written


def test_setup_logging_drops_preexisting_sinks(tmp_path, isolated_logger):
    """Important-находка ревью: прежняя версия этого теста проверяла только
    отсутствие DEBUG-записи в ФАЙЛЕ, а дефолтный хендлер loguru пишет в
    STDERR — тест оставался зелёным даже без logger.remove() внутри
    setup_logging (ревьюер это воспроизвёл).

    Naive-подсчёт числа хендлеров после setup_logging тоже мимо: фикстура
    isolated_logger сама делает logger.remove() ДО теста, так что дефолтный
    синк loguru (id 0) уже снят к моменту вызова setup_logging, независимо
    от того, зовёт ли она logger.remove() сама, — счётчик был бы «2» в
    обоих случаях (сам так и получил на реальной поломке, пока проверял).
    Поэтому явно заводим ПОСТОРОННИЙ синк перед setup_logging — эмулируем
    состояние «что-то уже сконфигурировано до нас» — и проверяем, что
    setup_logging его снимает: после неё должно остаться ровно два хендлера,
    оба наши (stderr + файл), независимо от того, что было до вызова.
    """
    logger.add(sys.stderr)  # посторонний синк, эмулирует «настроено до нас»
    log_file = tmp_path / "bot.log"

    setup_logging(str(log_file))

    assert len(logger._core.handlers) == 2

    logger.debug("Using cookies file: /tmp/jw_downloads/jw_cookies_abc/cookies.txt")
    logger.remove()

    written = log_file.read_text(encoding="utf-8")
    assert "Using cookies file" not in written


def test_exception_channel_is_masked_in_every_sink(tmp_path, isolated_logger, capsys):
    """Критическая находка ревью (C-3): loguru рендерит traceback из
    record["exception"] отдельно от record["message"] и дописывает его к
    каждому синку уже ПОСЛЕ патчера — маскировка текста сообщения секрет из
    исключения не видела (живые точки: logger.exception в downloader.py).
    Отягчающее: diagnose=True (дефолт loguru) дампит значения локальных
    переменных кадра — второй, независимый канал утечки (прокси-креды,
    путь к банке кук). Проверяем оба канала сразу в обоих синках."""
    log_file = tmp_path / "bot.log"
    setup_logging(str(log_file))

    def _boom():
        proxy_credential = FAKE_LOCAL_SECRET  # noqa: F841 — только для дампа локалей
        raise RuntimeError(f"POST https://api.telegram.org/bot{FAKE_SECRET}/sendVideo failed")

    try:
        _boom()
    except RuntimeError:
        logger.exception("Не удалось отправить файл")

    logger.remove()  # закрываем файловый синк, чтобы содержимое точно дошло до диска

    written = log_file.read_text(encoding="utf-8")
    stderr_output = capsys.readouterr().err

    for output, sink_name in ((written, "файл"), (stderr_output, "stderr")):
        assert "X" * 35 not in output, f"секрет из текста исключения утёк в {sink_name}"
        assert "W" * 35 not in output, f"секрет из локальной переменной утёк в {sink_name} (diagnose)"
        assert "424242:<redacted>" in output
        # Маскировка не должна тихо съедать сам факт исключения.
        assert "RuntimeError" in output


def test_stdlib_logging_is_bridged_and_masked(tmp_path, isolated_logger, capsys):
    """Important-находка ревью: aiogram/aiohttp логируют исключения хендлеров
    через стандартный logging с exc_info; корневой логгер не сконфигурирован
    — срабатывает logging.lastResort и пишет прямо в stderr контейнера, мимо
    патчера. Проверяем, что запись через stdlib logging доходит до loguru
    (и, соответственно, маскируется) в обоих синках."""
    log_file = tmp_path / "bot.log"
    setup_logging(str(log_file))

    stdlib_logger = logging.getLogger("tests.stdlib_bridge")
    try:
        raise RuntimeError(f"POST https://api.telegram.org/bot{FAKE_SECRET}/sendVideo failed")
    except RuntimeError:
        stdlib_logger.error("Unhandled exception in handler", exc_info=True)

    logger.remove()

    written = log_file.read_text(encoding="utf-8")
    stderr_output = capsys.readouterr().err

    for output, sink_name in ((written, "файл"), (stderr_output, "stderr")):
        assert "X" * 35 not in output, f"секрет из stdlib logging утёк в {sink_name}"
        assert "424242:<redacted>" in output
        assert "Unhandled exception in handler" in output


def test_scrub_log_file_rewrites_existing_log_in_place(tmp_path):
    log_file = tmp_path / "bot.log"
    log_file.write_text(
        "первая строка без секретов\n"
        f"вторая строка: https://api.telegram.org/bot{FAKE_SECRET}/getUpdates\n"
        "третья строка без секретов\n",
        encoding="utf-8",
    )

    changed = scrub_log_file(log_file)

    content = log_file.read_text(encoding="utf-8")
    assert changed == 1
    assert "X" * 35 not in content
    assert "424242:<redacted>" in content
    assert content.count("\n") == 3
    assert "первая строка без секретов" in content


def test_scrub_log_file_does_not_replace_inode(tmp_path):
    """Important-находка ревью: старая реализация писала во временный файл и
    делала replace() — атомарный rename меняет inode. У loguru файловый
    синк открыт в режиме "a" (O_APPEND): конкурентный писатель держит fd на
    СТАРЫЙ inode, после подмены секрет остаётся в открепленном inode
    (читается через /proc/<pid>/fd/N до перезапуска), а новые записи бота
    с этого момента на исходном пути не видны. Правка редактирует файл на
    месте — inode до и после должен быть тем же."""
    log_file = tmp_path / "bot.log"
    log_file.write_text(
        f"POST https://api.telegram.org/bot{FAKE_SECRET}/getUpdates\n",
        encoding="utf-8",
    )
    inode_before = os.stat(log_file).st_ino

    scrub_log_file(log_file)

    inode_after = os.stat(log_file).st_ino
    assert inode_before == inode_after


def test_scrub_log_file_preserves_a_live_appender(tmp_path):
    """Та же находка, поведенчески: имитируем живого писателя (loguru,
    mode="a"/O_APPEND по умолчанию) — держим свой fd открытым через весь
    scrub и проверяем, что запись ПОСЛЕ очистки долетает туда же, куда и
    запись ДО неё, а не в осиротевший inode."""
    log_file = tmp_path / "bot.log"
    log_file.write_text(
        f"before: https://api.telegram.org/bot{FAKE_SECRET}/getUpdates\n",
        encoding="utf-8",
    )

    with open(log_file, "a", encoding="utf-8") as live_writer:
        scrub_log_file(log_file)
        live_writer.write("after scrub: без секретов\n")

    content = log_file.read_text(encoding="utf-8")
    assert "X" * 35 not in content
    assert "424242:<redacted>" in content
    assert "after scrub: без секретов" in content
