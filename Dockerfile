FROM python:3.12-slim AS base

RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg curl unzip ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && curl -fsSL https://deno.land/install.sh | DENO_INSTALL=/usr/local sh \
    && deno --version

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && pip install --no-cache-dir --upgrade "yt-dlp[default,curl-cffi]" gallery-dl

# Тестовые зависимости ставятся ДО копирования кода: иначе любая правка
# исходников инвалидирует слой и каждый прогон тестов заново тянет pytest из сети.
FROM base AS testdeps
COPY requirements-dev.txt .
RUN pip install --no-cache-dir -r requirements-dev.txt

FROM base AS runtime
COPY . .
RUN mkdir -p /app/data /srv/jw_downloads
CMD ["python", "-m", "bot"]

FROM testdeps AS test
COPY . .
RUN mkdir -p /app/data /srv/jw_downloads
CMD ["python", "-m", "pytest", "-q"]
