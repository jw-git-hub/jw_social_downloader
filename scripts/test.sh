#!/usr/bin/env bash
# Единственный поддерживаемый способ прогона тестов: внутри образа,
# потому что хостовой Python 3.10 в этом окружении имеет битый aiogram.
set -euo pipefail
cd "$(dirname "$0")/.."
docker build --target test -t jw_downloader:test .
docker run --rm jw_downloader:test python -m pytest "$@"
