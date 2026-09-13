from __future__ import annotations

import logging
import os
import re
import sys
import traceback
from pathlib import Path

from loguru import logger

REDACTED = "<redacted>"

# Секрет бота в URL Bot API: <цифры>:<35+ символов base64url>. Цифровую часть
# оставляем — это публичный id бота, по нему удобно искать в логе.
# Лукбихайнд именно на цифру, а НЕ `\b`: в URL секрет идёт сразу за «bot»
# (`/bot424242:...`), и границы слова между `t` и `4` не существует.
_BOT_SECRET_RE = re.compile(r"(?<!\d)(\d{5,16}):[A-Za-z0-9_-]{30,}")

# Имена приватных пер-шаринговых токенов/кук — общие для query-строки и для
# форм вне неё (заголовок Cookie, netscape cookie-жестянка). Список один,
# чтобы обе формы не расходились между собой при правках.
_SENSITIVE_KEYS = (
    r"stkn|igsh|igshid|si|share_id|sharing_token|token|access_token"
    r"|auth_token|sig|signature|key|sessionid|csrftoken"
)

# Приватные токены в query-строке И вне неё: заголовок `Cookie: sessionid=…;
# csrftoken=…` не имеет `?`/`&` перед именем — там разделитель «; » или
# пробел после «:». Префикс расширен до «?/&/; или начало строки, или после
# пробела/двоеточия». MULTILINE — чтобы «начало строки» срабатывало и внутри
# многострочного текста (например, замаскированного traceback'а — находка
# ревью C-3, см. `_mask_record`). Отдельно исключаем `;` из символов
# значения — иначе значение первого параметра проглотило бы разделитель до
# следующего.
_QUERY_SECRET_RE = re.compile(
    rf"((?:[?&;]|^|(?<=[\s:]))(?:{_SENSITIVE_KEYS})=)[^&;\s\"'<>#]+",
    re.IGNORECASE | re.MULTILINE,
)

# Netscape-формат cookie-жестянки (yt-dlp/gallery-dl): поля разделены табом,
# у имени и значения нет «=» вообще — «...\tsessionid\tECT8Cy...». Отдельный
# шаблон, а не альтернатива в предыдущем: форма принципиально другая, а не
# просто другой разделитель.
_NETSCAPE_COOKIE_RE = re.compile(
    rf"(\t(?:{_SENSITIVE_KEYS})\t)[^\t\r\n]+",
    re.IGNORECASE,
)


def mask_secrets(text: str) -> str:
    """Вырезает секреты из произвольного текста: URL Bot API, query-параметры,
    заголовок Cookie, поля netscape cookie-жестянки.

    Идемпотентна: `<redacted>` под шаблоны не подходит (в частности, `<`/`>`
    исключены из символов значения), поэтому повторный прогон ничего не
    меняет. Никогда не бросает исключений — её зовут из патчера логгера, и
    падение здесь означало бы падение логирования.
    """
    if not isinstance(text, str):
        text = str(text)
    if not text:
        return text
    masked = _BOT_SECRET_RE.sub(lambda m: f"{m.group(1)}:{REDACTED}", text)
    masked = _QUERY_SECRET_RE.sub(lambda m: f"{m.group(1)}{REDACTED}", masked)
    return _NETSCAPE_COOKIE_RE.sub(lambda m: f"{m.group(1)}{REDACTED}", masked)


def _format_exception(record: dict) -> str:
    """Форматирует `record["exception"]` стандартным `traceback`, а НЕ
    диагностическим форматтером loguru.

    Это осознанный выбор, а не экономия: диагностический форматтер loguru
    (`diagnose=True`, дефолт) дампит значения локальных переменных кадра —
    ровно то, ради чего в этом раунде выставляется `diagnose=False`
    (прокси-креды, путь к банке кук). Стандартный `traceback` локали не
    печатает никогда, независимо ни от каких флагов — не «случайно не
    показывает», а структурно не умеет, поэтому и выбран.
    """
    exc = record["exception"]
    lines = traceback.format_exception(exc.value)
    return "".join(lines).rstrip("\n")


def _mask_record(record: dict) -> None:
    """Патчер loguru: правит уже отформатированное сообщение до всех синков.

    Секрет утекает не только через `record["message"]`. loguru рендерит
    traceback из `record["exception"]` отдельно и дописывает его к каждому
    синку уже ПОСЛЕ патчера — маскировка сообщения этот канал не видит
    (критическая находка ревью, C-3: `logger.exception(...)` в
    `bot/services/downloader.py`). Поэтому здесь же вручную форматируем
    исключение (см. `_format_exception`), маскируем результат, приклеиваем к
    сообщению и обнуляем `record["exception"]` — чтобы синки не отрендерили
    его ещё раз уже без маски поверх нашей работы.

    Форматирование исключения обёрнуто в `try`: если оно вдруг само
    сломается, важнее не уронить логирование и не пропустить неотформатированное
    исключение дальше как есть (это был бы путь утечки в обход маски) — в
    худшем случае теряем текст traceback для одной записи, но не секрет.
    """
    record["message"] = mask_secrets(record["message"])
    if record["exception"] is not None:
        try:
            formatted_exc = _format_exception(record)
        except Exception:
            formatted_exc = "[не удалось отформатировать traceback]"
        record["message"] += "\n" + mask_secrets(formatted_exc)
        record["exception"] = None


