from __future__ import annotations

import os
import re
import sys
import tempfile
from pathlib import Path

from loguru import logger

REDACTED = "<redacted>"

# Секрет бота в URL Bot API: <цифры>:<35+ символов base64url>. Цифровую часть
# оставляем — это публичный id бота, по нему удобно искать в логе.
# Лукбихайнд именно на цифру, а НЕ `\b`: в URL секрет идёт сразу за «bot»
# (`/bot424242:...`), и границы слова между `t` и `4` не существует.
_BOT_SECRET_RE = re.compile(r"(?<!\d)(\d{5,16}):[A-Za-z0-9_-]{30,}")

# Приватные токены в query-строке. Это пер-шаринговые значения, привязанные к
# аккаунту отправителя, а не публичные id: в лог им попадать нельзя.
_QUERY_SECRET_RE = re.compile(
    r"([?&](?:stkn|igsh|igshid|si|share_id|sharing_token|token|access_token"
    r"|auth_token|sig|signature|key|sessionid|csrftoken)=)[^&\s\"'<>#]+",
    re.IGNORECASE,
)


def mask_secrets(text: str) -> str:
    """Вырезает секреты из произвольного текста.

    Идемпотентна: `<redacted>` под шаблоны не подходит, поэтому повторный
    прогон ничего не меняет. Никогда не бросает исключений — её зовут из
    патчера логгера, и падение здесь означало бы падение логирования.
    """
    if not isinstance(text, str):
        text = str(text)
    if not text:
        return text
    masked = _BOT_SECRET_RE.sub(lambda m: f"{m.group(1)}:{REDACTED}", text)
    return _QUERY_SECRET_RE.sub(lambda m: f"{m.group(1)}{REDACTED}", masked)


def _mask_record(record: dict) -> None:
    """Патчер loguru: правит уже отформатированное сообщение до всех синков."""
    record["message"] = mask_secrets(record["message"])


def setup_logging(log_path: str = "data/bot.log") -> None:
    """Единственная точка настройки логирования.

    `logger.remove()` обязателен: дефолтный обработчик loguru — это `<stderr>`
    с уровнем DEBUG, и без его снятия DEBUG-записи загрузчика (блоб stderr
    внешнего процесса, путь к банке кук) уходят в лог контейнера мимо уровня
    INFO, настроенного на файловом синке.
    """
    logger.remove()
    logger.configure(patcher=_mask_record)
    logger.add(sys.stderr, level="INFO")
    logger.add(log_path, rotation="10 MB", retention="7 days", level="INFO")


def scrub_log_file(path: Path | str) -> int:
    """Переписывает существующий лог-файл без секретов. Возвращает число
    изменённых строк.

    Пишем во временный файл рядом и подменяем `replace()`, чтобы обрыв на
    середине не оставил полупустой лог. Исходник НЕ сохраняется: смысл
    операции — убрать секрет с диска, а копия рядом его бы сохранила.
    """
    path = Path(path)
    changed = 0
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".scrub-")
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as out, path.open(
            "r", encoding="utf-8", errors="replace", newline=""
        ) as src:
            for line in src:
                masked = mask_secrets(line)
                if masked != line:
                    changed += 1
                out.write(masked)
        os.chmod(tmp_path, 0o600)
        tmp_path.replace(path)
    finally:
        if tmp_path.exists():
            tmp_path.unlink()
    return changed
