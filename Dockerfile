FROM python:3.12-slim AS base

RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg curl unzip ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && curl -fsSL https://deno.land/install.sh | DENO_INSTALL=/usr/local sh \
    && deno --version

WORKDIR /app

COPY requirements.txt .
# Единственная установка зависимостей. Отдельной команды с апгрейдом пакетов
# поверх пинов быть не должно: она кэшировалась вместе с этим слоем и молча
# переустанавливала пакеты мимо requirements.txt. Теперь слой инвалидируется
# правкой самого requirements.txt.
RUN pip install --no-cache-dir -r requirements.txt

# Тестовые зависимости ставятся ДО копирования кода: иначе любая правка
# исходников инвалидирует слой и каждый прогон тестов заново тянет pytest из сети.
FROM base AS testdeps
COPY requirements-dev.txt .
RUN pip install --no-cache-dir -r requirements-dev.txt

FROM base AS runtime
COPY . .
# Бот кормит недоверенным контентом yt-dlp, gallery-dl и ffmpeg на каждой
# загрузке, работает с host-сетью и раньше делал это от root. Непривилегированный
# пользователь убирает самый дорогой исход любой дыры в этих утилитах.
RUN mkdir -p /app/data /srv/jw_downloads \
    && groupadd --gid 1000 botuser \
    && useradd --uid 1000 --gid 1000 --no-create-home --shell /usr/sbin/nologin botuser \
    && chown -R botuser:botuser /app /srv/jw_downloads
# Ручная проверка этой стадии (ревью раунда 1): дешёвого способа проверить
# uid боевой стадии изнутри pytest нет — тесты гоняются в стадии test,
# которая от runtime не наследуется и осознанно осталась root (см. ниже), а
# docker-in-docker ради одного assert того не стоит. Проверяется командой:
#   docker build --target runtime -t tmp . && docker run --rm tmp id
USER botuser
CMD ["python", "-m", "bot"]

FROM testdeps AS test
COPY . .
RUN mkdir -p /app/data /srv/jw_downloads
CMD ["python", "-m", "pytest", "-q"]