class _InterceptHandler(logging.Handler):
    """Мост stdlib `logging` → loguru (стандартный рецепт из документации
    loguru).

    aiohttp и aiogram логируют исключения хендлеров через стандартный
    `logging` с `exc_info`. Корневой логгер stdlib по умолчанию не
    сконфигурирован — срабатывает `logging.lastResort` и пишет прямо в
    stderr контейнера, мимо патчера и мимо уровня INFO файлового синка
    (важная находка ревью). Этот хендлер перехватывает такие записи на
    корне и передаёт их в loguru, где они проходят тот же патчер и те же
    синки, что и «родные» вызовы `logger.*`.
    """

    def emit(self, record: logging.LogRecord) -> None:
        try:
            level: str | int = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno

        # Находим кадр вызова, минуя внутренние кадры модуля logging, —
        # иначе loguru покажет в записи файл/строку самого logging, а не
        # реального источника.
        frame, depth = sys._getframe(6), 6
        while frame and frame.f_code.co_filename == logging.__file__:
            frame = frame.f_back
            depth += 1

        logger.opt(depth=depth, exception=record.exc_info).log(level, record.getMessage())


def setup_logging(log_path: str = "data/bot.log") -> None:
    """Единственная точка настройки логирования.

    `logger.remove()` обязателен: дефолтный обработчик loguru — это `<stderr>`
    с уровнем DEBUG, и без его снятия DEBUG-записи загрузчика (блоб stderr
    внешнего процесса, путь к банке кук) уходят в лог контейнера мимо уровня
    INFO, настроенного на файловом синке.

    `diagnose=False` на обоих синках — вторая линия обороны, а не единственная:
    патчер (`_mask_record`) уже обнуляет `record["exception"]` до того, как
    синк успел бы отрендерить диагностический блок с локалями, так что для
    ЭТИХ синков параметр практически не наблюдаем. Оставляем его явным, а не
    полагаемся молча на порядок вызовов внутри loguru (патчер до эмита
    синка) — читающий эту функцию должен увидеть явный отказ от дампа
    локалей, а не выводить его из чужого кода. `backtrace=False` — по той же
    логике «явно безопасное значение»: расширенный бэктрейс не несёт
    отдельного риска утечки секрета, но раскрывает больше внутренней
    структуры вызовов, чем нужно операционному логу.

    `logging.basicConfig(..., force=True)` заворачивает КОРНЕВОЙ stdlib-
    логгер на loguru через `_InterceptHandler` — без этого исключения
    хендлеров aiogram и внутренние записи aiohttp идут мимо патчера
    (см. `_InterceptHandler`). `force=True` снимает любые хендлеры, которые
    могла выставить сама stdlib `logging` до нас (иначе рядом с нашим
    остался бы дефолтный `lastResort`-путь).
    """
    logging.basicConfig(handlers=[_InterceptHandler()], level=0, force=True)

    logger.remove()
    logger.configure(patcher=_mask_record)
    logger.add(sys.stderr, level="INFO", diagnose=False, backtrace=False)
    logger.add(
        log_path,
        rotation="10 MB",
        retention="7 days",
        level="INFO",
        diagnose=False,
        backtrace=False,
    )


def scrub_log_file(path: Path | str) -> int:
    """Переписывает существующий лог-файл без секретов НА МЕСТЕ — тот же
    inode до и после. Возвращает число изменённых строк.

    Почему не временный файл + `replace()` (как было раньше, важная находка
    ревью): это ломает конкурентного писателя. loguru открывает файловый
    синк в режиме `"a"` (дефолт, `O_APPEND`), и его файловый дескриптор
    указывает на СТАРЫЙ inode. `replace()` — атомарный `rename()`: путь
    начинает указывать на новый inode, а старый (с ещё не вычищенным
    секретом внутри) не освобождается, пока жив хоть один открытый fd
    (POSIX «delete on last close») — секрет остаётся читаемым через
    `/proc/<pid>/fd/N` до перезапуска процесса. При этом новые записи
    живого бота с этого момента уходят в открепленный inode и с исходного
    пути больше не видны — выглядит как тихая потеря лога, а скрипт при
    этом рапортует успех.

    Правим на месте (`seek(0)` + запись + `truncate()`) — та же схема, что
    `copytruncate` в logrotate. inode не меняется, путь как был, так и
    остаётся целью fd живого писателя; поскольку этот fd открыт с
    `O_APPEND`, следующая запись бота атомарно на уровне ядра уйдёт в
    актуальный (уже урезанный) конец файла — потери не будет. Бота
    останавливать не нужно.

    Осознанный компромисс: пропадает атомарность при обрыве самого скрипта
    (раньше `replace()` гарантировал — либо старый файл целиком, либо
    новый; теперь обрыв между `seek(0)` и `truncate()` может оставить файл
    в промежуточном состоянии). Смягчаем тем, что весь маскированный текст
    собирается в памяти ДО открытия файла на запись — на диск попадает
    только короткая операция «записать готовые байты и обрезать хвост». Даже
    в худшем случае это чинится повторным запуском скрипта, а не утечкой
    секрета в открепленный inode до перезапуска бота — тот исход хуже.
    """
    path = Path(path)
    changed = 0
    masked_lines = []
    with path.open("r", encoding="utf-8", errors="replace", newline="") as src:
        for line in src:
            masked = mask_secrets(line)
            if masked != line:
                changed += 1
            masked_lines.append(masked)

    with path.open("r+", encoding="utf-8", newline="") as dst:
        dst.seek(0)
        dst.writelines(masked_lines)
        dst.truncate()
        dst.flush()
        os.fsync(dst.fileno())
    os.chmod(path, 0o600)
    return changed
