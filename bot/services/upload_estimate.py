"""Оценка времени отправки видео в Telegram (сервер → telegram-bot-api).

`reply_video` возвращает управление только после того, как файл целиком
принят сервером Telegram — самого прогресса отдачи бот не видит (см. живой
замер в брифе, раздел 1.4). Вместо честного процента показываем оценку:
размер файла делим на среднюю скорость последних крупных отправок.
"""

from __future__ import annotations

from collections import deque

DEFAULT_UPLOAD_MIB_PER_SEC = 4.8  # замеры 2026-09-27 сервер→Telegram: 4.6–5.2 МиБ/с
UPLOAD_SAMPLE_MIN_MB = 100  # на мелких файлах скорость тонет в накладных расходах
UPLOAD_SAMPLE_WINDOW = 5


class UploadRateTracker:
    """Скользящее среднее скорости отправки по последним крупным файлам.

    Живёт один экземпляр на процесс бота (см. `bot/handlers/user.py`):
    скорость сервер→Telegram не зависит от конкретного пользователя.
    """

    def __init__(self) -> None:
        self._samples: deque[tuple[float, float]] = deque(maxlen=UPLOAD_SAMPLE_WINDOW)

    def record(self, size_mb: float, seconds: float) -> None:
        """Игнорирует мелкие файлы и нулевую/отрицательную длительность."""
        if size_mb < UPLOAD_SAMPLE_MIN_MB or seconds <= 0:
            return
        self._samples.append((size_mb, seconds))

    def rate_mb_per_sec(self) -> float:
        """Скорость по сумме окна, а не среднее отдельных скоростей —
        так один короткий ретрай внутри крупной отправки не перекашивает
        оценку сильнее, чем реально исказил суммарное время."""
        if not self._samples:
            return DEFAULT_UPLOAD_MIB_PER_SEC
        total_size = sum(size for size, _seconds in self._samples)
        total_seconds = sum(seconds for _size, seconds in self._samples)
        return total_size / total_seconds

    def expected_seconds(self, size_mb: float) -> float:
        return size_mb / self.rate_mb_per_sec()
