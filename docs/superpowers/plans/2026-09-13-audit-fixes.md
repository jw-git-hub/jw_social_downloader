# Починка по ревизии 2026-09-12 — план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Закрыть находки ревизии 2026-09-12 (утечка секрета в логи, провалы отправок из-за уборщика, неатомарная квота, неидемпотентная выдача подписки, неэкранированный HTML, потери файлов Pinterest) восемью изолированными пакетами и финальной сборкой, не расконсервируя незавершённую миграцию на локальный Bot API.

**Architecture:** Работа нарезана на пакеты с жёстким разделением владения файлами — два пакета никогда не правят один файл, поэтому пакеты одной волны идут параллельно без конфликтов слияния. Первый пакет — операционный хотфикс на отдельной ветке от `main`: только он пригоден к выкладке в прод, пока ветка миграции в промежуточном состоянии. Всё остальное ложится на `feat/local-bot-api` поверх смерженного хотфикса.

**Tech Stack:** Python 3.12, aiogram 3.31.0, SQLAlchemy 2.0 (async) + aiosqlite, yt-dlp, gallery-dl 1.32.12, ffprobe, loguru, Docker Compose, pytest + pytest-asyncio.

**Spec:** `.claude/TODO_FIXES.md` — раздел «Ревизия 2026-09-12» плюс сохранившиеся C-1, H-2…H-7, M-*, Low. Читать вместе с планом: там для каждой находки указаны `файл:строка` и сценарий воспроизведения. Раздел «Порядок починки» того же файла задаёт приоритет, которому следует порядок пакетов ниже.

**Смежный незавершённый план:** `docs/superpowers/plans/2026-09-03-local-bot-api.md` (14 задач, выполнены 1–3). Его ломать нельзя. Что из ревизии сознательно отдано ему, а не этому плану, перечислено в разделе «Сознательно не закрывается этим планом».

---

## Global Constraints

- **Тесты запускаются ТОЛЬКО в контейнере** — `./scripts/test.sh [аргументы pytest]`. На хосте pytest не работает: сломаны ДВА пакета, `aiogram` (`UnicodeDecodeError` в `chat_full_info.py`) и `python-dotenv` (`ValueError: source code string cannot contain null bytes`), из-за чего не импортируется даже `bot.config`. Команда `pytest …` в плане не встречается ни разу; любой шаг «прогнать тесты» — это `./scripts/test.sh`.
- **Формат сообщений коммитов:** conventional commits, английский, как в истории проекта (`fix(media): check ffprobe exit code, harden parsing, add reject codes`, `feat(config): add local Bot API settings, raise size and timeout limits`, `test: add pytest infrastructure with a dedicated Docker stage`). **НИКАКИХ трейлеров `Co-Authored-By:` и `Claude-Session:`** — владелец запретил, Claude не должен попадать в контрибьюторы. Сообщение коммита заканчивается содержательной строкой, никаких служебных подписей после неё.
- **`docker compose up` в этом плане не делается ни одним шагом.** Разрешены `docker build` (через `scripts/test.sh`) и разовые `docker run --rm` для миграции БД и живых проверок. Выкладка — отдельное решение владельца после Task 14 плана local-bot-api.
- **ЗАПРЕТ из плана local-bot-api действует:** не делать `docker compose up -d --build` на ветке `feat/local-bot-api` до её Task 14 (загрузчик читает лимит 1500 МБ, транспорт ещё облачный с потолком 50 МБ, tmpfs 200 МБ — получится ENOSPC в середине загрузки). Не вызывать `logOut`.
- **Репозиторий ПУБЛИЧНЫЙ** (`jw-git-hub/jw_social_downloader`), `docs/` коммитится. В код, тесты, комментарии и документы не попадают: значения токенов и кук, реальные Telegram user-id (в примерах и тестах — только заведомо выдуманные вроде `1000000001`), содержимое `.env`, характеристики и сетевая топология хоста. Находка про утёкший секрет описывается как «секрет в логах», без значений и без номеров строк лога.
- **Ownership файлов жёсткий.** Задача правит только файлы своего пакета. Если реализация упирается в чужой файл — это сигнал, что интерфейс спроектирован неверно; перепроектировать, а не лезть в чужой файл.
- **Не ломать то, что ревизия признала работающим:** host-network у бота (Docker-NAT на этом хосте роняет аплоады крупнее ~1.5 МБ); приоритет H.264/avc1 над AV1 для Facebook (иначе «звук без картинки»); эфемерные копии cookies (yt-dlp переписывает файл кук и убивает `sessionid`); серверная проверка `ADMIN_ID` во всех восьми точках входа админки ДО разбора `callback_data`; `create_subprocess_exec` без `shell=True`; префикс `uuid4().hex` в именах файлов как изоляция пользователей.
- **`TelegramEntityTooLarge` наследует `TelegramNetworkError`** — порядок `except` имеет значение.
- **Миграций в проекте нет,** схему создаёт `Base.metadata.create_all`. `create_all` заводит отсутствующие таблицы, но НЕ добавляет индексы к уже существующим таблицам и не делает `ALTER`. Любая задача, меняющая схему на боевой БД, обязана включать свежий бэкап и явный идемпотентный DDL.
- **Боевая БД живёт в именованном docker-томе** `jw_downloader_bot_data`, файл `bot.db`. Единственный существующий бэкап — от 21 августа в `backups/`. Локальный `data/bot.db` протух (апрель) и контейнером не используется.
- Все пользовательские тексты — на русском, в стиле существующих сообщений бота. Комментарии в коде — на русском, как в остальном проекте.
- Каждая задача заканчивается коммитом. Ни один пакет не мержится в `feat/local-bot-api` до того, как `./scripts/test.sh -q` зелёный целиком.

---

## Порядок исполнения и параллельность

| Волна | Пакеты | Можно ли параллельно | Ветка |
|---|---|---|---|
| 1 | **0. Хотфикс** (Task 1–7) | нет, один пакет | `fix/ops-hotfix` от `main` |
| — | мерж хотфикса в `feat/local-bot-api` (Task 7) | — | — |
| 2 | **A. Рантайм** (8–10), **B. Загрузчик** (11–16), **C. Слой БД** (17–21), **F. Инфра** (22–24) | **да, четыре пакета одновременно** — общих файлов нет | `feat/local-bot-api` |
| 3 | **D. Юзер-хендлер** (25–29), **E. Админка** (30–33) | **да, два пакета одновременно** — общих файлов нет | `feat/local-bot-api` |
| 4 | **Финальная сборка** (Task 37) | нет, одна задача | `feat/local-bot-api` |
| ⟂ | **V. Проверки гипотез** (34–36) | да, три независимые; ведутся в любой момент после Task 6 | `feat/local-bot-api` |

Внутри пакета задачи идут **строго последовательно** в порядке номеров: каждая опирается на предыдущую.

Волна 3 не может начаться раньше, чем в `feat/local-bot-api` попадут пакеты B и C: пакет D потребляет `reserve_free_download`/`refund_free_download` и `esc()` из C и константы классификации медиа из B, пакет E потребляет `apply_subscription_change` и `esc()` из C.

Task 37 обязана идти последней: она удаляет функции, которые до конца волны 3 ещё импортируются хендлерами.

Пакет V кода не меняет и от волн не зависит, но требует сети, живых кук и собранного образа из Task 6. Task 36 дополнительно заблокирована до Task 14 плана local-bot-api.

### Владение файлами

| Пакет | Владеет файлами |
|---|---|
| 0. Хотфикс | `bot/__main__.py`, `bot/services/cleanup.py`, `bot/services/downloader.py` (только guard вокруг `stat()`), `Dockerfile`, `requirements.txt`, новые `bot/utils/log_guard.py`, `scripts/scrub_logs.py`, `tests/test_log_guard.py`, `tests/test_cleanup.py` |
| A. Рантайм | `bot/__main__.py`, `bot/middlewares/throttle.py`, `tests/test_throttle.py`, `tests/test_error_handler.py` |
| B. Загрузчик | `bot/services/downloader.py`, `bot/services/media_probe.py`, `bot/utils/url_parser.py`, свои тестовые файлы |
| C. Слой БД | `bot/db/queries.py`, `bot/db/models.py`, `bot/db/engine.py`, новые `bot/utils/text.py`, `scripts/migrate_20260913.py`, `tests/conftest.py`, свои тестовые файлы |
| D. Юзер-хендлер | `bot/handlers/user.py`, свои тестовые файлы |
| E. Админка | `bot/handlers/admin.py`, `bot/keyboards/inline.py`, свои тестовые файлы |
| F. Инфра | `docker-compose.yml`, `.env.example`, `bot/config.py`, `.gitignore`, `requirements-dev.txt`, `Dockerfile`, свои тестовые файлы |
| V. Проверки | ничего не правит, кроме `.claude/TODO_FIXES.md` |
| Финальная сборка | `bot/db/queries.py` (только удаление мёртвых функций), `tests/test_queries_api.py` |

Пересечения разрешены так:

- `bot/__main__.py` — у пакетов 0 и A. Конфликта нет: 0 идёт в первой волне и мержится до старта A.
- `bot/services/downloader.py` — у пакетов 0 и B. Конфликта нет по той же причине; пакет 0 трогает в нём ровно один аспект (guard вокруг `stat()`), пакет B — всё остальное.
- `Dockerfile` — у пакетов 0 и F. Конфликта нет: после мержа хотфикса Dockerfile переходит во владение F, а в волне 2 больше никто его не трогает.
- `tests/conftest.py` — только у пакета C. Пакеты A, B, F в той же волне создают собственные тестовые файлы и conftest не редактируют; D и E в волне 3 фикстуру `db_session` только потребляют.
- `bot/utils/log_guard.py` создаёт пакет 0, потребляет пакет B (только вызывает `mask_secrets`, файл не правит).
- `bot/utils/text.py` создаёт пакет C, потребляют пакеты D и E (только вызывают `esc`, файл не правят).
- `bot/db/queries.py` — у пакета C и у Task 37. Конфликта нет: Task 37 идёт последней и в одиночку.
- `requirements-dev.txt` — создан черри-пиком в Task 1, дальше принадлежит пакету F.

---

## Действия владельца вне плана

Эти шаги план не выполняет и выполнить не может — они делаются руками и вне кода.

- [ ] **Ротировать токен бота в BotFather.** Секрет утёк в файловый лог и в лог контейнера (находка C-3), утечка уже произошла и существующий секрет надо считать скомпрометированным. Task 2 закрывает источник утечки и вычищает файловый лог, но выданный ранее секрет ротацией не занимается.
- [ ] **После ротации — заменить значение в `.env`.** Файл в `.gitignore`, агенту его содержимое не нужно и читать его не следует.
- [ ] **Усечь лог контейнера.** Тот же текст продублирован в json-file логе докера (3×10 МБ). Файловый sink чистит `scripts/scrub_logs.py` (Task 2), лог докера — нет; он усекается только пересозданием контейнера, а это выкладка, которая в план не входит.
- [ ] **Привести права боевой банки кук к `0600`** вместе с обеими копиями рядом с ней: сейчас файл читается любой локальной учёткой на машине. Действие на машине владельца, кода не касается (находка H-16, половина про режим файла).
- [ ] **Подготовить именованный том перед первой выкладкой контейнера от непривилегированного пользователя** — команда записана в Task 24, Шаг 7. Без неё контейнер не поднимется.
- [ ] **Выложить пакет 0 в прод** после его мержа — отдельным решением, вне этого плана.

---

## Сознательно не закрывается этим планом

Перечисленное ниже есть в `.claude/TODO_FIXES.md`, но отдано другому плану или отложено осознанно. Чекбоксы в TODO по ним закрывать нельзя.

| Находка | Кому отдано | Почему |
|---|---|---|
| 0.3, H-1 — селекторы качества по платформам, снятие пина клиентов YouTube | Task 7 плана local-bot-api | Селекторы завязаны на потолок 1500 МБ, который заработает только вместе с локальным Bot API. До этого «максимальное качество» упрётся в транспорт. |
| H-5 — проверка размера до отправки, отказ от ретраев `TelegramEntityTooLarge` | Task 9 плана local-bot-api | Логика отправки там переписывается целиком (отдача ссылкой `file://`), правка «в лоб» была бы выброшена. |
| H-7 (структурная часть), M-7 — живучий подметальщик с учётом занятых папок | Task 8 плана local-bot-api | Требует рабочей папки на загрузку, которой ещё нет. В пакете 0 закрыта только причина ложных удалений и живучесть фоновой задачи. |
| C-2 (структурная часть) — рабочая папка на загрузку | Task 5 плана local-bot-api | Белый список расширений закрывает Task 13 этого плана; изоляция каталогом остаётся там. |
| Low — нулевые и обрезанные файлы не подчищаются на успешном пути | Task 5 плана local-bot-api | Закрывается структурно удалением рабочей папки целиком в `finally`. Введение здесь отдельного поля с префиксом создало бы конфликт. |
| M-6, M-12 — семафор на аплоад, убийство группы процессов по таймауту | Task 6 и Task 9 плана local-bot-api | Уже в том плане, дублировать нельзя. |
| M-4 — проверять бан не только на пути загрузки | отложено | Потребовало бы запрос к БД на каждый апдейт либо правку всех хендлеров сразу в двух пакетах. Текущее покрытие (бан проверяется там, где тратятся ресурсы) приемлемо; возвращаться после волны 3. |
| M-25 (полностью) — ограничить хендлеры приватным чатом | отложено | `waiting_for_url` и `admin_waiting_search` ключуются по `user_id`, а не `(chat_id, user_id)`; корректная починка — переход на FSM со storage, это отдельная работа. В Task 10 закрыт только её следствие: `AttributeError` в троттле на сообщении без `from_user`. |
| Low — состояние диалога в process-local сетах, теряется при рестарте | отложено | Та же причина: лечится переходом на FSM со storage. |
| Low — закоммиченная спека публикует железо и сеть хоста | решение владельца | Ревизия зафиксировала это как выбор владельца, а не как дефект. |
| Low — удалить протухший локальный `data/bot.db`, переустановить aiogram на хосте | вне репозитория | Действия на рабочей машине, кода не касаются. |
| M-3 — эфемерные куки создаются вне цикла ретраев (`downloader.py:334` против `:356`) | отложено | Правка меняет структуру `download_media` ровно там, где Task 5 и Task 6 плана local-bot-api переносят запуск подпроцесса на потоковый вариант с убийством группы. Делать её дважды бессмысленно; после тех задач она станет тривиальной. |
| M-10 — гард на каждый элемент альбома (`user.py:279-345`) | отложено | Пересобирать сборку альбома имеет смысл вместе с Task 9 плана local-bot-api, которая переводит отдачу на ссылки `file://` и переписывает этот блок целиком. Частично смягчено Task 13: `.webp` и `.heic` больше не попадают в выдачу из промежуточных файлов. |
| Low — `_NA.mp4` встаёт впереди `_1.jpg` в сортировке каруселей | отложено | Относится к шаблону yt-dlp (`filename_%(playlist_index)s`), а не gallery-dl. Task 12 чинит только шаблон gallery-dl; шаблоны yt-dlp переписывает Task 7 плана local-bot-api. |
| Low — `ProcessLookupError` даёт пустое «Непредвиденная ошибка: » (`downloader.py:365`) | Task 6 плана local-bot-api | Там запуск подпроцесса и убийство группы переписываются целиком, вместе с обработкой уже умершего процесса. |
| Low — добавить `/cancel`, `/start` должен сбрасывать режим ожидания | отложено | Новая команда — это изменение набора команд бота (`set_my_commands`) и правка `bot/__main__.py`, то есть третий владелец у файла в волне 3. Делать отдельной задачей после Task 37. |
| Low — статистика админа считает провалы как скачивания (`queries.py:112-128`) | отложено | Требует согласования с владельцем: считать только `status='success'` или показывать обе цифры. Продуктовое решение, а не дефект кода. |
| Low — аллоулист URL не учитывает редиректы | закрыт частично (Task 15) | Task 15 закрывает полноту самого аллоулиста и добавляет негативные тесты на локальные адреса. Проверка адреса ПОСЛЕ редиректа требует перехвата на уровне yt-dlp/gallery-dl (`--no-playlist` тут не помогает) — отдельная работа. |
| Low — healthcheck в compose | отложено | Осмысленный healthcheck для лонг-поллинг-бота без входящих портов — это проверка живости самого поллинга, а её ещё нет. Возвращаться после Task 14 плана local-bot-api. |
| Low — профиль Pinterest целиком и `/search/pins/?q=` уходят в gallery-dl без ограничений | закрыт частично (Task 12) | `--range` ограничивает объём любого запроса, включая профиль и поиск. Отдельный отказ на такие URL — продуктовое решение (сейчас они работают), выносится владельцу. |
| Компоненты, п. 4 — пин `aiogram/telegram-bot-api:latest` → `:10.3` | Task 14 плана local-bot-api | Образ появляется в `docker-compose.yml` только там; пинить нечего, пока сервиса нет. |
| Открытый вопрос — свежий yt-dlp#17643 (Facebook `/reel/`) | наблюдение | Не подтверждён как массовый. Отслеживать, не чинить вслепую. |
| CVE — ребейз вендоренного libcurl в curl_cffi | наблюдение | Обновление пакета проблему не закрывает: 0.16.2/0.16.3 копию не подняли. Следить за релизами на ребейз ≥ 8.22.0. |

---
## Пакет 0 — Операционный хотфикс (ветка `fix/ops-hotfix` от `main`)

Единственный пакет, пригодный к выкладке в прод: ветка `feat/local-bot-api` до своей Task 14 находится в промежуточном состоянии (лимит 1500 МБ против tmpfs на 200 МБ) и расконсервирована быть не может. Поэтому операционные находки — утечка секрета в лог, ложные удаления свежих файлов, падение хендлера на исчезнувшем файле, устаревший gallery-dl — чинятся отдельной веткой от `main` и после проверки мержатся в `feat/local-bot-api` (Task 7).

Владеет файлами: `bot/__main__.py`, `bot/services/cleanup.py`, `bot/services/downloader.py` (только guard вокруг `stat()`), `Dockerfile`, `requirements.txt`, плюс создаёт `bot/utils/log_guard.py`, `scripts/scrub_logs.py` и свои тестовые файлы. Задачи 1–7 строго последовательны.

---

### Task 1: Ветка хотфикса и тестовая оснастка на ней

**Закрывает:** ничего напрямую — это предусловие TDD для Task 2–6.

**Files:**
- Create (черри-пиком): `scripts/test.sh`, `pytest.ini`, `requirements-dev.txt`, `tests/__init__.py`, `tests/conftest.py`, `tests/test_smoke.py`, `.dockerignore`
- Modify (черри-пиком): `Dockerfile`, `docker-compose.yml`

**Interfaces:**
- Consumes: ничего.
- Produces: команда `./scripts/test.sh [аргументы pytest]` — единственный поддерживаемый способ прогона тестов на этой ветке. Все последующие задачи пакета 0 её используют.

На `main` тестов нет вообще: инфраструктуру (многостадийный `Dockerfile` со стадией `test`, `scripts/test.sh`, `pytest.ini`, `tests/`) добавили уже на ветке миграции коммитами `48e5c44` и `f41be6f`. Без неё хотфикс пришлось бы делать без TDD. Оба коммита трогают только тестовую и докерную обвязку и не содержат ни кода бота, ни изменений конфигурации, поэтому переносятся на ветку от `main` черри-пиком без конфликтов: их родитель отличается от `main` только документацией, а `Dockerfile` и `docker-compose.yml` в нём побайтово совпадают с версиями из `main`.

Общая ветвь истории — ещё и условие бесконфликтного мержа в Task 7.

- [ ] **Шаг 1: Создать ветку хотфикса от `main`**

```bash
git checkout main
git pull --ff-only
git checkout -b fix/ops-hotfix
```

- [ ] **Шаг 2: Перенести тестовую оснастку черри-пиком**

```bash
git cherry-pick 48e5c44 f41be6f
```

Ожидается: оба коммита применяются без конфликтов. Если конфликт всё-таки возник — остановиться и разобраться, а не разрешать его руками: это значит, что `main` ушла вперёд с момента написания плана.

- [ ] **Шаг 3: Убедиться, что оснастка работает**

Команда: `./scripts/test.sh -q`
Ожидается: PASS, 1 тест (`tests/test_smoke.py::test_bot_config_imports`). `tests/test_config.py` и `tests/test_media_probe.py` на этой ветке отсутствуют — они пришли с коммитами конфигурации и ffprobe-гейта, которых в хотфиксе нет и быть не должно.

- [ ] **Шаг 4: Зафиксировать состояние ветки**

Коммита на этом шаге нет: черри-пик уже создал два коммита. Проверить, что дерево чистое и в истории ровно два новых коммита поверх `main`:

```bash
git status --porcelain
git log --oneline main..HEAD
```

Ожидается: пустой `git status`, две строки в `git log`.

---

### Task 2: Маскирование секретов в логах и снятие дефолтного синка

**Закрывает:** C-3, M-18

**Files:**
- Create: `bot/utils/log_guard.py`
- Create: `scripts/scrub_logs.py`
- Create: `tests/test_log_guard.py`
- Modify: `bot/__main__.py:17-18`

**Interfaces:**
- Consumes: ничего.
- Produces:
  - `bot.utils.log_guard.mask_secrets(text: str) -> str` — вырезает из текста секрет бота (`<цифры>:<секрет>`) и приватные пер-шаринговые токены query-строки, заменяя значение на `<redacted>`. Идемпотентна, не бросает исключений. **Потребляется пакетом B** (Task 14) для редакции URL в логах загрузчика.
  - `bot.utils.log_guard.setup_logging(log_path: str = "data/bot.log") -> None` — единственная точка настройки логирования.
  - `bot.utils.log_guard.scrub_log_file(path: Path) -> int` — переписывает существующий лог-файл на месте, возвращает число изменённых строк.

Строковое представление ошибок aiohttp содержит полный URL запроса к Bot API, а в этом URL лежит секрет бота. Хендлер логирует `error={e}` в трёх местах (`bot/handlers/user.py:93`, `:394`, `:410`), и секрет уже осел в файловом логе. Отягчающее: файловый синк добавляется с `level="INFO"`, но дефолтный обработчик loguru (handler `0` = `<stderr>`, уровень DEBUG) не снимается, поэтому DEBUG-записи загрузчика — блоб stderr внешнего процесса (`downloader.py:40`) и путь к банке кук (`:115`) — дублируются в лог контейнера мимо настроенного уровня.

Решение — патчер loguru: функция, которую loguru вызывает ровно один раз на запись, до всех синков, и которая правит уже отформатированное `record["message"]`. Так маскирование применяется и к файлу, и к stderr контейнера, независимо от того, кто и как позвал логгер. Плюс `logger.remove()` перед добавлением своих синков — чтобы уровень определялся нами, а не дефолтом.

Оговорка, которую надо понимать: записи стандартного `logging` (aiohttp, aiogram) мимо loguru не проходят и патчером не покрыты. Сейчас они не конфигурируются вообще и до уровня WARNING ничего с URL не пишут, поэтому отдельного моста `logging → loguru` в этой задаче не заводим.

- [ ] **Шаг 1: Написать падающий тест**

Создать `tests/test_log_guard.py`:

```python
import pytest
from loguru import logger

from bot.utils.log_guard import mask_secrets, scrub_log_file, setup_logging

# Заведомо ненастоящий секрет: цифровая часть и 35 символов «X».
# В публичном репозитории реальных значений быть не может.
FAKE_SECRET = "424242:" + "X" * 35


@pytest.fixture
def isolated_logger():
    """Loguru глобален — снимаем все синки до теста и после него."""
    logger.remove()
    yield logger
    logger.remove()
    logger.configure(patcher=None)


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


def test_setup_logging_drops_the_default_debug_sink(tmp_path, isolated_logger):
    log_file = tmp_path / "bot.log"
    setup_logging(str(log_file))
    logger.debug("Using cookies file: /tmp/jw_downloads/jw_cookies_abc/cookies.txt")
    logger.remove()

    written = log_file.read_text(encoding="utf-8")
    assert "Using cookies file" not in written


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
```

- [ ] **Шаг 2: Прогнать тест, убедиться что падает**

Команда: `./scripts/test.sh tests/test_log_guard.py -q`
Ожидается: FAIL — `ModuleNotFoundError: No module named 'bot.utils.log_guard'`

- [ ] **Шаг 3: Создать `bot/utils/log_guard.py`**

```python
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
```

- [ ] **Шаг 4: Создать `scripts/scrub_logs.py`**

```python
#!/usr/bin/env python3
"""Вычистить секреты из уже написанных лог-файлов.

Боевой лог лежит в именованном томе, поэтому запускать внутри контейнера:

    docker build --target test -t jw_downloader:test .
    docker run --rm -v jw_downloader_bot_data:/app/data jw_downloader:test \\
        python scripts/scrub_logs.py /app/data/bot.log

Скрипт правит только файловый лог. Лог контейнера (json-file) он не трогает —
тот усекается пересозданием контейнера, что в этот план не входит.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot.utils.log_guard import scrub_log_file  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Вычистить секреты из лог-файлов.")
    parser.add_argument("paths", nargs="+", type=Path, help="пути к лог-файлам")
    args = parser.parse_args(argv)

    total = 0
    for path in args.paths:
        if not path.is_file():
            print(f"пропущен (не файл): {path}")
            continue
        changed = scrub_log_file(path)
        total += changed
        print(f"{path}: изменено строк — {changed}")
    print(f"итого изменено строк: {total}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

Сделать исполняемым: `chmod +x scripts/scrub_logs.py`

- [ ] **Шаг 5: Перевести `bot/__main__.py` на `setup_logging`**

В `bot/__main__.py` добавить импорт рядом с остальными импортами из `bot`:

```python
from bot.utils.log_guard import setup_logging
```

и заменить строку 18

```python
    logger.add("data/bot.log", rotation="10 MB", retention="7 days", level="INFO")
```

на

```python
    setup_logging()
```

- [ ] **Шаг 6: Прогнать тесты**

Команда: `./scripts/test.sh -q`
Ожидается: PASS, регрессий нет

- [ ] **Шаг 7: Вычистить боевой лог**

Это разовая операция над данными, не выкладка. Выполняется после того, как тесты зелёные:

```bash
docker build --target test -t jw_downloader:test .
docker run --rm -v jw_downloader_bot_data:/app/data jw_downloader:test \
    python scripts/scrub_logs.py /app/data/bot.log
```

Ожидается: в выводе ненулевое «изменено строк». Проверка — в файле не осталось ни одного совпадения с шаблоном секрета:

```bash
docker run --rm -v jw_downloader_bot_data:/app/data jw_downloader:test \
    python -c "import re,sys;p='/app/data/bot.log';t=open(p,encoding='utf-8',errors='replace').read();print('осталось совпадений:', len(re.findall(r'\b\d{5,16}:[A-Za-z0-9_-]{30,}', t)))"
```

Ожидается: `осталось совпадений: 0`

- [ ] **Шаг 8: Коммит**

```bash
git add bot/utils/log_guard.py scripts/scrub_logs.py tests/test_log_guard.py bot/__main__.py
git commit -m "fix(logging): mask secrets in every sink and drop the default debug handler"
```

---

### Task 3: Уборщик перестаёт удалять свежие файлы и переживает сбои

**Закрывает:** H-8, H-7 (живучесть фоновой задачи), Low про уровень лога у `remove_file`

**Files:**
- Modify: `bot/services/cleanup.py:13-40`
- Modify: `bot/__main__.py:35`
- Create: `tests/test_cleanup.py`

**Interfaces:**
- Consumes: ничего.
- Produces: `bot.services.cleanup.sweep_once(max_age_minutes: int, now: float | None = None) -> int` — один проход подметальщика, возвращает число удалённых файлов. Вынесен из бесконечного цикла ради тестируемости.

gallery-dl 1.32.x имеет `mtime: true` по умолчанию и выставляет `st_mtime` из заголовка `Last-Modified` CDN. Файл из старого поста приземляется с датой недельной или месячной давности, а подметальщик фильтрует ровно по `st_mtime` (`cleanup.py:32`) — условие становится истинным через секунды после скачивания. Доказательство из боевого лога: двенадцать файлов скачаны, через 83 секунды подметены, следом четыре попытки отправки падают с «Can not write request body», и двенадцать `remove_file` жалуются на `No such file or directory`. Бьёт по всем путям gallery-dl: Pinterest, Instagram `/p/`, TikTok `/photo/`.

Поднятие порога `CLEANUP_MAX_AGE_MIN` баг НЕ лечит: против mtime двухмесячной давности бессилен любой порог. Лечим источником правды о возрасте: `st_ctime` в Linux — время последнего изменения inode, и `utime()`, которым gallery-dl двигает mtime назад, его только обновляет на «сейчас». `max(st_mtime, st_ctime)` даёт настоящий момент появления файла у нас на диске и не требует ни менять настройки gallery-dl, ни вводить структуру каталогов, которой на `main` ещё нет.

Заодно две смежные болячки. `periodic_cleanup` не имеет ни одного `try` вокруг тела цикла и `stat()` вызывается вне защиты — одно исключение убивает фоновую задачу навсегда и молча, потому что задача создаётся без `add_done_callback` (`__main__.py:35`). И `remove_file` логирует ERROR на каждый уже удалённый файл — двенадцать ERROR-строк на одну подметённую карусель топят настоящие ошибки.

Структурный подметальщик, знающий про занятые прямо сейчас папки, — это Task 8 плана local-bot-api; здесь закрывается только причина ложных удалений и живучесть.

- [ ] **Шаг 1: Написать падающий тест**

Создать `tests/test_cleanup.py`:

```python
import asyncio
import os
import time

import pytest

from bot.services import cleanup

TWO_MONTHS = 60 * 60 * 24 * 60


def test_file_with_backdated_mtime_is_not_swept(tmp_path, monkeypatch):
    """H-8: gallery-dl ставит mtime из Last-Modified CDN.

    Свежескачанный файл из старого поста обязан пережить проход подметальщика.
    """
    monkeypatch.setattr(cleanup, "DOWNLOAD_DIR", tmp_path)
    victim = tmp_path / "deadbeef_1.jpg"
    victim.write_bytes(b"x" * 16)
    backdated = time.time() - TWO_MONTHS
    os.utime(victim, (backdated, backdated))

    removed = cleanup.sweep_once(max_age_minutes=10)

    assert removed == 0
    assert victim.exists()


def test_genuinely_old_file_is_swept(tmp_path, monkeypatch):
    monkeypatch.setattr(cleanup, "DOWNLOAD_DIR", tmp_path)
    stale = tmp_path / "deadbeef_1.mp4"
    stale.write_bytes(b"x" * 16)

    # Сдвигать ctime назад нельзя — вместо этого смотрим из будущего.
    removed = cleanup.sweep_once(max_age_minutes=10, now=time.time() + 3600)

    assert removed == 1
    assert not stale.exists()


def test_sweep_survives_a_file_vanishing_mid_pass(tmp_path, monkeypatch):
    monkeypatch.setattr(cleanup, "DOWNLOAD_DIR", tmp_path)
    (tmp_path / "a_1.mp4").write_bytes(b"x")
    (tmp_path / "b_1.mp4").write_bytes(b"x")

    real_unlink = os.unlink
    calls = {"n": 0}

    def flaky_unlink(path, *args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise FileNotFoundError(2, "No such file or directory", str(path))
        return real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(cleanup.os, "unlink", flaky_unlink)

    # Исчезнувший файл не должен ни ронять проход, ни считаться удалённым.
    removed = cleanup.sweep_once(max_age_minutes=10, now=time.time() + 3600)

    assert removed == 1


def test_sweep_skips_subdirectories(tmp_path, monkeypatch):
    monkeypatch.setattr(cleanup, "DOWNLOAD_DIR", tmp_path)
    (tmp_path / "jw_cookies_abc").mkdir()

    removed = cleanup.sweep_once(max_age_minutes=10, now=time.time() + 3600)

    assert removed == 0
    assert (tmp_path / "jw_cookies_abc").is_dir()


def test_sweep_returns_zero_when_directory_absent(tmp_path, monkeypatch):
    monkeypatch.setattr(cleanup, "DOWNLOAD_DIR", tmp_path / "нет-такого")
    assert cleanup.sweep_once(max_age_minutes=10) == 0


async def test_remove_file_does_not_log_error_for_already_gone_file(tmp_path):
    """Low: двенадцать ERROR-строк на одну подметённую карусель топят настоящие ошибки."""
    from loguru import logger

    records = []
    logger.remove()
    sink_id = logger.add(lambda msg: records.append(msg.record), level="DEBUG")
    try:
        await cleanup.remove_file(tmp_path / "никогда-не-существовал.mp4")
    finally:
        logger.remove(sink_id)

    assert records, "ожидалась хотя бы одна запись в лог"
    assert all(r["level"].name != "ERROR" for r in records)


async def test_periodic_cleanup_survives_a_failing_pass(tmp_path, monkeypatch):
    """H-7: одно исключение не должно убивать фоновую задачу навсегда."""
    monkeypatch.setattr(cleanup, "DOWNLOAD_DIR", tmp_path)
    passes = {"n": 0}

    def exploding_sweep(max_age_minutes, now=None):
        passes["n"] += 1
        if passes["n"] == 1:
            raise OSError("диск моргнул")
        return 0

    monkeypatch.setattr(cleanup, "sweep_once", exploding_sweep)

    task = asyncio.create_task(cleanup.periodic_cleanup(interval_minutes=0, max_age_minutes=10))
    # Даём циклу провернуться несколько раз и снимаем задачу.
    for _ in range(10):
        await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert passes["n"] >= 2, "после исключения цикл обязан продолжиться"
```

- [ ] **Шаг 2: Прогнать тест, убедиться что падает**

Команда: `./scripts/test.sh tests/test_cleanup.py -q`
Ожидается: FAIL — `AttributeError: module 'bot.services.cleanup' has no attribute 'sweep_once'`

- [ ] **Шаг 3: Переписать `bot/services/cleanup.py`**

Заменить содержимое файла целиком на:

```python
from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path

from loguru import logger

DOWNLOAD_DIR = Path("/tmp/jw_downloads")


async def remove_file(path: str | Path) -> None:
    try:
        os.unlink(path)
        logger.info("File removed: {}", path)
    except FileNotFoundError:
        # Файл уже подмели или он и не доехал — это штатная ситуация, а не сбой.
        # Раньше сюда прилетало по двенадцать ERROR-строк на одну карусель.
        logger.debug("File already gone: {}", path)
    except Exception as exc:
        logger.warning("Failed to remove file {}: {}", path, exc)


def _landed_at(entry: Path) -> float:
    """Момент, когда файл реально появился у нас на диске.

    `st_mtime` доверять нельзя: gallery-dl по умолчанию выставляет его из
    заголовка `Last-Modified` CDN, поэтому свежескачанный файл из старого
    поста приземляется с датой месячной давности и подметается через секунды
    после загрузки. `st_ctime` — время последнего изменения inode; `utime()`,
    которым gallery-dl двигает mtime назад, его только обновляет на «сейчас»
    и сдвинуть в прошлое не может. Максимум из двух и есть настоящий возраст.
    """
    st = entry.stat()
    return max(st.st_mtime, st.st_ctime)


def sweep_once(max_age_minutes: int, now: float | None = None) -> int:
    """Один проход подметальщика. Возвращает число удалённых файлов.

    Вынесен из цикла ради тестируемости: параметр `now` позволяет посмотреть
    на каталог «из будущего», не трогая часы и не подделывая ctime.
    """
    if not DOWNLOAD_DIR.exists():
        return 0

    now = time.time() if now is None else now
    max_age_sec = max_age_minutes * 60
    removed = 0

    for entry in DOWNLOAD_DIR.iterdir():
        try:
            if not entry.is_file():
                continue
            if (now - _landed_at(entry)) <= max_age_sec:
                continue
            os.unlink(entry)
            removed += 1
        except FileNotFoundError:
            # Гонка с хендлером, который удалил файл сам. Не сбой.
            continue
        except Exception as exc:
            logger.warning("Cleanup failed for {}: {}", entry, exc)

    return removed


async def periodic_cleanup(interval_minutes: int = 5, max_age_minutes: int = 10) -> None:
    while True:
        await asyncio.sleep(interval_minutes * 60)
        try:
            removed = sweep_once(max_age_minutes)
        except Exception as exc:
            # Цикл обязан пережить любой сбой прохода: раньше одно исключение
            # убивало фоновую задачу навсегда и молча.
            logger.exception("Periodic cleanup pass failed: {}", exc)
            continue
        if removed:
            logger.info("Periodic cleanup: removed {} file(s)", removed)
```

- [ ] **Шаг 4: Научиться замечать смерть фоновой задачи**

В `bot/__main__.py` заменить строку 35

```python
    _cleanup_task = asyncio.create_task(periodic_cleanup())  # noqa: F841
```

на

```python
    def _log_task_death(task: asyncio.Task) -> None:
        # Фоновая задача не должна завершаться вообще. Если завершилась —
        # об этом надо узнать из лога, а не по отсутствию уборки.
        if task.cancelled():
            logger.info("Cleanup task cancelled")
            return
        exc = task.exception()
        if exc is not None:
            logger.opt(exception=exc).error("Cleanup task died")
        else:
            logger.error("Cleanup task exited unexpectedly")

    _cleanup_task = asyncio.create_task(periodic_cleanup())  # noqa: F841
    _cleanup_task.add_done_callback(_log_task_death)
```

- [ ] **Шаг 5: Прогнать тесты**

Команда: `./scripts/test.sh -q`
Ожидается: PASS, регрессий нет

- [ ] **Шаг 6: Коммит**

```bash
git add bot/services/cleanup.py bot/__main__.py tests/test_cleanup.py
git commit -m "fix(cleanup): age files by inode change time, survive failed passes"
```

---

### Task 4: Исчезнувший файл больше не выносит хендлер

**Закрывает:** H-9

**Files:**
- Modify: `bot/services/downloader.py:277-305`, `:382-424`
- Create: `tests/test_downloader_stat_guard.py`

**Interfaces:**
- Consumes: ничего.
- Produces: `bot.services.downloader._sized_files(paths: list[Path]) -> list[tuple[Path, int]]` — оставляет только существующие непустые файлы вместе с их размерами.

`.stat()` зовётся в четырёх местах вне всякой защиты: `downloader.py:282` и `:286` внутри `_try_gallery_dl_fallback`, `:385` и `:400` внутри `download_media`. Первые два достижимы из `download_media:328` — то есть ДО `try:` на `:351`; сам `download_media` зовётся из `bot/handlers/user.py:261` — вне `try:` на `:278`. Если файл исчез между `_find_downloaded_files` и `.stat()`, `FileNotFoundError` улетает мимо обоих `try` прямо в диспетчер aiogram: «⏳ Скачиваю медиа...» висит вечно, в `download_log` не пишется ничего, `finally` на `user.py:417` не выполняется, файлы остаются в tmpfs. Частоту этому обеспечивала находка H-8, закрытая предыдущей задачей, — но гонка с подметальщиком и с параллельной загрузкой остаётся возможной и после неё, поэтому guard нужен независимо.

Решение — один помощник, который одновременно отсеивает исчезнувшие и нулевые файлы и отдаёт размеры, чтобы `stat()` не звался по второму разу ради суммы.

**Замечание для пакета B:** Task 12 этого плана меняет возвращаемый тип `_try_gallery_dl`. Тест `test_gallery_fallback_returns_none_when_files_vanish` ниже написан против интерфейса, существующего на момент пакета 0; при переходе на новый тип его надо адаптировать, сохранив смысл — падения быть не должно.

- [ ] **Шаг 1: Написать падающий тест**

Создать `tests/test_downloader_stat_guard.py`:

```python
from pathlib import Path

from bot.services import downloader


def test_sized_files_drops_missing_and_empty(tmp_path):
    good = tmp_path / "good.mp4"
    good.write_bytes(b"x" * 10)
    empty = tmp_path / "empty.mp4"
    empty.write_bytes(b"")
    missing = tmp_path / "missing.mp4"

    result = downloader._sized_files([good, empty, missing])

    assert result == [(good, 10)]


def test_sized_files_does_not_raise_on_directory(tmp_path):
    subdir = tmp_path / "jw_cookies_abc"
    subdir.mkdir()
    # st_size у каталога ненулевой, но отправлять его нельзя — важно лишь,
    # что вызов не падает; отсев каталогов делает _find_downloaded_files.
    assert downloader._sized_files([subdir]) != []


async def test_gallery_fallback_returns_none_when_files_vanish(tmp_path, monkeypatch):
    """H-9: файл исчез между поиском и stat() — это не повод падать."""
    ghost = tmp_path / "deadbeef_1.jpg"

    async def fake_gallery_dl(url, filename):
        return [ghost]

    monkeypatch.setattr(downloader, "_try_gallery_dl", fake_gallery_dl)

    result = await downloader._try_gallery_dl_fallback("https://pin.it/abc", "deadbeef")

    assert result is None


async def test_download_media_reports_failure_when_files_vanish(tmp_path, monkeypatch):
    """H-9 на пути yt-dlp: вместо исключения — честный неуспех."""
    monkeypatch.setattr(downloader, "DOWNLOAD_DIR", tmp_path)
    ghost = tmp_path / "deadbeef_1.mp4"

    class FakeProcess:
        returncode = 0

        async def communicate(self):
            return b"", b""

    async def fake_exec(*args, **kwargs):
        return FakeProcess()

    def fake_find(directory, prefix):
        return [ghost]

    async def no_gallery(url, filename):
        return None

    monkeypatch.setattr(downloader.asyncio, "create_subprocess_exec", fake_exec)
    monkeypatch.setattr(downloader, "_find_downloaded_files", fake_find)
    monkeypatch.setattr(downloader, "_try_gallery_dl_fallback", no_gallery)

    result = await downloader.download_media("https://www.youtube.com/watch?v=xyz", "youtube")

    assert result.success is False
    assert result.error_message
```

- [ ] **Шаг 2: Прогнать тест, убедиться что падает**

Команда: `./scripts/test.sh tests/test_downloader_stat_guard.py -q`
Ожидается: FAIL — `AttributeError: module 'bot.services.downloader' has no attribute '_sized_files'`, а тесты про исчезнувший файл падают с `FileNotFoundError`

- [ ] **Шаг 3: Добавить помощник в `bot/services/downloader.py`**

Сразу после `_cleanup_glob` (после строки 235) добавить:

```python
def _sized_files(paths: list[Path]) -> list[tuple[Path, int]]:
    """Существующие непустые файлы вместе с размерами.

    Между `_find_downloaded_files` и `.stat()` файл может исчезнуть — успевает
    вклиниться фоновый подметальщик. Раньше `FileNotFoundError` улетал мимо
    `try` загрузчика и мимо `try` хендлера прямо в диспетчер: статус-сообщение
    висело вечно, в `download_log` не писалось ничего, файлы оставались на диске.
    """
    result: list[tuple[Path, int]] = []
    for path in paths:
        try:
            size = path.stat().st_size
        except OSError:
            continue
        if size > 0:
            result.append((path, size))
    return result
```

- [ ] **Шаг 4: Перевести `_try_gallery_dl_fallback` на помощник**

Заменить строки 282–286

```python
    valid_gd = [f for f in gd_files if f.stat().st_size > 0]
    if not valid_gd:
        return None

    total_size_mb = round(sum(f.stat().st_size for f in valid_gd) / (1024 * 1024), 2)
```

на

```python
    sized_gd = _sized_files(gd_files)
    if not sized_gd:
        return None

    valid_gd = [path for path, _ in sized_gd]
    total_size_mb = round(sum(size for _, size in sized_gd) / (1024 * 1024), 2)
```

- [ ] **Шаг 5: Перевести `download_media` на помощник**

Заменить строку 385

```python
                valid_files = [f for f in actual_files if f.stat().st_size > 0]
                if not valid_files:
```

на

```python
                sized_files = _sized_files(actual_files)
                valid_files = [path for path, _ in sized_files]
                if not valid_files:
```

и строку 400

```python
                total_size_mb = round(sum(f.stat().st_size for f in valid_files) / (1024 * 1024), 2)
```

на

```python
                total_size_mb = round(sum(size for _, size in sized_files) / (1024 * 1024), 2)
```

- [ ] **Шаг 6: Прогнать тесты**

Команда: `./scripts/test.sh -q`
Ожидается: PASS, регрессий нет

- [ ] **Шаг 7: Коммит**

```bash
git add bot/services/downloader.py tests/test_downloader_stat_guard.py
git commit -m "fix(downloader): guard stat() against files vanishing mid-download"
```

---

### Task 5: Не переигрывать очередь апдейтов после простоя

**Закрывает:** M-19

**Files:**
- Modify: `bot/__main__.py:53`
- Create: `tests/test_polling_startup.py`

**Interfaces:**
- Consumes: ничего.
- Produces: `bot.__main__.run_polling(dp, bot) -> None` — запуск лонг-поллинга с фиксированными параметрами; выделен, чтобы параметры можно было зафиксировать тестом.

**⚠️ Меняет поведение для пользователя — нужна ручная проверка:** после рестарта бот перестаёт отвечать на ссылки, присланные, пока он лежал. Проверить руками: остановить бота, прислать ссылку, поднять бота — ответа быть не должно, квота списаться не должна.

Telegram держит очередь апдейтов до 24 часов. При `restart: always` и падении на старте цикл повторяется на каждом рестарте: каждая ссылка качается заново, квота списывается заново, ответ прилетает в давно забытый диалог. Это прямой денежный ущерб (повторное списание квоты) и мусор в чатах.

- [ ] **Шаг 1: Написать падающий тест**

Создать `tests/test_polling_startup.py`:

```python
from bot import __main__ as entrypoint


async def test_polling_drops_pending_updates():
    """M-19: очередь апдейтов за время простоя переигрываться не должна."""
    seen = {}

    class FakeDispatcher:
        async def start_polling(self, bot, **kwargs):
            seen["bot"] = bot
            seen["kwargs"] = kwargs

    sentinel = object()
    await entrypoint.run_polling(FakeDispatcher(), sentinel)

    assert seen["bot"] is sentinel
    assert seen["kwargs"].get("drop_pending_updates") is True
```

- [ ] **Шаг 2: Прогнать тест, убедиться что падает**

Команда: `./scripts/test.sh tests/test_polling_startup.py -q`
Ожидается: FAIL — `AttributeError: module 'bot.__main__' has no attribute 'run_polling'`

- [ ] **Шаг 3: Выделить запуск поллинга**

В `bot/__main__.py` добавить функцию перед `async def main()`:

```python
async def run_polling(dp, bot) -> None:
    """Запуск лонг-поллинга.

    `drop_pending_updates=True` обязателен: Telegram держит очередь до 24
    часов, и без сброса каждая ссылка, присланная во время простоя, качается
    заново со списанием квоты, а ответ прилетает в давно забытый диалог.
    """
    await dp.start_polling(bot, drop_pending_updates=True)
```

и заменить строку 53

```python
    await dp.start_polling(bot)
```

на

```python
    await run_polling(dp, bot)
```

- [ ] **Шаг 4: Прогнать тесты**

Команда: `./scripts/test.sh -q`
Ожидается: PASS, регрессий нет

- [ ] **Шаг 5: Коммит**

```bash
git add bot/__main__.py tests/test_polling_startup.py
git commit -m "fix(bot): drop pending updates on startup"
```

---

### Task 6: Настоящая инвалидация pip-слоя и точные пины

**Закрывает:** H-10, H-11, пункты 1, 2 и 5 раздела «Компоненты»

**Files:**
- Modify: `requirements.txt`
- Modify: `Dockerfile:11-13`
- Create: `tests/test_dependency_pins.py`

**Interfaces:**
- Consumes: ничего.
- Produces: образ, в котором версии gallery-dl и aiogram детерминированы файлом `requirements.txt`.

**⚠️ Меняет поведение для пользователя — нужна ручная проверка:** aiogram 3.31.0 чинит игнорирование части bot-level дефолтов (`parse_mode`, `protect_content`, `show_caption_above_media`, `link_preview`). Подписи к медиа и тексты меню начнут разбираться как HTML там, где раньше проскакивали сырыми. Проверить руками отправку одиночного видео, альбома и всех экранов меню. Риск ограничен: единственная подстановка в подписи — имя платформы из собственного аллоулиста, HTML-метасимволов в нём нет. Полное экранирование пользовательских данных приходит позже, в Task 27 и Task 30, — и это ещё одна причина не откладывать волну 3.

`docker history` показывает, что слой `pip install`/`apt-get` датирован августом и пересобирается только `COPY . .`. Причина в том, что `RUN pip install --upgrade "yt-dlp[...]" gallery-dl` стоит ПОСЛЕ `COPY requirements.txt .`, а сам файл не менялся — слой берётся из кэша. Обычный `docker compose build` не обновит ни yt-dlp, ни gallery-dl, поэтому переход на gallery-dl 1.32.12 (чинит падение TikTok `AttributeError: '_generate_headers'` в 1.32.11 и Instagram posts/reels в 1.32.12) физически не попал бы в образ.

Решение убирает саму конструкцию, а не подпирает её: `--upgrade` из `Dockerfile` уходит, все версии переезжают в `requirements.txt` точными пинами, и любая правка этого файла инвалидирует слой — то есть cache-buster и источник правды теперь один и тот же файл. Заодно исчезает молчаливый апгрейд мимо пинов: раньше `--upgrade` переустанавливал yt-dlp и gallery-dl поверх всего, что просил `requirements.txt`.

`curl_cffi` сознательно остаётся неприпиненным (приезжает через extras yt-dlp): его пин — предмет проверки в Task 35, чинить вслепую нельзя. yt-dlp пинится на текущую версию без апгрейда — по ревизии отставание нулевое.

- [ ] **Шаг 1: Написать падающий тест**

Создать `tests/test_dependency_pins.py`:

```python
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _requirements() -> str:
    return (ROOT / "requirements.txt").read_text(encoding="utf-8")


def test_gallery_dl_is_pinned_to_the_version_that_fixes_instagram():
    # 1.32.11 чинит падение TikTok, 1.32.12 — Instagram posts/reels.
    assert "gallery-dl==1.32.12" in _requirements()


def test_aiogram_is_pinned_exactly():
    assert "aiogram==3.31.0" in _requirements()


def test_ytdlp_keeps_the_curl_cffi_extra():
    # curl_cffi нужен для TikTok и Instagram; без extras он не приедет.
    assert re.search(r"yt-dlp\[[^\]]*curl-cffi[^\]]*\]==", _requirements())


def test_dockerfile_does_not_upgrade_past_the_pins():
    """H-11: `pip install --upgrade` в отдельном RUN обходил пины и кэшировался."""
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "--upgrade" not in dockerfile


def test_installed_versions_match_the_pins():
    """Тест гоняется внутри образа, поэтому проверяет фактически
    установленное, а не только текст файла."""
    from importlib.metadata import version

    assert version("gallery-dl") == "1.32.12"
    assert version("aiogram") == "3.31.0"
```

- [ ] **Шаг 2: Прогнать тест, убедиться что падает**

Команда: `./scripts/test.sh tests/test_dependency_pins.py -q`
Ожидается: FAIL — четыре-пять провалов: пинов в `requirements.txt` нет, `--upgrade` в `Dockerfile` есть, установленные версии не совпадают

- [ ] **Шаг 3: Переписать `requirements.txt`**

Заменить содержимое файла целиком на:

```
# Версии пинуются точно: этот файл — единственный источник правды о версиях
# и одновременно cache-buster для слоя `pip install` в Dockerfile.
aiogram==3.31.0
SQLAlchemy[asyncio]>=2.0,<3.0
aiosqlite>=0.20
pydantic-settings>=2.0
loguru>=0.7
# Extras обязательны: curl-cffi нужен для TikTok и Instagram.
yt-dlp[default,curl-cffi]==2026.8.19
# 1.32.11 чинит падение TikTok `_generate_headers`, 1.32.12 — Instagram posts/reels.
gallery-dl==1.32.12
```

- [ ] **Шаг 4: Убрать `--upgrade` из `Dockerfile`**

Заменить строки 11–13

```dockerfile
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && pip install --no-cache-dir --upgrade "yt-dlp[default,curl-cffi]" gallery-dl
```

на

```dockerfile
COPY requirements.txt .
# Единственная установка зависимостей. Отдельного `pip install --upgrade` быть
# не должно: он и кэшировался вместе с этим слоем, и переустанавливал пакеты
# поверх пинов. Теперь слой инвалидируется правкой requirements.txt.
RUN pip install --no-cache-dir -r requirements.txt
```

- [ ] **Шаг 5: Прогнать тесты**

Команда: `./scripts/test.sh -q`
Ожидается: PASS. Сборка на этом прогоне заметно дольше обычной — слой `pip install` пересобирается впервые с августа.

- [ ] **Шаг 6: Проверить версии в собранном образе**

```bash
docker run --rm jw_downloader:test sh -c 'gallery-dl --version; yt-dlp --version; python -c "import aiogram, curl_cffi; print(aiogram.__version__, curl_cffi.__version__)"'
```

Ожидается: `1.32.12`, `2026.08.19`, `3.31.0` и версия curl_cffi (её значение записать — оно понадобится в Task 35).

- [ ] **Шаг 7: Коммит**

```bash
git add requirements.txt Dockerfile tests/test_dependency_pins.py
git commit -m "build: pin gallery-dl and aiogram, make the pip layer actually rebuild"
```

---

### Task 7: Мерж хотфикса в ветку миграции

**Закрывает:** ничего — интеграционный шаг, открывающий волну 2.

**Files:**
- Modify: ничего вручную; результат мержа.

**Interfaces:**
- Consumes: всё, что сделали Task 1–6.
- Produces: `feat/local-bot-api` с закрытыми операционными находками — база для пакетов A, B, C, F.

Конфликтов не ожидается: `bot/__main__.py`, `bot/services/cleanup.py`, `bot/services/downloader.py` и `requirements.txt` побайтово совпадают на `main` и на `feat/local-bot-api`, а `Dockerfile` после черри-пика Task 1 приведён к той же версии, что на ветке миграции, и с тех пор на ней не менялся.

- [ ] **Шаг 1: Убедиться, что хотфикс зелёный и дерево чистое**

```bash
git checkout fix/ops-hotfix
git status --porcelain
./scripts/test.sh -q
```

Ожидается: пустой `git status`, PASS.

- [ ] **Шаг 2: Смержить в ветку миграции**

```bash
git checkout feat/local-bot-api
git merge --no-ff fix/ops-hotfix -m "merge: ops hotfix for the 2026-09-12 audit"
```

Ожидается: мерж без конфликтов.

- [ ] **Шаг 3: Прогнать весь набор на объединённой ветке**

Команда: `./scripts/test.sh -q`
Ожидается: PASS — сходятся тесты хотфикса и уже существовавшие 46 тестов ветки миграции.

- [ ] **Шаг 4: Отметить в TODO закрытые находки**

В `.claude/TODO_FIXES.md` проставить `[x]` у C-3, H-8, H-9, H-10, H-11, M-18, M-19, у Low про уровень лога `remove_file` и в блоке состояния наверху файла отметить, что H-7 закрыт в части живучести фоновой задачи, а структурная часть осталась за Task 8 плана local-bot-api. Файл в `.gitignore` и в репозиторий не попадает — коммита не требует.

- [ ] **Шаг 5: Зафиксировать и отдать владельцу**

Хотфикс готов к выкладке. Выкладка в план не входит — сообщить владельцу, что ветка `fix/ops-hotfix` пригодна к деплою и что перед ней нужна ротация секрета бота (см. «Действия владельца вне плана»).

---
## Пакет A — Рантайм (ветка `feat/local-bot-api`)

Владеет файлами `bot/__main__.py`, `bot/middlewares/throttle.py` и своими тестовыми файлами. Идёт во второй волне **параллельно с пакетами B, C и F** — общих файлов у них нет. Задачи 8–10 внутри пакета строго последовательны.

---

### Task 8: Глобальный обработчик ошибок

**Закрывает:** M-23, H-4 (симптом: «⏳ Скачиваю медиа…» висит вечно)

**Files:**
- Modify: `bot/__main__.py:28-33`
- Create: `tests/test_error_handler.py`

**Interfaces:**
- Consumes: ничего.
- Produces: `bot.__main__.on_unhandled_error(event) -> None` — обработчик, зарегистрированный через `dp.errors.register`.

**⚠️ Меняет поведение для пользователя — нужна ручная проверка:** любое необработанное исключение теперь отвечает пользователю «⚠️ Что-то пошло не так…» вместо тишины. Проверить руками, что сообщение появляется один раз, а не дублируется поверх штатных ответов.

Ни `dp.errors`, ни `dp.errors.register` в проекте нет. Любое исключение = полная тишина для пользователя и для админа, а неотвеченный `callback_query` держит крутилку до 30 секунд. Это усилитель сразу трёх других находок: `_safe_edit` (H-3) и четыре `except TelegramBadRequest` в `user.py` пропускают наружу всё, что не `TelegramBadRequest`, а `toggle_ban` на отсутствующем пользователе бросает `ValueError` (M-24).

Этим же закрывается H-4. Находка просит расширить `try` в `handle_url` так, чтобы он накрывал `download_media` и `log_download`, — потому что иначе статус-сообщение «⏳ Скачиваю медиа…» висит вечно. Конкретную причину зависания (`FileNotFoundError` из `.stat()`) уже убрала Task 4, а здесь исчезает сам класс исхода: ни одно исключение больше не уходит из хендлера без ответа пользователю. Расширять `try` после этого не нужно — и вредно: он и так растянут на полторы сотни строк, а Task 25 наоборот сужает его до одних лишь вызовов отправки, чтобы падение записи в БД не переписывало доставленный результат на «failed».

Обработчик работает по утиной типизации: у объекта события берётся `update`, у него — `callback_query` или `message`. Так его можно проверить простым стабом, не конструируя pydantic-модели aiogram.

- [ ] **Шаг 1: Написать падающий тест**

Создать `tests/test_error_handler.py`:

```python
from bot import __main__ as entrypoint


class FakeCallback:
    def __init__(self):
        self.answers = []

    async def answer(self, text=None, show_alert=None):
        self.answers.append((text, show_alert))


class FakeMessage:
    def __init__(self):
        self.answers = []

    async def answer(self, text, **kwargs):
        self.answers.append(text)


class FakeUpdate:
    update_id = 4242

    def __init__(self, callback_query=None, message=None):
        self.callback_query = callback_query
        self.message = message


class FakeErrorEvent:
    def __init__(self, update, exception):
        self.update = update
        self.exception = exception


async def test_callback_gets_answered_so_the_spinner_stops():
    callback = FakeCallback()
    event = FakeErrorEvent(FakeUpdate(callback_query=callback), RuntimeError("бум"))

    await entrypoint.on_unhandled_error(event)

    assert len(callback.answers) == 1
    assert callback.answers[0][0]


async def test_message_gets_a_reply():
    message = FakeMessage()
    event = FakeErrorEvent(FakeUpdate(message=message), RuntimeError("бум"))

    await entrypoint.on_unhandled_error(event)

    assert len(message.answers) == 1


async def test_handler_never_raises_when_answering_fails():
    class DeadCallback:
        async def answer(self, text=None, show_alert=None):
            raise RuntimeError("юзер заблокировал бота")

    event = FakeErrorEvent(FakeUpdate(callback_query=DeadCallback()), ValueError("бум"))

    await entrypoint.on_unhandled_error(event)  # не должно бросить


async def test_handler_survives_update_without_message_or_callback():
    event = FakeErrorEvent(FakeUpdate(), KeyError("бум"))

    await entrypoint.on_unhandled_error(event)  # не должно бросить
```

- [ ] **Шаг 2: Прогнать тест, убедиться что падает**

Команда: `./scripts/test.sh tests/test_error_handler.py -q`
Ожидается: FAIL — `AttributeError: module 'bot.__main__' has no attribute 'on_unhandled_error'`

- [ ] **Шаг 3: Добавить обработчик в `bot/__main__.py`**

Перед `async def main()` добавить:

```python
UNHANDLED_ERROR_TEXT = "⚠️ Что-то пошло не так. Попробуй ещё раз."


async def on_unhandled_error(event) -> None:
    """Последний рубеж: исключение, не пойманное хендлером.

    Без него любое исключение = тишина для пользователя и крутилка на
    неотвеченном колбэке до 30 секунд. Ответ пользователю — best-effort:
    он мог заблокировать бота ровно этим исключением, и падать здесь
    во второй раз бессмысленно.
    """
    update = getattr(event, "update", None)
    logger.opt(exception=getattr(event, "exception", None)).error(
        "Unhandled update error | update_id={}", getattr(update, "update_id", None)
    )

    callback = getattr(update, "callback_query", None)
    if callback is not None:
        try:
            await callback.answer(UNHANDLED_ERROR_TEXT, show_alert=False)
        except Exception:
            pass
        return

    message = getattr(update, "message", None)
    if message is not None:
        try:
            await message.answer(UNHANDLED_ERROR_TEXT)
        except Exception:
            pass
```

- [ ] **Шаг 4: Зарегистрировать обработчик**

В `bot/__main__.py` сразу после `dp = Dispatcher()` (строка 28) добавить:

```python
    dp.errors.register(on_unhandled_error)
```

- [ ] **Шаг 5: Прогнать тесты**

Команда: `./scripts/test.sh -q`
Ожидается: PASS, регрессий нет

- [ ] **Шаг 6: Коммит**

```bash
git add bot/__main__.py tests/test_error_handler.py
git commit -m "feat(bot): add a global error handler for unhandled updates"
```

---

### Task 9: Таймаут сессии под локальный Bot API

**Закрывает:** остаток Task 2 плана local-bot-api («180→900»), зафиксированный ревизией как незакрытый

**Files:**
- Modify: `bot/__main__.py:22`
- Create: `tests/test_session_timeout.py`

**Interfaces:**
- Consumes: `settings.TELEGRAM_REQUEST_TIMEOUT` (уже есть, значение 900).
- Produces: `bot.__main__.build_session() -> AiohttpSession`.

Task 2 плана local-bot-api помечена выполненной, но ревизия 2026-09-12 показала, что закрыта она наполовину: конфигурация подняла `DOWNLOAD_TIMEOUT` и `TELEGRAM_REQUEST_TIMEOUT` до 900, а `bot/__main__.py:22` по-прежнему создаёт сессию с зашитым `timeout=180`. Отдача полуторагигабайтного файла в 180 секунд не укладывается, и клиент отвалится по собственному таймауту раньше, чем сервер по своему `IDLE_TIMEOUT=500` — то есть ровно в том режиме, который в спеке назван непредсказуемым.

Значение берём из настроек, а не числом: `TELEGRAM_REQUEST_TIMEOUT` уже существует, уже задокументирован в `.env.example` и уже проверен тестом `test_request_timeout_exceeds_server_idle_timeout`.

- [ ] **Шаг 1: Написать падающий тест**

Создать `tests/test_session_timeout.py`:

```python
from bot import __main__ as entrypoint
from bot.config import settings


def test_session_timeout_comes_from_settings():
    session = entrypoint.build_session()
    assert session.timeout == settings.TELEGRAM_REQUEST_TIMEOUT


def test_session_timeout_outlives_the_server_idle_timeout():
    # У telegram-bot-api жёсткий IDLE_TIMEOUT=500 с. Соединение должен
    # закрывать сервер, а не мы, — иначе поведение непредсказуемо.
    assert entrypoint.build_session().timeout > 500
```

- [ ] **Шаг 2: Прогнать тест, убедиться что падает**

Команда: `./scripts/test.sh tests/test_session_timeout.py -q`
Ожидается: FAIL — `AttributeError: module 'bot.__main__' has no attribute 'build_session'`

- [ ] **Шаг 3: Выделить сборку сессии**

В `bot/__main__.py` перед `async def main()` добавить:

```python
def build_session() -> AiohttpSession:
    """HTTP-сессия к Bot API.

    Таймаут берётся из настроек и заведомо больше серверного IDLE_TIMEOUT=500:
    соединение должен закрывать сервер, а не мы. Зашитые 180 секунд не
    покрывали отдачу крупного файла даже теоретически.
    """
    return AiohttpSession(timeout=settings.TELEGRAM_REQUEST_TIMEOUT)
```

и заменить строку 22

```python
    session = AiohttpSession(timeout=180)
```

на

```python
    session = build_session()
```

- [ ] **Шаг 4: Прогнать тесты**

Команда: `./scripts/test.sh -q`
Ожидается: PASS, регрессий нет

- [ ] **Шаг 5: Коммит**

```bash
git add bot/__main__.py tests/test_session_timeout.py
git commit -m "fix(bot): take the session timeout from settings instead of a hardcoded 180s"
```

---

### Task 10: Троттлинг — гард на `from_user`, колбэки, внятный отказ

**Закрывает:** M-1, M-13, M-25 (только следствие: падение троттла без `from_user`)

**Files:**
- Modify: `bot/middlewares/throttle.py` (целиком)
- Modify: `bot/__main__.py:30`
- Create: `tests/test_throttle.py`

**Interfaces:**
- Consumes: ничего.
- Produces: `ThrottleMiddleware(rate_limit: float = 3.0, *, notify: bool = False)` — один экземпляр на один поток событий.

**⚠️ Меняет поведение для пользователя — нужна ручная проверка:** появляется троттлинг на нажатия кнопок и текстовый отказ «⏳ Слишком часто…». Проверить руками, что обычная навигация по меню (последовательные нажатия с задержкой в доли секунды) не отбивается, а быстрое «долбление» одной кнопкой — отбивается с всплывающей подсказкой.

Три связанные дырки. Первая: `throttle.py:19` читает `event.from_user.id` без гарда — на сообщении от анонимного админа группы `from_user` равен `None`, и получается `AttributeError` прямо в middleware, то есть до любого хендлера. Вторая: троттлинга на `callback_query` нет вовсе (`__main__.py:30` вешает middleware только на `dp.message`), и кнопки можно долбить без ограничений. Третья: отбитое событие молча проваливается — пользователь не понимает, почему бот не ответил.

Ключевой момент проектирования: **лимиты для сообщений и для колбэков разные**. Колбэк — это навигация по меню, и лимит сообщений в 3 секунды сделал бы её неюзабельной. Поэтому регистрируются два экземпляра с разными лимитами и раздельными словарями отметок. И второй ключевой момент: **отбитый колбэк обязан получить `answer()`**, иначе у пользователя висит крутилка до 30 секунд — добавление троттлинга на колбэки без ответа сделало бы интерфейс хуже, а не лучше.

Уведомление дедуплицируется: не чаще одного раза за окно на пользователя, иначе ответ на флуд сам становится флудом.

- [ ] **Шаг 1: Написать падающий тест**

Создать `tests/test_throttle.py`:

```python
import pytest

from bot.middlewares.throttle import ThrottleMiddleware


class FakeUser:
    def __init__(self, user_id):
        self.id = user_id


class FakeEvent:
    """Общий стаб под Message и CallbackQuery: обоим нужен from_user и answer."""

    def __init__(self, user_id=1000000001):
        self.from_user = FakeUser(user_id) if user_id is not None else None
        self.answers = []

    async def answer(self, text=None, **kwargs):
        self.answers.append(text)


async def _passthrough(event, data):
    return "handled"


async def test_first_event_passes_through():
    mw = ThrottleMiddleware(rate_limit=10.0)
    assert await mw(_passthrough, FakeEvent(), {}) == "handled"


async def test_second_event_within_window_is_dropped():
    mw = ThrottleMiddleware(rate_limit=10.0)
    await mw(_passthrough, FakeEvent(), {})
    assert await mw(_passthrough, FakeEvent(), {}) is None


async def test_different_users_do_not_throttle_each_other():
    mw = ThrottleMiddleware(rate_limit=10.0)
    await mw(_passthrough, FakeEvent(user_id=1000000001), {})
    assert await mw(_passthrough, FakeEvent(user_id=1000000002), {}) == "handled"


async def test_event_without_from_user_is_not_throttled_and_does_not_crash():
    """M-25: сообщение от анонимного админа группы приходит без from_user."""
    mw = ThrottleMiddleware(rate_limit=10.0)
    assert await mw(_passthrough, FakeEvent(user_id=None), {}) == "handled"


async def test_throttled_event_is_answered_when_notify_is_on():
    mw = ThrottleMiddleware(rate_limit=10.0, notify=True)
    await mw(_passthrough, FakeEvent(), {})
    second = FakeEvent()
    await mw(_passthrough, second, {})
    assert len(second.answers) == 1
    assert second.answers[0]


async def test_notice_is_sent_at_most_once_per_window():
    """Ответ на флуд не должен сам становиться флудом."""
    mw = ThrottleMiddleware(rate_limit=10.0, notify=True)
    await mw(_passthrough, FakeEvent(), {})
    events = [FakeEvent() for _ in range(5)]
    for event in events:
        await mw(_passthrough, event, {})
    assert sum(len(e.answers) for e in events) == 1


async def test_notice_failure_does_not_break_the_middleware():
    class DeadEvent(FakeEvent):
        async def answer(self, text=None, **kwargs):
            raise RuntimeError("юзер заблокировал бота")

    mw = ThrottleMiddleware(rate_limit=10.0, notify=True)
    await mw(_passthrough, FakeEvent(), {})
    assert await mw(_passthrough, DeadEvent(), {}) is None


async def test_stale_entries_are_evicted():
    mw = ThrottleMiddleware(rate_limit=0.0, notify=True)
    for user_id in range(1000000001, 1000000021):
        await mw(_passthrough, FakeEvent(user_id=user_id), {})
    # rate_limit=0 означает, что все прошлые отметки протухли сразу.
    assert len(mw.user_timestamps) <= 1
    assert len(mw.notified_at) == 0
```

- [ ] **Шаг 2: Прогнать тест, убедиться что падает**

Команда: `./scripts/test.sh tests/test_throttle.py -q`
Ожидается: FAIL — `TypeError: ThrottleMiddleware.__init__() got an unexpected keyword argument 'rate_limit'`, а тест про отсутствующий `from_user` падает с `AttributeError`

- [ ] **Шаг 3: Переписать `bot/middlewares/throttle.py`**

Заменить содержимое файла целиком на:

```python
import time
from typing import Any, Awaitable, Callable, Dict

from aiogram import BaseMiddleware

THROTTLE_NOTICE = "⏳ Слишком часто. Подожди пару секунд."


class ThrottleMiddleware(BaseMiddleware):
    """Ограничитель частоты на пользователя.

    Один экземпляр обслуживает один поток событий: для сообщений и для
    колбэков регистрируются РАЗНЫЕ экземпляры с разными лимитами и
    раздельными словарями отметок. Колбэк — это навигация по меню, и лимит
    сообщений в три секунды сделал бы её неюзабельной.
    """

    def __init__(self, rate_limit: float = 3.0, *, notify: bool = False) -> None:
        self.rate_limit = rate_limit
        self.notify = notify
        self.user_timestamps: dict[int, float] = {}
        self.notified_at: dict[int, float] = {}

    async def __call__(
        self,
        handler: Callable[[Any, Dict[str, Any]], Awaitable[Any]],
        event: Any,
        data: Dict[str, Any],
    ) -> Any:
        user = getattr(event, "from_user", None)
        if user is None:
            # Сообщение от анонимного админа группы или пост канала: троттлить
            # некого. Раньше здесь был AttributeError прямо в middleware,
            # то есть до любого хендлера.
            return await handler(event, data)

        user_id = user.id
        now = time.monotonic()

        last = self.user_timestamps.get(user_id)
        if last is not None and (now - last) < self.rate_limit:
            if self.notify:
                await self._notify_throttled(event, user_id, now)
            return None

        self.user_timestamps[user_id] = now
        self._evict_stale(now)
        return await handler(event, data)

    async def _notify_throttled(self, event: Any, user_id: int, now: float) -> None:
        """Сообщить об отбое не чаще раза за окно.

        Для колбэка это ещё и обязательный `answer()`: без него у пользователя
        висит крутилка до 30 секунд, и троттлинг кнопок сделал бы интерфейс
        хуже, а не лучше.
        """
        last_notice = self.notified_at.get(user_id)
        if last_notice is not None and (now - last_notice) < self.rate_limit:
            return
        self.notified_at[user_id] = now

        answer = getattr(event, "answer", None)
        if answer is None:
            return
        try:
            await answer(THROTTLE_NOTICE)
        except Exception:
            # Юзер мог заблокировать бота ровно сейчас. Молчим.
            pass

    def _evict_stale(self, now: float) -> None:
        """Оба словаря чистятся вместе, иначе второй растёт неограниченно."""
        for store in (self.user_timestamps, self.notified_at):
            stale = [uid for uid, ts in store.items() if (now - ts) > self.rate_limit]
            for uid in stale:
                del store[uid]
```

- [ ] **Шаг 4: Зарегистрировать оба экземпляра**

В `bot/__main__.py` заменить строку 30

```python
    dp.message.middleware(ThrottleMiddleware())
```

на

```python
    # Разные лимиты и раздельное состояние: сообщение — это загрузка,
    # колбэк — навигация по меню. Общий лимит в три секунды сделал бы
    # меню неюзабельным.
    dp.message.middleware(ThrottleMiddleware(rate_limit=3.0, notify=True))
    dp.callback_query.middleware(ThrottleMiddleware(rate_limit=0.7, notify=True))
```

- [ ] **Шаг 5: Прогнать тесты**

Команда: `./scripts/test.sh -q`
Ожидается: PASS, регрессий нет

- [ ] **Шаг 6: Коммит**

```bash
git add bot/middlewares/throttle.py bot/__main__.py tests/test_throttle.py
git commit -m "fix(throttle): guard missing from_user, throttle callbacks, report rejections"
```

---
## Пакет B — Загрузчик (ветка `feat/local-bot-api`)

Пакет владеет файлами `bot/services/downloader.py`, `bot/services/media_probe.py`, `bot/utils/url_parser.py` и своими тестовыми файлами (`tests/test_downloader_*.py`, `tests/test_url_parser.py`, `tests/_gallery_fake.py`, плюс дописывание в `tests/test_media_probe.py`). Идёт параллельно с пакетами A, C и F — общих файлов с ними нет. `tests/conftest.py` принадлежит пакету C и здесь не редактируется.

Задачи внутри пакета выполняются строго по порядку (11 → 16): Task 12 и 14 опираются на структуры, введённые в Task 11 и 12.

---

### Task 11: Классификация ошибок загрузки и доведение stderr gallery-dl до пользователя

**Закрывает:** M-14, M-15, M-8

**Files:**
- Modify: `bot/services/downloader.py:1-26` (импорты и константы), `:38-77` (`_parse_error`), `:238-305` (`_try_gallery_dl`, `_try_gallery_dl_fallback`), `:308-443` (`download_media` — сбор выводов и вызовы `_parse_error`)
- Test: `tests/test_downloader_errors.py` (новый файл)

**Interfaces:**
- Consumes: ничего из других пакетов.
- Produces:
  - `GalleryDlRun` — dataclass `files: list[Path]`, `stderr: str`, `returncode: int | None`;
  - `_try_gallery_dl(url: str, filename: str) -> GalleryDlRun` (больше не `list[Path] | None`);
  - `_try_gallery_dl_fallback(url: str, filename: str, outputs: list[tuple[str, str]]) -> DownloadResult | None` (третий параметр — накопитель выводов);
  - `_parse_error(outputs: Sequence[tuple[str, str]], platform: str) -> str`;
  - `_error_surface(text: str) -> str`.

**⚠️ Меняет поведение для пользователя — нужна ручная проверка:** тексты сообщений об ошибке меняются целиком. Проверить руками три сценария: (1) ссылка на удалённый пост — должно прийти «🔍 Публикация не найдена…», а не «🍪 cookies устарели»; (2) приватный аккаунт Instagram — «🔒 Это приватная публикация…»; (3) заведомо битая ссылка на несуществующий домен внутри поддерживаемой платформы — должен прийти фрагмент реального stderr в `<code>`, а не пустое сообщение.

Сейчас `_parse_error` (`downloader.py:38-77`) классифицирует ошибку поиском подстроки по всему блобу stderr, и побеждает первая сработавшая ветка. Ветка `"cookies" in stderr_lower` стоит первой и срабатывает на самом факте упоминания флага — а yt-dlp почти в каждую свою ошибку дописывает «Use --cookies-from-browser or --cookies for the authentication». Из-за этого пользователю на любой сбой говорят «cookies устарели», а админ гоняется за фантомным протуханием кук. Аналогично: `"rate"` ловится внутри слова `separate`, `"404"` — в любом числе, содержащем 404, `"private"` — в `private key`, `"geo"` — в произвольном слове.

Решение: (а) выделять «поверхность ошибки» — только строки, помеченные как ошибка (`ERROR:` у yt-dlp, `[extractor][error]` у gallery-dl), а не весь вывод; (б) классифицировать упорядоченным списком именованных правил на скомпилированных регекспах с границами слов, специфичные раньше общих; (в) убрать ветку, срабатывающую на голом слове «cookies» — вместо неё правило на конкретные фразы мёртвой сессии; (г) принимать на вход все выводы всех запущенных утилит, а не только yt-dlp — сейчас stderr gallery-dl логируется и выбрасывается, и реальная причина теряется (живой случай из TODO: yt-dlp упал на внутреннем `Failed to parse JSON`, gallery-dl сказал `[instagram][error] HTTP redirect to login page`, пользователь получил JSON-трейсбек); (д) в фолбэке предпочитать текст gallery-dl — он человекочитаемее вывода yt-dlp.

- [ ] **Шаг 1: Написать падающий тест**

Создать `tests/test_downloader_errors.py`:

```python
"""Классификация ошибок загрузчика.

Ключевая проверка файла — негативная: раньше любая ошибка yt-dlp
классифицировалась как «cookies устарели», потому что yt-dlp дописывает
совет «Use --cookies ...» почти в каждое своё сообщение об ошибке.
"""

from bot.services.downloader import _error_surface, _parse_error

# ── Реальные фрагменты stderr ────────────────────────────────────────────

YTDLP_BOT_CHECK = (
    "[youtube] Extracting URL: https://www.youtube.com/watch?v=aaaaaaaaaaa\n"
    "[youtube] aaaaaaaaaaa: Downloading webpage\n"
    "ERROR: [youtube] aaaaaaaaaaa: Sign in to confirm you're not a bot. "
    "Use --cookies-from-browser or --cookies for the authentication. "
    "See  https://github.com/yt-dlp/yt-dlp/wiki/FAQ  for how to manually pass cookies.\n"
)

YTDLP_GENERIC_WITH_COOKIE_HINT = (
    "[instagram] Setting up cookies from /tmp/jw_downloads/jw_cookies_x/cookies.txt\n"
    "ERROR: [instagram] DBc1: Unable to extract shared data; "
    "please report this issue. Use --cookies for the authentication.\n"
)

YTDLP_JSON_TRACEBACK = (
    "ERROR: [instagram] DBc1: Failed to parse JSON "
    "(caused by JSONDecodeError('Expecting value in \\'\\' line 1 column 1'))\n"
)

GALLERY_DL_LOGIN_REDIRECT = "[instagram][error] HTTP redirect to login page\n"

YTDLP_PRIVATE_KEY_NOISE = (
    "WARNING: unable to load private key from /etc/ssl/private key store\n"
    "ERROR: [facebook] 123: Unable to download webpage: HTTP Error 500\n"
)

YTDLP_SEPARATE_NOISE = (
    "ERROR: [tiktok] 7: Requested format is not available; "
    "video and audio are stored in separate streams\n"
)

YTDLP_NOT_FOUND = "ERROR: [tiktok] 7: Unable to download webpage: HTTP Error 404: Not Found\n"

YTDLP_PRIVATE = "ERROR: [instagram] DBc1: This post is private and cannot be downloaded\n"


# ── _error_surface ───────────────────────────────────────────────────────


def test_error_surface_keeps_only_error_lines():
    surface = _error_surface(YTDLP_BOT_CHECK)
    assert "Extracting URL" not in surface
    assert "Downloading webpage" not in surface
    assert "Sign in to confirm" in surface


def test_error_surface_understands_gallery_dl_marker():
    assert "HTTP redirect to login page" in _error_surface(GALLERY_DL_LOGIN_REDIRECT)


def test_error_surface_is_empty_when_nothing_looks_like_an_error():
    assert _error_surface("[youtube] aaa: Downloading webpage\n") == ""


# ── Негативные проверки: то, ради чего задача ────────────────────────────


def test_cookie_hint_in_ytdlp_error_is_not_reported_as_expired_cookies():
    msg = _parse_error([("yt-dlp", YTDLP_BOT_CHECK)], "youtube")
    assert "🍪" not in msg
    assert "🤖" in msg


def test_cookie_flag_echo_is_not_reported_as_expired_cookies():
    msg = _parse_error([("yt-dlp", YTDLP_GENERIC_WITH_COOKIE_HINT)], "instagram")
    assert "🍪" not in msg


def test_private_key_noise_is_not_reported_as_private_video():
    msg = _parse_error([("yt-dlp", YTDLP_PRIVATE_KEY_NOISE)], "facebook")
    assert "🔒" not in msg


def test_word_separate_is_not_reported_as_rate_limit():
    msg = _parse_error([("yt-dlp", YTDLP_SEPARATE_NOISE)], "tiktok")
    assert "⏳" not in msg
    assert "🔄" in msg


# ── Позитивные проверки ──────────────────────────────────────────────────


def test_gallery_dl_login_redirect_wins_over_ytdlp_json_traceback():
    msg = _parse_error(
        [("yt-dlp", YTDLP_JSON_TRACEBACK), ("gallery-dl", GALLERY_DL_LOGIN_REDIRECT)],
        "instagram",
    )
    assert "🍪" in msg


def test_http_404_is_reported_as_not_found():
    assert "🔍" in _parse_error([("yt-dlp", YTDLP_NOT_FOUND)], "tiktok")


def test_private_post_is_reported_as_private():
    assert "🔒" in _parse_error([("yt-dlp", YTDLP_PRIVATE)], "instagram")


def test_unclassified_error_shows_gallery_dl_text_escaped():
    msg = _parse_error(
        [("yt-dlp", YTDLP_JSON_TRACEBACK), ("gallery-dl", "[pinterest][error] weird <thing> & co\n")],
        "pinterest",
    )
    assert "&lt;thing&gt;" in msg
    assert "&amp;" in msg
    assert "<code>" in msg


def test_unclassified_error_falls_back_to_ytdlp_when_gallery_dl_silent():
    msg = _parse_error([("yt-dlp", YTDLP_JSON_TRACEBACK), ("gallery-dl", "")], "instagram")
    assert "Failed to parse JSON" in msg


def test_empty_outputs_never_produce_an_empty_message():
    msg = _parse_error([], "youtube")
    assert msg.strip()
    assert "<code>" in msg
```

- [ ] **Шаг 2: Прогнать тест, убедиться что падает**

Команда: `./scripts/test.sh tests/test_downloader_errors.py -q`
Ожидается: FAIL — `ImportError: cannot import name '_error_surface'`, а после появления функции — падение сигнатуры `_parse_error` (сейчас она принимает `str`, а не список пар).

- [ ] **Шаг 3: Реализовать**

В `bot/services/downloader.py` поднять `import re` из тела `_find_downloaded_files` на верх файла и добавить `Sequence` к импорту из `collections.abc`. Блок импортов и констант (`:1-26`) привести к:

```python
from __future__ import annotations

import asyncio
import contextlib
import html
import os
import re
import shutil
import tempfile
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4

from loguru import logger

from bot.config import settings

DOWNLOAD_DIR = Path("/tmp/jw_downloads")
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".gif"}
GALLERY_DL_FALLBACK_PLATFORMS = {"instagram", "pinterest", "tiktok"}
# TikTok периодически отдаёт транзиторный WAF-челлендж (JS rehydration / HTTP 403):
# видео доступно, но конкретная попытка срывается. Делаем один мягкий ретрай.
TIKTOK_MAX_ATTEMPTS = 2
TIKTOK_RETRY_DELAY = 5
TIKTOK_TRANSIENT_MARKERS = ("rehydration", "403", "unable to extract", "unable to download webpage")
```

Заменить `_parse_error` (`:38-77`) целиком на:

```python
# ── Классификация ошибок загрузки ────────────────────────────────────────

# Классифицируем только те строки, которые сами утилиты пометили как ошибку:
# yt-dlp печатает «ERROR: ...», gallery-dl — «[extractor][error] ...».
# Прогресс, предупреждения и эхо аргументов в классификацию не попадают —
# именно из-за них раньше любой сбой объявлялся протуханием cookies.
_ERROR_LINE_RE = re.compile(
    r"(?:^|\s)(?:ERROR:|\[[^\]\s]+\]\[error\]|error:)",
    re.IGNORECASE,
)

# Порядок значим: специфичные правила стоят раньше общих. Например
# «rate-limit reached» — это фирменная фраза Instagram про мёртвую сессию,
# а не про троттлинг, поэтому dead_session идёт раньше rate_limit.
_ERROR_RULES: tuple[tuple[str, re.Pattern[str], str], ...] = (
    (
        "bot_check",
        re.compile(
            r"confirm you(?:'|’)?re not a bot|confirm you are not a bot|are you a robot",
            re.IGNORECASE,
        ),
        "🤖 Платформа просит подтвердить, что запрос не от робота. Попробуй позже.",
    ),
    (
        "age_gate",
        re.compile(
            r"age[- ]restricted|age[_ ]gate|confirm your age|age[_ ]verification"
            r"|inappropriate for some users",
            re.IGNORECASE,
        ),
        "🔞 Видео с возрастным ограничением.",
    ),
    (
        "dead_session",
        re.compile(
            r"http redirect to login page|login required|rate-limit reached"
            r"|cookies are no longer valid|the provided cookies"
            r"|session (?:has )?expired|not logged[ -]?in|please log ?in",
            re.IGNORECASE,
        ),
        "🍪 Платформа не пускает без авторизации: сессия истекла. Обратитесь к админу.",
    ),
    (
        "private",
        re.compile(
            r"\bprivate (?:video|post|account|profile|content)\b"
            r"|(?:video|post|account|profile) is private",
            re.IGNORECASE,
        ),
        "🔒 Это приватная публикация. Скачивание невозможно.",
    ),
    (
        "not_found",
        re.compile(
            r"http error 404\b|\b404:? not found\b|video unavailable"
            r"|no longer available|has been removed|does not exist"
            r"|(?:post|page|content) (?:isn'?t|is not) available",
            re.IGNORECASE,
        ),
        "🔍 Публикация не найдена. Возможно, она удалена или ссылка неверная.",
    ),
    (
        "geo_block",
        re.compile(
            r"geo[- ]?(?:restrict|block)|not available (?:in|from) your (?:country|location|region)"
            r"|blocked in your country",
            re.IGNORECASE,
        ),
        "🌍 Видео недоступно в текущем регионе.",
    ),
    (
        "rate_limit",
        re.compile(r"http error 429\b|\btoo many requests\b|\brate[- ]limit", re.IGNORECASE),
        "⏳ Слишком много запросов. Попробуй через минуту.",
    ),
    (
        "auth_required",
        re.compile(
            r"http error 40[13]\b|authentication required|requires (?:a )?(?:login|account|subscription)",
            re.IGNORECASE,
        ),
        "🔐 {platform}: требуется авторизация.",
    ),
    (
        "no_formats",
        re.compile(
            r"requested format (?:is )?not available|no video formats found"
            r"|no (?:suitable )?formats found|unsupported url",
            re.IGNORECASE,
        ),
        "🔄 Формат видео не поддерживается. Попробуй другую ссылку.",
    ),
    (
        "too_large",
        re.compile(r"max-?filesize|file is larger than", re.IGNORECASE),
        "📦 Файл слишком большой (больше {max_mb} МБ).",
    ),
)

# В фолбэке текст gallery-dl предпочтительнее: он на порядок человекочитаемее
# внутренних трейсбеков yt-dlp.
_SOURCE_PRIORITY = ("gallery-dl", "yt-dlp")


def _error_surface(text: str) -> str:
    """Оставляет из вывода утилиты только строки, помеченные как ошибка."""
    lines = [ln.strip() for ln in text.splitlines() if _ERROR_LINE_RE.search(ln)]
    return "\n".join(lines)


def _pick_fallback(outputs: Sequence[tuple[str, str]]) -> str:
    for wanted in _SOURCE_PRIORITY:
        for name, text in outputs:
            if name != wanted:
                continue
            candidate = _error_surface(text) or text.strip()
            if candidate:
                return candidate
    for _name, text in outputs:
        if text.strip():
            return text.strip()
    return "утилита завершилась без сообщения об ошибке"


def _parse_error(outputs: Sequence[tuple[str, str]], platform: str) -> str:
    """Человеческое сообщение об ошибке по выводам всех запущенных утилит.

    `outputs` — пары («yt-dlp» | «gallery-dl», stderr) в порядке запуска.
    """
    haystack = "\n".join(s for s in (_error_surface(t) for _n, t in outputs) if s)
    logger.debug("Parsing error | platform={} surface={}", platform, haystack[:500])

    for _name, pattern, template in _ERROR_RULES:
        if pattern.search(haystack):
            return template.format(
                platform=platform.capitalize(),
                max_mb=settings.MAX_FILE_SIZE_MB,
            )

    short_err = _pick_fallback(outputs)[:200]
    # Экранируем: сообщение уходит с parse_mode=HTML, а сырой вывод утилит
    # может содержать <, >, & и ломать разметку.
    return f"❌ Ошибка загрузки:\n<code>{html.escape(short_err)}</code>"
```

Заменить `_try_gallery_dl` и `_try_gallery_dl_fallback` (`:238-305`) на:

```python
@dataclass
class GalleryDlRun:
    files: list[Path] = field(default_factory=list)
    stderr: str = ""
    returncode: int | None = None


async def _try_gallery_dl(url: str, filename: str) -> GalleryDlRun:
    with _ephemeral_cookies() as cookies_path:
        cmd = [
            "gallery-dl",
            "-D", str(DOWNLOAD_DIR),
            "-f", f"{filename}_{{num}}.{{extension}}",
        ]
        if cookies_path is not None:
            cmd.extend(["--cookies", str(cookies_path)])
        cmd.append(url)

        logger.info("Falling back to gallery-dl | url={}", url)

        try:
            process = await asyncio.create_subprocess_exec(
                *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
            try:
                _stdout, stderr = await asyncio.wait_for(
                    process.communicate(), timeout=settings.DOWNLOAD_TIMEOUT
                )
            except asyncio.TimeoutError:
                logger.warning("gallery-dl timeout | url={}", url)
                process.kill()
                await process.wait()
                return GalleryDlRun(stderr="gallery-dl timeout")

            stderr_text = stderr.decode(errors="replace").strip()
            if process.returncode != 0:
                logger.warning(
                    "gallery-dl failed | code={} stderr={}",
                    process.returncode, stderr_text[:300],
                )
                return GalleryDlRun(stderr=stderr_text, returncode=process.returncode)

            return GalleryDlRun(
                files=_find_downloaded_files(DOWNLOAD_DIR, filename),
                stderr=stderr_text,
                returncode=process.returncode,
            )
        except Exception as exc:
            logger.exception("gallery-dl error: {}", exc)
            return GalleryDlRun(stderr=str(exc))


async def _try_gallery_dl_fallback(
    url: str, filename: str, outputs: list[tuple[str, str]]
) -> DownloadResult | None:
    run = await _try_gallery_dl(url, filename)
    # stderr gallery-dl копим ВСЕГДА — даже на успехе, чтобы при последующем
    # провале другой ветки его текст не пропал.
    if run.stderr:
        outputs.append(("gallery-dl", run.stderr))
    if not run.files:
        return None

    # _sized_files, а не голый .stat(): файл может исчезнуть между поиском и
    # проверкой размера, и FileNotFoundError отсюда улетает мимо всех try
    # прямо в диспетчер (H-9, закрыто Task 4 — не откатывать).
    sized_gd = _sized_files(run.files)
    if not sized_gd:
        return None

    valid_gd = [path for path, _ in sized_gd]
    total_size_mb = round(sum(size for _, size in sized_gd) / (1024 * 1024), 2)
    ext = valid_gd[0].suffix.lower()
    media_type = "image" if ext in IMAGE_EXTS else "video"
    logger.info("gallery-dl complete | files={} total_size={}MB", len(valid_gd), total_size_mb)

    if len(valid_gd) > 1:
        return DownloadResult(
            file_path=str(valid_gd[0]),
            file_paths=[str(f) for f in valid_gd],
            file_size_mb=total_size_mb,
            success=True,
            media_type=media_type,
        )
    return DownloadResult(
        file_path=str(valid_gd[0]),
        file_size_mb=total_size_mb,
        success=True,
        media_type=media_type,
    )
```

В `download_media` завести накопитель выводов и провести его через все три вызова фолбэка и оба вызова `_parse_error`. Сразу после `filename = uuid4().hex` (`:311`) добавить:

```python
    # Выводы всех запущенных утилит в порядке запуска — на них строится
    # сообщение об ошибке, если ни одна ветка не дала файлов.
    outputs: list[tuple[str, str]] = []
```

Далее заменить пять мест:

```python
    # было: gd_result = await _try_gallery_dl_fallback(url, filename)      (:328)
    gd_result = await _try_gallery_dl_fallback(url, filename, outputs)

    # было: return DownloadResult(success=False, error_message=_parse_error(full_output, platform))  (:398)
    outputs.append(("yt-dlp", full_output))
    if platform in GALLERY_DL_FALLBACK_PLATFORMS:
        gd_result = await _try_gallery_dl_fallback(url, filename, outputs)
        if gd_result:
            return gd_result
    return DownloadResult(success=False, error_message=_parse_error(outputs, platform))

    # было: gd_result = await _try_gallery_dl_fallback(url, filename)      (:432)
    outputs.append(("yt-dlp", full_output))
    if platform in GALLERY_DL_FALLBACK_PLATFORMS:
        gd_result = await _try_gallery_dl_fallback(url, filename, outputs)
        if gd_result:
            return gd_result

    _cleanup_glob(DOWNLOAD_DIR, filename)
    return DownloadResult(success=False, error_message=_parse_error(outputs, platform))
```

Порядок в блоке `:391-398` важен: `outputs.append(("yt-dlp", full_output))` должен стоять ДО вызова фолбэка, иначе вывод yt-dlp попадёт в список после вывода gallery-dl и перестанет отражать порядок запуска.

- [ ] **Шаг 4: Адаптировать тест исчезнувшего файла из Task 4**

`tests/test_downloader_stat_guard.py` написан против прежнего интерфейса: он подменяет `_try_gallery_dl` функцией, возвращающей `list[Path]`, и зовёт `_try_gallery_dl_fallback` с двумя аргументами. Оба изменились. Смысл теста — «исчезнувший файл не роняет хендлер» — сохраняем, форму приводим к новой.

Заменить в `tests/test_downloader_stat_guard.py` тест `test_gallery_fallback_returns_none_when_files_vanish` на:

```python
async def test_gallery_fallback_returns_none_when_files_vanish(tmp_path, monkeypatch):
    """H-9: файл исчез между поиском и stat() — это не повод падать."""
    from bot.services.downloader import GalleryDlRun

    ghost = tmp_path / "deadbeef_1.jpg"

    async def fake_gallery_dl(url, filename):
        return GalleryDlRun(files=[ghost], stderr="", returncode=0)

    monkeypatch.setattr(downloader, "_try_gallery_dl", fake_gallery_dl)

    outputs: list[tuple[str, str]] = []
    result = await downloader._try_gallery_dl_fallback("https://pin.it/abc", "deadbeef", outputs)

    assert result is None
```

Если поля `GalleryDlRun` в Task 11 названы иначе — использовать фактические имена; проверяемое поведение от этого не зависит.

- [ ] **Шаг 5: Прогнать тесты**

Команда: `./scripts/test.sh -q`
Ожидается: PASS, все тесты файла зелёные, 46 существующих тестов не сломаны, `tests/test_downloader_stat_guard.py` зелёный.

- [ ] **Шаг 6: Коммит**

```bash
git add bot/services/downloader.py tests/test_downloader_errors.py tests/test_downloader_stat_guard.py
git commit -m "fix(downloader): classify errors by rules, surface gallery-dl stderr to users"
```

---

### Task 12: gallery-dl — уникальные имена, лимит элементов, частичный успех

**Закрывает:** H-14, H-6, Low «Чистить частичные файлы на таймауте gallery-dl»

**Files:**
- Modify: `bot/services/downloader.py` (константы, новая `_build_gallery_dl_cmd`, `_try_gallery_dl`)
- Create: `tests/_gallery_fake.py`
- Test: `tests/test_downloader_gallery.py` (новый файл)

**Interfaces:**
- Consumes: `GalleryDlRun`, `_try_gallery_dl` из Task 11.
- Produces:
  - `GALLERY_DL_MAX_ITEMS: int = 10` — потребляется пакетом D в тексте предупреждения о срезанной доске;
  - `_build_gallery_dl_cmd(url: str, prefix: str, cookies_path: Path | None) -> list[str]`;
  - `tests/_gallery_fake.py::write_fake_gallery_dl(bin_dir: Path, *, files: int, exit_code: int, stderr_text: str) -> None` — используется также в Task 14.

**⚠️ Меняет поведение для пользователя — нужна ручная проверка:** с доски Pinterest теперь приходит не 3 случайных файла, а до 10 разных. Проверить руками на реальной доске: пользователь получает альбом из 10 разных пинов, квота списывается один раз.

Шаблон имени `-f "{uuid}_{num}.{extension}"` (`downloader.py:243`) использует `{num}` — индекс файла ВНУТРИ поста (карусель 1..N), а не позицию пина в доске. Всё складывается плоско в один `-D`, поэтому на доске из 12 пинов на диск легло 3 файла (jpg/png/mp4), а 9 gallery-dl пропустил как «уже существует» — живой тест зафиксирован в TODO. Лечится добавлением в шаблон оригинального имени файла из метаданных gallery-dl: у Pinterest это хеш CDN, уникальный на пин. `{num}` оставляем ПЕРЕД ним, чтобы порядок карусели не терялся: ключ сортировки в `_find_downloaded_files` берёт первое `_<число>` после префикса, то есть по-прежнему видит именно `{num}`.

Параллельно закрываем H-6: одна единица квоты вытягивала доску целиком (в логах уже `files=12`, а доска — 1723 айтема). Ставим `--range 1-10`. И перестаём выбрасывать уже скачанное при `returncode != 0`: gallery-dl возвращает ненулевой код на частичных сбоях, а файлы при этом лежат на диске — ровно так же, как уже сделано для yt-dlp (`downloader.py:382-384`). Заодно чистим частичные файлы на таймауте: сейчас ветка `asyncio.TimeoutError` (`:257-261`) уходит без `_cleanup_glob`, и обрезки остаются в tmpfs до подметальщика.

Про шаблон: используется синтаксис альтернатив gallery-dl `{filename|num}` — «первое непустое значение». Он страхует случай, когда экстрактор не проставил `filename`. Живая проверка в конце задачи — гейт: если gallery-dl отвергнет шаблон с ошибкой формата, заменить `{filename|num}` на `{filename}` и перепрогнать проверку.

- [ ] **Шаг 1: Написать падающий тест**

Создать `tests/_gallery_fake.py` (вспомогательный модуль, не тест):

```python
"""Поддельный gallery-dl для тестов без сети.

Кладёт исполняемый скрипт с именем `gallery-dl` в указанный каталог; тест
подставляет этот каталог в PATH. Скрипт разбирает `-D` и `-f` из argv,
создаёт запрошенное число файлов и завершается заданным кодом.
"""

from __future__ import annotations

from pathlib import Path

_TEMPLATE = '''#!/usr/bin/env python3
import pathlib
import sys

argv = sys.argv[1:]
dest = pathlib.Path(argv[argv.index("-D") + 1])
name_format = argv[argv.index("-f") + 1]
prefix = name_format.split("_{{", 1)[0]

dest.mkdir(parents=True, exist_ok=True)
for i in range(1, {files} + 1):
    # Имитируем новый шаблон: <prefix>_<num>_<уникальное имя>.<ext>
    (dest / f"{{prefix}}_1_item{{i}}.jpg").write_bytes(b"\\xff\\xd8\\xff" + b"x" * 200)

sys.stderr.write({stderr_text!r})
sys.exit({exit_code})
'''


def write_fake_gallery_dl(
    bin_dir: Path, *, files: int = 2, exit_code: int = 0, stderr_text: str = ""
) -> None:
    bin_dir.mkdir(parents=True, exist_ok=True)
    script = bin_dir / "gallery-dl"
    script.write_text(
        _TEMPLATE.format(files=files, exit_code=exit_code, stderr_text=stderr_text)
    )
    script.chmod(0o755)
```

Создать `tests/test_downloader_gallery.py`:

```python
from pathlib import Path

import pytest

from bot.services import downloader
from bot.services.downloader import (
    GALLERY_DL_MAX_ITEMS,
    _build_gallery_dl_cmd,
    _find_downloaded_files,
    _try_gallery_dl,
)
from tests._gallery_fake import write_fake_gallery_dl


# ── Командная строка ─────────────────────────────────────────────────────


def test_name_template_is_unique_per_item():
    cmd = _build_gallery_dl_cmd("https://www.pinterest.com/u/board/", "deadbeef", None)
    template = cmd[cmd.index("-f") + 1]
    assert template.startswith("deadbeef_")
    # {num} — порядок внутри поста, он должен идти первым, иначе сломается
    # ключ сортировки в _find_downloaded_files.
    assert template.index("{num}") < template.index("{filename")
    assert "{filename|num}" in template
    assert template.endswith(".{extension}")


def test_item_count_is_capped():
    cmd = _build_gallery_dl_cmd("https://www.pinterest.com/u/board/", "deadbeef", None)
    assert "--range" in cmd
    assert cmd[cmd.index("--range") + 1] == f"1-{GALLERY_DL_MAX_ITEMS}"
    assert GALLERY_DL_MAX_ITEMS <= 10


def test_cookies_are_passed_only_when_present(tmp_path):
    assert "--cookies" not in _build_gallery_dl_cmd("https://pin.it/a", "abc", None)
    jar = tmp_path / "cookies.txt"
    jar.write_text("# Netscape HTTP Cookie File\n")
    cmd = _build_gallery_dl_cmd("https://pin.it/a", "abc", jar)
    assert cmd[cmd.index("--cookies") + 1] == str(jar)


def test_url_is_the_last_argument():
    cmd = _build_gallery_dl_cmd("https://pin.it/abc", "abc", None)
    assert cmd[-1] == "https://pin.it/abc"


# ── Сортировка при новом шаблоне ─────────────────────────────────────────


def test_sort_key_still_follows_carousel_order(tmp_path):
    prefix = "abc123"
    for name in (f"{prefix}_2_zzz.jpg", f"{prefix}_10_aaa.jpg", f"{prefix}_1_mmm.jpg"):
        (tmp_path / name).write_bytes(b"\xff\xd8\xffdata")
    found = [p.name for p in _find_downloaded_files(tmp_path, prefix)]
    assert found == [f"{prefix}_1_mmm.jpg", f"{prefix}_2_zzz.jpg", f"{prefix}_10_aaa.jpg"]


def test_board_items_share_num_and_sort_deterministically(tmp_path):
    prefix = "abc123"
    for name in (f"{prefix}_1_ccc.jpg", f"{prefix}_1_aaa.jpg", f"{prefix}_1_bbb.jpg"):
        (tmp_path / name).write_bytes(b"\xff\xd8\xffdata")
    found = [p.name for p in _find_downloaded_files(tmp_path, prefix)]
    assert found == [f"{prefix}_1_aaa.jpg", f"{prefix}_1_bbb.jpg", f"{prefix}_1_ccc.jpg"]


# ── Частичный успех и таймаут ────────────────────────────────────────────


@pytest.fixture
def fake_gallery_env(tmp_path, monkeypatch):
    bin_dir = tmp_path / "bin"
    dl_dir = tmp_path / "downloads"
    dl_dir.mkdir()
    monkeypatch.setenv("PATH", str(bin_dir))
    monkeypatch.setattr(downloader, "DOWNLOAD_DIR", dl_dir)
    return bin_dir, dl_dir


async def test_partial_failure_keeps_already_downloaded_files(fake_gallery_env):
    bin_dir, _dl_dir = fake_gallery_env
    write_fake_gallery_dl(
        bin_dir,
        files=3,
        exit_code=1,
        stderr_text="[pinterest][error] 2 items could not be downloaded\n",
    )
    run = await _try_gallery_dl("https://www.pinterest.com/u/board/", "abc123")
    assert run.returncode == 1
    assert len(run.files) == 3
    assert "could not be downloaded" in run.stderr


async def test_clean_exit_returns_files(fake_gallery_env):
    bin_dir, _dl_dir = fake_gallery_env
    write_fake_gallery_dl(bin_dir, files=2, exit_code=0)
    run = await _try_gallery_dl("https://www.pinterest.com/u/board/", "abc123")
    assert run.returncode == 0
    assert len(run.files) == 2


async def test_timeout_removes_partial_files(fake_gallery_env, monkeypatch):
    bin_dir, dl_dir = fake_gallery_env
    write_fake_gallery_dl(bin_dir, files=2, exit_code=0)
    monkeypatch.setattr(downloader.settings, "DOWNLOAD_TIMEOUT", 900)

    real_wait_for = downloader.asyncio.wait_for

    async def fake_wait_for(awaitable, timeout):
        # Дать поддельному gallery-dl реально дописать файлы, и только потом
        # изобразить таймаут — иначе проверять было бы нечего.
        await real_wait_for(awaitable, timeout)
        raise downloader.asyncio.TimeoutError

    monkeypatch.setattr(downloader.asyncio, "wait_for", fake_wait_for)

    run = await _try_gallery_dl("https://www.pinterest.com/u/board/", "abc123")
    assert run.files == []
    assert list(Path(dl_dir).glob("abc123*")) == []
```

- [ ] **Шаг 2: Прогнать тест, убедиться что падает**

Команда: `./scripts/test.sh tests/test_downloader_gallery.py -q`
Ожидается: FAIL — `ImportError: cannot import name 'GALLERY_DL_MAX_ITEMS'` / `'_build_gallery_dl_cmd'`.

- [ ] **Шаг 3: Реализовать**

В блок констант `bot/services/downloader.py` добавить:

```python
# Потолок числа элементов, которые gallery-dl вытянет за один запрос.
# Без него один запрос к доске Pinterest (1723 айтема в живом тесте)
# стоит пользователю одну единицу квоты.
GALLERY_DL_MAX_ITEMS = 10
```

Добавить чистую функцию построения команды (перед `_try_gallery_dl`):

```python
def _build_gallery_dl_cmd(url: str, prefix: str, cookies_path: Path | None) -> list[str]:
    """Командная строка gallery-dl. Вынесена отдельно, чтобы её можно было
    проверить тестом без сети.

    `prefix` — наш uuid4-префикс. `{filename}` в шаблоне — это поле МЕТАДАННЫХ
    gallery-dl (оригинальное имя файла у источника), а не наша переменная:
    двойные фигурные скобки в f-строке дают литеральные одинарные. Именно оно
    делает имя уникальным на элемент — без него все пины доски разрешались в
    одно и то же имя и 9 из 12 пропускались как «уже существует».
    `{filename|num}` — синтаксис альтернатив gallery-dl: если экстрактор не
    проставил `filename`, подставится `num`.
    """
    cmd = [
        "gallery-dl",
        "-D", str(DOWNLOAD_DIR),
        "-f", f"{prefix}_{{num}}_{{filename|num}}.{{extension}}",
        "--range", f"1-{GALLERY_DL_MAX_ITEMS}",
    ]
    if cookies_path is not None:
        cmd.extend(["--cookies", str(cookies_path)])
    cmd.append(url)
    return cmd
```

Переписать тело `_try_gallery_dl` (внутренности `with _ephemeral_cookies()`), сохранив сигнатуру и тип возврата из Task 11:

```python
async def _try_gallery_dl(url: str, filename: str) -> GalleryDlRun:
    with _ephemeral_cookies() as cookies_path:
        cmd = _build_gallery_dl_cmd(url, filename, cookies_path)

        logger.info("Falling back to gallery-dl | url={}", url)

        try:
            process = await asyncio.create_subprocess_exec(
                *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
            try:
                _stdout, stderr = await asyncio.wait_for(
                    process.communicate(), timeout=settings.DOWNLOAD_TIMEOUT
                )
            except asyncio.TimeoutError:
                logger.warning("gallery-dl timeout | url={}", url)
                process.kill()
                await process.wait()
                # Обрезки после таймаута раньше оставались в tmpfs до
                # подметальщика и могли уйти пользователю как готовое медиа.
                _cleanup_glob(DOWNLOAD_DIR, filename)
                return GalleryDlRun(stderr="gallery-dl timeout")

            stderr_text = stderr.decode(errors="replace").strip()
            files = _find_downloaded_files(DOWNLOAD_DIR, filename)

            if process.returncode != 0:
                if files:
                    # Частичный сбой: часть элементов доски недоступна, но
                    # остальные уже на диске. Выбрасывать их — терять работу,
                    # за которую с пользователя уже списана квота.
                    logger.warning(
                        "gallery-dl exited with code {} but files exist | files={} stderr={}",
                        process.returncode, len(files), stderr_text[:300],
                    )
                else:
                    logger.warning(
                        "gallery-dl failed | code={} stderr={}",
                        process.returncode, stderr_text[:300],
                    )

            return GalleryDlRun(
                files=files, stderr=stderr_text, returncode=process.returncode
            )
        except Exception as exc:
            logger.exception("gallery-dl error: {}", exc)
            return GalleryDlRun(stderr=str(exc))
```

- [ ] **Шаг 4: Прогнать тесты**

Команда: `./scripts/test.sh -q`
Ожидается: PASS, регрессий нет.

- [ ] **Шаг 5: Живая проверка на реальной доске (требует сети; куки опциональны)**

Взять любую публичную доску Pinterest с не менее чем 12 пинами (ту же, на которой снималась находка H-14) и прогнать новый шаблон напрямую:

```bash
docker build --target test -t jw_downloader:test .
docker run --rm -v "$PWD/secrets:/app/secrets:ro" jw_downloader:test \
  sh -c 'mkdir -p /tmp/pb && gallery-dl -D /tmp/pb \
           -f "probe_{num}_{filename|num}.{extension}" --range 1-12 \
           --cookies /app/secrets/cookies.txt "<URL публичной доски>" ; \
         ls -1 /tmp/pb | wc -l ; ls -1 /tmp/pb'
```

Критерий решения: на диск легло **≥ 10 различимых файлов** (до фикса было 3). Если gallery-dl отвергает шаблон с ошибкой формата — заменить `{filename|num}` на `{filename}` в `_build_gallery_dl_cmd` и в тесте `test_name_template_is_unique_per_item`, перепрогнать команду и тесты.

- [ ] **Шаг 6: Коммит**

```bash
git add bot/services/downloader.py tests/_gallery_fake.py tests/test_downloader_gallery.py
git commit -m "fix(downloader): unique gallery-dl filenames, cap item count, keep partial results"
```

---

### Task 13: Белый список расширений, `.gif` как анимация, контейнер Instagram

**Закрывает:** C-2 (частично), M-17, M-16

**Files:**
- Modify: `bot/services/downloader.py:19` (константы расширений), `:120-128` (ветка Instagram), `:213-226` (`_find_downloaded_files`), `:288` и `:408` (вычисление `media_type`)
- Test: `tests/test_downloader_files.py` (новый файл)

**Interfaces:**
- Consumes: ничего из других пакетов.
- Produces (потребляет пакет D следующей волной):
  - `VIDEO_EXTS: set[str]`, `IMAGE_EXTS: set[str]` (уже без `.gif`), `ANIMATION_EXTS: set[str] = {".gif"}`, `ALLOWED_EXTS: set[str]`;
  - `_media_type_for(path: Path) -> str` → `"video" | "image" | "animation"`;
  - `DownloadResult.media_type` теперь может принимать значение `"animation"`.

**⚠️ Меняет поведение для пользователя — нужна ручная проверка:** (1) `.gif` из Instagram/Pinterest должен приходить анимацией, а не статичным кадром (отправку делает пакет D — до его выполнения `media_type="animation"` обрабатывается пакетом D как видео, проверять после D); (2) битые и обрезанные загрузки больше не уходят как успех — проверить, что на заведомо срывающейся ссылке приходит текст ошибки, а не пустое видео, и квота не списывается.

`_find_downloaded_files` (`downloader.py:213-226`) отбирает файлы по одному лишь префиксу имени. Поэтому промежуточные артефакты yt-dlp — `.part`, `.ytdl`, отдельные DASH-дорожки `.f140.m4a` / `.f137.mp4` — проходят отбор и уходят пользователю как готовое медиа со списанием квоты. Это ровно C-2. Белый список расширений плюс явное исключение фрагментов `.f<id>.<ext>` закрывает вопрос: `.f137.mp4` имеет допустимый суффикс, поэтому одного белого списка мало.

**ВАЖНО:** пункт с белым списком расширений частично пересекается с Task 5 плана `docs/superpowers/plans/2026-09-03-local-bot-api.md` («Рабочая папка на загрузку и белый список расширений»), которая ещё не начата. После этой задачи Task 5 того плана **обязана быть пересмотрена**: белый список расширений в ней уже закрыт здесь, остаётся только структурная часть — отдельная рабочая папка на каждую загрузку. Это записать в `.superpowers/sdd/2026-09-03-local-bot-api/progress.md` при мерже пакета.

`.gif` лежит в `IMAGE_EXTS` (`:19`), поэтому уходит через `sendPhoto`/`InputMediaPhoto` и теряет анимацию — пользователь получает статичный кадр с подписью «✅ Фото». Вводим третий тип `animation`.

Instagram — единственная ветка `_build_command` без `--merge-output-format mp4` (`:120-128`): селектор `bv*+ba/b` при vp9/opus склеится в `.mkv`/`.webm`, расширение не совпадёт с `IMAGE_EXTS`, файл уйдёт через `reply_video` и не проиграется в Telegram.

- [ ] **Шаг 1: Написать падающий тест**

Создать `tests/test_downloader_files.py`:

```python
from pathlib import Path

from bot.services.downloader import (
    ALLOWED_EXTS,
    ANIMATION_EXTS,
    IMAGE_EXTS,
    VIDEO_EXTS,
    _build_command,
    _find_downloaded_files,
    _media_type_for,
)


def _touch(directory: Path, name: str) -> Path:
    p = directory / name
    p.write_bytes(b"\x00" * 64)
    return p


# ── Белый список расширений (C-2) ────────────────────────────────────────


def test_partial_and_fragment_files_are_not_treated_as_media(tmp_path):
    prefix = "abc123"
    _touch(tmp_path, f"{prefix}.mp4")
    _touch(tmp_path, f"{prefix}.mp4.part")
    _touch(tmp_path, f"{prefix}.ytdl")
    _touch(tmp_path, f"{prefix}.f137.mp4")
    _touch(tmp_path, f"{prefix}.f140.m4a")
    found = [p.name for p in _find_downloaded_files(tmp_path, prefix)]
    assert found == [f"{prefix}.mp4"]


def test_unknown_extensions_are_rejected(tmp_path):
    prefix = "abc123"
    _touch(tmp_path, f"{prefix}.json")
    _touch(tmp_path, f"{prefix}.txt")
    _touch(tmp_path, f"{prefix}.description")
    assert _find_downloaded_files(tmp_path, prefix) == []


def test_other_prefixes_are_never_picked_up(tmp_path):
    _touch(tmp_path, "abc123.mp4")
    _touch(tmp_path, "def456.mp4")
    found = [p.name for p in _find_downloaded_files(tmp_path, "abc123")]
    assert found == ["abc123.mp4"]


def test_allowed_exts_is_the_union_of_the_three_sets():
    assert ALLOWED_EXTS == VIDEO_EXTS | IMAGE_EXTS | ANIMATION_EXTS
    assert ".mp4" in VIDEO_EXTS
    assert ".webm" in VIDEO_EXTS
    assert ".mkv" in VIDEO_EXTS


# ── .gif как анимация (M-17) ─────────────────────────────────────────────


def test_gif_is_no_longer_an_image():
    assert ".gif" not in IMAGE_EXTS
    assert ANIMATION_EXTS == {".gif"}


def test_media_type_has_three_values(tmp_path):
    assert _media_type_for(tmp_path / "a.gif") == "animation"
    assert _media_type_for(tmp_path / "a.jpg") == "image"
    assert _media_type_for(tmp_path / "a.PNG") == "image"
    assert _media_type_for(tmp_path / "a.mp4") == "video"
    assert _media_type_for(tmp_path / "a.webm") == "video"


# ── Контейнер Instagram (M-16) ───────────────────────────────────────────


def test_instagram_pins_output_container_to_mp4(tmp_path):
    cmd = _build_command(
        "https://www.instagram.com/p/DBc1/", "instagram", tmp_path / "out.%(ext)s", None
    )
    assert "--merge-output-format" in cmd
    assert cmd[cmd.index("--merge-output-format") + 1] == "mp4"


def test_every_platform_pins_output_container_to_mp4(tmp_path):
    for platform in ("instagram", "tiktok", "pinterest", "facebook", "youtube"):
        cmd = _build_command(
            "https://example.invalid/x", platform, tmp_path / "out.%(ext)s", None
        )
        assert "--merge-output-format" in cmd, platform
        assert cmd[cmd.index("--merge-output-format") + 1] == "mp4", platform
```

- [ ] **Шаг 2: Прогнать тест, убедиться что падает**

Команда: `./scripts/test.sh tests/test_downloader_files.py -q`
Ожидается: FAIL — `ImportError: cannot import name 'ANIMATION_EXTS'`.

- [ ] **Шаг 3: Реализовать**

Заменить строку `IMAGE_EXTS = {...}` (`:19`) на блок:

```python
VIDEO_EXTS = {".mp4", ".mkv", ".webm", ".mov", ".m4v"}
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".heic"}
# .gif вынесен из изображений: sendPhoto сохраняет только первый кадр,
# анимацию отдаёт sendAnimation.
ANIMATION_EXTS = {".gif"}
ALLOWED_EXTS = VIDEO_EXTS | IMAGE_EXTS | ANIMATION_EXTS
# Промежуточные артефакты yt-dlp: отдельные DASH-дорожки вида `.f137.mp4`
# имеют допустимое расширение, поэтому одного белого списка мало.
_FRAGMENT_RE = re.compile(r"\.f\d+\.[A-Za-z0-9]+$")
```

Добавить рядом с `_find_downloaded_files`:

```python
def _is_finished_media(path: Path) -> bool:
    """Готовый к отправке файл, а не промежуточный артефакт загрузчика."""
    if path.suffix.lower() not in ALLOWED_EXTS:
        return False
    return not _FRAGMENT_RE.search(path.name)


def _media_type_for(path: Path) -> str:
    ext = path.suffix.lower()
    if ext in ANIMATION_EXTS:
        return "animation"
    if ext in IMAGE_EXTS:
        return "image"
    return "video"
```

Заменить `_find_downloaded_files` (`:213-226`) на:

```python
def _find_downloaded_files(directory: Path, prefix: str) -> list[Path]:
    result = []
    for f in directory.iterdir():
        if not f.is_file() or not f.name.startswith(prefix):
            continue
        if not _is_finished_media(f):
            continue
        result.append(f)

    def _sort_key(p: Path) -> tuple[int, str]:
        # Числовой суффикс сразу после префикса: prefix_42.ext → 42
        rest = p.name[len(prefix):]
        m = re.search(r"_(\d+)", rest)
        return (int(m.group(1)) if m else 0, p.name)

    return sorted(result, key=_sort_key)
```

В ветке Instagram `_build_command` (`:120-128`) после закрывающей скобки `cmd.extend([...])` добавить строку:

```python
        cmd.extend(["--merge-output-format", "mp4"])
```

Заменить оба вычисления типа медиа на вызов общей функции:

```python
    # было в _try_gallery_dl_fallback (:288):
    #   ext = valid_gd[0].suffix.lower()
    #   media_type = "image" if ext in IMAGE_EXTS else "video"
    media_type = _media_type_for(valid_gd[0])

    # было в download_media (:407-408):
    #   ext = first_file.suffix.lower()
    #   media_type = "image" if ext in IMAGE_EXTS else "video"
    media_type = _media_type_for(first_file)
```

- [ ] **Шаг 4: Прогнать тесты**

Команда: `./scripts/test.sh -q`
Ожидается: PASS, регрессий нет.

- [ ] **Шаг 5: Коммит**

```bash
git add bot/services/downloader.py tests/test_downloader_files.py
git commit -m "fix(downloader): whitelist media extensions, treat gif as animation, pin Instagram container"
```

---

### Task 14: Безопасность вызовов yt-dlp и логов

**Закрывает:** H-15, M-20, M-21, Low «Убрать `--age-limit 99`»

**Files:**
- Modify: `bot/services/downloader.py:102-115` (`_build_command`), `:80-99` (`_ephemeral_cookies`), логирующие вызовы с URL (`:249`, `:258`, `:344`, `:364`)
- Test: `tests/test_downloader_security.py` (новый файл)

**Interfaces:**
- Consumes: `mask_secrets` из `bot/utils/log_guard.py` (создан пакетом 0, здесь НЕ редактируется); `write_fake_gallery_dl` из `tests/_gallery_fake.py` (Task 12).
- Produces: ничего нового наружу.

**⚠️ Меняет поведение для пользователя — нужна ручная проверка:** восстановленная верификация TLS может отбить загрузку там, где раньше она проходила (перехватывающий прокси, свой CA, устаревший бандл сертификатов в образе). После задачи обязателен смоук-прогон по всем пяти платформам: каждая должна отдавать медиа как раньше. Если какая-то платформа начала падать с ошибкой сертификата — это не повод возвращать флаг, а сигнал разбираться с доверенными корнями в образе.

**(а) Снять `--no-check-certificates` (`:105`).** Верификация TLS выключена для всех запросов yt-dlp, а эфемерная банка несёт живые сессионные куки пяти платформ. Подмена DNS, враждебный upstream или captive portal получают `sessionid` в чистом виде. Флаг ставился под «живые куки», но именно живые куки и делают его опасным.

**(б) Убрать `--age-limit 99` (`:108`).** У yt-dlp по умолчанию возрастного фильтра нет вовсе; `99` не «отключает фильтр», а **добавляет** условие «возрастной рейтинг ≤ 99» и отбрасывает всё, у чего рейтинг выше или неизвестен нестандартным образом. Ветку `_parse_error` про возрастное ограничение при этом **оставляем**: возрастной гейт отдаёт сам сервис (YouTube отвечает «Sign in to confirm your age»), к клиентскому фильтру это отношения не имеет, и ветка достижима. В TODO эта ветка помечена как «недостижимая» — это неверно, и решение оставить её осознанное.

**(в) Копию кук класть в RAM-каталог.** `tempfile.mkdtemp(prefix="jw_cookies_")` (`:93`) пишет в `/tmp`, а tmpfs смонтирован только на `/tmp/jw_downloads` — то есть копия боевых кук ложится в writable-слой контейнера на флеш. Переносим в `DOWNLOAD_DIR`. **Отдельного подметальщика этих каталогов не добавляем, и это осознанное решение:** tmpfs живёт в памяти и очищается перезапуском контейнера, `finally: shutil.rmtree(ignore_errors=True)` уже отрабатывает и на исключении, и на таймауте, а подметальщик, не различающий «каталог идёт прямо сейчас» и «каталог осиротел», убьёт куки у параллельной загрузки (семафор разрешает три). Структурный подметальщик с учётом занятости придёт с Task 8 плана local-bot-api.

Каталоги кук не попадут ни в `_find_downloaded_files`, ни в `_cleanup_glob`, ни в `periodic_cleanup` — все три фильтруют по `is_file()`.

**(г) Редакция секретов в логах.** `downloader.py:344` пишет полный URL в лог; в живом логе видны `?stkn=…` и `?igsh=…` — это пер-шаринговые токены, привязанные к аккаунту отправителя. Прогоняем URL через `mask_secrets` во всех логирующих вызовах загрузчика.

- [ ] **Шаг 1: Написать падающий тест**

Создать `tests/test_downloader_security.py`:

```python
import pytest
from loguru import logger

from bot.services import downloader
from bot.services.downloader import _build_command, _ephemeral_cookies, _try_gallery_dl
from tests._gallery_fake import write_fake_gallery_dl

PLATFORMS = ("instagram", "tiktok", "pinterest", "facebook", "youtube")


# ── TLS и возрастной фильтр ──────────────────────────────────────────────


def test_tls_verification_is_never_disabled(tmp_path):
    for platform in PLATFORMS:
        cmd = _build_command(
            "https://example.invalid/x", platform, tmp_path / "o.%(ext)s", None
        )
        assert "--no-check-certificates" not in cmd, platform
        assert "--no-check-certificate" not in cmd, platform


def test_client_side_age_filter_is_not_imposed(tmp_path):
    for platform in PLATFORMS:
        cmd = _build_command(
            "https://example.invalid/x", platform, tmp_path / "o.%(ext)s", None
        )
        assert "--age-limit" not in cmd, platform


def test_cookies_flag_still_passed_when_jar_given(tmp_path):
    jar = tmp_path / "cookies.txt"
    jar.write_text("# Netscape HTTP Cookie File\n")
    cmd = _build_command("https://example.invalid/x", "tiktok", tmp_path / "o.mp4", jar)
    assert cmd[cmd.index("--cookies") + 1] == str(jar)


# ── Копия кук в RAM-каталоге ─────────────────────────────────────────────


def test_cookie_copy_lands_inside_the_tmpfs_download_dir(tmp_path, monkeypatch):
    dl_dir = tmp_path / "downloads"
    master = tmp_path / "master-cookies.txt"
    original = "# Netscape HTTP Cookie File\n.example.com\tTRUE\t/\tTRUE\t0\tsessionid\tSECRET\n"
    master.write_text(original)

    monkeypatch.setattr(downloader, "DOWNLOAD_DIR", dl_dir)
    monkeypatch.setattr(downloader.settings, "COOKIES_FILE", str(master))

    with _ephemeral_cookies() as jar:
        assert jar is not None
        # Каталог кук — ВНУТРИ tmpfs-папки загрузок, а не в /tmp контейнера.
        assert jar.parent.parent == dl_dir
        assert jar.read_text() == original
        # Копия перезаписывается утилитами — имитируем это и проверяем,
        # что мастер-файл не пострадал.
        jar.write_text("# Netscape HTTP Cookie File\n")
        leaked = jar.parent

    # Каталог убран в finally.
    assert not leaked.exists()
    # Мастер-файл цел: ради этого эфемерные копии и заведены.
    assert master.read_text() == original


def test_no_cookie_file_yields_none(tmp_path, monkeypatch):
    monkeypatch.setattr(downloader, "DOWNLOAD_DIR", tmp_path / "downloads")
    monkeypatch.setattr(downloader.settings, "COOKIES_FILE", "")
    with _ephemeral_cookies() as jar:
        assert jar is None


# ── Редакция секретов в логах ────────────────────────────────────────────


@pytest.fixture
def captured_logs():
    messages: list[str] = []
    sink_id = logger.add(lambda m: messages.append(m.record["message"]), level="DEBUG")
    try:
        yield messages
    finally:
        logger.remove(sink_id)


async def test_share_token_never_reaches_the_log(tmp_path, monkeypatch, captured_logs):
    bin_dir = tmp_path / "bin"
    dl_dir = tmp_path / "downloads"
    dl_dir.mkdir()
    write_fake_gallery_dl(bin_dir, files=1, exit_code=0)
    monkeypatch.setenv("PATH", str(bin_dir))
    monkeypatch.setattr(downloader, "DOWNLOAD_DIR", dl_dir)
    monkeypatch.setattr(downloader.settings, "COOKIES_FILE", "")

    secret = "PRIVATESHARETOKENVALUE"
    await _try_gallery_dl(f"https://www.instagram.com/p/DBc1/?igsh={secret}", "abc123")

    joined = "\n".join(captured_logs)
    assert "instagram.com/p/DBc1" in joined  # сама ссылка осталась читаемой
    assert secret not in joined
    assert "<redacted>" in joined
```

- [ ] **Шаг 2: Прогнать тест, убедиться что падает**

Команда: `./scripts/test.sh tests/test_downloader_security.py -q`
Ожидается: FAIL — `assert '--no-check-certificates' not in cmd`, `assert jar.parent.parent == dl_dir`, `assert secret not in joined`.

- [ ] **Шаг 3: Реализовать**

Добавить импорт в `bot/services/downloader.py` (после `from bot.config import settings`):

```python
from bot.utils.log_guard import mask_secrets
```

Заменить голову `_build_command` (`:102-115`) на:

```python
def _build_command(url: str, platform: str, output_path: Path, cookies_path: Path | None) -> list[str]:
    cmd = [
        "yt-dlp",
        # --no-check-certificates НЕ ставим: в банке едут живые сессионные куки
        # пяти платформ, и отключённая верификация TLS отдаёт их любому, кто
        # сумеет встать посередине (подмена DNS, captive portal).
        "--socket-timeout", "30",
        "--retries", "3",
        # --age-limit НЕ ставим: у yt-dlp по умолчанию возрастного фильтра нет,
        # а «--age-limit 99» не отключает фильтр, а добавляет условие
        # «рейтинг ≤ 99». Возрастной гейт со стороны сервиса это не снимает.
        "--max-filesize", f"{settings.MAX_FILE_SIZE_MB}M",
        "-o", str(output_path),
    ]

    if cookies_path is not None:
        cmd.extend(["--cookies", str(cookies_path)])
        logger.debug("Using cookies file: {}", cookies_path)
```

Заменить хвост `_ephemeral_cookies` (`:93`) на:

```python
    # Каталог кладём в DOWNLOAD_DIR: он смонтирован как tmpfs, то есть живёт в
    # памяти. Дефолтный /tmp — обычный слой контейнера на флеше, и копия боевых
    # кук переживала бы там `docker kill` и пропадание питания.
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    tmp_dir = Path(tempfile.mkdtemp(prefix="jw_cookies_", dir=DOWNLOAD_DIR))
```

Прогнать URL через `mask_secrets` во всех четырёх логирующих вызовах, где он фигурирует:

```python
# _try_gallery_dl:
logger.info("Falling back to gallery-dl | url={}", mask_secrets(url))
logger.warning("gallery-dl timeout | url={}", mask_secrets(url))

# download_media:
logger.info("Starting download | platform={} url={}", platform, mask_secrets(url))
logger.warning("Download timeout | url={}", mask_secrets(url))
logger.info(
    "gallery-dl primary returned nothing, falling back to yt-dlp | platform={} url={}",
    platform, mask_secrets(url),
)
```

- [ ] **Шаг 4: Прогнать тесты**

Команда: `./scripts/test.sh -q`
Ожидается: PASS, регрессий нет.

- [ ] **Шаг 5: Коммит**

```bash
git add bot/services/downloader.py tests/test_downloader_security.py
git commit -m "fix(downloader): restore TLS verification, keep cookie copies in RAM, redact share tokens"
```

---

### Task 15: `url_parser` — хосты Pinterest, ссылки без схемы, хвостовая пунктуация

**Закрывает:** Low «Белый список хостов Pinterest узкий», Low «Обрезать хвостовую пунктуацию в URL», часть Low «Аллоулист URL: учитывать редиректы» (в части полноты аллоулиста)

**Files:**
- Modify: `bot/utils/url_parser.py` (весь файл)
- Test: `tests/test_url_parser.py` (новый файл — на `url_parser` до сих пор нет ни одного теста)

**Interfaces:**
- Consumes: ничего.
- Produces: `parse_url(text: str) -> tuple[str, str] | None` — сигнатура и контракт не меняются (нормализованный URL + имя платформы), меняется только множество принимаемых ссылок.

**⚠️ Меняет поведение для пользователя — нужна ручная проверка:** ссылки, которые раньше отбивались текстом «Это не похоже на ссылку», теперь принимаются. Проверить руками: `pin.it/<id>` без схемы, ссылка на региональный домен Pinterest, ссылка с точкой в конце предложения.

Белый список Pinterest (`url_parser.py:26-30`) содержит пять хостов и отвергает `pinterest.de/.co.uk/.fr/.it/.es/.jp/.ca/.com.mx`, субдомены `br./in./tr./nl./pl.` и `m.pinterest.com` — при том, что gallery-dl все эти хосты понимает. Ссылки без схемы матчатся только для доменов YouTube: регексп `_URL_RE` (`:6`) перечисляет их явно, всё остальное требует `http`. Хвостовая пунктуация втягивается в URL: `…/p/abc/.` или `…),` уходят в yt-dlp как есть.

Решение: (1) нормализовать хост (нижний регистр, снять завершающую точку, снять префиксы `www.`/`m.`/`mobile.`) и сверять с компактным словарём; (2) для Pinterest — проверка регекспом по форме хоста, а не перечислением: `(?:<label>.)*pinterest.<tld>[.<tld2>]`, якорь `$` обязателен, иначе `pinterest.evil.com` пролезет; (3) собрать регексп бессхемных ссылок из того же списка доменов, чтобы «поддерживаемый домен» был описан ровно один раз; (4) обрезать хвостовую пунктуацию.

Закрывающие скобки обрезаем **только непарные**: если в URL столько же или больше открывающих, сколько закрывающих, скобка считается частью адреса. Иначе ломались бы легитимные пути со скобками, а таких на Facebook и в старых пермалинках хватает.

Аллоулист остаётся единственной защитой от SSRF: у контейнера `network_mode: host`, поэтому `127.0.0.1` из него — это хостовой loopback. Негативные проверки в тестах обязательны и не должны удаляться.

- [ ] **Шаг 1: Написать падающий тест**

Создать `tests/test_url_parser.py`:

```python
import pytest

from bot.utils.url_parser import parse_url


# ── Позитив: по одной ссылке на каждую платформу ─────────────────────────


@pytest.mark.parametrize(
    "text, platform",
    [
        ("https://www.instagram.com/p/DBc1abc/", "instagram"),
        ("https://www.instagram.com/reel/DBc1abc/", "instagram"),
        ("https://www.tiktok.com/@user/video/7123456789012345678", "tiktok"),
        ("https://vm.tiktok.com/ZSabc123/", "tiktok"),
        ("https://www.facebook.com/watch/?v=123456789", "facebook"),
        ("https://fb.watch/abcDEF/", "facebook"),
        ("https://www.pinterest.com/pin/123456789012345678/", "pinterest"),
        ("https://www.youtube.com/watch?v=aaaaaaaaaaa", "youtube"),
        ("https://youtu.be/aaaaaaaaaaa", "youtube"),
    ],
)
def test_supported_links_are_recognised(text, platform):
    result = parse_url(text)
    assert result is not None, text
    assert result[1] == platform


# ── Региональные домены Pinterest ────────────────────────────────────────


@pytest.mark.parametrize(
    "host",
    [
        "pinterest.com", "www.pinterest.com", "m.pinterest.com",
        "ru.pinterest.com", "id.pinterest.com", "br.pinterest.com",
        "in.pinterest.com", "tr.pinterest.com", "nl.pinterest.com",
        "pl.pinterest.com", "pinterest.de", "pinterest.fr", "pinterest.it",
        "pinterest.es", "pinterest.jp", "pinterest.ca", "pinterest.co.uk",
        "pinterest.com.mx", "pinterest.com.au",
    ],
)
def test_regional_pinterest_hosts_are_accepted(host):
    result = parse_url(f"https://{host}/pin/123456789012345678/")
    assert result is not None, host
    assert result[1] == "pinterest"


# ── Ссылки без схемы ─────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "text, platform",
    [
        ("pin.it/abcDEF12", "pinterest"),
        ("www.pinterest.com/pin/123/", "pinterest"),
        ("pinterest.co.uk/pin/123/", "pinterest"),
        ("instagram.com/p/DBc1abc/", "instagram"),
        ("vt.tiktok.com/ZSabc/", "tiktok"),
        ("youtu.be/aaaaaaaaaaa", "youtube"),
    ],
)
def test_schemeless_links_are_accepted_and_normalised(text, platform):
    result = parse_url(text)
    assert result is not None, text
    url, detected = result
    assert detected == platform
    assert url.startswith("https://")


# ── Хвостовая пунктуация ─────────────────────────────────────────────────


@pytest.mark.parametrize(
    "text, expected",
    [
        ("Смотри https://www.instagram.com/p/DBc1abc/.", "https://www.instagram.com/p/DBc1abc/"),
        ("https://pin.it/abcDEF12,", "https://pin.it/abcDEF12"),
        ("(https://youtu.be/aaaaaaaaaaa)", "https://youtu.be/aaaaaaaaaaa"),
        ("https://www.tiktok.com/@u/video/7123!", "https://www.tiktok.com/@u/video/7123"),
        ("Вот: https://fb.watch/abcDEF/…", "https://fb.watch/abcDEF/"),
    ],
)
def test_trailing_punctuation_is_trimmed(text, expected):
    result = parse_url(text)
    assert result is not None, text
    assert result[0] == expected


def test_balanced_closing_bracket_is_kept():
    # Скобка парная — значит она часть адреса, а не обрамление из текста.
    url = "https://www.facebook.com/photo/a_(b)"
    result = parse_url(url)
    assert result is not None
    assert result[0] == url


# ── Негатив: аллоулист — единственная защита от SSRF ─────────────────────


@pytest.mark.parametrize(
    "text",
    [
        "http://127.0.0.1/admin",
        "http://127.0.0.1:9999/internal",
        "http://localhost/",
        "http://[::1]/",
        "http://169.254.169.254/latest/meta-data/",
        "http://10.0.0.5/",
        "http://192.168.1.1/",
        "file:///etc/passwd",
        "https://evil.example/?redirect=https://www.instagram.com/p/DBc1/",
        "https://pinterest.com.evil.net/pin/1/",
        "https://notpinterest.com/pin/1/",
        # Бессхемный вариант той же подделки: без лукбихайнда регексп нашёл бы
        # «pinterest.com/pin/1/» внутри чужого домена и подставил схему.
        "notpinterest.com/pin/1/",
        "my-instagram.com/p/DBc1/",
        "https://instagram.com.evil.net/p/1/",
        "просто текст без ссылки",
        "",
    ],
)
def test_hostile_and_unsupported_urls_are_rejected(text):
    assert parse_url(text) is None, text


def test_userinfo_trick_does_not_bypass_the_allowlist():
    # Хост здесь — 127.0.0.1, а не pinterest.com.
    assert parse_url("https://www.pinterest.com@127.0.0.1/pin/1/") is None


def test_schemed_url_wins_over_bare_domain_later_in_the_text():
    result = parse_url("https://youtu.be/aaaaaaaaaaa и ещё pin.it/abc")
    assert result is not None
    assert result[1] == "youtube"
```

- [ ] **Шаг 2: Прогнать тест, убедиться что падает**

Команда: `./scripts/test.sh tests/test_url_parser.py -q`
Ожидается: FAIL на региональных доменах Pinterest, на бессхемных ссылках и на хвостовой пунктуации (позитивные проверки по 5 платформам и негативные — уже зелёные).

- [ ] **Шаг 3: Реализовать**

Заменить `bot/utils/url_parser.py` целиком на:

```python
from __future__ import annotations

import re
from urllib.parse import urlparse

# Домены, с которых мы принимаем ссылку в том числе БЕЗ схемы.
# Регексп ниже собирается из этого же списка, чтобы «поддерживаемый домен»
# был описан ровно один раз.
_BARE_DOMAINS = (
    "youtu.be",
    "youtube.com",
    "youtube-nocookie.com",
    "instagram.com",
    "ddinstagram.com",
    "tiktok.com",
    "facebook.com",
    "fb.watch",
    "pinterest.com",
    "pin.it",
)

_BARE_ALT = "|".join(
    [r"(?:[\w-]+\.)*" + re.escape(d) for d in _BARE_DOMAINS]
    # Мультирегиональный Pinterest: перечислять все ccTLD бессмысленно,
    # описываем формой — так же, как в _PINTEREST_HOST_RE ниже.
    + [r"(?:[\w-]+\.)*pinterest\.[a-z]{2,4}(?:\.[a-z]{2})?"]
)

_URL_RE = re.compile(
    # Лукбихайнд обязателен: без него `notpinterest.com/pin/1` даёт совпадение
    # с позиции 3 и молча превращается в ссылку на настоящий pinterest.com.
    r"(?<![\w.-])(?:"
    # Схемная ссылка — приоритетный вариант; при равной позиции побеждает он.
    r"https?://[^\s<>\"']+"
    # Бессхемная — только для перечисленных доменов.
    rf"|(?:{_BARE_ALT})(?:/[^\s<>\"']*)?"
    r")",
    re.IGNORECASE,
)

# Хост Pinterest задаётся формой, а не перечислением: доменов вида
# pinterest.<tld> и <регион>.pinterest.com слишком много, чтобы держать список.
# Якорь $ обязателен — без него pinterest.evil.com прошёл бы проверку.
_PINTEREST_HOST_RE = re.compile(
    r"^(?:[a-z0-9-]+\.)*pinterest\.[a-z]{2,4}(?:\.[a-z]{2})?$"
)

_DOMAIN_TO_PLATFORM: dict[str, str] = {
    "instagram.com": "instagram",
    "ddinstagram.com": "instagram",
    "tiktok.com": "tiktok",
    "vm.tiktok.com": "tiktok",
    "vt.tiktok.com": "tiktok",
    "lite.tiktok.com": "tiktok",
    "facebook.com": "facebook",
    "web.facebook.com": "facebook",
    "fb.watch": "facebook",
    "pin.it": "pinterest",
    "youtube.com": "youtube",
    "youtu.be": "youtube",
    "music.youtube.com": "youtube",
    "youtube-nocookie.com": "youtube",
}

_HOST_PREFIXES = ("www.", "m.", "mobile.")

# Символы, которые в конце ссылки почти всегда принадлежат тексту, а не URL.
_TRAILING_PUNCT = ".,;:!?…'\"«»"
_BRACKET_PAIRS = {")": "(", "]": "[", "}": "{"}


def _normalize_host(host: str) -> str:
    host = host.lower().rstrip(".")
    changed = True
    while changed:
        changed = False
        for prefix in _HOST_PREFIXES:
            if host.startswith(prefix):
                host = host[len(prefix):]
                changed = True
                break
    return host


def _trim_trailing(url: str) -> str:
    while url:
        if url[-1] in _TRAILING_PUNCT:
            url = url[:-1]
            continue
        if url[-1] in _BRACKET_PAIRS:
            closing = url[-1]
            opening = _BRACKET_PAIRS[closing]
            # Парную скобку оставляем: она часть адреса, а не текста.
            if url.count(opening) >= url.count(closing):
                break
            url = url[:-1]
            continue
        break
    return url


def _detect_platform(host: str | None) -> str | None:
    if not host:
        return None
    normalized = _normalize_host(host)
    platform = _DOMAIN_TO_PLATFORM.get(normalized)
    if platform:
        return platform
    if _PINTEREST_HOST_RE.match(normalized):
        return "pinterest"
    return None


def parse_url(text: str) -> tuple[str, str] | None:
    match = _URL_RE.search(text)
    if not match:
        return None

    url = _trim_trailing(match.group(0))
    if not url:
        return None

    normalized = url if url.lower().startswith(("http://", "https://")) else "https://" + url
    platform = _detect_platform(urlparse(normalized).hostname)
    if not platform:
        return None

    return normalized, platform
```

- [ ] **Шаг 4: Прогнать тесты**

Команда: `./scripts/test.sh -q`
Ожидается: PASS, регрессий нет.

- [ ] **Шаг 5: Коммит**

```bash
git add bot/utils/url_parser.py tests/test_url_parser.py
git commit -m "feat(url-parser): accept regional Pinterest hosts and schemeless links, trim trailing punctuation"
```

---

### Task 16: Харденинг флагов `ffprobe`

**Закрывает:** пункт «ffmpeg в trixie: ~25 CVE в статусе `vulnerable (no-dsa/postponed)`» из раздела «Компоненты (обновления)»

**Files:**
- Modify: `bot/services/media_probe.py:134-146` (`probe_media`)
- Test: `tests/test_media_probe.py` (дописать, существующие 40 тестов не трогать)

**Interfaces:**
- Consumes: ничего.
- Produces: `_ffprobe_cmd(path: Path) -> list[str]` — чистая функция построения argv, чтобы её можно было проверить тестом.

`bot/services/media_probe.py:141-146` зовёт `ffprobe -v error -show_streams -show_format -print_format json <path>` без ограничения протоколов. В trixie ~25 CVE ffmpeg висят в статусе «vulnerable (no-dsa/postponed)» — трекер сознательно отказался их патчить; среди них OOB-чтение в DASH-демуксере, апстрим-фикс есть, в дистрибутив не портирован. Путь «недоверенный скачанный файл → ffprobe» проходится на каждой отправке видео, и версией это не лечится. `-protocol_whitelist file` запрещает демуксеру открывать что-либо, кроме обычного файла: манифест внутри контейнера, ссылающийся на `http(s)`, не будет загружен вовсе.

**Почему НЕ трогаем `-probesize`/`-analyzeduration`:** их занижение ломает обнаружение аудиодорожки, начинающейся не в начале файла, а гейт трактует «нет звука» как отказ — пользователь увидит ошибку на исправном видео. Ложный отказ здесь дороже, чем экономия чтения. Работу ffprobe и так ограничивает существующий таймаут 30 с с `kill()` и `wait()` (`media_probe.py:156-168`). Это осознанное решение, а не упущение.

- [ ] **Шаг 1: Написать падающий тест**

Дописать в конец `tests/test_media_probe.py`:

```python
# ---------------------------------------------------------------------------
# Харденинг флагов: ffprobe не должен уметь ходить в сеть по ссылке,
# спрятанной внутри недоверенного контейнера.
# ---------------------------------------------------------------------------

from bot.services.media_probe import _ffprobe_cmd  # noqa: E402


def test_ffprobe_command_restricts_protocols_to_plain_files(tmp_path):
    target = tmp_path / "clip.mp4"
    cmd = _ffprobe_cmd(target)
    assert "-protocol_whitelist" in cmd
    assert cmd[cmd.index("-protocol_whitelist") + 1] == "file"


def test_protocol_whitelist_precedes_the_input_path(tmp_path):
    target = tmp_path / "clip.mp4"
    cmd = _ffprobe_cmd(target)
    # Опции AVFormat применяются к следующему входу, поэтому флаг обязан
    # стоять ДО пути; путь при этом остаётся последним аргументом.
    assert cmd[-1] == str(target)
    assert cmd.index("-protocol_whitelist") < len(cmd) - 1


def test_probe_limits_are_left_at_defaults(tmp_path):
    # Осознанное решение: занижение probesize/analyzeduration даёт ложное
    # «нет звука» на файлах, где аудиодорожка начинается не сразу.
    cmd = _ffprobe_cmd(tmp_path / "clip.mp4")
    assert "-probesize" not in cmd
    assert "-analyzeduration" not in cmd


async def test_hardened_ffprobe_still_reads_a_real_file(media_files):
    # Регрессия: ограничение протоколов не должно мешать обычному локальному
    # файлу — он читается протоколом `file`.
    info = await probe_media(media_files["ok"])
    assert info is not None
    assert info.has_video is True
    assert info.has_audio is True
    assert info.duration > 0
    assert info.width > 0 and info.height > 0
```

- [ ] **Шаг 2: Прогнать тест, убедиться что падает**

Команда: `./scripts/test.sh tests/test_media_probe.py -q`
Ожидается: FAIL — `ImportError: cannot import name '_ffprobe_cmd' from 'bot.services.media_probe'`.

- [ ] **Шаг 3: Реализовать**

В `bot/services/media_probe.py` добавить перед `probe_media`:

```python
def _ffprobe_cmd(path: Path) -> list[str]:
    """argv для ffprobe.

    `-protocol_whitelist file` обязателен: контейнер недоверенный, а демуксеры
    ffmpeg умеют открывать вложенные ссылки (например, DASH-манифест внутри
    файла). Из Debian trixie приехали неисправленные OOB-чтения в
    DASH-демуксере — не даём ему ходить никуда, кроме обычного файла.

    `-probesize`/`-analyzeduration` сознательно оставлены дефолтными: их
    занижение даёт ложное «нет звука» на файлах, где аудиодорожка начинается
    не с нулевой отметки, а это отказ, видимый пользователю. Объём работы
    ограничивает таймаут FFPROBE_TIMEOUT с последующим kill().
    """
    return [
        "ffprobe", "-v", "error",
        "-protocol_whitelist", "file",
        "-show_streams", "-show_format",
        "-print_format", "json",
        str(path),
    ]
```

Заменить построение команды внутри `probe_media` (`:141-146`) на:

```python
    cmd = _ffprobe_cmd(path)
```

- [ ] **Шаг 4: Прогнать тесты**

Команда: `./scripts/test.sh -q`
Ожидается: PASS, все 40 существующих тестов `test_media_probe.py` зелёные, плюс 4 новых.

- [ ] **Шаг 5: Коммит**

```bash
git add bot/services/media_probe.py tests/test_media_probe.py
git commit -m "fix(media): restrict ffprobe to the file protocol"
```

---

**Итог пакета B.** После Task 16 на ветке закрыты: C-2 (частично — расширения), H-6, H-14, H-15, M-8, M-14, M-15, M-16, M-17, M-20, M-21, пункт CVE ffmpeg и четыре находки Low (`--age-limit`, хосты Pinterest, хвостовая пунктуация, частичные файлы gallery-dl). Пакет D следующей волной опирается на `ANIMATION_EXTS`, `_media_type_for` и трёхзначный `DownloadResult.media_type`.
## Пакет C — Слой БД (ветка `feat/local-bot-api`)

Владеет файлами `bot/db/queries.py`, `bot/db/models.py`, `bot/db/engine.py`, создаёт `bot/utils/text.py`, `scripts/migrate_20260913.py` и **единолично владеет `tests/conftest.py`**. Идёт во второй волне **параллельно с пакетами A, B и F**; пакеты D и E из третьей волны потребляют его интерфейсы и фикстуры, но ничего из перечисленного не редактируют.

**Жёсткое ограничение пакета:** ни `decrement_free_downloads`, ни `update_subscription` в этом пакете **не удаляются**, хотя обе становятся мёртвыми. Их удаляет Task 37 — после того, как пакеты D и E перестанут их импортировать. `bot/handlers/user.py:17` и `bot/handlers/admin.py:10` импортируют эти имена на уровне модуля, а `bot/__main__.py` импортирует роутеры: удаление в волне 2 уронило бы импорт `bot.handlers` и, следом, весь набор тестов.

---

### Task 17: Общий модуль экранирования и фикстуры БД для тестов

**Закрывает:** ничего напрямую — это предусловие для Task 18–21, 25–33.

**Files:**
- Create: `bot/utils/text.py`
- Modify: `tests/conftest.py`
- Create: `tests/test_text.py`

**Interfaces:**
- Consumes: ничего.
- Produces:
  - `bot.utils.text.esc(value: object) -> str` — экранирование для вставки в текст с `parse_mode=HTML`. **Потребляют пакеты D (Task 27) и E (Task 30).**
  - Фикстура `db_session` — `AsyncSession` поверх временной SQLite-базы со схемой из моделей. **Потребляют пакеты D и E.**
  - Фикстура `make_user` — фабрика объектов `User` с разумными значениями по умолчанию.

В `bot/handlers/` и `bot/keyboards/` ноль вызовов `html.escape` при глобальном `parse_mode=HTML` (`bot/__main__.py:26`). Чинить это в двух пакетах одновременно двумя разными помощниками — гарантированный разъезд, поэтому помощник заводится здесь, один на всех, и дальше только потребляется.

`quote=False` выбран сознательно: экранировать кавычки внутри текста сообщения не требуется (мы не подставляем значения в атрибуты тегов), а `&quot;` в русском тексте читается заметно хуже самой кавычки. `None` превращается в пустую строку, а не в «None»: подставляем в места, где отсутствующее значение должно выглядеть пустым.

Фикстура БД заводится здесь по той же причине: пакетам C, D и E нужна одна и та же, а `tests/conftest.py` может принадлежать только одному пакету.

- [ ] **Шаг 1: Написать падающий тест**

Создать `tests/test_text.py`:

```python
from bot.utils.text import esc


def test_escapes_html_metacharacters():
    assert esc("Ann <3 & Bob") == "Ann &lt;3 &amp; Bob"


def test_none_becomes_empty_string_not_the_word_none():
    assert esc(None) == ""


def test_numbers_pass_through_as_text():
    assert esc(42) == "42"


def test_quotes_are_left_readable():
    # quote=False: значения подставляются в текст сообщения, а не в атрибуты
    # тегов, и &quot; в русском тексте читается хуже самой кавычки.
    assert esc('скажи "привет"') == 'скажи "привет"'


def test_already_escaped_text_is_escaped_again():
    # esc() не идемпотентна и не должна быть: двойное экранирование —
    # это ошибка вызывающего, и её надо видеть, а не прятать.
    assert esc("&lt;b&gt;") == "&amp;lt;b&amp;gt;"
```

- [ ] **Шаг 2: Прогнать тест, убедиться что падает**

Команда: `./scripts/test.sh tests/test_text.py -q`
Ожидается: FAIL — `ModuleNotFoundError: No module named 'bot.utils.text'`

- [ ] **Шаг 3: Создать `bot/utils/text.py`**

```python
from __future__ import annotations

import html


def esc(value: object) -> str:
    """Экранирует значение для вставки в текст с `parse_mode=HTML`.

    `quote=False`: значения подставляются в текст сообщения, а не в атрибуты
    тегов, поэтому экранировать кавычки не нужно, а `&quot;` в русском тексте
    читается хуже самой кавычки.

    `None` даёт пустую строку, а не слово «None»: подставляем в места, где
    отсутствующее значение должно выглядеть пустым.
    """
    if value is None:
        return ""
    return html.escape(str(value), quote=False)
```

- [ ] **Шаг 4: Добавить фикстуры БД в `tests/conftest.py`**

Дописать в конец `tests/conftest.py` (не трогая существующее содержимое):

```python
@pytest.fixture
async def db_session(tmp_path):
    """Чистая БД на каждый тест: временный SQLite со схемой из моделей.

    Собственный движок, а не `bot.db.engine.async_session`: тот создаётся на
    импорте из `settings.DATABASE_URL` и указывает на рабочую базу.
    """
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from bot.db.models import Base

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'test.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session:
        yield session
    await engine.dispose()


@pytest.fixture
def make_user():
    """Фабрика пользователей. Telegram-id заведомо выдуманные."""
    from bot.db.models import User

    def _make(user_id: int = 1000000001, **overrides):
        fields = {
            "id": user_id,
            "username": "tester",
            "full_name": "Test User",
            "free_downloads_left": 3,
            "subscription_until": None,
            "is_banned": False,
            "total_downloads": 0,
        }
        fields.update(overrides)
        return User(**fields)

    return _make
```

- [ ] **Шаг 5: Проверить, что фикстуры работают**

Дописать в `tests/test_text.py`:

```python
async def test_db_session_fixture_gives_a_working_schema(db_session, make_user):
    from sqlalchemy import select

    from bot.db.models import User

    async with db_session.begin():
        db_session.add(make_user(username="Ann"))

    found = await db_session.scalar(select(User).where(User.username == "Ann"))
    assert found is not None
    assert found.free_downloads_left == 3
```

- [ ] **Шаг 6: Прогнать тесты**

Команда: `./scripts/test.sh -q`
Ожидается: PASS, регрессий нет

- [ ] **Шаг 7: Коммит**

```bash
git add bot/utils/text.py tests/conftest.py tests/test_text.py
git commit -m "feat(utils): add a shared HTML escaper and database fixtures for tests"
```

---

### Task 18: Атомарное резервирование квоты

**Закрывает:** C-1 (серверная половина)

**Files:**
- Modify: `bot/db/queries.py:66-72`
- Create: `tests/test_quota_queries.py`

**Interfaces:**
- Consumes: фикстуры `db_session`, `make_user` из Task 17.
- Produces:
  - `reserve_free_download(session: AsyncSession, user_id: int) -> bool` — атомарно занимает одну бесплатную единицу; `True`, если заняли.
  - `refund_free_download(session: AsyncSession, user_id: int) -> None` — возвращает ровно одну ранее зарезервированную единицу.
  - **Потребляет пакет D (Task 25).**

Сейчас остаток читается в одной транзакции (`user.py:232-241`), решение принимается в другой (`:247-253`), а списание происходит в третьей и уже после аплоада (`:365-367`). Замерено: с одним оставшимся скачиванием пользователь получает от трёх до семи. Отдельно: `rowcount` не проверяется нигде в репозитории, поэтому «не списалось» неотличимо от «списалось» — `decrement_free_downloads` возвращает `None` при любом исходе.

Чиним тем, что решение и списание становятся одним оператором: условие `free_downloads_left > 0` проверяет СУБД в том же `UPDATE`, который декрементирует, и успех определяется по `rowcount == 1`. Между проверкой и списанием больше нет окна.

Возврат сделан отдельной функцией, а не «прибавить, если было списано»: вызывающий обязан звать её не более одного раза на одно резервирование, и это прямо записано в докстроке. Верхнего ограничителя у возврата нет сознательно — попытка ограничить его значением `FREE_DOWNLOADS` сломала бы случай, когда админ выдал пользователю больше единиц вручную.

- [ ] **Шаг 1: Написать падающий тест**

Создать `tests/test_quota_queries.py`:

```python
import asyncio

from sqlalchemy import select

from bot.db.models import User
from bot.db.queries import refund_free_download, reserve_free_download


async def _left(session, user_id: int) -> int:
    return await session.scalar(select(User.free_downloads_left).where(User.id == user_id))


async def test_reservation_succeeds_and_decrements(db_session, make_user):
    async with db_session.begin():
        db_session.add(make_user(free_downloads_left=3))

    async with db_session.begin():
        assert await reserve_free_download(db_session, 1000000001) is True

    assert await _left(db_session, 1000000001) == 2


async def test_reservation_fails_on_empty_quota_and_changes_nothing(db_session, make_user):
    async with db_session.begin():
        db_session.add(make_user(free_downloads_left=0))

    async with db_session.begin():
        assert await reserve_free_download(db_session, 1000000001) is False

    assert await _left(db_session, 1000000001) == 0


async def test_last_unit_can_be_taken_only_once(db_session, make_user):
    """C-1: с одним оставшимся скачиванием пользователь получал 3–7."""
    async with db_session.begin():
        db_session.add(make_user(free_downloads_left=1))

    async with db_session.begin():
        first = await reserve_free_download(db_session, 1000000001)
        second = await reserve_free_download(db_session, 1000000001)

    assert (first, second) == (True, False)
    assert await _left(db_session, 1000000001) == 0


async def test_reservation_on_missing_user_returns_false(db_session):
    async with db_session.begin():
        assert await reserve_free_download(db_session, 1000000999) is False


async def test_refund_returns_exactly_one_unit(db_session, make_user):
    async with db_session.begin():
        db_session.add(make_user(free_downloads_left=1))

    async with db_session.begin():
        await reserve_free_download(db_session, 1000000001)
        await refund_free_download(db_session, 1000000001)

    assert await _left(db_session, 1000000001) == 1


async def test_refund_on_missing_user_does_not_raise(db_session):
    async with db_session.begin():
        await refund_free_download(db_session, 1000000999)
```

- [ ] **Шаг 2: Прогнать тест, убедиться что падает**

Команда: `./scripts/test.sh tests/test_quota_queries.py -q`
Ожидается: FAIL — `ImportError: cannot import name 'reserve_free_download' from 'bot.db.queries'`

- [ ] **Шаг 3: Добавить функции в `bot/db/queries.py`**

Сразу после `decrement_free_downloads` (после строки 72) добавить:

```python
async def reserve_free_download(session: AsyncSession, user_id: int) -> bool:
    """Атомарно занимает одну бесплатную единицу. `True` — заняли.

    Условие `free_downloads_left > 0` проверяет СУБД в том же операторе,
    который декрементирует, поэтому окна между «проверили остаток» и
    «списали» больше нет: параллельные загрузки одного пользователя не
    могут занять одну и ту же единицу дважды.

    Успех определяется по `rowcount == 1`. Раньше `rowcount` не проверялся
    нигде в репозитории, и «не списалось» было неотличимо от «списалось».
    """
    result = await session.execute(
        update(User)
        .where(User.id == user_id, User.free_downloads_left > 0)
        .values(free_downloads_left=User.free_downloads_left - 1)
    )
    await session.flush()
    return result.rowcount == 1


async def refund_free_download(session: AsyncSession, user_id: int) -> None:
    """Возвращает ровно одну ранее зарезервированную единицу.

    Вызывающий обязан звать это не более одного раза на одно успешное
    резервирование: верхнего ограничителя здесь нет сознательно — админ
    может выдать пользователю больше единиц, чем FREE_DOWNLOADS, и упирать
    возврат в эту константу означало бы молча отнимать выданное.
    """
    await session.execute(
        update(User)
        .where(User.id == user_id)
        .values(free_downloads_left=User.free_downloads_left + 1)
    )
    await session.flush()
```

- [ ] **Шаг 4: Пометить старую функцию как мёртвую**

Над `decrement_free_downloads` (строка 66) добавить комментарий — удалять функцию сейчас нельзя, её импортирует `bot/handlers/user.py:17`:

```python
# УСТАРЕЛО. Заменена на reserve_free_download: списание после доставки давало
# пользователю от трёх до семи скачиваний при одном оставшемся (C-1).
# Удаляется в Task 37, после того как пакет D перестанет её импортировать.
```

- [ ] **Шаг 5: Прогнать тесты**

Команда: `./scripts/test.sh -q`
Ожидается: PASS, регрессий нет

- [ ] **Шаг 6: Коммит**

```bash
git add bot/db/queries.py tests/test_quota_queries.py
git commit -m "feat(db): reserve free downloads atomically and allow refunds"
```

---

### Task 19: Журнал и идемпотентность изменений подписки

**Закрывает:** H-12 (серверная половина)

**Files:**
- Modify: `bot/db/models.py`
- Modify: `bot/db/queries.py:44-54`
- Create: `tests/test_subscription_queries.py`

**Interfaces:**
- Consumes: фикстуры `db_session`, `make_user`.
- Produces:
  - `bot.db.models.SubscriptionGrant` — таблица `subscription_grant`.
  - `bot.db.queries.GrantOutcome` — `@dataclass(frozen=True)` с полями `applied: bool`, `user_found: bool`, `duplicate: bool`, `subscription_until: datetime | None`.
  - `bot.db.queries.apply_subscription_change(session, *, user_id: int, admin_id: int, days: int | None, idempotency_key: str) -> GrantOutcome` — единственная точка изменения подписки.
  - **Потребляет пакет E (Task 31, Task 32).**

`update_subscription` (`queries.py:44-54`) считает `base = max(now, existing) + days`. Повторный вызов складывается: двойной клик «+30 дней» из-за лага — это 60 дней за одну оплату. Карточка с рабочей клавиатурой остаётся в истории чата админа навсегда, и старый `callback_data` с тем же `user_id` валиден через месяц. Отдельной таблицы платежей нет, `subscription_until` перезатирается следующим грантом, отмены в UI нет вовсе, а `admin.py` не импортирует логгер, поэтому ни одна админская операция не оставляет следа.

Решение: одна функция на все изменения (продление, сокращение, полное снятие) плюс журнал с уникальным ключом идемпотентности. Ключ генерирует UI при отрисовке карточки, поэтому повторный клик по той же кнопке — это повторный ключ, и изменение не применяется. `days=None` означает снятие подписки; отрицательное значение — сокращение. Разводить это на три функции не стали: журнальная запись, проверка дубля и приведение таймзон у них общие, а различие сводится к одной ветке вычисления новой даты.

Новая таблица создаётся `create_all` даже на существующей БД, поэтому отдельного DDL ей не нужно — но бэкап перед выкаткой всё равно обязателен, см. Task 21.

- [ ] **Шаг 1: Написать падающий тест**

Создать `tests/test_subscription_queries.py`:

```python
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from bot.db.models import SubscriptionGrant, User
from bot.db.queries import apply_subscription_change

ADMIN = 1000000777


async def _grant_rows(session) -> int:
    return await session.scalar(select(func.count(SubscriptionGrant.id)))


async def test_grant_extends_from_now_for_a_fresh_user(db_session, make_user):
    async with db_session.begin():
        db_session.add(make_user())

    async with db_session.begin():
        outcome = await apply_subscription_change(
            db_session, user_id=1000000001, admin_id=ADMIN, days=30, idempotency_key="k1"
        )

    assert outcome.applied is True
    assert outcome.duplicate is False
    assert outcome.user_found is True
    delta = outcome.subscription_until - datetime.now(timezone.utc)
    assert timedelta(days=29) < delta < timedelta(days=31)


async def test_repeated_key_changes_nothing(db_session, make_user):
    """H-12: двойной клик «+30 дней» из-за лага давал 60 дней за одну оплату."""
    async with db_session.begin():
        db_session.add(make_user())

    async with db_session.begin():
        first = await apply_subscription_change(
            db_session, user_id=1000000001, admin_id=ADMIN, days=30, idempotency_key="k1"
        )
    async with db_session.begin():
        second = await apply_subscription_change(
            db_session, user_id=1000000001, admin_id=ADMIN, days=30, idempotency_key="k1"
        )

    assert second.duplicate is True
    assert second.applied is False
    assert second.subscription_until == first.subscription_until
    assert await _grant_rows(db_session) == 1


async def test_different_keys_stack_as_intended(db_session, make_user):
    async with db_session.begin():
        db_session.add(make_user())

    async with db_session.begin():
        await apply_subscription_change(
            db_session, user_id=1000000001, admin_id=ADMIN, days=7, idempotency_key="k1"
        )
    async with db_session.begin():
        second = await apply_subscription_change(
            db_session, user_id=1000000001, admin_id=ADMIN, days=7, idempotency_key="k2"
        )

    delta = second.subscription_until - datetime.now(timezone.utc)
    assert timedelta(days=13) < delta < timedelta(days=15)
    assert await _grant_rows(db_session) == 2


async def test_negative_days_shorten_the_subscription(db_session, make_user):
    async with db_session.begin():
        db_session.add(make_user())
    async with db_session.begin():
        await apply_subscription_change(
            db_session, user_id=1000000001, admin_id=ADMIN, days=30, idempotency_key="k1"
        )

    async with db_session.begin():
        shortened = await apply_subscription_change(
            db_session, user_id=1000000001, admin_id=ADMIN, days=-7, idempotency_key="k2"
        )

    delta = shortened.subscription_until - datetime.now(timezone.utc)
    assert timedelta(days=22) < delta < timedelta(days=24)


async def test_none_days_clears_the_subscription(db_session, make_user):
    async with db_session.begin():
        db_session.add(make_user())
    async with db_session.begin():
        await apply_subscription_change(
            db_session, user_id=1000000001, admin_id=ADMIN, days=30, idempotency_key="k1"
        )

    async with db_session.begin():
        cleared = await apply_subscription_change(
            db_session, user_id=1000000001, admin_id=ADMIN, days=None, idempotency_key="k2"
        )

    assert cleared.applied is True
    assert cleared.subscription_until is None
    stored = await db_session.scalar(select(User.subscription_until).where(User.id == 1000000001))
    assert stored is None


async def test_missing_user_is_reported_not_silently_ignored(db_session):
    """M-24: grant на отсутствующего юзера рапортовал успех."""
    async with db_session.begin():
        outcome = await apply_subscription_change(
            db_session, user_id=1000000999, admin_id=ADMIN, days=30, idempotency_key="k1"
        )

    assert outcome.user_found is False
    assert outcome.applied is False
    assert await _grant_rows(db_session) == 0


async def test_journal_records_who_did_what(db_session, make_user):
    async with db_session.begin():
        db_session.add(make_user())
    async with db_session.begin():
        await apply_subscription_change(
            db_session, user_id=1000000001, admin_id=ADMIN, days=30, idempotency_key="k1"
        )

    row = await db_session.scalar(select(SubscriptionGrant))
    assert row.user_id == 1000000001
    assert row.admin_id == ADMIN
    assert row.days == 30
    assert row.idempotency_key == "k1"
    assert row.subscription_until_after is not None
```

- [ ] **Шаг 2: Прогнать тест, убедиться что падает**

Команда: `./scripts/test.sh tests/test_subscription_queries.py -q`
Ожидается: FAIL — `ImportError: cannot import name 'SubscriptionGrant' from 'bot.db.models'`

- [ ] **Шаг 3: Добавить модель в `bot/db/models.py`**

Дописать в конец файла:

```python
class SubscriptionGrant(Base):
    """Журнал изменений подписки: одна строка на каждое применённое изменение.

    `idempotency_key` уникален. Ключ генерирует UI при отрисовке карточки,
    поэтому повторный клик по той же кнопке — в том числе по карточке,
    оставшейся в истории чата админа с прошлого месяца, — даёт тот же ключ,
    и изменение не применяется второй раз.

    `days = NULL` означает снятие подписки, отрицательное — сокращение.
    """

    __tablename__ = "subscription_grant"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"), index=True)
    admin_id: Mapped[int] = mapped_column(BigInteger)
    days: Mapped[Optional[int]] = mapped_column(nullable=True)
    idempotency_key: Mapped[str] = mapped_column(String(64), unique=True)
    subscription_until_after: Mapped[Optional[datetime]] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=func.now(), index=True)
```

- [ ] **Шаг 4: Добавить функцию в `bot/db/queries.py`**

В начало файла добавить импорт `dataclass` и `SubscriptionGrant`:

```python
from dataclasses import dataclass
```

```python
from bot.db.models import DownloadLog, SubscriptionGrant, User
```

Затем сразу после `get_user_by_username` (после строки 41) добавить:

```python
def _as_utc(value: datetime | None) -> datetime | None:
    """SQLite отдаёт naive datetime. Всё, что туда записано, записано в UTC."""
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


@dataclass(frozen=True)
class GrantOutcome:
    """Исход изменения подписки.

    `applied` — изменение реально применено;
    `user_found` — пользователь существует;
    `duplicate` — такой `idempotency_key` уже применялся;
    `subscription_until` — состояние ПОСЛЕ операции, tz-aware UTC.
    """

    applied: bool
    user_found: bool
    duplicate: bool
    subscription_until: datetime | None


async def apply_subscription_change(
    session: AsyncSession,
    *,
    user_id: int,
    admin_id: int,
    days: int | None,
    idempotency_key: str,
) -> GrantOutcome:
    """Единственная точка изменения подписки.

    `days > 0` — продлить от `max(now, текущая дата)`, `days < 0` — сократить,
    `days is None` — снять подписку полностью. Каждое применённое изменение
    пишет строку в `subscription_grant`; повтор с тем же ключом ничего не
    меняет и возвращает `duplicate=True`.
    """
    seen = await session.scalar(
        select(SubscriptionGrant).where(SubscriptionGrant.idempotency_key == idempotency_key)
    )
    if seen is not None:
        user_exists = await session.get(User, user_id) is not None
        return GrantOutcome(
            applied=False,
            user_found=user_exists,
            duplicate=True,
            subscription_until=_as_utc(seen.subscription_until_after),
        )

    user = await session.get(User, user_id)
    if user is None:
        return GrantOutcome(
            applied=False, user_found=False, duplicate=False, subscription_until=None
        )

    if days is None:
        new_until = None
    else:
        now = datetime.now(timezone.utc)
        existing = _as_utc(user.subscription_until)
        base = max(now, existing) if existing else now
        new_until = base + timedelta(days=days)

    user.subscription_until = new_until
    session.add(
        SubscriptionGrant(
            user_id=user_id,
            admin_id=admin_id,
            days=days,
            idempotency_key=idempotency_key,
            subscription_until_after=new_until,
        )
    )
    await session.flush()
    return GrantOutcome(
        applied=True, user_found=True, duplicate=False, subscription_until=new_until
    )
```

- [ ] **Шаг 5: Пометить старую функцию как мёртвую**

Над `update_subscription` (строка 44) добавить комментарий — удалять её сейчас нельзя, её импортирует `bot/handlers/admin.py:10`:

```python
# УСТАРЕЛО. Заменена на apply_subscription_change: не идемпотентна, не
# журналируется, отмены не поддерживает (H-12). Удаляется в Task 37, после
# того как пакет E перестанет её импортировать.
```

- [ ] **Шаг 6: Прогнать тесты**

Команда: `./scripts/test.sh -q`
Ожидается: PASS, регрессий нет

- [ ] **Шаг 7: Коммит**

```bash
git add bot/db/models.py bot/db/queries.py tests/test_subscription_queries.py
git commit -m "feat(db): journal subscription changes and make them idempotent"
```

---

### Task 20: Регистронезависимый поиск по нику

**Закрывает:** M-22, Low про регистрозависимый поиск

**Files:**
- Modify: `bot/db/queries.py:37-41`
- Create: `tests/test_username_lookup.py`

**Interfaces:**
- Consumes: фикстуры `db_session`, `make_user`.
- Produces: `get_user_by_username(session, username) -> User | None` с прежней сигнатурой и новым поведением: регистр не важен, дубликаты не роняют вызов. **Потребляет пакет E.**

Две находки на одной строке. `queries.py:39` сравнивает `==` по TEXT без `NOCASE`, хотя Telegram-ники регистронезависимы: админ вбивает `@Ivan` при сохранённом `ivan` и получает «Пользователь не найден» — ложный отрицательный на платящем клиенте. И `scalar_one_or_none()` (`:41`) бросает `MultipleResultsFound` на дубликате ника: пользователь A с `@foo` переименовался и боту больше не пишет, поэтому в БД остался `foo`; пользователь B занял `@foo` и нажал `/start` — две строки, исключение, ответа нет, режим поиска уже сброшен на `admin.py:85`, платящего клиента не найти ни по нику, ни по id.

Уникальный индекс (Task 21) закроет появление новых дубликатов, но старые могут остаться, а восстановление БД из бэкапа может вернуть их снова. Поэтому запрос обязан пережить дубликат: берём первую строку, упорядочив по `updated_at` по убыванию — то есть того, кто писал боту позже всех. Это и есть текущий владелец ника.

- [ ] **Шаг 1: Написать падающий тест**

Создать `tests/test_username_lookup.py`:

```python
from datetime import datetime, timedelta, timezone

from bot.db.queries import get_user_by_username


async def test_lookup_ignores_case(db_session, make_user):
    async with db_session.begin():
        db_session.add(make_user(username="ivan"))

    found = await get_user_by_username(db_session, "@Ivan")

    assert found is not None
    assert found.id == 1000000001


async def test_lookup_strips_at_sign_and_spaces(db_session, make_user):
    async with db_session.begin():
        db_session.add(make_user(username="ivan"))

    assert await get_user_by_username(db_session, "  @IVAN  ") is not None


async def test_duplicate_nicks_do_not_raise_and_pick_the_recent_one(db_session, make_user):
    """M-22: раньше здесь был MultipleResultsFound и полная тишина в ответ."""
    old = datetime(2025, 1, 1, tzinfo=timezone.utc).replace(tzinfo=None)
    recent = (datetime.now(timezone.utc) - timedelta(minutes=1)).replace(tzinfo=None)
    async with db_session.begin():
        db_session.add(make_user(1000000001, username="foo", updated_at=old))
        db_session.add(make_user(1000000002, username="Foo", updated_at=recent))

    found = await get_user_by_username(db_session, "@foo")

    assert found is not None
    assert found.id == 1000000002


async def test_unknown_nick_returns_none(db_session, make_user):
    async with db_session.begin():
        db_session.add(make_user(username="ivan"))

    assert await get_user_by_username(db_session, "@petr") is None


async def test_empty_input_returns_none(db_session, make_user):
    async with db_session.begin():
        db_session.add(make_user(username="ivan"))

    assert await get_user_by_username(db_session, "@") is None
    assert await get_user_by_username(db_session, "   ") is None
```

- [ ] **Шаг 2: Прогнать тест, убедиться что падает**

Команда: `./scripts/test.sh tests/test_username_lookup.py -q`
Ожидается: FAIL — поиск по `@Ivan` возвращает `None`, а тест на дубликаты падает с `MultipleResultsFound`

- [ ] **Шаг 3: Переписать запрос в `bot/db/queries.py`**

Заменить строки 37–41

```python
async def get_user_by_username(session: AsyncSession, username: str) -> User | None:
    result = await session.execute(
        select(User).where(User.username == username.lstrip("@"))
    )
    return result.scalar_one_or_none()
```

на

```python
async def get_user_by_username(session: AsyncSession, username: str) -> User | None:
    """Регистронезависимый поиск по нику.

    Telegram-ники регистронезависимы, а колонка — TEXT без NOCASE, поэтому
    `@Ivan` при сохранённом `ivan` давал «Пользователь не найден».

    Первая строка вместо `scalar_one_or_none()`: уникальный индекс закрывает
    появление новых дубликатов, но старые могут остаться, а восстановление
    БД из бэкапа способно вернуть их снова, и падать `MultipleResultsFound`
    на платящем клиенте недопустимо. Из дубликатов берём того, кто писал
    боту позже всех, — это текущий владелец ника.
    """
    needle = username.lstrip("@").strip().lower()
    if not needle:
        return None
    result = await session.execute(
        select(User)
        .where(func.lower(User.username) == needle)
        .order_by(User.updated_at.desc())
        .limit(1)
    )
    return result.scalars().first()
```

- [ ] **Шаг 4: Прогнать тесты**

Команда: `./scripts/test.sh -q`
Ожидается: PASS, регрессий нет

- [ ] **Шаг 5: Коммит**

```bash
git add bot/db/queries.py tests/test_username_lookup.py
git commit -m "fix(db): look up usernames case-insensitively and survive duplicates"
```

---

### Task 21: Индексы, внешние ключи, ретеншен и миграция боевой БД

**Закрывает:** M-27, Low про `PRAGMA foreign_keys` и отсутствие миграций, M-21 (половина про вечное хранение URL)

**Files:**
- Modify: `bot/db/models.py` (константы DDL)
- Modify: `bot/db/engine.py` (целиком)
- Create: `scripts/migrate_20260913.py`
- Create: `tests/test_schema_migration.py`

**Interfaces:**
- Consumes: `bot.db.models.Base`.
- Produces:
  - `bot.db.models.EXTRA_INDEX_DDL: tuple[str, ...]`, `bot.db.models.USERNAME_UNIQUE_DDL: str` — идемпотентный DDL, который `create_all` не выполнит на существующей таблице.
  - `bot.db.engine.DOWNLOAD_LOG_RETENTION_DAYS: int`.
  - `scripts/migrate_20260913.py` с функцией `dedupe_usernames(conn) -> list[tuple[int, str]]`.

Ни одного индекса в схеме нет. `get_stats` делает три `COUNT(*)` по неиндексированному `created_at` — три полных сканирования на каждое нажатие «📈 Статистика», а клавиатура специально предлагает «🔄 Обновить». `users.username` без индекса даёт полное сканирование в поиске, `download_log.user_id` имеет FK, но SQLite FK-колонки автоматически не индексирует. Строки `download_log` не удаляет никто: `periodic_cleanup` чистит только файлы, а в журнале лежат полные URL с приватными пер-шаринговыми токенами — в живом логе видны `?stkn=` и `?igsh=`. И `engine.py:15-20` не включает `PRAGMA foreign_keys`, поэтому строки журнала переживают удаление пользователя сиротами и попадают в статистику.

Ключевой момент: `Base.metadata.create_all` заводит отсутствующие таблицы (значит, `subscription_grant` из Task 19 приедет сам), но для уже существующей таблицы он пропускает её целиком вместе с индексами и `ALTER` не делает вовсе. Поэтому индексы на `users` и `download_log` заводятся явным идемпотентным DDL в `init_db`.

Уникальный индекс по нику вынесен отдельно и в собственную транзакцию: он не создастся, если в базе уже лежат дубликаты, а падение на старте из-за исторических данных недопустимо. Дубликаты снимает разовый скрипт миграции; до его прогона бот работает без уникальности, но с честной ошибкой в логе.

Ретеншен считается от 180 дней — заведомо больше самого длинного окна статистики (30 дней), чтобы чистка не искажала цифры.

- [ ] **Шаг 1: Сделать свежий бэкап боевой БД**

Обязательный шаг перед любым изменением схемы. Единственный существующий бэкап — от 21 августа.

```bash
mkdir -p backups
docker run --rm -v jw_downloader_bot_data:/v -v "$PWD/backups":/b alpine \
    sh -c 'cp /v/bot.db "/b/bot.db.bak-$(date +%Y%m%d-%H%M%S)" && ls -l /b'
```

Ожидается: в `backups/` появился свежий файл. Каталог `backups/` в `.gitignore` — в репозиторий он не попадёт.

- [ ] **Шаг 2: Написать падающий тест**

Создать `tests/test_schema_migration.py`:

```python
import importlib.util
import sqlite3
from pathlib import Path

import pytest

from bot.db.models import EXTRA_INDEX_DDL, USERNAME_UNIQUE_DDL

ROOT = Path(__file__).resolve().parent.parent


def _load_migration():
    spec = importlib.util.spec_from_file_location(
        "migrate_20260913", ROOT / "scripts" / "migrate_20260913.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _legacy_db(path: Path) -> sqlite3.Connection:
    """Схема без индексов — такая, какая лежит в боевом томе."""
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE users (
            id INTEGER PRIMARY KEY,
            username VARCHAR(255),
            full_name VARCHAR(255) NOT NULL,
            free_downloads_left INTEGER NOT NULL DEFAULT 3,
            subscription_until DATETIME,
            is_banned BOOLEAN NOT NULL DEFAULT 0,
            total_downloads INTEGER NOT NULL DEFAULT 0,
            created_at DATETIME NOT NULL,
            updated_at DATETIME NOT NULL
        );
        CREATE TABLE download_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id),
            url TEXT NOT NULL,
            platform VARCHAR(32) NOT NULL,
            status VARCHAR(16) NOT NULL,
            file_size_mb FLOAT,
            created_at DATETIME NOT NULL
        );
        """
    )
    return conn


def test_extra_ddl_is_idempotent(tmp_path):
    conn = _legacy_db(tmp_path / "bot.db")
    for statement in EXTRA_INDEX_DDL:
        conn.execute(statement)
        conn.execute(statement)  # второй прогон не должен падать
    names = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")}
    assert "ix_download_log_created_at" in names
    assert "ix_download_log_user_id" in names


def test_unique_index_refuses_duplicates_after_migration(tmp_path):
    conn = _legacy_db(tmp_path / "bot.db")
    conn.executescript(
        """
        INSERT INTO users VALUES (1000000001,'foo','A',3,NULL,0,0,'2025-01-01','2025-01-01');
        INSERT INTO users VALUES (1000000002,'Foo','B',3,NULL,0,0,'2025-01-01','2026-09-01');
        INSERT INTO users VALUES (1000000003,NULL,'C',3,NULL,0,0,'2025-01-01','2025-01-01');
        """
    )
    conn.commit()

    # До миграции уникальный индекс создать нельзя — это и есть повод для скрипта.
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(USERNAME_UNIQUE_DDL)

    migration = _load_migration()
    cleared = migration.dedupe_usernames(conn)
    conn.commit()

    assert cleared == [(1000000001, "foo")]
    conn.execute(USERNAME_UNIQUE_DDL)  # теперь проходит

    # У более свежей строки ник сохранён, у старой снят, NULL не тронут.
    rows = dict(conn.execute("SELECT id, username FROM users"))
    assert rows == {1000000001: None, 1000000002: "Foo", 1000000003: None}


def test_unique_index_allows_many_nulls(tmp_path):
    conn = _legacy_db(tmp_path / "bot.db")
    conn.execute(USERNAME_UNIQUE_DDL)
    conn.executescript(
        """
        INSERT INTO users VALUES (1000000001,NULL,'A',3,NULL,0,0,'2025-01-01','2025-01-01');
        INSERT INTO users VALUES (1000000002,NULL,'B',3,NULL,0,0,'2025-01-01','2025-01-01');
        """
    )
    conn.commit()
    assert conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 2


def test_unique_index_is_case_insensitive(tmp_path):
    conn = _legacy_db(tmp_path / "bot.db")
    conn.execute(USERNAME_UNIQUE_DDL)
    conn.execute("INSERT INTO users VALUES (1000000001,'foo','A',3,NULL,0,0,'2025-01-01','2025-01-01')")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO users VALUES (1000000002,'FOO','B',3,NULL,0,0,'2025-01-01','2025-01-01')")


async def test_foreign_keys_pragma_is_on(db_session):
    from sqlalchemy import text

    value = await db_session.scalar(text("PRAGMA foreign_keys"))
    assert value == 1
```

Последний тест требует, чтобы `PRAGMA foreign_keys=ON` вешался на движок, который создаёт фикстура. Поэтому листенер надо вынести в функцию и применять её и в `engine.py`, и в фикстуре.

- [ ] **Шаг 3: Прогнать тест, убедиться что падает**

Команда: `./scripts/test.sh tests/test_schema_migration.py -q`
Ожидается: FAIL — `ImportError: cannot import name 'EXTRA_INDEX_DDL' from 'bot.db.models'`

- [ ] **Шаг 4: Добавить константы DDL в `bot/db/models.py`**

Дописать в конец файла:

```python
# DDL, который `create_all` не выполнит на УЖЕ существующей таблице: для неё
# он пропускает таблицу целиком вместе с индексами, а `ALTER` не делает вовсе.
# Живёт здесь, а не в engine.py, чтобы скрипт миграции мог импортировать эти
# строки, не поднимая настройки и не создавая движок.
EXTRA_INDEX_DDL: tuple[str, ...] = (
    "CREATE INDEX IF NOT EXISTS ix_download_log_created_at ON download_log (created_at)",
    "CREATE INDEX IF NOT EXISTS ix_download_log_user_id ON download_log (user_id)",
    "CREATE INDEX IF NOT EXISTS ix_users_subscription_until ON users (subscription_until)",
)

# Уникальность ника — отдельно: на существующей базе она может не примениться
# из-за исторических дубликатов, и падать на старте из-за этого нельзя.
# Частичный индекс: NULL-ников может быть сколько угодно.
USERNAME_UNIQUE_DDL: str = (
    "CREATE UNIQUE INDEX IF NOT EXISTS ix_users_username_nocase "
    "ON users (username COLLATE NOCASE) WHERE username IS NOT NULL"
)
```

- [ ] **Шаг 5: Переписать `bot/db/engine.py`**

Заменить содержимое файла целиком на:

```python
from datetime import datetime, timedelta, timezone

from loguru import logger
from sqlalchemy import delete, event, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bot.config import settings
from bot.db.models import EXTRA_INDEX_DDL, USERNAME_UNIQUE_DDL, Base, DownloadLog

# Журнал загрузок хранит полные URL с приватными пер-шаринговыми токенами.
# Порог заведомо больше самого длинного окна статистики (30 дней), чтобы
# чистка не искажала цифры.
DOWNLOAD_LOG_RETENTION_DAYS = 180


def apply_sqlite_pragmas(dbapi_conn) -> None:
    """PRAGMA, обязательные на КАЖДОМ соединении SQLite.

    `foreign_keys` SQLite по умолчанию не проверяет: без него строки
    `download_log` переживают удаление пользователя сиротами и попадают
    в статистику.
    """
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA journal_mode=WAL")
    cur.execute("PRAGMA synchronous=NORMAL")
    cur.execute("PRAGMA foreign_keys=ON")
    cur.close()


if settings.DATABASE_URL.startswith("sqlite"):
    # busy_timeout=30s (connect_args timeout) чтобы конкурентные записи ждали
    # снятия блокировки вместо мгновенного "database is locked".
    async_engine = create_async_engine(
        settings.DATABASE_URL,
        connect_args={"timeout": 30},
    )

    @event.listens_for(async_engine.sync_engine, "connect")
    def _set_sqlite_pragma(dbapi_conn, _):
        apply_sqlite_pragmas(dbapi_conn)
else:
    # Не-SQLite (напр. будущий Postgres) — без SQLite-специфичных настроек.
    async_engine = create_async_engine(settings.DATABASE_URL)

async_session = async_sessionmaker(async_engine, expire_on_commit=False)


async def _purge_old_download_logs() -> None:
    """Ретеншен журнала загрузок. Строки не удалял никто."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=DOWNLOAD_LOG_RETENTION_DAYS)
    try:
        async with async_engine.begin() as conn:
            result = await conn.execute(delete(DownloadLog).where(DownloadLog.created_at < cutoff))
        if result.rowcount:
            logger.info("Download log retention: removed {} row(s)", result.rowcount)
    except Exception as exc:
        logger.warning("Download log retention failed: {}", exc)


async def init_db() -> None:
    async with async_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        for statement in EXTRA_INDEX_DDL:
            await conn.execute(text(statement))

    # Отдельной транзакцией: неудача здесь не должна откатывать индексы выше
    # и не должна ронять старт бота.
    try:
        async with async_engine.begin() as conn:
            await conn.execute(text(USERNAME_UNIQUE_DDL))
    except Exception as exc:
        logger.error(
            "Уникальный индекс по users.username не создан — в базе есть дубликаты ников. "
            "Запустить scripts/migrate_20260913.py. ({})",
            exc,
        )

    await _purge_old_download_logs()
```

- [ ] **Шаг 6: Применить те же PRAGMA в тестовой фикстуре**

В `tests/conftest.py` в фикстуре `db_session` заменить создание движка на версию с листенером:

```python
    from sqlalchemy import event
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from bot.db.engine import apply_sqlite_pragmas
    from bot.db.models import Base

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'test.db'}")

    @event.listens_for(engine.sync_engine, "connect")
    def _pragmas(dbapi_conn, _):
        apply_sqlite_pragmas(dbapi_conn)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
```

- [ ] **Шаг 7: Создать `scripts/migrate_20260913.py`**

```python
#!/usr/bin/env python3
"""Разовая миграция под ревизию 2026-09-12.

Снимает дубликаты `users.username` (без учёта регистра), чтобы `init_db()`
смог создать уникальный индекс `ix_users_username_nocase`, и заводит недостающие
индексы. Ник остаётся у той строки, которая обновлялась позже всех: это тот,
кто реально пишет боту сейчас. У остальных `username` обнуляется — данные не
теряются, поиск по id продолжает работать.

Перед запуском обязателен свежий бэкап (см. план, Task 21, Шаг 1).

Останавливать бота не требуется: SQLite в режиме WAL, операция затрагивает
единицы строк, busy_timeout 30 секунд.

    docker build --target test -t jw_downloader:test .
    docker run --rm -v jw_downloader_bot_data:/app/data jw_downloader:test \\
        python scripts/migrate_20260913.py --database /app/data/bot.db
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot.db.models import EXTRA_INDEX_DDL, USERNAME_UNIQUE_DDL  # noqa: E402


def dedupe_usernames(conn: sqlite3.Connection) -> list[tuple[int, str]]:
    """Обнуляет ник у всех строк группы, кроме самой свежей.

    Возвращает список `(user_id, снятый ник)` в порядке возрастания id.
    """
    rows = conn.execute(
        "SELECT id, username, updated_at FROM users "
        "WHERE username IS NOT NULL AND username <> ''"
    ).fetchall()

    groups: dict[str, list[tuple[int, str, str]]] = {}
    for user_id, username, updated_at in rows:
        groups.setdefault(username.lower(), []).append((user_id, username, updated_at or ""))

    cleared: list[tuple[int, str]] = []
    for members in groups.values():
        if len(members) < 2:
            continue
        # Позже обновлялся — тот и оставляет ник за собой.
        members.sort(key=lambda m: (m[2], m[0]), reverse=True)
        for user_id, username, _ in members[1:]:
            conn.execute("UPDATE users SET username = NULL WHERE id = ?", (user_id,))
            cleared.append((user_id, username))

    cleared.sort()
    return cleared


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Миграция схемы под ревизию 2026-09-12.")
    parser.add_argument("--database", required=True, type=Path, help="путь к bot.db")
    args = parser.parse_args(argv)

    if not args.database.is_file():
        print(f"файла нет: {args.database}")
        return 1

    conn = sqlite3.connect(args.database, timeout=30)
    try:
        conn.execute("PRAGMA busy_timeout = 30000")
        cleared = dedupe_usernames(conn)
        for user_id, username in cleared:
            print(f"снят дублирующийся ник: id={user_id} username={username}")

        for statement in EXTRA_INDEX_DDL:
            conn.execute(statement)
        conn.execute(USERNAME_UNIQUE_DDL)
        conn.commit()
    finally:
        conn.close()

    print(f"снято дублирующихся ников: {len(cleared)}")
    print("индексы созданы")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

Сделать исполняемым: `chmod +x scripts/migrate_20260913.py`

- [ ] **Шаг 8: Прогнать тесты**

Команда: `./scripts/test.sh -q`
Ожидается: PASS, регрессий нет

- [ ] **Шаг 9: Прогнать миграцию на КОПИИ боевой БД**

Сначала на копии, а не на боевом томе:

```bash
cp "$(ls -t backups/bot.db.bak-* | head -1)" /tmp/bot.db.migrate-test
docker run --rm -v /tmp:/work jw_downloader:test \
    python scripts/migrate_20260913.py --database /work/bot.db.migrate-test
```

Ожидается: «индексы созданы», ошибок нет. Проверить, что данные на месте:

```bash
docker run --rm -v /tmp:/work jw_downloader:test python -c "
import sqlite3
c = sqlite3.connect('/work/bot.db.migrate-test')
print('users:', c.execute('SELECT COUNT(*) FROM users').fetchone()[0])
print('log rows:', c.execute('SELECT COUNT(*) FROM download_log').fetchone()[0])
print('indexes:', sorted(r[0] for r in c.execute(\"SELECT name FROM sqlite_master WHERE type='index' AND name LIKE 'ix_%'\")))
"
```

Ожидается: число пользователей и строк журнала совпадает с тем, что было до миграции; в списке индексов есть `ix_download_log_created_at`, `ix_download_log_user_id`, `ix_users_subscription_until`, `ix_users_username_nocase`.

- [ ] **Шаг 10: Прогнать миграцию на боевой БД**

Только после того, как предыдущий шаг прошёл чисто и бэкап на месте:

```bash
docker run --rm -v jw_downloader_bot_data:/app/data jw_downloader:test \
    python scripts/migrate_20260913.py --database /app/data/bot.db
```

Ожидается: «индексы созданы». Если вывод сообщает о снятых дубликатах — записать, у кого именно, чтобы админ знал, почему поиск по нику для этого пользователя больше не работает (поиск по id работает всегда).

- [ ] **Шаг 11: Коммит**

```bash
git add bot/db/models.py bot/db/engine.py tests/conftest.py scripts/migrate_20260913.py tests/test_schema_migration.py
git commit -m "feat(db): add indexes, enforce foreign keys, retain download log for 180 days"
```

---
## Пакет F — Инфраструктура (ветка `feat/local-bot-api`)

Владеет файлами `bot/config.py`, `.env.example`, `docker-compose.yml`, `.gitignore`, `requirements-dev.txt` и, начиная с этой волны, `Dockerfile` (пакет 0 закончил с ним в Task 6 и смержен). Идёт во второй волне **параллельно с пакетами A, B и C**. `tests/conftest.py` принадлежит пакету C — пакет F его не трогает и заводит собственные тестовые файлы.

---

### Task 22: Опечатка в имени ключа конфигурации снова падает, а не игнорируется

**Закрывает:** M-26

**Files:**
- Modify: `bot/config.py:20-45`
- Modify: `.env.example`
- Create: `tests/test_config_validation.py`

**Interfaces:**
- Consumes: ничего.
- Produces:
  - Поля `Settings.TELEGRAM_API_ID: str`, `Settings.TELEGRAM_API_HASH: str` — объявлены явно.
  - `bot.config.check_env_keys(environ: Mapping[str, str] | None = None) -> list[str]` — ловит опечатки в именах ключей.

Коммит `25fe5ae` добавил `extra="ignore"` — и был обязан это сделать: в окружении осознанно лежат ключи для контейнера `telegram-bot-api`, которых `Settings` не объявляет, а источник dotenv, в отличие от переменных окружения, подаёт в валидацию все непустые ключи файла. Но вместе с падением на постороннем ключе исчезло и падение на опечатке в своём: `USDT_TRC2O_ADDRESS` (ноль вместо буквы «O») теперь просто игнорируется, поле остаётся пустым по умолчанию, и пользователи видят `💎 USDT (TRC20):` с пустым `<code></code>` (`user.py:174`). То же с `SUBSCRIPTION_PRICE_USDT/VND/THB` — показываются дефолты из кода.

Решение разделяет два случая, которые `extra="ignore"` свалил в один. Ключи для соседнего контейнера объявляются полями явно (заодно перестают быть «посторонними»), а проверка ловит только те переменные окружения, которые отличаются от объявленного поля **ровно одной правкой символа** — это и есть определение опечатки. Ругаться на все посторонние переменные нельзя: в контейнере их сотни (`PATH`, `HOME`, `PYTHON_VERSION`…), и предупреждение утонуло бы в шуме. Проверяется именно окружение, а не файл `.env`: compose подаёт ключи через `env_file`, то есть переменными окружения, а самого файла в образе нет — он в `.dockerignore`.

Осознанный размен: опечатка теперь роняет бота на старте. Это ровно тот fail-fast, который был до `25fe5ae`, и ложное срабатывание стоило бы перезапуска с исправленным именем — а молчаливая недонастройка стоит денег, потому что пользователь видит пустой адрес оплаты.

Вторая половина M-26 — недокументированные ключи в `.env.example` — на ветке `feat/local-bot-api` уже закрыта коммитом `c5ad393`: `FREE_DOWNLOADS`, `MAX_FILE_SIZE_MB`, `DOWNLOAD_TIMEOUT` и `SUBSCRIPTION_PRICE_*` в файле есть закомментированными. Вместо повторной починки заводим тест, который не даст расхождению вернуться.

- [ ] **Шаг 1: Написать падающий тест**

Создать `tests/test_config_validation.py`:

```python
from pathlib import Path

import pytest

from bot.config import Settings, check_env_keys

ROOT = Path(__file__).resolve().parent.parent


def test_typo_in_a_money_key_is_fatal():
    """M-26: `USDT_TRC2O_ADDRESS` игнорировался, и адрес оплаты был пустым."""
    with pytest.raises(ValueError) as excinfo:
        check_env_keys({"USDT_TRC2O_ADDRESS": "whatever"})
    assert "USDT_TRC2O_ADDRESS" in str(excinfo.value)
    assert "USDT_TRC20_ADDRESS" in str(excinfo.value)


def test_typo_by_a_missing_character_is_fatal():
    with pytest.raises(ValueError):
        check_env_keys({"FREE_DOWNLOAD": "5"})


def test_typo_by_an_extra_character_is_fatal():
    with pytest.raises(ValueError):
        check_env_keys({"ADMIN_IDD": "1"})


def test_declared_keys_pass():
    assert check_env_keys({"ADMIN_ID": "1", "TELEGRAM_API_ID": "1", "TELEGRAM_API_HASH": "x"}) == []


def test_unrelated_environment_variables_are_ignored():
    # В контейнере таких сотни — ругаться на них нельзя.
    assert check_env_keys({"PATH": "/usr/bin", "HOME": "/root", "LANG": "C.UTF-8"}) == []


def test_telegram_container_keys_are_declared_fields():
    assert "TELEGRAM_API_ID" in Settings.model_fields
    assert "TELEGRAM_API_HASH" in Settings.model_fields


def test_every_setting_is_documented_in_env_example():
    """Не даёт вернуться расхождению: новое поле без строки в .env.example."""
    example = (ROOT / ".env.example").read_text(encoding="utf-8")
    missing = [name for name in Settings.model_fields if name not in example]
    assert missing == [], f"не задокументированы в .env.example: {missing}"
```

- [ ] **Шаг 2: Прогнать тест, убедиться что падает**

Команда: `./scripts/test.sh tests/test_config_validation.py -q`
Ожидается: FAIL — `ImportError: cannot import name 'check_env_keys' from 'bot.config'`

- [ ] **Шаг 3: Объявить ключи соседнего контейнера полями**

В `bot/config.py` в секцию «Локальный Bot API» (после строки 25) добавить:

```python
    # Ключи приложения с my.telegram.org для контейнера telegram-bot-api.
    # Сам бот их не использует, но объявлены явно: так они перестают быть
    # «посторонними» для источника dotenv и попадают под проверку опечаток.
    TELEGRAM_API_ID: str = ""
    TELEGRAM_API_HASH: str = ""
```

- [ ] **Шаг 4: Добавить проверку опечаток в `bot/config.py`**

Заменить комментарий на строках 40–44 и строку 48 на:

```python
    # extra="ignore" остаётся: в окружении осознанно лежат ключи, которых
    # Settings не объявляет, а источник dotenv подаёт в валидацию ВСЕ непустые
    # ключи файла. Опечатки в СВОИХ ключах ловит check_env_keys ниже.
    model_config = {"env_file": ".env", "extra": "ignore"}


def _within_one_edit(a: str, b: str) -> bool:
    """True, если строки различаются не более чем одной правкой символа."""
    if a == b:
        return True
    la, lb = len(a), len(b)
    if abs(la - lb) > 1:
        return False
    if la == lb:
        return sum(1 for x, y in zip(a, b) if x != y) == 1

    short, long_ = (a, b) if la < lb else (b, a)
    i = j = 0
    skipped = False
    while i < len(short) and j < len(long_):
        if short[i] == long_[j]:
            i += 1
            j += 1
            continue
        if skipped:
            return False
        skipped = True
        j += 1
    return True


def check_env_keys(environ: Mapping[str, str] | None = None) -> list[str]:
    """Ловит опечатки в именах ключей конфигурации.

    `extra="ignore"` необходим, но он же превратил опечатку в имени денежного
    ключа из падения на импорте в молчаливую недонастройку: `USDT_TRC2O_ADDRESS`
    просто игнорируется, и пользователи видят пустой адрес оплаты.

    Ругаться на все посторонние переменные окружения нельзя — в контейнере их
    сотни. Поэтому ловим только те, что отличаются от объявленного поля ровно
    одной правкой символа: это и есть определение опечатки. Проверяем именно
    окружение, а не файл `.env`: compose подаёт ключи через `env_file`, то есть
    переменными окружения, а самого файла в образе нет.

    Возвращает пустой список: непохожие переменные — не наша забота. Опечатка
    же поднимает ValueError, потому что молчаливо неверный адрес оплаты стоит
    денег, а лишний перезапуск с исправленным именем — нет.
    """
    source = os.environ if environ is None else environ
    known = set(Settings.model_fields)
    typos = [
        (key, near)
        for key in source
        if key not in known
        for near in (next((k for k in known if _within_one_edit(key, k)), None),)
        if near is not None
    ]
    if typos:
        details = ", ".join(f"{bad} → похоже на {good}" for bad, good in typos)
        raise ValueError(f"Опечатка в именах ключей конфигурации: {details}")
    return []


settings = Settings()
check_env_keys()
```

В начало файла добавить импорты:

```python
import os
from typing import Mapping
```

- [ ] **Шаг 5: Прогнать тесты**

Команда: `./scripts/test.sh -q`
Ожидается: PASS, регрессий нет. Если тест `test_every_setting_is_documented_in_env_example` упал на `TELEGRAM_API_ID`/`TELEGRAM_API_HASH` — значит, строки в `.env.example` действительно нет; дописать её в секцию «Локальный Bot API сервер» ровно в том виде, в каком там уже документированы остальные ключи.

- [ ] **Шаг 6: Коммит**

```bash
git add bot/config.py .env.example tests/test_config_validation.py
git commit -m "feat(config): fail fast on misspelled setting names"
```

---

### Task 23: Секреты только на чтение, память и процессы под потолком

**Закрывает:** H-16 (часть про rw-монтирование), Low про лимиты памяти и pids, Low из раздела 4 про монтирование `secrets/` в режиме `:ro`

**Files:**
- Modify: `docker-compose.yml:16-20`
- Modify: `requirements-dev.txt`
- Create: `tests/test_compose.py`

**Interfaces:**
- Consumes: ничего.
- Produces: ничего для кода; ограничения контейнера.

**⚠️ Меняет поведение для пользователя — нужна ручная проверка:** появляется потолок памяти. При выкладке проверить, что загрузка крупного файла не приводит к OOM-kill контейнера (`docker inspect` → `State.OOMKilled`). Потолок придётся пересмотреть в Task 14 плана local-bot-api, когда tmpfs уедет на диск и размеры файлов вырастут.

`docker inspect` показывает `Memory=0` и `PidsLimit=<nil>`: у контейнера нет ни потолка памяти, ни потолка по процессам, а tmpfs под загрузки — это та же оперативная память. Три параллельные загрузки загоняют коробку в своп-трэшинг под OOM-killer, и страдает не только бот: на том же хосте живут другие сервисы. `memswap_limit`, равный `mem_limit`, отключает своп для контейнера: лучше предсказуемое убийство одного процесса, чем трэшинг всей коробки.

`secrets/` смонтирован в режиме `rw`, хотя после перехода на эфемерные копии кук (`62f7c30`) запись в мастер-банку не нужна вообще. Любой процесс в контейнере может её перезаписать — ровно тот сценарий, из-за которого куки уже терялись.

Тест читает `docker-compose.yml` как YAML, поэтому в тестовые зависимости добавляется `pyyaml`. Текстовые проверки по подстроке здесь не годятся: `:ro` легко приписать не к той строке, а YAML-разбор проверяет именно то, что увидит Docker.

- [ ] **Шаг 1: Написать падающий тест**

Создать `tests/test_compose.py`:

```python
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


def _bot_service() -> dict:
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    return compose["services"]["bot"]


def test_secrets_are_mounted_read_only():
    """H-16: любой процесс в контейнере мог перезаписать мастер-банку кук."""
    mounts = _bot_service()["volumes"]
    secrets = [m for m in mounts if "/app/secrets" in m]
    assert secrets, "монтирование secrets/ пропало"
    assert all(m.endswith(":ro") for m in secrets)


def test_data_volume_stays_writable():
    mounts = _bot_service()["volumes"]
    data = [m for m in mounts if m.endswith(":/app/data")]
    assert data, "именованный том с базой пропал"


def test_memory_and_pids_are_capped():
    service = _bot_service()
    assert service.get("mem_limit")
    assert service.get("pids_limit")


def test_swap_is_disabled_for_the_container():
    # memswap_limit == mem_limit означает «своп не использовать»: лучше
    # предсказуемый OOM одного контейнера, чем трэшинг всей коробки.
    service = _bot_service()
    assert service["memswap_limit"] == service["mem_limit"]


def test_host_network_is_preserved():
    # Docker-NAT на этом хосте роняет аплоады крупнее ~1.5 МБ. Трогать нельзя.
    assert _bot_service()["network_mode"] == "host"
```

- [ ] **Шаг 2: Добавить `pyyaml` в тестовые зависимости**

В `requirements-dev.txt` дописать строку:

```
pyyaml>=6.0
```

- [ ] **Шаг 3: Прогнать тест, убедиться что падает**

Команда: `./scripts/test.sh tests/test_compose.py -q`
Ожидается: FAIL — три провала: `secrets` монтируется без `:ro`, `mem_limit` и `pids_limit` отсутствуют

- [ ] **Шаг 4: Поправить `docker-compose.yml`**

Заменить строки 16–20

```yaml
    volumes:
      - bot_data:/app/data
      - ./secrets:/app/secrets
    tmpfs:
      - /tmp/jw_downloads:size=200M
```

на

```yaml
    volumes:
      - bot_data:/app/data
      # Только чтение: банка кук — мастер-копия боевых сессий. Загрузчик с
      # 62f7c30 работает с эфемерной копией, писать в мастер незачем, а любой
      # процесс в контейнере мог её перезаписать.
      - ./secrets:/app/secrets:ro
    tmpfs:
      - /tmp/jw_downloads:size=200M,mode=1777
    # Потолки обязательны: tmpfs под загрузки — это та же оперативная память,
    # а хост бот делит с другими сервисами. memswap_limit, равный mem_limit,
    # отключает своп: предсказуемый OOM одного контейнера лучше, чем
    # своп-трэшинг всей коробки. Пересмотреть в Task 14 плана local-bot-api,
    # когда загрузки уедут с tmpfs на диск.
    mem_limit: 1g
    memswap_limit: 1g
    pids_limit: 256
```

- [ ] **Шаг 5: Прогнать тесты**

Команда: `./scripts/test.sh -q`
Ожидается: PASS, регрессий нет

- [ ] **Шаг 6: Коммит**

```bash
git add docker-compose.yml requirements-dev.txt tests/test_compose.py
git commit -m "fix(compose): mount secrets read-only, cap container memory and pids"
```

---

### Task 24: Контейнер от непривилегированного пользователя, `.superpowers/` в `.gitignore`

**Закрывает:** Low про контейнер от root, Low про отсутствие `.superpowers/` в корневом `.gitignore`

**Files:**
- Modify: `Dockerfile:21-24`
- Modify: `.gitignore`
- Create: `tests/test_image_hardening.py`

**Interfaces:**
- Consumes: ничего.
- Produces: ничего для кода.

**⚠️ Меняет поведение для пользователя — нужна ручная проверка:** контейнер перестаёт работать от root. **Без подготовки тома бот не поднимется** — именованный том с базой создан root-ом, и uid 1000 в него не запишет. Подготовительная команда приведена ниже; выполнить её обязательно ДО первой выкладки этого изменения.

`docker inspect` показывает `User=""`, то есть uid 0. Бот с host-сетью, правами root и без потолков — избыточная поверхность: любая дыра в yt-dlp, gallery-dl или ffmpeg, которых мы кормим недоверенным контентом на каждой загрузке, отрабатывает от root.

Стадия `test` остаётся от root сознательно: тесты создают временные файлы в произвольных местах, и ужесточать их окружение ради чистоты незачем — в прод эта стадия не попадает (`docker-compose.yml` собирает `target: runtime`).

`.superpowers/` спасает только вложенный `.superpowers/sdd/.gitignore` с `*`; файл, положенный напрямую в `.superpowers/`, не игнорируется — проверено `git check-ignore`. В публичном репозитории это прямой риск: рабочие заметки, брифы задач и леджеры туда попадать не должны.

- [ ] **Шаг 1: Написать падающий тест**

Создать `tests/test_image_hardening.py`:

```python
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
```

- [ ] **Шаг 2: Прогнать тест, убедиться что падает**

Команда: `./scripts/test.sh tests/test_image_hardening.py -q`
Ожидается: FAIL — `USER botuser` в стадии runtime отсутствует, `.superpowers/` в `.gitignore` отсутствует

- [ ] **Шаг 3: Поправить стадию `runtime` в `Dockerfile`**

Заменить строки 21–24

```dockerfile
FROM base AS runtime
COPY . .
RUN mkdir -p /app/data /srv/jw_downloads
CMD ["python", "-m", "bot"]
```

на

```dockerfile
FROM base AS runtime
COPY . .
# Бот кормит недоверенным контентом yt-dlp, gallery-dl и ffmpeg на каждой
# загрузке, работает с host-сетью и раньше делал это от root. Непривилегированный
# пользователь убирает самый дорогой исход любой дыры в этих утилитах.
RUN mkdir -p /app/data /srv/jw_downloads \
    && groupadd --gid 1000 botuser \
    && useradd --uid 1000 --gid 1000 --no-create-home --shell /usr/sbin/nologin botuser \
    && chown -R botuser:botuser /app /srv/jw_downloads
USER botuser
CMD ["python", "-m", "bot"]
```

- [ ] **Шаг 4: Дописать `.gitignore`**

В секцию «Локальные инструменты / приватное» добавить строку:

```
.superpowers/
```

- [ ] **Шаг 5: Прогнать тесты**

Команда: `./scripts/test.sh -q`
Ожидается: PASS, регрессий нет

- [ ] **Шаг 6: Проверить, что `.superpowers/` действительно игнорируется**

```bash
git check-ignore -v .superpowers/проверка.txt
```

Ожидается: строка с указанием на корневой `.gitignore`. Если команда ничего не вывела — правило не сработало.

- [ ] **Шаг 7: Записать подготовительную команду для выкладки**

Выкладка в план не входит, но без этой команды контейнер от uid 1000 не поднимется: именованный том с базой создан root-ом. Команду записать в `.claude/TODO_FIXES.md` рядом с отметкой о закрытии находки, чтобы она не потерялась к моменту деплоя:

```bash
docker run --rm -v jw_downloader_bot_data:/data alpine chown -R 1000:1000 /data
```

Туда же — про права на хостовую банку кук: файл лежит с режимом `0644`, то есть читается любой локальной учёткой. Привести к `0600` вместе с обеими копиями рядом с ним. Это действие владельца на его машине, кода не касается.

- [ ] **Шаг 8: Коммит**

```bash
git add Dockerfile .gitignore tests/test_image_hardening.py
git commit -m "fix(docker): run the runtime stage as an unprivileged user"
```

---
## Пакет D — Пользовательский хендлер (ветка `feat/local-bot-api`)

Пакет владеет ровно одним файлом продакшн-кода — `bot/handlers/user.py` — и своими тестовыми
файлами (`tests/test_user_quota.py`, `tests/test_user_safe_edit.py`, `tests/test_user_escaping.py`,
`tests/test_user_texts.py`, `tests/test_user_media.py`). Больше он не трогает ничего: ни
`bot/handlers/admin.py`, ни `bot/keyboards/inline.py`, ни `bot/db/*`, ни `bot/services/*`, ни
`tests/conftest.py`. Идёт в третьей волне, параллельно с пакетом E, после того как пакеты A, B, C и F
смержены в `feat/local-bot-api`. Задачи внутри пакета — строго последовательно, Task 25 → 29: каждая
следующая правит код, который написала предыдущая.

---

### Task 25: Квота — резервирование до загрузки вместо списания после доставки

**Закрывает:** C-1, M-9

**Files:**
- Modify: `bot/handlers/user.py:16-21` (импорты слоя БД)
- Modify: `bot/handlers/user.py:228-423` (первая транзакция, границы `try`, все выходы хендлера)
- Test: `tests/test_user_quota.py`

**Interfaces:**
- Consumes: `bot.db.queries.reserve_free_download(session, user_id) -> bool`,
  `bot.db.queries.refund_free_download(session, user_id) -> None`,
  `bot.db.queries.increment_total_downloads`, `bot.db.queries.log_download`,
  `bot.db.queries.get_or_create_user` (всё из пакета C), фикстура `db_session` из `tests/conftest.py`.
- Produces: `_reserve_quota(session, tg_user) -> tuple[int, bool, bool, bool]`,
  `_quota_action(reserved: bool, download_ok: bool, media_sent_count: int) -> str`,
  `_refund_quota(db_user_id: int) -> None`. Task 29 заворачивает тело хендлера в
  `_process_download(message, url, platform)` — до этого момента тело живёт прямо в `handle_url`.

**⚠️ Меняет поведение для пользователя — нужна ручная проверка:** единица квоты теперь списывается
в момент отправки ссылки, а не после доставки файла. Проверить руками на тестовом аккаунте:
(1) успешная загрузка — остаток уменьшился ровно на 1; (2) заведомо битая ссылка — остаток вернулся
на место; (3) две ссылки подряд при остатке 1 — вторая упирается в пейволл, а не качается.

Сейчас `user.py:232-241` читает остаток, `:247-253` пропускает по гейту, а списание идёт на
`:365-367` — то есть ПОСЛЕ аплоада. Между чтением и списанием помещаются ещё две-три параллельные
загрузки того же пользователя: замерено, что с одним оставшимся скачиванием юзер получает 3–7.
Вдобавок транзакция списания `:365-371` лежит ВНУТРИ `try`, открытого на `:278`: её падение
откатывает декремент, инкремент и success-лог, управление уходит в `except` на `:409-416`, который
пишет `status="failed"` и показывает «⚠️ Ошибка при отправке файла», хотя медиа уже доставлено.

Лечение — резервирование вместо проверки: единица списывается в той же короткой транзакции, что и
чтение пользователя, одним атомарным `UPDATE ... WHERE free_downloads_left > 0` с проверкой
`rowcount` (это уже сделано пакетом C в `reserve_free_download`). Дальше единица либо расходуется,
либо возвращается ровно один раз. Политика возврата вынесена в чистую функцию `_quota_action`,
чтобы её можно было закрыть тестами целиком: возвращаем, только если пользователь не получил ничего
— загрузка провалилась, либо не ушёл ни один файл. Частично доставленный альбом не возвращаем:
медиа у пользователя уже есть. Граница `try` доставки сдвигается так, чтобы внутри неё остались
ТОЛЬКО вызовы отправки в Telegram; все записи в БД и все ответы пользователю выполняются после
`try`, по значению `send_error`.

- [ ] **Шаг 1: Написать падающий тест**

Создать `tests/test_user_quota.py`:

```python
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from bot.config import settings
from bot.db.models import User
from bot.db.queries import refund_free_download, reserve_free_download
from bot.handlers.user import _quota_action, _reserve_quota


def _tg_user(uid: int = 555001, username: str = "quota_tester", full_name: str = "Quota Tester"):
    # Хендлер обращается ровно к трём атрибутам Telegram-пользователя,
    # поэтому полноценная pydantic-модель aiogram здесь не нужна.
    return SimpleNamespace(id=uid, username=username, full_name=full_name)


# ── политика возврата единицы ──


def test_quota_action_keeps_when_nothing_was_reserved():
    # У подписчика и у забаненного резервирования не было — возвращать нечего.
    assert _quota_action(False, download_ok=False, media_sent_count=0) == "keep"
    assert _quota_action(False, download_ok=True, media_sent_count=0) == "keep"


def test_quota_action_refunds_when_download_failed():
    assert _quota_action(True, download_ok=False, media_sent_count=0) == "refund"


def test_quota_action_refunds_when_nothing_was_delivered():
    assert _quota_action(True, download_ok=True, media_sent_count=0) == "refund"


def test_quota_action_keeps_when_at_least_one_file_delivered():
    # Частично доставленный альбом не возвращаем: медиа у юзера уже есть.
    assert _quota_action(True, download_ok=True, media_sent_count=1) == "keep"
    assert _quota_action(True, download_ok=True, media_sent_count=5) == "keep"


# ── резервирование в одной транзакции с чтением ──


async def test_reserve_quota_takes_one_unit_from_new_user(db_session):
    uid, banned, has_sub, reserved = await _reserve_quota(db_session, _tg_user())

    assert (banned, has_sub, reserved) == (False, False, True)
    db_session.expire_all()
    user = await db_session.get(User, uid)
    assert user.free_downloads_left == settings.FREE_DOWNLOADS - 1


async def test_reserve_quota_is_atomic_for_the_last_unit(db_session):
    tg = _tg_user()
    db_session.add(
        User(id=tg.id, username=tg.username, full_name=tg.full_name, free_downloads_left=1)
    )
    await db_session.flush()

    first = (await _reserve_quota(db_session, tg))[3]
    second = (await _reserve_quota(db_session, tg))[3]

    # Второй заход обязан получить False, а не увести остаток в минус.
    assert (first, second) == (True, False)
    db_session.expire_all()
    assert (await db_session.get(User, tg.id)).free_downloads_left == 0


async def test_reserve_quota_does_not_touch_banned_user(db_session):
    tg = _tg_user()
    db_session.add(
        User(
            id=tg.id,
            username=tg.username,
            full_name=tg.full_name,
            free_downloads_left=3,
            is_banned=True,
        )
    )
    await db_session.flush()

    uid, banned, has_sub, reserved = await _reserve_quota(db_session, tg)

    assert banned is True
    assert reserved is False
    db_session.expire_all()
    assert (await db_session.get(User, tg.id)).free_downloads_left == 3


async def test_reserve_quota_skips_subscriber(db_session):
    tg = _tg_user()
    # subscription_until хранится naive и трактуется слоем БД как UTC.
    until = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=5)
    db_session.add(
        User(
            id=tg.id,
            username=tg.username,
            full_name=tg.full_name,
            free_downloads_left=0,
            subscription_until=until,
        )
    )
    await db_session.flush()

    uid, banned, has_sub, reserved = await _reserve_quota(db_session, tg)

    assert has_sub is True
    assert reserved is False


async def test_refund_returns_exactly_one_unit(db_session):
    # Контракт пакета C, на который опирается хендлер: возврат отдаёт ровно
    # единицу и не «чинит» остаток до максимума.
    tg = _tg_user()
    db_session.add(
        User(id=tg.id, username=tg.username, full_name=tg.full_name, free_downloads_left=3)
    )
    await db_session.flush()

    assert await reserve_free_download(db_session, tg.id) is True
    await refund_free_download(db_session, tg.id)

    db_session.expire_all()
    assert (await db_session.get(User, tg.id)).free_downloads_left == 3
```

- [ ] **Шаг 2: Прогнать тест, убедиться что падает**

Команда: `./scripts/test.sh tests/test_user_quota.py -q`
Ожидается: FAIL на этапе импорта — `ImportError: cannot import name '_quota_action' from 'bot.handlers.user'`.

- [ ] **Шаг 3: Реализовать**

В `bot/handlers/user.py` заменить блок импортов из слоя БД (`:16-21`) на:

```python
from bot.db.queries import (
    get_or_create_user,
    increment_total_downloads,
    log_download,
    refund_free_download,
    reserve_free_download,
)
```

`decrement_free_downloads` из импортов исчезает — после пакета C она устарела и больше не
вызывается ниоткуда (удаляет её из `bot/db/queries.py` пакет C, не мы).

Добавить три помощника сразу после `_send_with_retry` (то есть после `user.py:95`):

```python
async def _reserve_quota(session, tg_user) -> tuple[int, bool, bool, bool]:
    """Первый шаг загрузки: пользователь, бан, подписка и резерв единицы квоты.

    Возвращает (user_id, is_banned, has_subscription, reserved). Резервирование
    делается ЗДЕСЬ ЖЕ, в той же транзакции, что и чтение — иначе между чтением
    остатка и списанием помещаются параллельные загрузки того же юзера, и с
    одним оставшимся скачиванием он получает три-семь.

    Забаненному и подписчику единица не резервируется: первому загрузка
    запрещена, второму квота не нужна.
    """
    user = await get_or_create_user(session, tg_user)
    db_user_id = user.id
    is_banned = user.is_banned

    sub_until = user.subscription_until
    if sub_until and sub_until.tzinfo is None:
        sub_until = sub_until.replace(tzinfo=timezone.utc)
    has_subscription = bool(sub_until and sub_until > datetime.now(timezone.utc))

    if is_banned or has_subscription:
        return db_user_id, is_banned, has_subscription, False

    reserved = await reserve_free_download(session, db_user_id)
    return db_user_id, is_banned, has_subscription, reserved


def _quota_action(reserved: bool, download_ok: bool, media_sent_count: int) -> str:
    """Что сделать с зарезервированной единицей: "keep" или "refund".

    Возвращаем только если пользователь не получил ничего: либо провалилась
    загрузка, либо не ушёл ни один файл. Частично доставленный альбом не
    возвращается — медиа у пользователя уже есть.
    """
    if not reserved:
        return "keep"
    if not download_ok:
        return "refund"
    if media_sent_count == 0:
        return "refund"
    return "keep"


async def _refund_quota(db_user_id: int) -> None:
    """Возврат единицы отдельной короткой транзакцией.

    Падение возврата не должно ронять хендлер: пользователь уже увидел ошибку,
    а потерянная единица — меньшее зло, чем необработанное исключение.
    """
    try:
        async with async_session() as session, session.begin():
            await refund_free_download(session, db_user_id)
    except Exception as exc:
        logger.error("Не удалось вернуть единицу квоты | user={} error={}", db_user_id, exc)
```

Заменить весь блок `user.py:228-423` (от комментария «F1 шаг 1» до конца функции) на:

```python
    # C-1 шаг 1: одна короткая транзакция читает пользователя И резервирует
    # единицу квоты. Раньше остаток читался здесь, а списывался после аплоада.
    async with async_session() as session, session.begin():
        db_user_id, is_banned, has_subscription, reserved = await _reserve_quota(
            session, message.from_user
        )

    if is_banned:
        # Резервирования не было — возвращать нечего.
        await message.reply("🚫 Ваш аккаунт заблокирован. Обратитесь к администратору.")
        return

    if not has_subscription and not reserved:
        await message.answer(
            "🚫 Бесплатный лимит исчерпан.\n"
            "Оформи подписку для безлимитного доступа 👇",
            reply_markup=get_paywall_kb(is_admin=_is_admin(message.from_user.id)),
        )
        return

    status_msg = await message.reply("⏳ <b>Скачиваю медиа...</b>\nЭто займёт несколько секунд")

    # Скачивание — БЕЗ открытой db-сессии, чтобы не держать SQLite write-lock
    # на всю длину download+upload.
    async with download_semaphore:
        dl_result = await download_media(url, platform)

    if not dl_result.success:
        async with async_session() as session, session.begin():
            await log_download(session, db_user_id, url, platform, "failed")
        if _quota_action(reserved, download_ok=False, media_sent_count=0) == "refund":
            await _refund_quota(db_user_id)
        try:
            await status_msg.edit_text(
                f"❌ <b>Не удалось скачать</b>\n{dl_result.error_message}"
            )
        except TelegramBadRequest:
            pass
        return

    media_total_count = len(dl_result.file_paths) if dl_result.file_paths else 1
    media_sent_count = 0
    send_error: Exception | None = None

    # C-1 шаг 2: внутри try остаются ТОЛЬКО вызовы отправки в Telegram.
    # Записи в БД и ответы пользователю вынесены наружу: раньше транзакция
    # успеха лежала внутри, и её падение откатывало инкремент и success-лог, а
    # управление уходило в except, который писал status="failed" и показывал
    # «Ошибка при отправке», хотя медиа уже было доставлено.
    try:
        if dl_result.file_paths and len(dl_result.file_paths) > 1:
            # Множественные файлы (карусель) — отправляем media group чанками.
            # Telegram limit: 10 items per media group, но чанки ближе к 10
            # файлам/10 МБ вызывают таймауты при отправке, поэтому режем
            # на чанки по MEDIA_GROUP_CHUNK_SIZE (5) элементов.
            #
            # F11: Telegram отклоняет фото > 10 МБ и НЕ допускает смешивания
            # документов с фото/видео в одной media group. Поэтому крупные
            # изображения вынимаем из группы и шлём отдельными документами.
            caption = f"✅ Медиа из {platform.capitalize()}"
            total = len(dl_result.file_paths)
            first_media_captioned = False
            for chunk_start in range(0, total, MEDIA_GROUP_CHUNK_SIZE):
                chunk = dl_result.file_paths[chunk_start:chunk_start + MEDIA_GROUP_CHUNK_SIZE]
                sendable: list[tuple[str, bool]] = []
                oversized_images = []
                for path_str in chunk:
                    ext = path_str.rsplit(".", 1)[-1].lower() if "." in path_str else ""
                    is_image = ext in ("jpg", "jpeg", "png", "webp", "heic", "gif")
                    try:
                        size_mb = os.path.getsize(path_str) / (1024 * 1024)
                    except OSError:
                        size_mb = 0
                    if is_image and size_mb > 10:
                        oversized_images.append(path_str)
                        continue
                    sendable.append((path_str, is_image))

                if len(sendable) == 1:
                    # Telegram отклоняет альбом не из 2–10 элементов, поэтому
                    # единственный не-oversized элемент шлём одиночно.
                    path_str, is_image = sendable[0]
                    f = FSInputFile(path_str)
                    cap = caption if not first_media_captioned else None
                    if is_image:
                        await _send_with_retry(
                            lambda ff=f, c=cap: message.reply_photo(photo=ff, caption=c)
                        )
                    else:
                        await _send_with_retry(
                            lambda ff=f, c=cap: message.reply_video(video=ff, caption=c)
                        )
                    first_media_captioned = True
                    media_sent_count += 1
                elif len(sendable) >= 2:
                    media_group = []
                    for path_str, is_image in sendable:
                        f = FSInputFile(path_str)
                        if not first_media_captioned:
                            if is_image:
                                media_group.append(InputMediaPhoto(media=f, caption=caption))
                            else:
                                media_group.append(InputMediaVideo(media=f, caption=caption))
                            first_media_captioned = True
                        else:
                            if is_image:
                                media_group.append(InputMediaPhoto(media=f))
                            else:
                                media_group.append(InputMediaVideo(media=f))
                    await _send_with_retry(lambda mg=media_group: message.reply_media_group(media=mg))
                    media_sent_count += len(media_group)

                for path_str in oversized_images:
                    doc = FSInputFile(path_str)
                    await _send_with_retry(lambda d=doc: message.reply_document(document=d))
                    media_sent_count += 1
        else:
            # Одиночный файл
            media = FSInputFile(dl_result.file_path)
            if dl_result.media_type == "image":
                if dl_result.file_size_mb and dl_result.file_size_mb > 10:
                    await _send_with_retry(
                        lambda: message.reply_document(
                            document=media, caption=f"✅ Фото из {platform.capitalize()}"
                        )
                    )
                else:
                    await _send_with_retry(
                        lambda: message.reply_photo(
                            photo=media, caption=f"✅ Фото из {platform.capitalize()}"
                        )
                    )
            else:
                await _send_with_retry(
                    lambda: message.reply_video(
                        video=media, caption=f"✅ Видео из {platform.capitalize()}"
                    )
                )
            media_sent_count = 1
    except (TelegramNetworkError, TelegramRetryAfter, asyncio.TimeoutError, aiohttp.ClientError) as e:
        send_error = e
        logger.error(
            f"Сетевая ошибка при отправке медиа, попытки исчерпаны | "
            f"sent={media_sent_count}/{media_total_count} error={e}"
        )
    except Exception as e:
        send_error = e
        logger.error(f"Failed to send file: {e}")
    finally:
        paths_to_remove = dl_result.file_paths or []
        if dl_result.file_path and dl_result.file_path not in paths_to_remove:
            paths_to_remove.append(dl_result.file_path)
        for p in paths_to_remove:
            await remove_file(p)

    if send_error is None:
        async with async_session() as session, session.begin():
            await increment_total_downloads(session, db_user_id)
            await log_download(
                session, db_user_id, url, platform, "success", dl_result.file_size_mb
            )
        try:
            await status_msg.delete()
        except TelegramBadRequest:
            pass
        # Best-effort: медиа доставлено, успех зафиксирован, status_msg удалён.
        # Сбой этого финального ответа не должен ничего переписывать.
        try:
            await message.answer(
                "✅ Готово! Что дальше?",
                reply_markup=get_after_download_kb(is_admin=_is_admin(message.from_user.id)),
            )
        except Exception:
            pass
        return

    async with async_session() as session, session.begin():
        await log_download(session, db_user_id, url, platform, "failed")
    if _quota_action(reserved, download_ok=True, media_sent_count=media_sent_count) == "refund":
        await _refund_quota(db_user_id)

    is_network_error = isinstance(
        send_error,
        (TelegramNetworkError, TelegramRetryAfter, asyncio.TimeoutError, aiohttp.ClientError),
    )
    try:
        if media_total_count > 1:
            tail = "ошибка сети" if is_network_error else "ошибка"
            await status_msg.edit_text(
                f"⚠️ Отправлено {media_sent_count} из {media_total_count} файлов, "
                f"дальше произошла {tail}. Попробуй ещё раз."
            )
        elif is_network_error:
            await status_msg.edit_text("⚠️ Ошибка сети при отправке файла. Попробуй ещё раз.")
        else:
            await status_msg.edit_text("⚠️ Ошибка при отправке файла. Попробуй ещё раз.")
    except TelegramBadRequest:
        pass
```

Блок отправки (от `if dl_result.file_paths and len(...) > 1:` до `media_sent_count = 1`) перенесён из
старого кода дословно — его переписывает Task 29.

- [ ] **Шаг 4: Прогнать тесты**

Команда: `./scripts/test.sh -q`
Ожидается: PASS, все тесты пакетов A–C зелёные, регрессий нет.

- [ ] **Шаг 5: Коммит**

```bash
git add bot/handlers/user.py tests/test_user_quota.py
git commit -m "fix(user): reserve free download quota before the download starts"
```

---

### Task 26: `_safe_edit`, недоступные сообщения и блокировка бота

**Закрывает:** H-3, M-5, Low «`TelegramForbiddenError` не ловится ни в одном `except TelegramBadRequest`»

**Files:**
- Modify: `bot/handlers/user.py:9` (импорт `TelegramForbiddenError`, `InaccessibleMessage`)
- Modify: `bot/handlers/user.py:60-64` (`_safe_edit`)
- Modify: `bot/handlers/user.py` — четыре `except TelegramBadRequest` в `handle_url` (в коде ПОСЛЕ
  Task 25 это: статус при провале загрузки, `status_msg.delete()` и два `status_msg.edit_text` в
  ветке ошибки отправки)
- Test: `tests/test_user_safe_edit.py`

**Interfaces:**
- Consumes: `_safe_edit` вызывается из `cb_main_menu`, `cb_download`, `cb_status`, `cb_subscribe`,
  `cb_pay_details`, `cb_support`, `cb_help` — сигнатура не меняется.
- Produces: `_is_not_modified(exc: TelegramBadRequest) -> bool`; `_safe_edit` больше не пробрасывает
  исключений наружу.

**⚠️ Меняет поведение для пользователя — нужна ручная проверка:** кнопки меню на старом сообщении
(старше 48 часов) теперь присылают новый экран вместо молчания. Проверить руками: открыть старый
диалог с ботом, нажать «📊 Мой статус» — должен прийти новый экран статуса. И повторное нажатие той
же кнопки на актуальном сообщении не должно порождать дубль.

`_safe_edit` (`user.py:60-64`) ловит только `TelegramBadRequest` и повторяет ТОТ ЖЕ текст через
`.answer()`. Если причина отказа в самой разметке, вторая попытка падает так же, и исключение уходит
наружу необработанным. `callback.message` бывает `InaccessibleMessage` или `None` (Telegram отдаёт
такое для сообщений старше 48 часов) — тогда падает и первая попытка, и фолбэк, потому что оба
ходят через `callback.message`. Отдельный случай — «message is not modified»: это не ошибка, а
no-op, но сейчас он порождает дубль сообщения (M-5). И `TelegramForbiddenError` (юзер заблокировал
бота посреди загрузки) не ловится ни в одном из четырёх `except TelegramBadRequest`.

Решение: проверять доступность сообщения ДО попытки редактирования (`InaccessibleMessage` — это
отдельный класс, не подкласс `Message`), фолбэк отправлять через `callback.bot.send_message`, а не
через `callback.message.answer`, распознавать «message is not modified» как успех и ловить на
последней попытке `Exception` с логом вместо проброса. `TelegramForbiddenError` трактуется как «юзер
ушёл»: без ретрая, с логом на уровне INFO — это штатное событие, а не сбой.

- [ ] **Шаг 1: Написать падающий тест**

Создать `tests/test_user_safe_edit.py`:

```python
from types import SimpleNamespace

from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError

from bot.handlers.user import _is_not_modified, _safe_edit


class _StubBot:
    def __init__(self) -> None:
        self.sent: list[tuple[int, str]] = []

    async def send_message(self, chat_id, text, reply_markup=None):
        self.sent.append((chat_id, text))


class _StubMessage:
    """Минимальный двойник Message: хендлеру нужны только chat и edit_text."""

    def __init__(self, raises: Exception | None = None) -> None:
        self.chat = SimpleNamespace(id=4242)
        self.raises = raises
        self.edit_calls: list[str] = []
        self.answer_calls: list[str] = []

    async def edit_text(self, text, reply_markup=None):
        self.edit_calls.append(text)
        if self.raises is not None:
            raise self.raises

    async def answer(self, text, reply_markup=None):
        self.answer_calls.append(text)


class _StubCallback:
    def __init__(self, message) -> None:
        self.message = message
        self.bot = _StubBot()
        self.from_user = SimpleNamespace(id=777)


# TelegramAPIError.__init__(method, message) ничего не валидирует, method
# просто сохраняется — поэтому в тестах допустимо передать None.
def _bad_request(text: str) -> TelegramBadRequest:
    return TelegramBadRequest(method=None, message=text)


def test_is_not_modified_recognises_telegram_wording():
    assert _is_not_modified(_bad_request("Bad Request: message is not modified")) is True
    assert _is_not_modified(_bad_request("Bad Request: can't parse entities")) is False


async def test_safe_edit_edits_when_message_is_editable():
    msg = _StubMessage()
    cb = _StubCallback(msg)

    await _safe_edit(cb, "новый текст")

    assert msg.edit_calls == ["новый текст"]
    assert cb.bot.sent == []


async def test_safe_edit_treats_not_modified_as_noop():
    msg = _StubMessage(raises=_bad_request("Bad Request: message is not modified"))
    cb = _StubCallback(msg)

    await _safe_edit(cb, "тот же текст")

    # Ни нового сообщения, ни дубля через answer().
    assert cb.bot.sent == []
    assert msg.answer_calls == []


async def test_safe_edit_sends_new_message_when_edit_is_rejected():
    msg = _StubMessage(raises=_bad_request("Bad Request: can't parse entities"))
    cb = _StubCallback(msg)

    await _safe_edit(cb, "экран меню")

    assert cb.bot.sent == [(4242, "экран меню")]


async def test_safe_edit_survives_missing_message():
    # callback.message == None: сообщение старше 48 часов.
    cb = _StubCallback(None)

    await _safe_edit(cb, "экран меню")

    assert cb.bot.sent == [(777, "экран меню")]


async def test_safe_edit_swallows_forbidden_error():
    msg = _StubMessage(raises=TelegramForbiddenError(method=None, message="bot was blocked"))
    cb = _StubCallback(msg)

    # Не должно бросить: юзер заблокировал бота — это штатный исход.
    await _safe_edit(cb, "экран меню")

    assert cb.bot.sent == []


async def test_safe_edit_does_not_leak_unexpected_errors():
    msg = _StubMessage(raises=RuntimeError("что-то совсем неожиданное"))
    cb = _StubCallback(msg)

    await _safe_edit(cb, "экран меню")

    # Падение edit_text не должно мешать доставить экран новым сообщением.
    assert cb.bot.sent == [(4242, "экран меню")]
```

- [ ] **Шаг 2: Прогнать тест, убедиться что падает**

Команда: `./scripts/test.sh tests/test_user_safe_edit.py -q`
Ожидается: FAIL — `ImportError: cannot import name '_is_not_modified' from 'bot.handlers.user'`.

- [ ] **Шаг 3: Реализовать**

Заменить строку импорта исключений (`user.py:9`) на:

```python
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramRetryAfter,
)
```

В импорт типов (`user.py:11`) добавить `InaccessibleMessage`:

```python
from aiogram.types import (
    CallbackQuery,
    FSInputFile,
    InaccessibleMessage,
    InputMediaPhoto,
    InputMediaVideo,
    Message,
)
```

Заменить `_safe_edit` (`user.py:60-64`) на:

```python
NOT_MODIFIED_MARKER = "message is not modified"


def _is_not_modified(exc: TelegramBadRequest) -> bool:
    """«Текст и клавиатура уже такие» — это успех, а не ошибка."""
    return NOT_MODIFIED_MARKER in str(exc).lower()


async def _safe_edit(callback: CallbackQuery, text: str, reply_markup=None) -> None:
    """Правит сообщение под кнопкой, а если это невозможно — шлёт новое.

    Прежний фолбэк повторял ТОТ ЖЕ текст через callback.message.answer(): если
    причина отказа была в самой разметке, вторая попытка падала так же, а
    исключение уходило наружу. Плюс callback.message бывает InaccessibleMessage
    или None (сообщение старше 48 часов) — тогда падали обе попытки сразу.
    Поэтому доступность проверяем заранее, а фолбэк шлём через bot.send_message.
    """
    msg = callback.message
    editable = msg is not None and not isinstance(msg, InaccessibleMessage)
    chat_id = msg.chat.id if msg is not None else callback.from_user.id

    if editable:
        try:
            await msg.edit_text(text, reply_markup=reply_markup)
            return
        except TelegramBadRequest as exc:
            if _is_not_modified(exc):
                return
            logger.debug("edit_text отклонён, шлём новым сообщением | error={}", exc)
        except TelegramForbiddenError:
            logger.info("Пользователь заблокировал бота | user={}", callback.from_user.id)
            return
        except Exception as exc:
            logger.warning("edit_text упал неожиданно | error={}", exc)

    try:
        await callback.bot.send_message(
            chat_id=chat_id, text=text, reply_markup=reply_markup
        )
    except TelegramForbiddenError:
        logger.info("Пользователь заблокировал бота | user={}", callback.from_user.id)
    except Exception as exc:
        logger.warning(
            "Не удалось доставить экран | user={} error={}", callback.from_user.id, exc
        )
```

В `handle_url` (код после Task 25) заменить все четыре `except TelegramBadRequest:` вокруг работы со
статус-сообщением на:

```python
        except (TelegramBadRequest, TelegramForbiddenError):
            pass
```

Места: (1) `status_msg.edit_text` в ветке `if not dl_result.success:`; (2) `status_msg.delete()`;
(3) и (4) — `status_msg.edit_text` в ветке ошибки отправки (обе ветки `if/elif/else` накрыты одним
`try`, так что правится один `except`).

И добавить в `handle_url` отдельную ветку для блокировки бота — перед `except Exception as e:`:

```python
    except TelegramForbiddenError as e:
        # Юзер заблокировал бота посреди отправки. Это не сбой: писать ему
        # больше некуда, статус-сообщение править бессмысленно.
        send_error = e
        logger.info("Пользователь заблокировал бота во время отправки | user={}", user_id)
```

- [ ] **Шаг 4: Прогнать тесты**

Команда: `./scripts/test.sh -q`
Ожидается: PASS, регрессий нет.

- [ ] **Шаг 5: Коммит**

```bash
git add bot/handlers/user.py tests/test_user_safe_edit.py
git commit -m "fix(user): make menu edits survive inaccessible messages and blocks"
```

---

### Task 27: Экранирование HTML во всех пользовательских текстах

**Закрывает:** H-13 (пользовательская половина)

**Files:**
- Modify: `bot/handlers/user.py:168-179` (реквизиты), `:182-191` (поддержка), текст ошибки загрузки
  и подписи к медиа
- Test: `tests/test_user_escaping.py`

**Interfaces:**
- Consumes: `bot.utils.text.esc(value) -> str` (создан пакетом C).
- Produces: `_payment_details_text() -> str`, `_support_text() -> str`,
  `_download_failed_text(error_message: str | None) -> str`, `_media_caption(platform: str, kind: str) -> str`.
  Task 29 использует `_media_caption` для подписи к анимации.

**⚠️ Меняет поведение для пользователя — нужна ручная проверка:** экран «💳 Показать реквизиты» и
экран поддержки. Проверить руками с реквизитами, содержащими `&` и `<` (например
`NGUYEN VAN A & CO <VCB>`): экран должен открыться и показать значение как есть, а не упасть.

В `bot/handlers/` ноль вызовов `html.escape`, при этом `bot/__main__.py:26` включает
`parse_mode=HTML` глобально. Латентная денежная ветка: `user.py:168-179` подставляет
`USDT_TRC20_ADDRESS`/`VN_BANK_DETAILS`/`TH_BANK_DETAILS` в HTML сырыми — как только в реквизитах
появится `&` или `<`, кнопка «Показать реквизиты» начнёт падать необработанным исключением у ВСЕХ
пользователей. `ADMIN_USERNAME` (`:188`) подставляется так же сырым.

**Что здесь НЕ экранируется и почему.** `dl_result.error_message` приходит из загрузчика уже
экранированным: `downloader.py:77` оборачивает хвост stderr в `<code>{html.escape(short_err)}</code>`,
`downloader.py:443` прогоняет через `html.escape` текст исключения, а все остальные ветки
`_parse_error` возвращают фиксированные строки без спецсимволов. Второй проход по `esc()` превратил
бы `&lt;` в `&amp;lt;` и показал пользователю мусор. Поэтому `_download_failed_text` только
подставляет значение и подменяет `None` на внятный текст. `platform` берётся из значений
`_DOMAIN_TO_PLATFORM` (фиксированный набор латинских слов), но через `esc()` его всё равно
прогоняем — это бесплатная страховка на случай появления платформы с дефисом или точкой в названии.

- [ ] **Шаг 1: Написать падающий тест**

Создать `tests/test_user_escaping.py`:

```python
from bot.config import settings
from bot.handlers.user import (
    _download_failed_text,
    _media_caption,
    _payment_details_text,
    _support_text,
)


def test_payment_details_escape_ampersand_and_brackets(monkeypatch):
    monkeypatch.setattr(settings, "VN_BANK_DETAILS", "NGUYEN VAN A & CO <VCB>")
    monkeypatch.setattr(settings, "USDT_TRC20_ADDRESS", "T<addr>")
    monkeypatch.setattr(settings, "TH_BANK_DETAILS", "K & Bank")

    text = _payment_details_text()

    assert "NGUYEN VAN A &amp; CO &lt;VCB&gt;" in text
    assert "T&lt;addr&gt;" in text
    assert "K &amp; Bank" in text
    # Сырых спецсимволов из значений в разметке остаться не должно.
    assert "<VCB>" not in text
    assert "& CO" not in text


def test_payment_details_survive_empty_settings(monkeypatch):
    # Дефолты в репозитории пустые — экран обязан открываться и так.
    monkeypatch.setattr(settings, "VN_BANK_DETAILS", "")
    monkeypatch.setattr(settings, "USDT_TRC20_ADDRESS", "")
    monkeypatch.setattr(settings, "TH_BANK_DETAILS", "")

    text = _payment_details_text()

    assert "<code></code>" in text


def test_support_text_escapes_admin_username(monkeypatch):
    monkeypatch.setattr(settings, "ADMIN_USERNAME", "@admin<b>")

    text = _support_text()

    assert "@admin&lt;b&gt;" in text
    assert "@admin<b>" not in text


def test_download_failed_text_does_not_escape_twice():
    # Загрузчик уже экранировал stderr (downloader.py:77, :443).
    already_escaped = "<code>ERROR: &lt;html&gt; not found</code>"

    text = _download_failed_text(already_escaped)

    assert already_escaped in text
    assert "&amp;lt;" not in text


def test_download_failed_text_handles_missing_message():
    text = _download_failed_text(None)

    assert "None" not in text
    assert "Не удалось скачать" in text


def test_media_caption_escapes_platform():
    assert _media_caption("tiktok", "video") == "✅ Видео из Tiktok"
    assert _media_caption("pinterest", "image") == "✅ Фото из Pinterest"
    assert _media_caption("in<s>ta", "video") == "✅ Видео из In&lt;s&gt;ta"
```

- [ ] **Шаг 2: Прогнать тест, убедиться что падает**

Команда: `./scripts/test.sh tests/test_user_escaping.py -q`
Ожидается: FAIL — `ImportError: cannot import name '_payment_details_text' from 'bot.handlers.user'`.

- [ ] **Шаг 3: Реализовать**

Добавить импорт в `bot/handlers/user.py` (к остальным импортам из `bot.`):

```python
from bot.utils.text import esc
```

Добавить четыре функции текста рядом с `_is_admin` (то есть после `user.py:57`):

```python
def _payment_details_text() -> str:
    """Экран реквизитов. Все три значения приходят из .env и экранируются:
    «NGUYEN VAN A & CO» в сыром HTML роняет экран у всех пользователей."""
    return (
        "🏦 <b>Реквизиты для оплаты:</b>\n\n"
        f"💎 <b>USDT (TRC20):</b>\n<code>{esc(settings.USDT_TRC20_ADDRESS)}</code>\n\n"
        f"🇻🇳 <b>VN Bank:</b>\n<code>{esc(settings.VN_BANK_DETAILS)}</code>\n\n"
        f"🇹🇭 <b>TH Bank:</b>\n<code>{esc(settings.TH_BANK_DETAILS)}</code>\n\n"
        "📩 После оплаты отправь скриншот администратору 👇"
    )


def _support_text() -> str:
    return (
        "✉️ <b>Связь с администратором</b>\n\n"
        f"Напиши администратору: {esc(settings.ADMIN_USERNAME)}\n\n"
        "Отправь ему скриншот оплаты или опиши проблему."
    )


def _download_failed_text(error_message: str | None) -> str:
    """error_message приходит из загрузчика УЖЕ экранированным
    (downloader.py:77 и :443 прогоняют текст через html.escape).
    Экранировать второй раз нельзя — пользователь увидит «&amp;lt;»."""
    return f"❌ <b>Не удалось скачать</b>\n{error_message or 'Причина неизвестна'}"


def _media_caption(platform: str, kind: str) -> str:
    """Подпись к отправляемому медиа. kind: "video" | "image" | "animation" | "album"."""
    titles = {"video": "Видео", "image": "Фото", "animation": "GIF", "album": "Медиа"}
    return f"✅ {titles.get(kind, 'Медиа')} из {esc(platform.capitalize())}"
```

Заменить тело `cb_pay_details` (`user.py:171-179`) на:

```python
    await _safe_edit(
        callback,
        _payment_details_text(),
        reply_markup=get_payment_details_kb(is_admin=_is_admin(callback.from_user.id)),
    )
```

Заменить тело `cb_support` (`user.py:185-191`) на:

```python
    await _safe_edit(
        callback,
        _support_text(),
        reply_markup=get_back_to_menu_kb(is_admin=_is_admin(callback.from_user.id)),
    )
```

В `handle_url` (код после Task 26) заменить построение текста ошибки загрузки на
`await status_msg.edit_text(_download_failed_text(dl_result.error_message))`, а все
подписи к медиа — на `_media_caption(...)`:

- `caption = f"✅ Медиа из {platform.capitalize()}"` → `caption = _media_caption(platform, "album")`
- подпись фото-документа и фото → `_media_caption(platform, "image")`
- подпись видео → `_media_caption(platform, "video")`

- [ ] **Шаг 4: Прогнать тесты**

Команда: `./scripts/test.sh -q`
Ожидается: PASS, регрессий нет.

- [ ] **Шаг 5: Коммит**

```bash
git add bot/handlers/user.py tests/test_user_escaping.py
git commit -m "fix(user): escape payment details and admin handle in HTML texts"
```

---

### Task 28: Честные тексты про квоту и скорость, ссылка в подписи к фото

**Закрывает:** M-29, M-30, M-2, Low «обещания скорости против реальных таймаутов»

**Files:**
- Modify: `bot/handlers/user.py:42-53` (`WELCOME_TEXT` → функции), `:98-103` (`cmd_start`),
  `:106-111` (`cb_main_menu`), `:128-149` (`cb_status`), `:194-206` (`cb_help`), `:209` (фильтр
  хендлера), статус-сообщение «Это займёт несколько секунд»
- Test: `tests/test_user_texts.py`

**Interfaces:**
- Consumes: `bot.db.queries.get_or_create_user`, `bot.config.settings.FREE_DOWNLOADS`.
- Produces: `_quota_line(free_left: int, has_subscription: bool) -> str`,
  `_welcome_text(free_left: int, has_subscription: bool) -> str`,
  `_help_text(free_left: int, has_subscription: bool) -> str`,
  `_status_text(free_left: int, has_subscription: bool, sub_until, total_downloads: int) -> str`,
  `_user_quota_state(tg_user) -> tuple[int, bool]`, `_incoming_text(message) -> str`.
  Константа `WELCOME_TEXT` удаляется.

**⚠️ Меняет поведение для пользователя — нужна ручная проверка:** меняются четыре экрана и один
фильтр. Проверить руками: (1) `/start` у нового пользователя — «Осталось бесплатных: 3 из 3»;
(2) `/start` у исчерпавшего квоту — текст про то, что бесплатные закончились, без цифры 3;
(3) `/start` у подписчика — строка про активную подписку; (4) «🏠 Главное меню» показывает то же,
что `/start`; (5) «📖 Помощь» — без обещания «за секунды»; (6) «📊 Мой статус» — формулировка
«Осталось бесплатных: N из M»; (7) фото с ссылкой в подписи — бот качает; (8) фото БЕЗ ссылки — бот
молчит, не спамит меню.

`user.py:51` жёстко зашивает «🎁 У тебя <b>3 бесплатных</b> скачивания», игнорируя и
`settings.FREE_DOWNLOADS`, и фактический остаток; текст показывается на каждом `/start` и при каждом
возврате в главное меню (`:111`). Исчерпавший квоту после пейволла жмёт «🏠 Главное меню» и читает,
что у него 3 бесплатных; платящий подписчик читает то же. Вторая копия в «Помощи» (`:204`).
Формулировка `:145` «Бесплатных скачиваний: 3/3» читается как «использовано 3 из 3» и уже породила
ложный инцидент. `user.py:256` обещает «Это займёт несколько секунд», `:202` — «Получи видео за
секунды!» при `DOWNLOAD_TIMEOUT=900` и потолке 1500 МБ.

**Решение по `WELCOME_TEXT`.** Константа превращается в функцию `_welcome_text(free_left,
has_subscription)`, а `cb_main_menu` начинает открывать db-сессию, которой у него сейчас нет. Это
осознанный размен: один `SELECT` по первичному ключу на нажатие «Главное меню» против экрана,
который врёт. Цена мала — `cb_status` уже делает ровно такой же запрос на соседней кнопке, а
`cmd_start` и так вызывает `get_or_create_user`. Альтернативу «оставить константу и дописывать
остаток отдельной строкой» отвергаем: пользователь читает первое числительное на экране, и именно
оно должно быть верным.

**Решение по M-2.** Фильтр `F.text` меняется на `F.text | F.caption`, текст берётся из
`_incoming_text`. Чтобы бот не отвечал главным меню на каждое пересланное изображение, ветка «ссылки
нет» отвечает только на текстовые сообщения; фото без ссылки и не в режиме ожидания игнорируется
молча.

- [ ] **Шаг 1: Написать падающий тест**

Создать `tests/test_user_texts.py`:

```python
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from bot.config import settings
from bot.handlers.user import (
    _help_text,
    _incoming_text,
    _quota_line,
    _status_text,
    _welcome_text,
)


def test_quota_line_reads_as_remainder_not_usage():
    # «Бесплатных скачиваний: 3/3» читалось как «использовано 3 из 3».
    line = _quota_line(3, has_subscription=False)

    assert "Осталось бесплатных" in line
    assert f"3 из {settings.FREE_DOWNLOADS}" in line
    assert "3/3" not in line


def test_quota_line_for_exhausted_user_promises_nothing():
    line = _quota_line(0, has_subscription=False)

    assert "Осталось бесплатных" not in line
    assert "закончились" in line


def test_quota_line_for_subscriber_ignores_free_counter():
    line = _quota_line(0, has_subscription=True)

    assert "Подписка активна" in line
    assert "бесплатн" not in line.lower()


def test_welcome_shows_actual_remainder_not_hardcoded_three():
    text = _welcome_text(1, has_subscription=False)

    assert f"1 из {settings.FREE_DOWNLOADS}" in text
    assert "<b>3 бесплатных</b>" not in text


def test_welcome_and_help_do_not_promise_seconds():
    for text in (
        _welcome_text(3, False),
        _welcome_text(0, True),
        _help_text(3, False),
        _help_text(0, True),
    ):
        assert "за секунды" not in text
        assert "несколько секунд" not in text


def test_status_text_uses_remainder_wording():
    text = _status_text(2, False, None, 7)

    assert f"Осталось бесплатных: <b>2 из {settings.FREE_DOWNLOADS}</b>" in text
    assert "❌ Не активна" in text
    assert "<b>7</b>" in text


def test_status_text_renders_active_subscription_date():
    until = datetime.now(timezone.utc) + timedelta(days=3)

    text = _status_text(0, True, until, 0)

    assert until.strftime("%d.%m.%Y") in text


def test_incoming_text_prefers_text_then_caption():
    assert _incoming_text(SimpleNamespace(text="привет", caption=None)) == "привет"
    assert (
        _incoming_text(SimpleNamespace(text=None, caption="смотри https://vt.tiktok.com/abc"))
        == "смотри https://vt.tiktok.com/abc"
    )
    assert _incoming_text(SimpleNamespace(text=None, caption=None)) == ""
```

- [ ] **Шаг 2: Прогнать тест, убедиться что падает**

Команда: `./scripts/test.sh tests/test_user_texts.py -q`
Ожидается: FAIL — `ImportError: cannot import name '_help_text' from 'bot.handlers.user'`.

- [ ] **Шаг 3: Реализовать**

Удалить константу `WELCOME_TEXT` (`user.py:42-53`) и добавить на её место:

```python
def _quota_line(free_left: int, has_subscription: bool) -> str:
    """Одна строка о правах пользователя. Раньше здесь была захардкоженная
    тройка, которую читали и исчерпавший квоту, и платящий подписчик."""
    if has_subscription:
        return "👑 Подписка активна — скачивай без ограничений."
    if free_left > 0:
        return f"🎁 Осталось бесплатных: <b>{free_left} из {settings.FREE_DOWNLOADS}</b>."
    return "🚫 Бесплатные скачивания закончились — нужна подписка."


def _welcome_text(free_left: int, has_subscription: bool) -> str:
    return (
        "👋 <b>Добро пожаловать!</b>\n\n"
        "Я — бот для скачивания видео из соцсетей.\n\n"
        "🌐 <b>Поддерживаемые платформы:</b>\n"
        "├ 📸 Instagram\n"
        "├ 🎵 TikTok\n"
        "├ 📘 Facebook\n"
        "├ 📌 Pinterest\n"
        "└ 📺 YouTube\n\n"
        f"{_quota_line(free_left, has_subscription)}\n\n"
        "Выбери действие 👇"
    )


def _help_text(free_left: int, has_subscription: bool) -> str:
    return (
        "📖 <b>Как пользоваться ботом:</b>\n\n"
        "1️⃣ Нажми <b>«Скачать видео»</b>\n"
        "2️⃣ Отправь ссылку на видео\n"
        "3️⃣ Дождись файла — длинное видео в высоком качестве качается минутами\n\n"
        "🌐 <b>Платформы:</b> Instagram, TikTok, Facebook, Pinterest, YouTube\n\n"
        f"{_quota_line(free_left, has_subscription)}"
    )


def _status_text(
    free_left: int, has_subscription: bool, sub_until, total_downloads: int
) -> str:
    if has_subscription and sub_until is not None:
        sub_text = "✅ до " + sub_until.strftime("%d.%m.%Y")
    else:
        sub_text = "❌ Не активна"
    return (
        f"📊 <b>Твой профиль:</b>\n\n"
        f"🎟 Осталось бесплатных: <b>{free_left} из {settings.FREE_DOWNLOADS}</b>\n"
        f"👑 Подписка: <b>{sub_text}</b>\n"
        f"📥 Всего скачано: <b>{total_downloads}</b>"
    )


def _incoming_text(message) -> str:
    """Текст сообщения или подпись к медиа. Раньше хендлер смотрел только
    в .text, поэтому бот молчал на фото со ссылкой в подписи."""
    return message.text or message.caption or ""
```

Добавить загрузчик состояния рядом с `_reserve_quota`:

```python
async def _user_quota_state(tg_user) -> tuple[int, bool]:
    """(остаток бесплатных, активна ли подписка) — одна короткая транзакция."""
    async with async_session() as session, session.begin():
        user = await get_or_create_user(session, tg_user)
        sub_until = user.subscription_until
        if sub_until and sub_until.tzinfo is None:
            sub_until = sub_until.replace(tzinfo=timezone.utc)
        has_subscription = bool(sub_until and sub_until > datetime.now(timezone.utc))
        return user.free_downloads_left, has_subscription
```

Заменить `cmd_start` (`user.py:98-103`):

```python
@router.message(Command("start"))
async def cmd_start(message: Message) -> None:
    free_left, has_subscription = await _user_quota_state(message.from_user)
    is_admin = _is_admin(message.from_user.id)
    await message.answer(
        _welcome_text(free_left, has_subscription),
        reply_markup=get_main_menu_kb(is_admin=is_admin),
    )
```

Заменить `cb_main_menu` (`user.py:106-111`):

```python
@router.callback_query(F.data == "menu:main")
async def cb_main_menu(callback: CallbackQuery) -> None:
    await callback.answer()
    waiting_for_url.discard(callback.from_user.id)
    free_left, has_subscription = await _user_quota_state(callback.from_user)
    is_admin = _is_admin(callback.from_user.id)
    await _safe_edit(
        callback,
        _welcome_text(free_left, has_subscription),
        reply_markup=get_main_menu_kb(is_admin=is_admin),
    )
```

Заменить `cb_status` (`user.py:128-149`):

```python
@router.callback_query(F.data == "menu:status")
async def cb_status(callback: CallbackQuery) -> None:
    await callback.answer()
    async with async_session() as session, session.begin():
        user = await get_or_create_user(session, callback.from_user)
        free_left = user.free_downloads_left
        total_downloads = user.total_downloads
        sub_until = user.subscription_until

    if sub_until and sub_until.tzinfo is None:
        sub_until = sub_until.replace(tzinfo=timezone.utc)
    has_subscription = bool(sub_until and sub_until > datetime.now(timezone.utc))

    await _safe_edit(
        callback,
        _status_text(free_left, has_subscription, sub_until, total_downloads),
        reply_markup=get_status_kb(is_admin=_is_admin(callback.from_user.id)),
    )
```

Заменить `cb_help` (`user.py:194-206`):

```python
@router.callback_query(F.data == "menu:help")
async def cb_help(callback: CallbackQuery) -> None:
    await callback.answer()
    free_left, has_subscription = await _user_quota_state(callback.from_user)
    await _safe_edit(
        callback,
        _help_text(free_left, has_subscription),
        reply_markup=get_help_kb(is_admin=_is_admin(callback.from_user.id)),
    )
```

Заменить декоратор и начало `handle_url` (`user.py:209-223`):

```python
@router.message(F.text | F.caption)
async def handle_url(message: Message) -> None:
    user_id = message.from_user.id
    result = parse_url(_incoming_text(message))

    if result is None and user_id in waiting_for_url:
        await message.answer(
            "🔗 Это не похоже на ссылку. Отправь ссылку из Instagram, TikTok, "
            "Facebook, Pinterest или YouTube.",
            reply_markup=get_back_to_menu_kb(is_admin=_is_admin(user_id)),
        )
        return

    if result is None:
        if message.text is None:
            # Медиа без ссылки в подписи — молчим, чтобы не отвечать меню на
            # каждое пересланное изображение.
            return
        await message.answer(
            "Выбери действие 👇",
            reply_markup=get_main_menu_kb(is_admin=_is_admin(user_id)),
        )
        return
```

Заменить текст статус-сообщения (в коде после Task 25):

```python
    status_msg = await message.reply(
        "⏳ <b>Скачиваю медиа...</b>\nБольшой файл может качаться несколько минут"
    )
```

- [ ] **Шаг 4: Прогнать тесты**

Команда: `./scripts/test.sh -q`
Ожидается: PASS, регрессий нет.

- [ ] **Шаг 5: Коммит**

```bash
git add bot/handlers/user.py tests/test_user_texts.py
git commit -m "fix(user): report real free-download balance and accept links in captions"
```

---

### Task 29: `.gif` как анимация, лимит слотов на пользователя, гигиена `waiting_for_url`

**Закрывает:** M-17 (отправляющая половина), M-28, Low «`waiting_for_url` растёт неограниченно»

**Files:**
- Modify: `bot/handlers/user.py:32` (импорт `ANIMATION_EXTS`, `IMAGE_EXTS`), `:36-37` (слоты и
  множество ожидающих), `:114-125` (`cb_download`), блок отправки в `handle_url`
- Test: `tests/test_user_media.py`

**Interfaces:**
- Consumes: `bot.services.downloader.ANIMATION_EXTS`, `bot.services.downloader.IMAGE_EXTS`,
  `DownloadResult.media_type in {"video", "image", "animation"}` (всё из пакета B);
  `_media_caption` из Task 27.
- Produces: `_classify(path_str: str) -> str`, `_try_take_user_slot(user_id: int) -> bool`,
  `_release_user_slot(user_id: int) -> None`, `_mark_waiting(user_id: int) -> None`,
  `_is_waiting(user_id: int) -> bool`, `_process_download(message, url, platform) -> None`.
  `waiting_for_url` меняет тип с `set[int]` на `dict[int, float]`.

**⚠️ Меняет поведение для пользователя — нужна ручная проверка:** (1) прислать ссылку на Pinterest с
`.gif` — должна прийти анимация, подписанная «✅ GIF из …», а не статичный кадр «✅ Фото»;
(2) прислать вторую ссылку, не дождавшись первой — должен прийти внятный отказ, а не тихое занятие
второго слота; (3) нажать «📥 Скачать видео», уйти и вернуться через час — бот не должен считать,
что всё ещё ждёт ссылку.

**(а) `.gif`.** `user.py:297` включает `gif` в набор изображений; `sendPhoto`/`InputMediaPhoto`
сохраняет статичный кадр, а подпись говорит «✅ Фото». Правильный метод — `sendAnimation`. Учитываем
ограничение Telegram: анимацию нельзя класть в media group вместе с фото и видео — группа будет
отклонена целиком. Поэтому анимации выносим из альбома отдельными отправками, ровно тем же
механизмом, которым уже выносятся крупные изображения (`user.py:294`, `:342-345`). Заодно
классификация переезжает с локального кортежа расширений без точки на общие множества из
`bot/services/downloader.py` — разъезд двух списков расширений и был причиной того, что `.gif`
лечится в двух местах.

**(б) Слоты.** `download_semaphore = asyncio.Semaphore(3)` (`user.py:36`) общий, без per-user
лимита; троттл 3 с (`throttle.py:10`) позволяет одному пользователю занять все три слота за девять
секунд на всю длину загрузки (до `DOWNLOAD_TIMEOUT=900`), остальные висят на «⏳ Скачиваю медиа...»
до пятнадцати минут. Ставим `MAX_CONCURRENT_PER_USER = 1`: одна загрузка на пользователя
одновременно. Счётчик берётся ДО резервирования квоты, чтобы отказ не стоил пользователю единицы, и
освобождается в `finally` — для этого тело хендлера выносится в `_process_download`.

**(в) `waiting_for_url`.** Множество (`user.py:37`) пополняется на `:117`, вычищается только на
`:109`/`:226`: кто нажал «Скачать видео» и ушёл, остаётся в нём до конца жизни процесса. Переводим
на словарь с отметкой времени и вытеснением по возрасту — тем же приёмом, что уже применён в
`bot/middlewares/throttle.py:28-34`.

**Что в этом пакете НЕ чинится.** Low-находка «нулевые и обрезанные файлы не подчищаются на успешном
пути» (`user.py:419-423` удаляет только `dl_result.file_paths`, а `_cleanup_glob` на успехе не
зовётся) здесь не лечится сознательно. Она закрывается структурно — Task 5 плана
`docs/superpowers/plans/2026-09-03-local-bot-api.md` даёт каждой загрузке собственную рабочую папку,
удаляемую целиком в `finally`. Введение здесь отдельного поля с префиксом файлов в `DownloadResult`
создало бы прямой конфликт с той задачей и было бы выброшено через неделю.

- [ ] **Шаг 1: Написать падающий тест**

Создать `tests/test_user_media.py`:

```python
from types import SimpleNamespace

from bot.handlers.user import (
    MAX_CONCURRENT_PER_USER,
    WAITING_TTL,
    _classify,
    _is_waiting,
    _mark_waiting,
    _release_user_slot,
    _try_take_user_slot,
    user_active_downloads,
    waiting_for_url,
)


# ── классификация файлов ──


def test_gif_is_classified_as_animation():
    assert _classify("/tmp/jw_downloads/abc_1.gif") == "animation"
    assert _classify("/tmp/jw_downloads/abc_1.GIF") == "animation"


def test_still_images_stay_images():
    for name in ("a.jpg", "a.jpeg", "a.png", "a.webp", "a.heic"):
        assert _classify(f"/tmp/jw_downloads/{name}") == "image"


def test_everything_else_is_video():
    assert _classify("/tmp/jw_downloads/a.mp4") == "video"
    assert _classify("/tmp/jw_downloads/a.mkv") == "video"
    assert _classify("/tmp/jw_downloads/noextension") == "video"


# ── per-user лимит слотов ──


def test_second_parallel_download_of_same_user_is_refused():
    uid = 990001
    try:
        assert MAX_CONCURRENT_PER_USER == 1
        assert _try_take_user_slot(uid) is True
        assert _try_take_user_slot(uid) is False
        _release_user_slot(uid)
        assert _try_take_user_slot(uid) is True
    finally:
        user_active_downloads.pop(uid, None)


def test_other_users_are_not_blocked():
    a, b = 990002, 990003
    try:
        assert _try_take_user_slot(a) is True
        assert _try_take_user_slot(b) is True
    finally:
        user_active_downloads.pop(a, None)
        user_active_downloads.pop(b, None)


def test_released_slot_leaves_no_garbage():
    uid = 990004
    _try_take_user_slot(uid)
    _release_user_slot(uid)
    assert uid not in user_active_downloads


# ── гигиена множества ожидающих ссылку ──


def test_waiting_entry_expires_by_age(monkeypatch):
    clock = {"now": 1000.0}
    monkeypatch.setattr(
        "bot.handlers.user.time", SimpleNamespace(monotonic=lambda: clock["now"])
    )
    waiting_for_url.clear()
    uid = 990005

    _mark_waiting(uid)
    assert _is_waiting(uid) is True

    clock["now"] += WAITING_TTL + 1
    assert _is_waiting(uid) is False
    assert uid not in waiting_for_url


def test_stale_entries_are_evicted_on_new_marks(monkeypatch):
    clock = {"now": 0.0}
    monkeypatch.setattr(
        "bot.handlers.user.time", SimpleNamespace(monotonic=lambda: clock["now"])
    )
    waiting_for_url.clear()

    _mark_waiting(990006)
    clock["now"] = WAITING_TTL + 10
    _mark_waiting(990007)

    assert 990006 not in waiting_for_url
    assert 990007 in waiting_for_url
```

- [ ] **Шаг 2: Прогнать тест, убедиться что падает**

Команда: `./scripts/test.sh tests/test_user_media.py -q`
Ожидается: FAIL — `ImportError: cannot import name 'MAX_CONCURRENT_PER_USER' from 'bot.handlers.user'`.

- [ ] **Шаг 3: Реализовать**

Добавить импорты в `bot/handlers/user.py`:

```python
import time
from pathlib import Path
```

и расширить импорт из загрузчика (`user.py:32`):

```python
from bot.services.downloader import ANIMATION_EXTS, IMAGE_EXTS, download_media
```

Заменить строки состояния модуля (`user.py:36-37`) на:

```python
download_semaphore = asyncio.Semaphore(3)

# Один пользователь — одна загрузка одновременно. Общего семафора на три слота
# мало: троттл в 3 с позволяет занять все три за девять секунд на всю длину
# загрузки (до DOWNLOAD_TIMEOUT), и остальные висят на «Скачиваю медиа...».
MAX_CONCURRENT_PER_USER = 1
user_active_downloads: dict[int, int] = {}

# Кто нажал «Скачать видео» и ещё не прислал ссылку. Раньше был set, который
# рос до конца жизни процесса: ушедшего пользователя из него ничто не убирало.
WAITING_TTL = 3600.0
waiting_for_url: dict[int, float] = {}
```

Добавить помощники рядом с `_is_admin`:

```python
def _classify(path_str: str) -> str:
    """"animation" | "image" | "video" по расширению файла.

    Расширения берутся из bot/services/downloader.py: раньше здесь был свой
    кортеж без точек, и он разъехался с загрузчиком на .gif.
    """
    ext = Path(path_str).suffix.lower()
    if ext in ANIMATION_EXTS:
        return "animation"
    if ext in IMAGE_EXTS:
        return "image"
    return "video"


def _try_take_user_slot(user_id: int) -> bool:
    """Занимает слот загрузки. False — у пользователя уже идёт загрузка."""
    if user_active_downloads.get(user_id, 0) >= MAX_CONCURRENT_PER_USER:
        return False
    user_active_downloads[user_id] = user_active_downloads.get(user_id, 0) + 1
    return True


def _release_user_slot(user_id: int) -> None:
    left = user_active_downloads.get(user_id, 0) - 1
    if left > 0:
        user_active_downloads[user_id] = left
    else:
        user_active_downloads.pop(user_id, None)


def _mark_waiting(user_id: int) -> None:
    """Отмечает ожидание ссылки и попутно вытесняет протухшие записи
    (тот же приём, что в bot/middlewares/throttle.py:28-34)."""
    now = time.monotonic()
    waiting_for_url[user_id] = now
    stale = [uid for uid, ts in waiting_for_url.items() if (now - ts) > WAITING_TTL]
    for uid in stale:
        del waiting_for_url[uid]


def _is_waiting(user_id: int) -> bool:
    ts = waiting_for_url.get(user_id)
    if ts is None:
        return False
    if (time.monotonic() - ts) > WAITING_TTL:
        del waiting_for_url[user_id]
        return False
    return True
```

Заменить `waiting_for_url.add(callback.from_user.id)` в `cb_download` (`user.py:117`) на
`_mark_waiting(callback.from_user.id)`, а оба `waiting_for_url.discard(...)` (`user.py:109` в
`cb_main_menu` и `:226` в `handle_url`) — на `waiting_for_url.pop(<id>, None)`. В `handle_url`
проверку `user_id in waiting_for_url` заменить на `_is_waiting(user_id)`.

Разделить `handle_url` на приёмник и тело:

```python
@router.message(F.text | F.caption)
async def handle_url(message: Message) -> None:
    user_id = message.from_user.id
    result = parse_url(_incoming_text(message))

    if result is None and _is_waiting(user_id):
        await message.answer(
            "🔗 Это не похоже на ссылку. Отправь ссылку из Instagram, TikTok, "
            "Facebook, Pinterest или YouTube.",
            reply_markup=get_back_to_menu_kb(is_admin=_is_admin(user_id)),
        )
        return

    if result is None:
        if message.text is None:
            return
        await message.answer(
            "Выбери действие 👇",
            reply_markup=get_main_menu_kb(is_admin=_is_admin(user_id)),
        )
        return

    url, platform = result
    waiting_for_url.pop(user_id, None)

    # Слот берём ДО резервирования квоты: отказ не должен стоить единицы.
    if not _try_take_user_slot(user_id):
        await message.reply(
            "⏳ Я ещё качаю твою предыдущую ссылку. Дождись её и пришли следующую."
        )
        return
    try:
        await _process_download(message, url, platform)
    finally:
        _release_user_slot(user_id)


async def _process_download(message: Message, url: str, platform: str) -> None:
```

Тело `_process_download` — это код от `# C-1 шаг 1` до конца функции, написанный в Task 25 и
поправленный в Task 26–28, без изменений, кроме блока отправки ниже. Первой строкой тела добавить
`user_id = message.from_user.id`, потому что переменная осталась в приёмнике.

Заменить блок отправки (ветка `if dl_result.file_paths and len(dl_result.file_paths) > 1:` целиком)
на:

```python
        if dl_result.file_paths and len(dl_result.file_paths) > 1:
            # Карусель — media group чанками по MEDIA_GROUP_CHUNK_SIZE.
            # Вне альбома идут: анимации (Telegram не смешивает их с фото и
            # видео в одной группе) и изображения тяжелее 10 МБ (их не примут
            # как photo).
            caption = _media_caption(platform, "album")
            total = len(dl_result.file_paths)
            first_media_captioned = False
            for chunk_start in range(0, total, MEDIA_GROUP_CHUNK_SIZE):
                chunk = dl_result.file_paths[chunk_start:chunk_start + MEDIA_GROUP_CHUNK_SIZE]
                sendable: list[tuple[str, str]] = []
                standalone: list[tuple[str, str]] = []
                for path_str in chunk:
                    kind = _classify(path_str)
                    try:
                        size_mb = os.path.getsize(path_str) / (1024 * 1024)
                    except OSError:
                        size_mb = 0
                    if kind == "animation":
                        standalone.append((path_str, "animation"))
                        continue
                    if kind == "image" and size_mb > 10:
                        standalone.append((path_str, "document"))
                        continue
                    sendable.append((path_str, kind))

                if len(sendable) == 1:
                    # Telegram отклоняет альбом не из 2–10 элементов.
                    path_str, kind = sendable[0]
                    f = FSInputFile(path_str)
                    cap = caption if not first_media_captioned else None
                    if kind == "image":
                        await _send_with_retry(
                            lambda ff=f, c=cap: message.reply_photo(photo=ff, caption=c)
                        )
                    else:
                        await _send_with_retry(
                            lambda ff=f, c=cap: message.reply_video(video=ff, caption=c)
                        )
                    first_media_captioned = True
                    media_sent_count += 1
                elif len(sendable) >= 2:
                    media_group = []
                    for path_str, kind in sendable:
                        f = FSInputFile(path_str)
                        cap = caption if not first_media_captioned else None
                        if kind == "image":
                            media_group.append(InputMediaPhoto(media=f, caption=cap))
                        else:
                            media_group.append(InputMediaVideo(media=f, caption=cap))
                        first_media_captioned = True
                    await _send_with_retry(
                        lambda mg=media_group: message.reply_media_group(media=mg)
                    )
                    media_sent_count += len(media_group)

                for path_str, kind in standalone:
                    f = FSInputFile(path_str)
                    cap = caption if not first_media_captioned else None
                    if kind == "animation":
                        await _send_with_retry(
                            lambda ff=f, c=cap: message.reply_animation(animation=ff, caption=c)
                        )
                    else:
                        await _send_with_retry(
                            lambda ff=f, c=cap: message.reply_document(document=ff, caption=c)
                        )
                    first_media_captioned = True
                    media_sent_count += 1
```

и одиночную ветку — на:

```python
        else:
            media = FSInputFile(dl_result.file_path)
            if dl_result.media_type == "animation":
                await _send_with_retry(
                    lambda: message.reply_animation(
                        animation=media, caption=_media_caption(platform, "animation")
                    )
                )
            elif dl_result.media_type == "image":
                if dl_result.file_size_mb and dl_result.file_size_mb > 10:
                    await _send_with_retry(
                        lambda: message.reply_document(
                            document=media, caption=_media_caption(platform, "image")
                        )
                    )
                else:
                    await _send_with_retry(
                        lambda: message.reply_photo(
                            photo=media, caption=_media_caption(platform, "image")
                        )
                    )
            else:
                await _send_with_retry(
                    lambda: message.reply_video(
                        video=media, caption=_media_caption(platform, "video")
                    )
                )
            media_sent_count = 1
```

- [ ] **Шаг 4: Прогнать тесты**

Команда: `./scripts/test.sh -q`
Ожидается: PASS, регрессий нет.

- [ ] **Шаг 5: Коммит**

```bash
git add bot/handlers/user.py tests/test_user_media.py
git commit -m "fix(user): send gifs as animation, cap per-user downloads, expire url waiters"
```
## Пакет E — Админка (ветка `feat/local-bot-api`)

Пакет владеет файлами `bot/handlers/admin.py`, `bot/keyboards/inline.py` и своими тестовыми файлами (`tests/test_admin_card.py`, `tests/test_admin_sub_actions.py`, `tests/test_admin_missing_user.py`, `tests/test_admin_routing.py`). Идёт в третьей волне, параллельно с пакетом D, после того как A, B, C и F смержены. `tests/conftest.py` пакет E **не редактирует** — фикстуру `db_session` он только потребляет, а стабы объявляет прямо в своих тестовых файлах.

> **Предусловие к порядку задач.** Пакет C заменяет `update_subscription` на `apply_subscription_change`. `bot/handlers/admin.py:10` импортирует `update_subscription` по имени: как только пакет C удалит эту функцию, модуль перестанет импортироваться и **все** тесты пакета E упадут на `ImportError` ещё до первого ассерта. Поэтому пакет C её **не удаляет**: Task 19 только помечает её устаревшей комментарием, а фактическое удаление вынесено в Task 37, которая выполняется после всех волн. Отдельных действий от пакета E это не требует — важно лишь не «прибираться» в `bot/db/queries.py` по ходу дела: этот файл принадлежит пакету C.

> **Что в этом пакете трогать нельзя.** Ревизия отдельно подтвердила, что авторизация админки закрыта: все восемь точек входа сверяют `from_user.id` с `ADMIN_ID` серверно и **до** разбора `callback_data`; `ADMIN_ID` не имеет дефолта; `_maybe_admin_row` fail-closed; порядок роутеров фильтры не перекрывает. Каждая задача пакета обязана это сохранить — проверка прав остаётся первым, что делает хендлер, и ни одна правка не переносит её после `callback.data.split(":")`.

---

### Task 30: Экранирование карточки, таймзона дат, живучий `_safe_edit`

**Закрывает:** H-13 (админская половина), H-2, H-3 (половина `admin.py`), Low «Даты подписки показываются как naive UTC без пометки», Low «`admin.py:88-99` делает `await message.answer(...)` внутри открытой `session.begin()`»

**Files:**
- Modify: `bot/handlers/admin.py:1-14` (импорты), `:21-33` (`_user_card_text`), `:55-59` (`_safe_edit`), `:83-104` (`admin_text_handler`)
- Test: `tests/test_admin_card.py`

**Interfaces:**
- Consumes: `esc(value) -> str` из `bot/utils/text.py` (создан пакетом C); `get_user_by_id`, `get_user_by_username` из `bot/db/queries.py`.
- Produces: `_user_card_text(user) -> str` — текст карточки со всеми подстановками, пропущенными через `esc()`; `_format_dt(value: datetime | None) -> str` — дата в UTC с явной пометкой зоны, `"❌ Нет"` для `None`; `_safe_edit(callback, text, reply_markup=None) -> None` — правка сообщения, которая никогда не бросает; `logger` из loguru, импортированный в модуль (Task 31 пишет им журнал операций).

**⚠️ Меняет поведение для пользователя — нужна ручная проверка:** в карточке пользователя дата подписки теперь печатается как `30.09.2026 20:00 UTC` (раньше — `30.09.2026 20:00` без зоны). Проверить руками: открыть карточку пользователя с активной подпиской и убедиться, что дата совпадает с той, что видит сам пользователь в «📊 Мой статус», и что пометка `UTC` на месте.

Сейчас `_user_card_text` (`admin.py:21-33`, строка 27 и 28) подставляет `username` и `full_name` в HTML сырыми при глобальном `parse_mode=HTML` (`bot/__main__.py:26`). Пользователь с именем `Ann <3` ломает разметку: `admin.py:104` зовёт `message.answer(_user_card_text(user))` вообще без гарда, Telegram отвечает `can't parse entities`, исключение уходит наружу, админ не получает ничего, а `admin_waiting_search` уже очищен на `:85` — карточку не открыть. **Важно для исполнителя: небанимым это пользователя НЕ делает.** Если карточка уже открыта, `cb_ban` (`admin.py:168-179`) сначала коммитит `toggle_ban`, отдаёт toast `callback.answer("🔴 Заблокирован")` без `parse_mode` (он проходит), и только потом падает на `_safe_edit`. Бан применяется, админ уведомление видит. Ломается путь поиска и просмотра, а не бана — не чините несуществующее.

Второй дефект той же строки — `_safe_edit` (`admin.py:55-59`): он ловит только `TelegramBadRequest` и повторяет **тот же самый текст** через `.answer()`. Если причина отказа в самой разметке, вторая попытка падает так же и исключение уходит необработанным. Плюс `callback.message` у сообщения старше 48 часов приходит как `InaccessibleMessage`, у которого метода `edit_text` нет вовсе — это `AttributeError`, а не `TelegramBadRequest`, и текущий `except` его не ловит. Третий дефект — `admin.py:22` печатает `subscription_until` через `strftime` без приведения таймзоны, в отличие от `user.py:135-136`: арифметика продления корректна (подтверждено ревизией), дефект только в отображении. Четвёртый — `admin.py:88-99` делает `await message.answer(...)` внутри открытой `session.begin()`, то есть сетевой round-trip с удерживаемым соединением SQLite; ветка «неверный формат» выносится из транзакции.

- [ ] **Шаг 1: Написать падающий тест**

Создать `tests/test_admin_card.py`:

```python
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import Chat, InaccessibleMessage

from bot.handlers.admin import _format_dt, _safe_edit, _user_card_text


def _fake_user(**overrides):
    """Минимальный дублёр строки users: только поля, которые читает карточка."""
    data = dict(
        id=1000000001,
        username="ivan",
        full_name="Ivan Petrov",
        free_downloads_left=2,
        subscription_until=None,
        total_downloads=5,
        is_banned=False,
    )
    data.update(overrides)
    return SimpleNamespace(**data)


# ── экранирование ──


def test_card_escapes_angle_brackets_in_full_name():
    text = _user_card_text(_fake_user(full_name="Ann <3"))
    assert "Ann &lt;3" in text
    assert "Ann <3" not in text


def test_card_escapes_ampersand_in_full_name():
    # Латентная денежная ветка того же класса: реквизиты вида «NGUYEN VAN A & CO».
    text = _user_card_text(_fake_user(full_name="NGUYEN VAN A & CO"))
    assert "A &amp; CO" in text


def test_card_escapes_username():
    text = _user_card_text(_fake_user(username="a<b>c"))
    assert "a&lt;b&gt;c" in text
    assert "<b>c" not in text


def test_card_keeps_its_own_markup():
    text = _user_card_text(_fake_user())
    assert text.startswith("👤 <b>Карточка пользователя</b>")
    assert "<code>1000000001</code>" in text


def test_card_without_username_keeps_previous_wording():
    assert "📛 Username: @нет" in _user_card_text(_fake_user(username=None))


# ── таймзона ──


def test_card_without_subscription_shows_no_marker():
    assert "👑 Подписка до: ❌ Нет" in _user_card_text(_fake_user())


def test_naive_datetime_is_printed_as_utc():
    # В SQLite tzinfo не хранится, колонка отдаёт naive-UTC.
    assert _format_dt(datetime(2026, 9, 30, 20, 0, 0)) == "30.09.2026 20:00 UTC"


def test_aware_datetime_is_converted_to_utc():
    aware = datetime(2026, 10, 1, 3, 0, 0, tzinfo=timezone(timedelta(hours=7)))
    assert _format_dt(aware) == "30.09.2026 20:00 UTC"


def test_none_datetime_renders_as_absent():
    assert _format_dt(None) == "❌ Нет"


# ── _safe_edit ──


class _FakeBot:
    def __init__(self):
        self.sent = []

    async def send_message(self, chat_id, text, reply_markup=None):
        self.sent.append((chat_id, text, reply_markup))


class _FakeMessage:
    def __init__(self, error=None):
        self.error = error
        self.edited = []

    async def edit_text(self, text, reply_markup=None):
        if self.error is not None:
            raise self.error
        self.edited.append((text, reply_markup))


class _FakeCallback:
    def __init__(self, message, bot=None):
        self.message = message
        self.bot = bot if bot is not None else _FakeBot()
        self.from_user = SimpleNamespace(id=1000000001)


def _bad_request(text):
    # method=None: TelegramAPIError только сохраняет аргумент, валидации нет.
    return TelegramBadRequest(method=None, message=text)


async def test_safe_edit_edits_when_telegram_is_happy():
    cb = _FakeCallback(_FakeMessage())
    await _safe_edit(cb, "текст")
    assert cb.message.edited == [("текст", None)]
    assert cb.bot.sent == []


async def test_safe_edit_treats_not_modified_as_success():
    cb = _FakeCallback(_FakeMessage(_bad_request("Bad Request: message is not modified")))
    await _safe_edit(cb, "тот же текст")
    # Дубль сообщения не отправлен: пользователь и так видит нужный текст.
    assert cb.bot.sent == []


async def test_safe_edit_falls_back_to_a_new_message_on_markup_error():
    cb = _FakeCallback(_FakeMessage(_bad_request("Bad Request: can't parse entities")))
    await _safe_edit(cb, "текст")
    assert [t for _, t, _ in cb.bot.sent] == ["текст"]


async def test_safe_edit_survives_non_telegram_exception():
    cb = _FakeCallback(_FakeMessage(RuntimeError("boom")))
    await _safe_edit(cb, "текст")
    assert len(cb.bot.sent) == 1


async def test_safe_edit_sends_new_message_when_message_is_none():
    cb = _FakeCallback(None)
    await _safe_edit(cb, "текст")
    assert len(cb.bot.sent) == 1


async def test_safe_edit_handles_message_older_than_48_hours():
    # Telegram отдаёт InaccessibleMessage — у него нет метода edit_text вовсе.
    inaccessible = InaccessibleMessage(
        chat=Chat(id=1000000001, type="private"),
        message_id=42,
        date=datetime.fromtimestamp(0, tz=timezone.utc),
    )
    cb = _FakeCallback(inaccessible)
    await _safe_edit(cb, "текст")
    assert len(cb.bot.sent) == 1


async def test_safe_edit_never_raises_when_sending_also_fails():
    class _BrokenBot:
        async def send_message(self, *args, **kwargs):
            raise RuntimeError("network down")

    cb = _FakeCallback(_FakeMessage(RuntimeError("boom")), bot=_BrokenBot())
    await _safe_edit(cb, "текст")  # не должно бросить
```

- [ ] **Шаг 2: Прогнать тест, убедиться что падает**

Команда: `./scripts/test.sh tests/test_admin_card.py -q`
Ожидается: FAIL — `ImportError: cannot import name '_format_dt' from 'bot.handlers.admin'`.

- [ ] **Шаг 3: Реализовать**

В `bot/handlers/admin.py` заменить блок импортов (`:1-14`) на:

```python
from __future__ import annotations

from datetime import datetime, timezone

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, Filter
from aiogram.types import (
    CallbackQuery,
    InaccessibleMessage,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from loguru import logger

from bot.config import settings
from bot.db.engine import async_session
from bot.db.queries import get_stats, get_user_by_id, get_user_by_username, toggle_ban, update_subscription
from bot.keyboards.inline import get_admin_menu_kb, get_user_card_kb
from bot.utils.text import esc

router = Router(name="admin")
admin_waiting_search: set[int] = set()
```

Заменить `_user_card_text` (`:21-33`) на:

```python
def _format_dt(value: datetime | None) -> str:
    """Дата подписки в UTC с явной пометкой зоны.

    В SQLite tzinfo не хранится, колонка отдаёт naive-datetime в UTC. Без
    приведения strftime печатал их как есть, без пометки: истечение
    30.09 20:00 UTC читалось как «до 30.09», хотя у пользователя это уже
    1 октября. Арифметика продления при этом корректна — дефект был
    только в отображении.
    """
    if value is None:
        return "❌ Нет"
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    else:
        value = value.astimezone(timezone.utc)
    return value.strftime("%d.%m.%Y %H:%M") + " UTC"


def _user_card_text(user) -> str:
    # Всё, что пришло от пользователя, уходит через esc(): parse_mode=HTML
    # задан глобально, и имя вида «Ann <3» раньше роняло отправку карточки.
    status = "🔴 Заблокирован" if user.is_banned else "🟢 Активен"
    return (
        f"👤 <b>Карточка пользователя</b>\n\n"
        f"🆔 ID: <code>{user.id}</code>\n"
        f"📛 Username: @{esc(user.username) if user.username else 'нет'}\n"
        f"👤 Имя: {esc(user.full_name)}\n"
        f"🎟 Бесплатных: {user.free_downloads_left}\n"
        f"👑 Подписка до: {_format_dt(user.subscription_until)}\n"
        f"📥 Скачано: {user.total_downloads}\n"
        f"📌 Статус: {status}"
    )
```

Заменить `_safe_edit` (`:55-59`) на:

```python
async def _send_fresh(callback: CallbackQuery, text: str, reply_markup) -> None:
    """Последняя линия обороны: доставить текст новым сообщением.

    callback.message может быть недоступен (старше 48 часов), поэтому шлём
    по chat_id админа, а не через объект сообщения.
    """
    try:
        await callback.bot.send_message(
            callback.from_user.id, text, reply_markup=reply_markup
        )
    except Exception as exc:
        logger.error("Не удалось доставить админу сообщение: {}", exc)


async def _safe_edit(callback: CallbackQuery, text: str, reply_markup=None) -> None:
    """Правка сообщения, переживающая любую причину отказа Telegram.

    Прежняя версия ловила только TelegramBadRequest и повторяла ТОТ ЖЕ текст
    через .answer(): если причина отказа в самой HTML-разметке, вторая попытка
    падала так же и исключение уходило наружу необработанным. Плюс у
    InaccessibleMessage метода edit_text нет вовсе — это AttributeError.
    """
    message = callback.message
    if message is None or isinstance(message, InaccessibleMessage):
        await _send_fresh(callback, text, reply_markup)
        return

    try:
        await message.edit_text(text, reply_markup=reply_markup)
        return
    except TelegramBadRequest as exc:
        if "message is not modified" in str(exc).lower():
            # Текст и клавиатура совпали с текущими — это успех, а не ошибка.
            # Слать дубль не нужно.
            return
        logger.warning("edit_text отклонён Telegram, шлём новым сообщением: {}", exc)
    except Exception as exc:
        logger.warning("edit_text не удался, шлём новым сообщением: {}", exc)

    await _send_fresh(callback, text, reply_markup)
```

Заменить тело `admin_text_handler` (`:83-104`) на:

```python
@router.message(F.text, AdminSearchFilter())
async def admin_text_handler(message: Message):
    admin_waiting_search.discard(message.from_user.id)

    arg = message.text.strip()
    user_id: int | None = None
    if not arg.startswith("@"):
        try:
            user_id = int(arg)
        except ValueError:
            # Разбор аргумента и отказ — ДО открытия сессии: раньше ответ
            # уходил в сеть с удерживаемым соединением SQLite.
            await message.answer(
                "Неверный формат. Укажите ID (число) или @username.",
                reply_markup=get_admin_menu_kb(),
            )
            return

    async with async_session() as session, session.begin():
        if user_id is None:
            user = await get_user_by_username(session, arg.lstrip("@"))
        else:
            user = await get_user_by_id(session, user_id)

    if not user:
        await message.answer("❌ Пользователь не найден.", reply_markup=get_admin_menu_kb())
        return
    await message.answer(_user_card_text(user), reply_markup=get_user_card_kb(user.id))
```

- [ ] **Шаг 4: Прогнать тесты**

Команда: `./scripts/test.sh -q`
Ожидается: PASS, регрессий нет.

- [ ] **Шаг 5: Коммит**

```bash
git add bot/handlers/admin.py tests/test_admin_card.py
git commit -m "fix(admin): escape user data, mark dates as UTC, harden _safe_edit"
```

---

### Task 31: Выдача подписки — подтверждение, идемпотентность, журнал, отмена

**Закрывает:** H-12

**Files:**
- Modify: `bot/keyboards/inline.py:85-94` (`get_user_card_kb`) + новые функции формата `callback_data`
- Modify: `bot/handlers/admin.py:153-165` (`cb_grant` → два новых хендлера + легаси-заглушка)
- Test: `tests/test_admin_sub_actions.py`

**Interfaces:**
- Consumes: `apply_subscription_change(session, *, user_id, admin_id, days, idempotency_key) -> GrantOutcome` и `GrantOutcome(applied, user_found, duplicate, subscription_until)` из `bot/db/queries.py` (пакет C); `get_user_by_id`; `esc`, `_format_dt`, `_safe_edit`, `logger` из Task 30.
- Produces: в `bot/keyboards/inline.py` — константы `CB_MAX_BYTES`, `SUB_ASK`, `SUB_CONFIRM`, `SUB_DAYS_OFF`; `build_sub_action_cb(prefix, user_id, days, nonce) -> str`; `parse_sub_action_cb(data) -> tuple[str, int, int | None, str] | None`; `get_user_card_kb(user_id, nonce) -> InlineKeyboardMarkup` (**сигнатура изменилась — добавился обязательный `nonce`**); `get_sub_confirm_kb(user_id, days, nonce) -> InlineKeyboardMarkup`. В `bot/handlers/admin.py` — `_new_nonce() -> str`, `_idempotency_key(user_id, days, nonce) -> str`, `_days_phrase(days) -> str`, хендлеры `cb_sub_ask`, `cb_sub_confirm`, `cb_card`, `cb_grant_legacy`.

**⚠️ Меняет поведение для пользователя — нужна ручная проверка:** поток выдачи подписки становится двухшаговым и в карточке появляются две новые кнопки. Проверить руками: (1) «+30 дней» → экран подтверждения → «✅ Подтвердить» продлевает ровно один раз; (2) повторный клик по той же кнопке «✅ Подтвердить» отдаёт тост «Уже применено ранее» и дату не двигает; (3) «↩️ Отмена» возвращает карточку; (4) «🚫 Снять подписку» обнуляет дату; (5) «➖ −7 дней» сокращает; (6) кнопка «+30 дней» из карточки, открытой до деплоя, отдаёт тост «Кнопка устарела — откройте карточку заново» и ничего не меняет.

`queries.py:44-54` считает `base = max(now, existing) + days`, то есть повторный вызов складывается: двойной клик по «+30 дней» из-за сетевого лага даёт 60 дней за одну оплату. Хуже того, карточка с рабочей клавиатурой остаётся в истории чата админа навсегда, и старый `callback_data` с тем же `user_id` валиден и через месяц — случайный скролл по истории выдаёт подписку заново. Журнала нет: `bot/handlers/admin.py` до Task 30 вообще не импортировал логгер, отдельной таблицы платежей нет, `subscription_until` перезатирается следующим грантом. Отмены нет: в UI только `+7` и `+30` (`inline.py:88-89`) — ни снять, ни сократить.

Решение: (а) **двухшаговый поток** — кнопка карточки ведёт на экран подтверждения, и только подтверждение применяет изменение; (б) при отрисовке карточки генерируется одноразовый `nonce`, он вшивается во все её кнопки и доезжает до экрана подтверждения; из него же строится `idempotency_key`, поэтому повторный клик по той же кнопке — в том числе по карточке из истории чата — попадает в `duplicate=True` и ничего не меняет, а свежая карточка получает новый nonce и работает; (в) в карточку добавляются `−7 дней` и `🚫 Снять подписку`; (г) каждая операция пишется в лог. Разбор `callback_data` вынесен в чистую функцию `parse_sub_action_cb`, которая на мусоре возвращает `None`: сейчас `int(parts[2])` на `:158` и `:173` падает на любом неожиданном значении. Формат кодирования лежит рядом с построением кнопок, в `inline.py`, чтобы строитель и разборщик не разъехались.

**Лимит Telegram — 64 байта на `callback_data`.** Худший случай: префикс `admin:gc:` (9 байт) + 16-значный `user_id` (потолок 52-битных Telegram-id) + `:` + `off` (3) + `:` + nonce из 8 hex-символов = 38 байт. Запас двукратный; тест фиксирует лимит для всех кнопок карточки и экрана подтверждения.

- [ ] **Шаг 1: Написать падающий тест**

Создать `tests/test_admin_sub_actions.py`:

```python
from types import SimpleNamespace

import pytest

from bot.keyboards.inline import (
    CB_MAX_BYTES,
    SUB_ASK,
    SUB_CONFIRM,
    build_sub_action_cb,
    get_sub_confirm_kb,
    get_user_card_kb,
    parse_sub_action_cb,
)

# 16 девяток — потолок 52-битных Telegram-id, худший случай по длине.
WORST_CASE_ID = 9999999999999999
NONCE = "abcdef12"


# ── формат callback_data ──


def test_every_card_button_fits_telegram_callback_limit():
    kb = get_user_card_kb(WORST_CASE_ID, NONCE)
    for row in kb.inline_keyboard:
        for button in row:
            assert len(button.callback_data.encode()) <= CB_MAX_BYTES, button.callback_data


def test_every_confirm_button_fits_telegram_callback_limit():
    for days in (7, 30, -7, None):
        kb = get_sub_confirm_kb(WORST_CASE_ID, days, NONCE)
        for row in kb.inline_keyboard:
            for button in row:
                assert len(button.callback_data.encode()) <= CB_MAX_BYTES, button.callback_data


def test_build_and_parse_roundtrip():
    for days in (7, 30, -7, None):
        data = build_sub_action_cb(SUB_ASK, 1000000001, days, NONCE)
        assert parse_sub_action_cb(data) == (SUB_ASK, 1000000001, days, NONCE)


def test_build_refuses_to_exceed_the_limit():
    with pytest.raises(ValueError):
        build_sub_action_cb(SUB_ASK, WORST_CASE_ID, 30, "x" * 40)


def test_parse_rejects_garbage():
    assert parse_sub_action_cb("admin:gq:notanumber:30:abcdef12") is None
    assert parse_sub_action_cb("admin:gq:1000000001:xx:abcdef12") is None
    assert parse_sub_action_cb("admin:gq:1000000001:30") is None
    assert parse_sub_action_cb("admin:gq:1000000001:30:abcdef12:extra") is None
    assert parse_sub_action_cb("admin:panel") is None
    assert parse_sub_action_cb("admin:gq:-5:30:abcdef12") is None
    assert parse_sub_action_cb("admin:gq:1000000001:0:abcdef12") is None
    assert parse_sub_action_cb("admin:gq:1000000001:30:") is None
    assert parse_sub_action_cb("") is None


# ── клавиатура карточки ──


def _parsed_card_buttons(user_id, nonce):
    kb = get_user_card_kb(user_id, nonce)
    parsed = []
    for row in kb.inline_keyboard:
        for button in row:
            item = parse_sub_action_cb(button.callback_data)
            if item is not None:
                parsed.append(item)
    return parsed


def test_card_buttons_all_carry_the_same_nonce():
    assert {item[3] for item in _parsed_card_buttons(1000000001, NONCE)} == {NONCE}


def test_card_buttons_all_point_at_the_confirmation_step():
    assert {item[0] for item in _parsed_card_buttons(1000000001, NONCE)} == {SUB_ASK}


def test_card_offers_extend_reduce_and_remove():
    assert {item[2] for item in _parsed_card_buttons(1000000001, NONCE)} == {7, 30, -7, None}


def test_card_keeps_ban_search_and_panel_buttons():
    kb = get_user_card_kb(1000000001, NONCE)
    data = [b.callback_data for row in kb.inline_keyboard for b in row]
    assert "admin:ban:1000000001" in data
    assert "admin:search" in data
    assert "admin:panel" in data


def test_confirm_keyboard_offers_apply_and_cancel():
    kb = get_sub_confirm_kb(1000000001, 30, NONCE)
    data = [b.callback_data for row in kb.inline_keyboard for b in row]
    assert build_sub_action_cb(SUB_CONFIRM, 1000000001, 30, NONCE) in data
    assert "admin:card:1000000001" in data


# ── ключ идемпотентности ──


def test_two_cards_get_different_nonces():
    from bot.handlers.admin import _new_nonce

    assert _new_nonce() != _new_nonce()


def test_idempotency_key_is_stable_and_distinct():
    from bot.handlers.admin import _idempotency_key

    assert _idempotency_key(1000000001, 30, NONCE) == _idempotency_key(1000000001, 30, NONCE)
    assert _idempotency_key(1000000001, 30, NONCE) != _idempotency_key(1000000001, 30, "12abcdef")
    assert _idempotency_key(1000000001, None, NONCE) != _idempotency_key(1000000001, 7, NONCE)
    assert _idempotency_key(1000000001, 30, NONCE) != _idempotency_key(1000000002, 30, NONCE)


# ── хендлер подтверждения ──


class _FakeBot:
    def __init__(self):
        self.sent = []

    async def send_message(self, chat_id, text, reply_markup=None):
        self.sent.append((chat_id, text, reply_markup))


class _FakeMessage:
    def __init__(self):
        self.edited = []

    async def edit_text(self, text, reply_markup=None):
        self.edited.append((text, reply_markup))


class _FakeCallback:
    def __init__(self, data, from_id=1000000001):
        self.data = data
        self.message = _FakeMessage()
        self.bot = _FakeBot()
        self.from_user = SimpleNamespace(id=from_id)
        self.answers = []

    async def answer(self, text=None, show_alert=False):
        self.answers.append((text, show_alert))


class _FakeSession:
    """Дублёр async_session(): и сам контекст, и то, что отдаёт begin()."""

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False

    def begin(self):
        return self


def _fake_session_factory():
    return _FakeSession()


def _fake_user(**overrides):
    data = dict(
        id=1000000002,
        username="ivan",
        full_name="Ivan Petrov",
        free_downloads_left=2,
        subscription_until=None,
        total_downloads=5,
        is_banned=False,
    )
    data.update(overrides)
    return SimpleNamespace(**data)


def _patch_admin_module(monkeypatch, apply_impl, user=None):
    from bot.handlers import admin as admin_mod

    async def _get_user(session, user_id):
        return _fake_user(id=user_id) if user is None else user

    monkeypatch.setattr(admin_mod, "is_admin", lambda user_id: True)
    monkeypatch.setattr(admin_mod, "async_session", _fake_session_factory)
    monkeypatch.setattr(admin_mod, "apply_subscription_change", apply_impl)
    monkeypatch.setattr(admin_mod, "get_user_by_id", _get_user)
    return admin_mod


async def test_confirm_passes_the_nonce_based_idempotency_key(monkeypatch):
    from bot.db.queries import GrantOutcome

    calls = []

    async def _apply(session, *, user_id, admin_id, days, idempotency_key):
        calls.append((user_id, admin_id, days, idempotency_key))
        return GrantOutcome(
            applied=True, user_found=True, duplicate=False, subscription_until=None
        )

    admin_mod = _patch_admin_module(monkeypatch, _apply)
    cb = _FakeCallback(f"{SUB_CONFIRM}:1000000002:30:{NONCE}", from_id=1000000001)
    await admin_mod.cb_sub_confirm(cb)

    assert calls == [
        (1000000002, 1000000001, 30, admin_mod._idempotency_key(1000000002, 30, NONCE))
    ]
    assert cb.answers and cb.answers[0][0] == "✅ Готово"


async def test_second_click_on_the_same_button_reports_a_duplicate(monkeypatch):
    from bot.db.queries import GrantOutcome

    async def _apply(session, *, user_id, admin_id, days, idempotency_key):
        return GrantOutcome(
            applied=False, user_found=True, duplicate=True, subscription_until=None
        )

    admin_mod = _patch_admin_module(monkeypatch, _apply)
    cb = _FakeCallback(f"{SUB_CONFIRM}:1000000002:30:{NONCE}")
    await admin_mod.cb_sub_confirm(cb)

    assert cb.answers and "ранее" in cb.answers[0][0]


async def test_confirm_survives_broken_callback_data(monkeypatch):
    async def _apply(session, **kwargs):  # pragma: no cover - вызываться не должен
        raise AssertionError("apply_subscription_change не должен вызываться")

    admin_mod = _patch_admin_module(monkeypatch, _apply)
    cb = _FakeCallback(f"{SUB_CONFIRM}:notanumber:30:{NONCE}")
    await admin_mod.cb_sub_confirm(cb)

    assert cb.answers  # спиннер погашен, исключения нет


async def test_ask_step_does_not_change_anything(monkeypatch):
    async def _apply(session, **kwargs):  # pragma: no cover - вызываться не должен
        raise AssertionError("шаг подтверждения не должен менять подписку")

    admin_mod = _patch_admin_module(monkeypatch, _apply)
    cb = _FakeCallback(f"{SUB_ASK}:1000000002:30:{NONCE}")
    await admin_mod.cb_sub_ask(cb)

    assert cb.message.edited, "экран подтверждения не показан"
    text, markup = cb.message.edited[0]
    assert "Подтвердите" in text
    assert build_sub_action_cb(SUB_CONFIRM, 1000000002, 30, NONCE) == (
        markup.inline_keyboard[0][0].callback_data
    )


async def test_legacy_grant_button_is_refused_not_applied(monkeypatch):
    async def _apply(session, **kwargs):  # pragma: no cover - вызываться не должен
        raise AssertionError("кнопка старого формата не должна менять подписку")

    admin_mod = _patch_admin_module(monkeypatch, _apply)
    cb = _FakeCallback("admin:grant:1000000002:30")
    await admin_mod.cb_grant_legacy(cb)

    assert cb.answers and "устарела" in cb.answers[0][0]
```

- [ ] **Шаг 2: Прогнать тест, убедиться что падает**

Команда: `./scripts/test.sh tests/test_admin_sub_actions.py -q`
Ожидается: FAIL — `ImportError: cannot import name 'CB_MAX_BYTES' from 'bot.keyboards.inline'`.

- [ ] **Шаг 3: Реализовать**

В `bot/keyboards/inline.py` добавить в начало файла, сразу после импорта:

```python
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

# Жёсткий лимит Telegram на callback_data. Кнопка длиннее просто не отправится,
# поэтому проверяем при построении, а не по факту отказа API.
CB_MAX_BYTES = 64

# Действия над подпиской кодируются двумя префиксами: «спросить» и «применить».
# Между ними стоит экран подтверждения — без него двойной клик по «+30 дней»
# из-за сетевого лага давал 60 дней за одну оплату.
SUB_ASK = "admin:gq"
SUB_CONFIRM = "admin:gc"

# Полное снятие подписки кодируется словом вместо числа дней.
SUB_DAYS_OFF = "off"


def build_sub_action_cb(prefix: str, user_id: int, days: int | None, nonce: str) -> str:
    days_part = SUB_DAYS_OFF if days is None else str(days)
    data = f"{prefix}:{user_id}:{days_part}:{nonce}"
    if len(data.encode()) > CB_MAX_BYTES:
        raise ValueError(f"callback_data длиннее {CB_MAX_BYTES} байт: {data!r}")
    return data


def parse_sub_action_cb(data: str) -> tuple[str, int, int | None, str] | None:
    """Разбирает callback_data действия над подпиской.

    None — если строка не наша или повреждена. Хендлер обязан молча отбиться,
    а не падать на int() посреди разбора: раньше admin.py:158 и :173 роняли
    исключение на любом неожиданном значении.
    """
    parts = data.split(":")
    if len(parts) != 5:
        return None
    prefix = f"{parts[0]}:{parts[1]}"
    if prefix not in (SUB_ASK, SUB_CONFIRM):
        return None
    try:
        user_id = int(parts[2])
    except ValueError:
        return None
    if user_id <= 0:
        return None

    days_part = parts[3]
    if days_part == SUB_DAYS_OFF:
        days: int | None = None
    else:
        try:
            days = int(days_part)
        except ValueError:
            return None
        if days == 0:
            return None

    nonce = parts[4]
    if not nonce or not nonce.isalnum():
        return None
    return prefix, user_id, days, nonce
```

Заменить `get_user_card_kb` (`inline.py:85-94`) на:

```python
def get_user_card_kb(user_id: int, nonce: str) -> InlineKeyboardMarkup:
    # nonce одноразовый и общий для всех кнопок этой отрисовки карточки:
    # из него строится ключ идемпотентности, поэтому повторный клик по
    # кнопке из истории чата попадает в уже применённую операцию.
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="📅 +7 дней",
                callback_data=build_sub_action_cb(SUB_ASK, user_id, 7, nonce),
            ),
            InlineKeyboardButton(
                text="📅 +30 дней",
                callback_data=build_sub_action_cb(SUB_ASK, user_id, 30, nonce),
            ),
        ],
        [
            InlineKeyboardButton(
                text="➖ −7 дней",
                callback_data=build_sub_action_cb(SUB_ASK, user_id, -7, nonce),
            ),
            InlineKeyboardButton(
                text="🚫 Снять подписку",
                callback_data=build_sub_action_cb(SUB_ASK, user_id, None, nonce),
            ),
        ],
        [InlineKeyboardButton(text="🔨 Бан/Разбан", callback_data=f"admin:ban:{user_id}")],
        [InlineKeyboardButton(text="🔍 Найти другого", callback_data="admin:search")],
        [InlineKeyboardButton(text="⚙️  Админ-панель", callback_data="admin:panel")],
    ])


def get_sub_confirm_kb(user_id: int, days: int | None, nonce: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="✅ Подтвердить",
            callback_data=build_sub_action_cb(SUB_CONFIRM, user_id, days, nonce),
        )],
        [InlineKeyboardButton(text="↩️ Отмена", callback_data=f"admin:card:{user_id}")],
    ])
```

В `bot/handlers/admin.py` заменить импорт очереди и клавиатур:

```python
from bot.db.queries import (
    apply_subscription_change,
    get_stats,
    get_user_by_id,
    get_user_by_username,
    toggle_ban,
)
from bot.keyboards.inline import (
    SUB_ASK,
    SUB_CONFIRM,
    get_admin_menu_kb,
    get_sub_confirm_kb,
    get_user_card_kb,
    parse_sub_action_cb,
)
```

Добавить после `is_admin` (`:17-18`):

```python
def _new_nonce() -> str:
    # 32 бита случайности: достаточно, чтобы клик по старой карточке из истории
    # чата не совпал с ключом свежей операции, и коротко для лимита в 64 байта.
    return uuid4().hex[:8]


def _idempotency_key(user_id: int, days: int | None, nonce: str) -> str:
    days_part = "off" if days is None else str(days)
    return f"sub:{user_id}:{days_part}:{nonce}"


def _days_phrase(days: int | None) -> str:
    if days is None:
        return "снять подписку полностью"
    if days > 0:
        return f"продлить подписку на {days} дн."
    return f"сократить подписку на {-days} дн."
```

и `from uuid import uuid4` в импорты.

Заменить `cb_grant` (`:153-165`) на четыре хендлера:

```python
@router.callback_query(F.data.startswith("admin:card:"))
async def cb_card(callback: CallbackQuery):
    # Проверка прав — ДО разбора callback_data.
    if not is_admin(callback.from_user.id):
        await callback.answer()
        return
    await callback.answer()

    parts = callback.data.split(":")
    try:
        user_id = int(parts[2])
    except (IndexError, ValueError):
        await _safe_edit(callback, "⚠️ Повреждённая кнопка.", reply_markup=get_admin_menu_kb())
        return

    async with async_session() as session, session.begin():
        user = await get_user_by_id(session, user_id)
    if user is None:
        await _safe_edit(callback, "❌ Пользователь не найден.", reply_markup=get_admin_menu_kb())
        return
    # Каждая отрисовка карточки — новый nonce: прошлые кнопки больше не применятся.
    await _safe_edit(
        callback, _user_card_text(user), reply_markup=get_user_card_kb(user.id, _new_nonce())
    )


@router.callback_query(F.data.startswith(SUB_ASK + ":"))
async def cb_sub_ask(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer()
        return
    await callback.answer()

    parsed = parse_sub_action_cb(callback.data)
    if parsed is None:
        await _safe_edit(
            callback,
            "⚠️ Повреждённая кнопка. Откройте карточку заново.",
            reply_markup=get_admin_menu_kb(),
        )
        return
    _, user_id, days, nonce = parsed

    async with async_session() as session, session.begin():
        user = await get_user_by_id(session, user_id)
    if user is None:
        await _safe_edit(callback, "❌ Пользователь не найден.", reply_markup=get_admin_menu_kb())
        return

    await _safe_edit(
        callback,
        f"❓ <b>Подтвердите действие</b>\n\n"
        f"Пользователь: <code>{user.id}</code> ({esc(user.full_name)})\n"
        f"Действие: <b>{_days_phrase(days)}</b>\n"
        f"Сейчас подписка до: {_format_dt(user.subscription_until)}",
        reply_markup=get_sub_confirm_kb(user_id, days, nonce),
    )


@router.callback_query(F.data.startswith(SUB_CONFIRM + ":"))
async def cb_sub_confirm(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer()
        return

    parsed = parse_sub_action_cb(callback.data)
    if parsed is None:
        await callback.answer("⚠️ Повреждённая кнопка", show_alert=True)
        return
    _, user_id, days, nonce = parsed

    async with async_session() as session, session.begin():
        outcome = await apply_subscription_change(
            session,
            user_id=user_id,
            admin_id=callback.from_user.id,
            days=days,
            idempotency_key=_idempotency_key(user_id, days, nonce),
        )
        user = await get_user_by_id(session, user_id)

    if not outcome.user_found:
        logger.warning("Подписка не изменена: пользователь не найден | target={} days={}", user_id, days)
        await callback.answer("❌ Пользователь не найден", show_alert=True)
        await _safe_edit(callback, "❌ Пользователь не найден.", reply_markup=get_admin_menu_kb())
        return

    if outcome.duplicate:
        logger.info("Повтор операции подписки проигнорирован | target={} days={}", user_id, days)
        await callback.answer("↩️ Уже применено ранее", show_alert=True)
    else:
        logger.info(
            "Подписка изменена | target={} days={} until={}",
            user_id, days, outcome.subscription_until,
        )
        await callback.answer("✅ Готово")

    await _safe_edit(
        callback, _user_card_text(user), reply_markup=get_user_card_kb(user_id, _new_nonce())
    )


@router.callback_query(F.data.startswith("admin:grant:"))
async def cb_grant_legacy(callback: CallbackQuery):
    """Кнопки старого формата, оставшиеся в истории чата.

    Обработчик нужен именно как отбой: без него такая кнопка стала бы мёртвой
    и крутила бы спиннер 30 секунд. Применять её нельзя — в ней нет nonce,
    то есть нет защиты от повторного клика.
    """
    if not is_admin(callback.from_user.id):
        await callback.answer()
        return
    await callback.answer("Кнопка устарела — откройте карточку заново", show_alert=True)
```

Обновить оставшиеся два вызова `get_user_card_kb`: в `admin_text_handler` (последняя строка) и в `cb_ban` — на `get_user_card_kb(user.id, _new_nonce())`.

- [ ] **Шаг 4: Прогнать тесты**

Команда: `./scripts/test.sh -q`
Ожидается: PASS, регрессий нет.

- [ ] **Шаг 5: Коммит**

```bash
git add bot/handlers/admin.py bot/keyboards/inline.py tests/test_admin_sub_actions.py
git commit -m "feat(admin): confirm, log and make subscription changes idempotent"
```

---

### Task 32: Отсутствующий пользователь и спиннер у не-админа

**Закрывает:** M-24, M-11

**Files:**
- Modify: `bot/handlers/admin.py:110-115` (`cb_panel`), `:118-131` (`cb_search`), `:134-140` (`cb_cancel_search`), `:143-150` (`cb_stats`), `:168-179` (`cb_ban`) и новые хендлеры из Task 31
- Test: `tests/test_admin_missing_user.py`

**Interfaces:**
- Consumes: `toggle_ban`, `get_user_by_id` из `bot/db/queries.py`; `GrantOutcome.user_found` (используется Task 31); `_safe_edit`, `_user_card_text`, `_new_nonce`, `logger` из Task 30 и Task 31.
- Produces: `_deny(callback) -> bool` — единая точка отбоя не-админа: гасит спиннер пустым `answer()` и возвращает `True`, если вызов нужно прекратить.

**⚠️ Меняет поведение для пользователя — нужна ручная проверка:** админ, нажавший «🔨 Бан/Разбан» на карточке пользователя, которого уже нет в БД, теперь получает алерт «❌ Пользователь не найден» вместо 30-секундного спиннера. Проверить руками: открыть карточку, дождаться ситуации с отсутствующим id (воспроизводится на тестовой БД) и убедиться, что алерт приходит мгновенно и админ-панель открывается.

`queries.py:44-47` при `user is None` молча делает `return`, а старый `cb_grant` безусловно отдавал `callback.answer("✅ Подписка +N дней")` (`admin.py:160-165`) — админ видел успех там, где ничего не произошло. Это уже закрыто в Task 31 через `GrantOutcome.user_found`; здесь остаётся вторая половина: `toggle_ban` в той же ситуации бросает `ValueError` (`queries.py:60`) необработанным, и `callback.answer()` на `:177` просто не достигается — у админа висит спиннер. Сценарий реальный: после восстановления БД из бэкапа старые карточки в истории чата ссылаются на исчезнувшие id.

Отдельно M-11: все восемь точек входа делают `return` без `callback.answer()`, когда вызывающий не админ (`admin.py:112, 120, 136, 145, 155, 170`) — у не-админа, дотянувшегося до чужой кнопки, спиннер крутится 30 секунд, пока Telegram не сдастся. Отбой делаем пустым `answer()` без текста: сообщать «у вас нет прав» нельзя, это подтверждает существование панели. Проверка прав остаётся первым действием хендлера, **до** разбора `callback_data` — это ровно то, что ревизия признала корректным. В `cb_ban` заменяем ловлю `ValueError` на предварительную проверку `get_user_by_id`: так транзакция не остаётся наполовину открытой, а контракт `toggle_ban` (файл пакета C) не меняется.

- [ ] **Шаг 1: Написать падающий тест**

Создать `tests/test_admin_missing_user.py`:

```python
from types import SimpleNamespace


class _FakeBot:
    def __init__(self):
        self.sent = []

    async def send_message(self, chat_id, text, reply_markup=None):
        self.sent.append((chat_id, text, reply_markup))


class _FakeMessage:
    def __init__(self):
        self.edited = []

    async def edit_text(self, text, reply_markup=None):
        self.edited.append((text, reply_markup))


class _FakeCallback:
    def __init__(self, data, from_id=1000000001):
        self.data = data
        self.message = _FakeMessage()
        self.bot = _FakeBot()
        self.from_user = SimpleNamespace(id=from_id)
        self.answers = []

    async def answer(self, text=None, show_alert=False):
        self.answers.append((text, show_alert))


class _FakeSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False

    def begin(self):
        return self


def _fake_session_factory():
    return _FakeSession()


async def test_no_admin_callback_leaves_the_spinner_for_a_stranger(monkeypatch):
    """Каждый колбэк-хендлер админки обязан погасить спиннер у не-админа."""
    from bot.handlers import admin as admin_mod

    monkeypatch.setattr(admin_mod, "is_admin", lambda user_id: False)

    handlers = admin_mod.router.callback_query.handlers
    assert handlers, "в админ-роутере нет колбэк-хендлеров — тест бесполезен"
    for handler in handlers:
        cb = _FakeCallback("admin:panel", from_id=1000000009)
        await handler.callback(cb)
        assert cb.answers, f"{handler.callback.__name__} оставил спиннер у не-админа"
        assert cb.message.edited == [], f"{handler.callback.__name__} ответил не-админу содержимым"


async def test_ban_on_a_missing_user_reports_instead_of_raising(monkeypatch):
    from bot.handlers import admin as admin_mod

    async def _no_user(session, user_id):
        return None

    async def _toggle(session, user_id):  # pragma: no cover - вызываться не должен
        raise AssertionError("toggle_ban не должен вызываться для отсутствующего юзера")

    monkeypatch.setattr(admin_mod, "is_admin", lambda user_id: True)
    monkeypatch.setattr(admin_mod, "async_session", _fake_session_factory)
    monkeypatch.setattr(admin_mod, "get_user_by_id", _no_user)
    monkeypatch.setattr(admin_mod, "toggle_ban", _toggle)

    cb = _FakeCallback("admin:ban:1000000002")
    await admin_mod.cb_ban(cb)

    assert cb.answers and "не найден" in cb.answers[0][0]


async def test_ban_survives_broken_callback_data(monkeypatch):
    from bot.handlers import admin as admin_mod

    monkeypatch.setattr(admin_mod, "is_admin", lambda user_id: True)

    cb = _FakeCallback("admin:ban:notanumber")
    await admin_mod.cb_ban(cb)

    assert cb.answers  # спиннер погашен, исключения нет


async def test_ban_on_an_existing_user_still_works(monkeypatch):
    from bot.handlers import admin as admin_mod

    user = SimpleNamespace(
        id=1000000002,
        username="ivan",
        full_name="Ivan Petrov",
        free_downloads_left=2,
        subscription_until=None,
        total_downloads=5,
        is_banned=True,
    )

    async def _get_user(session, user_id):
        return user

    async def _toggle(session, user_id):
        return True

    monkeypatch.setattr(admin_mod, "is_admin", lambda user_id: True)
    monkeypatch.setattr(admin_mod, "async_session", _fake_session_factory)
    monkeypatch.setattr(admin_mod, "get_user_by_id", _get_user)
    monkeypatch.setattr(admin_mod, "toggle_ban", _toggle)

    cb = _FakeCallback("admin:ban:1000000002")
    await admin_mod.cb_ban(cb)

    assert cb.answers and cb.answers[0][0] == "🔴 Заблокирован"
    assert cb.message.edited, "карточка не перерисована"
```

- [ ] **Шаг 2: Прогнать тест, убедиться что падает**

Команда: `./scripts/test.sh tests/test_admin_missing_user.py -q`
Ожидается: FAIL — `test_no_admin_callback_leaves_the_spinner_for_a_stranger` падает на `cb_panel оставил спиннер у не-админа`; `test_ban_on_a_missing_user_reports_instead_of_raising` падает на `AssertionError: toggle_ban не должен вызываться`.

- [ ] **Шаг 3: Реализовать**

Добавить в `bot/handlers/admin.py` рядом с `is_admin` (`:17-18`):

```python
async def _deny(callback: CallbackQuery) -> bool:
    """Отбой не-админа. True — вызов нужно прекратить.

    Спиннер на кнопке крутится 30 секунд, пока Telegram не получит
    answerCallbackQuery, поэтому гасим его пустым answer(). Текста не даём:
    подсказывать постороннему, что кнопка существует, нельзя.
    Проверка прав остаётся ДО разбора callback_data.
    """
    if is_admin(callback.from_user.id):
        return False
    await callback.answer()
    return True
```

Во всех колбэк-хендлерах админки заменить

```python
    if not is_admin(callback.from_user.id):
        return
```

и добавленный в Task 31 вариант

```python
    if not is_admin(callback.from_user.id):
        await callback.answer()
        return
```

на единый

```python
    if await _deny(callback):
        return
```

Затрагиваются: `cb_panel`, `cb_search`, `cb_cancel_search`, `cb_stats`, `cb_ban`, `cb_card`, `cb_sub_ask`, `cb_sub_confirm`, `cb_grant_legacy`.

Заменить `cb_ban` (`:168-179`) на:

```python
@router.callback_query(F.data.startswith("admin:ban:"))
async def cb_ban(callback: CallbackQuery):
    if await _deny(callback):
        return

    parts = callback.data.split(":")
    try:
        user_id = int(parts[2])
    except (IndexError, ValueError):
        await callback.answer("⚠️ Повреждённая кнопка", show_alert=True)
        return

    async with async_session() as session, session.begin():
        # Проверяем существование заранее: toggle_ban на отсутствующем юзере
        # бросает ValueError, и до callback.answer() управление не доходило —
        # у админа висел спиннер. Контракт toggle_ban при этом не меняем.
        user = await get_user_by_id(session, user_id)
        if user is None:
            new_status = None
        else:
            new_status = await toggle_ban(session, user_id)
            user = await get_user_by_id(session, user_id)

    if new_status is None:
        logger.warning("Бан не применён: пользователь не найден | target={}", user_id)
        await callback.answer("❌ Пользователь не найден", show_alert=True)
        await _safe_edit(callback, "❌ Пользователь не найден.", reply_markup=get_admin_menu_kb())
        return

    logger.info("Статус бана изменён | target={} banned={}", user_id, new_status)
    await callback.answer("🔴 Заблокирован" if new_status else "🟢 Разблокирован")
    await _safe_edit(
        callback, _user_card_text(user), reply_markup=get_user_card_kb(user.id, _new_nonce())
    )
```

- [ ] **Шаг 4: Прогнать тесты**

Команда: `./scripts/test.sh -q`
Ожидается: PASS, регрессий нет.

- [ ] **Шаг 5: Коммит**

```bash
git add bot/handlers/admin.py tests/test_admin_missing_user.py
git commit -m "fix(admin): report missing users honestly and never leave a spinner"
```

---

### Task 33: Режим поиска не съедает команды, `/admin` не молчит

**Закрывает:** Low «Режим админского поиска съедает `/start`», Low «`/admin` от обычного юзера — полная тишина»

**Files:**
- Modify: `bot/handlers/admin.py:65-69` (`cmd_admin`), `:75-80` (`AdminSearchFilter`)
- Test: `tests/test_admin_routing.py`

**Interfaces:**
- Consumes: `get_main_menu_kb(is_admin: bool = False)` из `bot/keyboards/inline.py`; `is_admin`, `admin_waiting_search`.
- Produces: `AdminSearchFilter` пропускает сообщения, начинающиеся с `/`, и снимает режим поиска; `cmd_admin` отвечает и не-админу.

**⚠️ Меняет поведение для пользователя — нужна ручная проверка:** обычный пользователь, отправивший `/admin`, теперь получает главное меню вместо тишины. Проверить руками с непривилегированного аккаунта: `/admin` → приходит «Выбери действие 👇» с обычным меню и **без** кнопки «⚙️ Админ-панель». Отдельно проверить админом: нажать «🔍 Поиск пользователя», затем отправить `/start` — должно открыться главное меню, а не «Неверный формат», и следующее обычное сообщение уже не должно трактоваться как поисковый запрос.

`admin.py:83` фильтруется на `F.text`, а `admin_router` включён первым (`__main__.py:32-33`), поэтому в режиме поиска он матчит раньше `Command("start")` из `user_router`: `/start` уходит в `int("/start")` → «Неверный формат». Решение — пропускать любые сообщения, начинающиеся с `/`, мимо фильтра. **Режим поиска при этом снимаем**, и вот почему: админ, отправивший команду, явно переключился на другое, а оставленный флаг превратит следующее обычное сообщение (например ссылку на скачивание) в поисковый запрос — это тот же баг, только сдвинутый на одно сообщение. Побочный эффект внутри фильтра допустим: фильтр вызывается ровно один раз на сообщение для единственного хендлера `admin_text_handler`, и его `False` пускает сообщение дальше по цепочке роутеров.

`/admin` от обычного пользователя — полная тишина: `admin.py:65-69` фильтрует только по `Command("admin")`, проверка прав внутри тела с `return`, сообщение поглощается и до catch-all в `user.py` не доходит. Прав это не выдаёт, но неотличимо от падения бота. Отвечаем ровно тем же, чем catch-all отвечает на любое непонятное сообщение — «Выбери действие 👇» с главным меню и `is_admin=False`. Говорить «у вас нет прав» нельзя: это подтверждает существование панели. Текст дублируется с `user.py:222` осознанно — импортировать хендлер из соседнего модуля ради одной строки хуже, чем повторить её; `bot/handlers/user.py` принадлежит пакету D и в этом пакете не трогается.

Заодно `AdminSearchFilter` переводится с прямого сравнения `message.from_user.id == settings.ADMIN_ID` на вызов `is_admin(...)`: сравнение то же самое и так же серверное, но определение «кто админ» остаётся в одном месте.

- [ ] **Шаг 1: Написать падающий тест**

Создать `tests/test_admin_routing.py`:

```python
from types import SimpleNamespace


class _FakeMessage:
    def __init__(self, text, user_id=1000000001):
        self.text = text
        self.from_user = SimpleNamespace(id=user_id)
        self.answers = []

    async def answer(self, text, reply_markup=None):
        self.answers.append((text, reply_markup))


# ── AdminSearchFilter ──


async def test_search_filter_matches_plain_text_in_search_mode(monkeypatch):
    from bot.handlers import admin as admin_mod

    monkeypatch.setattr(admin_mod, "is_admin", lambda user_id: True)
    admin_mod.admin_waiting_search.add(1000000001)
    try:
        assert await admin_mod.AdminSearchFilter()(_FakeMessage("@ivan")) is True
    finally:
        admin_mod.admin_waiting_search.discard(1000000001)


async def test_search_filter_lets_commands_through(monkeypatch):
    from bot.handlers import admin as admin_mod

    monkeypatch.setattr(admin_mod, "is_admin", lambda user_id: True)
    admin_mod.admin_waiting_search.add(1000000001)
    try:
        assert await admin_mod.AdminSearchFilter()(_FakeMessage("/start")) is False
    finally:
        admin_mod.admin_waiting_search.discard(1000000001)


async def test_search_filter_clears_the_mode_when_a_command_arrives(monkeypatch):
    from bot.handlers import admin as admin_mod

    monkeypatch.setattr(admin_mod, "is_admin", lambda user_id: True)
    admin_mod.admin_waiting_search.add(1000000001)
    try:
        await admin_mod.AdminSearchFilter()(_FakeMessage("/start"))
        assert 1000000001 not in admin_mod.admin_waiting_search
    finally:
        admin_mod.admin_waiting_search.discard(1000000001)


async def test_search_filter_ignores_users_outside_search_mode(monkeypatch):
    from bot.handlers import admin as admin_mod

    monkeypatch.setattr(admin_mod, "is_admin", lambda user_id: True)
    assert await admin_mod.AdminSearchFilter()(_FakeMessage("@ivan")) is False


async def test_search_filter_ignores_non_admin(monkeypatch):
    from bot.handlers import admin as admin_mod

    monkeypatch.setattr(admin_mod, "is_admin", lambda user_id: False)
    admin_mod.admin_waiting_search.add(1000000009)
    try:
        assert await admin_mod.AdminSearchFilter()(_FakeMessage("@ivan", user_id=1000000009)) is False
    finally:
        admin_mod.admin_waiting_search.discard(1000000009)


# ── /admin ──


async def test_admin_command_from_admin_opens_the_panel(monkeypatch):
    from bot.handlers import admin as admin_mod

    monkeypatch.setattr(admin_mod, "is_admin", lambda user_id: True)
    message = _FakeMessage("/admin")
    await admin_mod.cmd_admin(message)

    assert message.answers
    text, markup = message.answers[0]
    assert "Админ-панель" in text
    assert any(
        b.callback_data == "admin:search" for row in markup.inline_keyboard for b in row
    )


async def test_admin_command_from_a_stranger_gets_the_generic_reply(monkeypatch):
    from bot.handlers import admin as admin_mod

    monkeypatch.setattr(admin_mod, "is_admin", lambda user_id: False)
    message = _FakeMessage("/admin", user_id=1000000009)
    await admin_mod.cmd_admin(message)

    assert message.answers, "не-админ не получил ответа"
    text, markup = message.answers[0]
    # Ни слова про панель и права: существование админки не подтверждаем.
    assert "Админ" not in text
    assert "прав" not in text
    assert all(
        b.callback_data != "admin:panel" for row in markup.inline_keyboard for b in row
    )
```

- [ ] **Шаг 2: Прогнать тест, убедиться что падает**

Команда: `./scripts/test.sh tests/test_admin_routing.py -q`
Ожидается: FAIL — `test_search_filter_lets_commands_through` получает `True` вместо `False`; `test_admin_command_from_a_stranger_gets_the_generic_reply` падает на `assert message.answers` (не-админ не получает ничего).

- [ ] **Шаг 3: Реализовать**

Добавить `get_main_menu_kb` в импорт клавиатур в `bot/handlers/admin.py`:

```python
from bot.keyboards.inline import (
    SUB_ASK,
    SUB_CONFIRM,
    get_admin_menu_kb,
    get_main_menu_kb,
    get_sub_confirm_kb,
    get_user_card_kb,
    parse_sub_action_cb,
)
```

Заменить `cmd_admin` (`:65-69`):

```python
@router.message(Command("admin"))
async def cmd_admin(message: Message):
    if not is_admin(message.from_user.id):
        # Не-админ не должен отличать закрытую панель от несуществующей команды:
        # отвечаем тем же, чем catch-all в user.py отвечает на любое непонятное
        # сообщение. Говорить «нет прав» нельзя — это подтверждает, что панель
        # существует. Раньше сообщение просто поглощалось: прав не выдавало, но
        # выглядело как падение бота.
        await message.answer("Выбери действие 👇", reply_markup=get_main_menu_kb(is_admin=False))
        return
    await message.answer("⚙️ <b>Админ-панель</b>", reply_markup=get_admin_menu_kb())
```

Заменить `AdminSearchFilter` (`:75-80`):

```python
class AdminSearchFilter(Filter):
    async def __call__(self, message: Message) -> bool:
        if not is_admin(message.from_user.id):
            return False
        if message.from_user.id not in admin_waiting_search:
            return False

        text = (message.text or "").strip()
        if text.startswith("/"):
            # admin_router включён первым, поэтому в режиме поиска этот хендлер
            # матчил раньше Command("start") из user_router и «/start» уходил
            # в int() → «Неверный формат». Команду пропускаем дальше и режим
            # поиска снимаем: админ явно переключился на другое, иначе
            # следующее обычное сообщение стало бы поисковым запросом.
            admin_waiting_search.discard(message.from_user.id)
            return False
        return True
```

- [ ] **Шаг 4: Прогнать тесты**

Команда: `./scripts/test.sh -q`
Ожидается: PASS, регрессий нет.

- [ ] **Шаг 5: Коммит**

```bash
git add bot/handlers/admin.py tests/test_admin_routing.py
git commit -m "fix(admin): stop search mode from eating commands, answer /admin for everyone"
```
## Пакет V — Проверки гипотез (ветка `feat/local-bot-api`)

Три находки помечены в `.claude/TODO_FIXES.md` как открытый вопрос или подтверждённые частично. Чинить их вслепую нельзя: каждая «починка» здесь — это изменение параметров, которые уже подобраны экспериментально, и неверная гипотеза сделает хуже. Поэтому вместо правок — диагностика с заранее объявленным критерием решения.

Задачи пакета **кода не меняют** и **между собой независимы** — можно вести все три параллельно. Результат каждой записывается в `.claude/TODO_FIXES.md` (файл в `.gitignore`, в публичный репозиторий не попадает) в раздел «Открытые вопросы», рядом с формулировкой гипотезы. Если проверка подтверждает гипотезу — заводится отдельная задача на починку, в этот план она не входит.

Задачам 34 и 35 нужны сеть и живые куки. Задача 36 заблокирована до Task 14 плана local-bot-api.

---

### Task 34: Жив ли пин клиента `android_vr` для YouTube

**Закрывает:** первый открытый вопрос из `.claude/TODO_FIXES.md` («Работает ли ещё явный выбор клиента `android_vr` после yt-dlp#17461»)

**Files:**
- Modify: `.claude/TODO_FIXES.md` (запись результата; файл в `.gitignore`)

**Interfaces:**
- Consumes: собранный образ `jw_downloader:test` из Task 6.
- Produces: решение «пин оставить» или «пин снять», записанное с доказательством.

`downloader.py:191` пинит `youtube:player_client=web_safari,android_vr,tv`. yt-dlp#17461 убрал `android_vr` из клиентов по умолчанию, и два независимых чтения исходников разошлись в том, работает ли ещё явное указание. Разница дорогая: если клиент молча игнорируется, селектор формата упирается в то, что отдают оставшиеся клиенты, и ролик уходит в 360p при живом 1080p.

Проверяется одним прогоном с `-v`: verbose-вывод yt-dlp перечисляет, какие клиенты он реально опрашивал, и жалуется на неизвестные. Ролик взят намеренно старый и заведомо публичный (Big Buck Bunny, официальная публикация Blender Foundation) — он переживает любые изменения политик и есть в 1080p.

- [ ] **Шаг 1: Снять список форматов с текущим пином**

```bash
docker run --rm --network host -v "$PWD/secrets:/app/secrets:ro" jw_downloader:test \
    yt-dlp -v --simulate --no-playlist \
    --extractor-args "youtube:player_client=web_safari,android_vr,tv" \
    -F "https://www.youtube.com/watch?v=aqz-KE-bpKQ" 2>&1 | tee /tmp/yt-pinned.txt
```

- [ ] **Шаг 2: Снять список форматов без пина**

```bash
docker run --rm --network host -v "$PWD/secrets:/app/secrets:ro" jw_downloader:test \
    yt-dlp -v --simulate --no-playlist \
    -F "https://www.youtube.com/watch?v=aqz-KE-bpKQ" 2>&1 | tee /tmp/yt-default.txt
```

- [ ] **Шаг 3: Сравнить**

```bash
grep -iE "player_client|android_vr|unsupported client|skipping client|Downloading .* player API" /tmp/yt-pinned.txt
echo "--- максимальная высота с пином ---"
grep -oE "[0-9]{3,4}x[0-9]{3,4}" /tmp/yt-pinned.txt | sort -t x -k2 -n | tail -1
echo "--- максимальная высота без пина ---"
grep -oE "[0-9]{3,4}x[0-9]{3,4}" /tmp/yt-default.txt | sort -t x -k2 -n | tail -1
echo "--- есть ли avc1 1080p с пином ---"
grep -c "avc1.*1920x1080" /tmp/yt-pinned.txt
```

- [ ] **Шаг 4: Принять решение по критерию**

- **Пин оставить**, если verbose-вывод показывает обращение к `android_vr` (строка про его player API) и в таблице форматов с пином есть `avc1` в 1920×1080.
- **Пин снять** (`--extractor-args` убрать полностью), если verbose явно сообщает, что клиент неизвестен или пропущен, **либо** если максимальная высота без пина строго больше, чем с пином.
- **Оставить как есть и вернуться позже**, если оба прогона упали на анти-боте (в выводе `Sign in to confirm you're not a bot`): результат недостоверен, нужен прогон с живыми куками YouTube.

- [ ] **Шаг 5: Записать результат**

В `.claude/TODO_FIXES.md` под первым открытым вопросом дописать дату проверки, выбранный вариант и три цифры: обращался ли yt-dlp к `android_vr`, максимальная высота с пином, максимальная высота без пина. Если решение — «пин снять», завести отдельную задачу; в этот план она не входит, а селекторы YouTube в целом закрывает Task 7 плана local-bot-api.

---

### Task 35: Влияет ли отпечаток `chrome150` на отказы TikTok

**Закрывает:** второй открытый вопрос («гипотеза `DEFAULT_CHROME=chrome150` → детерминированные отказы TikTok»), пункт 3 раздела «Компоненты»

**Files:**
- Modify: `.claude/TODO_FIXES.md` (запись результата)

**Interfaces:**
- Consumes: собранный образ `jw_downloader:test`, версия curl_cffi из Task 6, Шаг 6.
- Produces: решение «пинить curl_cffi / задать `impersonate` / не трогать».

`curl_cffi/requests/impersonate.py:81` задаёт `DEFAULT_CHROME = "chrome150"`, и константа не менялась с 0.16.1 по 0.16.3 — то есть апгрейд пакета гипотезу не проверяет и не лечит. По первоисточнику yt-dlp#17604 цель `chrome-150` коррелирует с антибот-отказами TikTok, но контрибьютор с `chrome-146` воспроизвести не смог: подтверждено частично.

Важно: в проектной памяти уже зафиксировано, что TikTok-WAF флапает и лечится ретраем. Значит, одиночный прогон ничего не доказывает — нужен A/B с повторами и статистический критерий. Ретраи в самом боте на время проверки не участвуют: зовём `yt-dlp` напрямую.

Ссылку берём не из головы, а из журнала загрузок: нужна та, что заведомо качалась.

- [ ] **Шаг 1: Достать ссылку, которая раньше качалась успешно**

```bash
docker run --rm -v jw_downloader_bot_data:/app/data jw_downloader:test python -c "
import sqlite3
c = sqlite3.connect('/app/data/bot.db')
row = c.execute(\"SELECT url FROM download_log WHERE platform='tiktok' AND status='success' ORDER BY created_at DESC LIMIT 1\").fetchone()
print(row[0] if row else 'успешных загрузок TikTok в журнале нет')
"
```

Если журнал пуст — взять любую публичную ссылку на видео TikTok и зафиксировать её в записи результата. Ссылку в `docs/` не переносить.

- [ ] **Шаг 2: Десять прогонов с отпечатком по умолчанию**

Подставить ссылку из предыдущего шага вместо `$URL`:

```bash
for i in $(seq 1 10); do
  docker run --rm --network host -v "$PWD/secrets:/app/secrets:ro" jw_downloader:test \
      yt-dlp --simulate --no-playlist --socket-timeout 30 "$URL" >/dev/null 2>&1 \
      && echo "$i ok" || echo "$i fail"
  sleep 30
done | tee /tmp/tiktok-default.txt
```

- [ ] **Шаг 3: Десять прогонов с отпечатком `chrome124`**

```bash
for i in $(seq 1 10); do
  docker run --rm --network host -v "$PWD/secrets:/app/secrets:ro" jw_downloader:test \
      yt-dlp --simulate --no-playlist --socket-timeout 30 \
      --extractor-args "tiktok:impersonate=chrome124" "$URL" >/dev/null 2>&1 \
      && echo "$i ok" || echo "$i fail"
  sleep 30
done | tee /tmp/tiktok-chrome124.txt
```

- [ ] **Шаг 4: Посчитать**

```bash
echo "по умолчанию: $(grep -c ' ok' /tmp/tiktok-default.txt) из 10"
echo "chrome124:    $(grep -c ' ok' /tmp/tiktok-chrome124.txt) из 10"
```

- [ ] **Шаг 5: Принять решение по критерию**

- **Гипотеза подтверждена**, если по умолчанию успехов ≤ 3 из 10, а с `chrome124` ≥ 8 из 10. Тогда завести отдельную задачу: добавить `--extractor-args "tiktok:impersonate=chrome124"` в ветку TikTok в `_build_command`. Пин `curl_cffi==0.16.0` предпочитать не следует — он меняет отпечаток для всех платформ сразу, а нужен только TikTok.
- **Гипотеза отклонена**, если обе группы дали ≥ 8 из 10 либо разница между группами не больше двух прогонов. Тогда записать это как закрытый вопрос: отказы транзиторны, лечатся существующим ретраем, ничего не менять.
- **Результат недостоверен**, если обе группы дали ≤ 3 из 10: проблема не в отпечатке, а в куках или в IP. Тогда проверить свежесть кук TikTok и повторить.

- [ ] **Шаг 6: Записать результат**

В `.claude/TODO_FIXES.md` дописать дату, обе цифры, версию curl_cffi (из Task 6, Шаг 6) и принятое решение. Заодно уточнить существующую заметку проектной памяти «TikTok WAF флапает, лечится ретраем»: она верна или нет — теперь известно по цифрам.

---

### Task 36: Лимит `sendPhoto` на локальном Bot API

**Закрывает:** третий открытый вопрос («лимит `sendPhoto` 10 МБ на локальном Bot API по исходникам не подтверждён»)

**Files:**
- Modify: `.claude/TODO_FIXES.md` (запись результата)

**Interfaces:**
- Consumes: работающий контейнер `telegram-bot-api`.
- Produces: подтверждённое или опровергнутое число, от которого зависит порог в `user.py:302` и `:350`.

**ЗАДАЧА ЗАБЛОКИРОВАНА до Task 14 плана `docs/superpowers/plans/2026-09-03-local-bot-api.md`.** Локального Bot API ещё не существует, проверять нечего, а поднимать его раньше срока запрещено (ветка в промежуточном состоянии, ENOSPC в середине загрузки). Задача описана здесь, чтобы процедура не потерялась.

`user.py:302` и `:350` вынимают из альбома изображения крупнее 10 МБ и шлют их документом. Порог взят из лимита облачного Bot API. По исходникам `telegram-bot-api` это ограничение не подтверждается — константа, судя по всему, лежит в общем коде TDLib, а не в самом сервере. Если на локальном сервере порог выше, вынос крупных изображений в документы лишний: пользователь получает файл вместо картинки там, где мог бы получить картинку.

- [ ] **Шаг 1: Дождаться Task 14 плана local-bot-api**

Предусловие: `telegram-bot-api` поднят, бот на него переключён, смоук-тест отправки пройден. Раньше — не начинать.

- [ ] **Шаг 2: Подготовить изображения возрастающего размера**

```bash
docker run --rm -v /tmp:/out jw_downloader:test sh -c '
for mb in 8 12 20 40; do
  ffmpeg -v error -f lavfi -i "color=c=blue:s=8000x6000" -frames:v 1 \
    -q:v $((mb)) "/out/probe_${mb}mb.jpg"
  ls -l "/out/probe_${mb}mb.jpg"
done'
```

Ожидается: четыре JPEG. Если фактические размеры далеки от целевых — подобрать `-q:v` или разрешение так, чтобы получить файлы примерно на 8, 12, 20 и 40 МБ, и записать фактические размеры.

- [ ] **Шаг 3: Отправить каждый через `sendPhoto`**

Отправлять себе (админу) через локальный сервер, ссылкой `file://` — тем же способом, каким бот отдаёт медиа после Task 9 плана local-bot-api. Токен и chat_id в командной строке не писать: взять из окружения.

- [ ] **Шаг 4: Принять решение по критерию**

- Если `sendPhoto` принял 12 МБ — порог 10 МБ в `user.py` занижен; зафиксировать наибольший принятый размер и завести отдельную задачу на подъём порога до значения на ступень ниже первого отказа.
- Если 12 МБ отвергнут с `PHOTO_INVALID_DIMENSIONS` или аналогом про размер — порог 10 МБ подтверждён, ничего не менять.
- Если отказ пришёл по геометрии, а не по размеру (изображение 8000×6000 превышает лимит Telegram по сумме сторон) — повторить с меньшим разрешением и большим весом, иначе измеряется не тот лимит.

- [ ] **Шаг 5: Записать результат**

В `.claude/TODO_FIXES.md` дописать дату, фактические размеры файлов, ответ сервера на каждый и принятое решение.

---
## Финальная сборка

Одна задача, выполняется **после всех волн**, в одиночку. Трогает `bot/db/queries.py` — файл пакета C, — но к этому моменту ни один другой пакет не работает, поэтому конфликта нет.

---

### Task 37: Убрать мёртвые функции и свести ручные проверки

**Закрывает:** ничего из списка находок — интеграционный шаг.

**Files:**
- Modify: `bot/db/queries.py` (удаление `decrement_free_downloads` и `update_subscription`)
- Create: `tests/test_queries_api.py`
- Modify: `.claude/TODO_FIXES.md` (отметки, файл в `.gitignore`)

**Interfaces:**
- Consumes: всё, что сделали пакеты 0, A, B, C, D, E, F.
- Produces: ветку `feat/local-bot-api`, готовую к продолжению плана local-bot-api с его Task 4.

`decrement_free_downloads` и `update_subscription` пакет C заменил, но удалить их там было нельзя: `bot/handlers/user.py:17` и `bot/handlers/admin.py:10` импортируют эти имена на уровне модуля, а `bot/__main__.py` импортирует роутеры — удаление в волне 2 уронило бы импорт `bot.handlers` и весь набор тестов ещё до первого ассерта. После волны 3 обе функции никем не вызываются.

Оставлять их нельзя: обе — рабочие реализации ровно того поведения, которое мы починили (списание после доставки и складывающееся продление подписки). Рядом с правильными функциями они выглядят как альтернатива и рано или поздно будут вызваны снова.

- [ ] **Шаг 1: Написать падающий тест**

Создать `tests/test_queries_api.py`:

```python
import bot.db.queries as queries


def test_replacements_are_present():
    assert hasattr(queries, "reserve_free_download")
    assert hasattr(queries, "refund_free_download")
    assert hasattr(queries, "apply_subscription_change")


def test_superseded_quota_function_is_gone():
    """C-1: списание после доставки давало 3–7 скачиваний при одном оставшемся.
    Рабочая реализация этого поведения рядом с правильной — приглашение
    вызвать её снова."""
    assert not hasattr(queries, "decrement_free_downloads")


def test_superseded_subscription_function_is_gone():
    """H-12: складывающееся продление давало 60 дней за одну оплату."""
    assert not hasattr(queries, "update_subscription")
```

- [ ] **Шаг 2: Прогнать тест, убедиться что падает**

Команда: `./scripts/test.sh tests/test_queries_api.py -q`
Ожидается: FAIL — оба теста про удалённые функции падают, потому что функции на месте

- [ ] **Шаг 3: Убедиться, что вызовов не осталось**

```bash
grep -rn "decrement_free_downloads\|update_subscription" --include=*.py bot/ tests/ scripts/
```

Ожидается: совпадения только в `bot/db/queries.py` (сами определения и комментарии «УСТАРЕЛО») и в новом `tests/test_queries_api.py`. Любое другое совпадение означает, что пакет D или E не доведён до конца — остановиться и доделать его, а не удалять функцию.

- [ ] **Шаг 4: Удалить обе функции**

Из `bot/db/queries.py` удалить целиком: комментарий «УСТАРЕЛО…» и тело `update_subscription`, комментарий «УСТАРЕЛО…» и тело `decrement_free_downloads`.

- [ ] **Шаг 5: Прогнать весь набор**

Команда: `./scripts/test.sh -q`
Ожидается: PASS целиком — тесты пакетов 0, A, B, C, D, E, F и 46 тестов, существовавших до этого плана.

- [ ] **Шаг 6: Сверить, что ни один файл не правился двумя пакетами вслепую**

```bash
git log --oneline main..HEAD --name-only | sort -u | grep -E '^(bot|tests|scripts)/' | sort | uniq -c | sort -rn | head -20
```

Это не автоматическая проверка, а глазами: файлы, изменённые больше чем в одном пакете, должны совпадать со списком согласованных пересечений из раздела «Владение файлами». Неожиданный файл в списке — повод посмотреть его историю до мержа.

- [ ] **Шаг 7: Отметить закрытые находки**

В `.claude/TODO_FIXES.md` проставить `[x]` по списку: C-1, C-2 (частично — отметить, что осталась структурная часть), C-3, H-6, H-7 (частично), H-8, H-9, H-10, H-11, H-12, H-13, H-14, H-15, H-16 (частично — осталось действие владельца по правам файла), H-2, H-3, M-1, M-2, M-8, M-9, M-11, M-13, M-14, M-15, M-16, M-17, M-18, M-19, M-20, M-21, M-22, M-23, M-24, M-26, M-27, M-28, M-29, M-30, плюс закрытые находки Low. Открытыми остаются M-3, M-4, M-5 (проверить: закрыт Task 26), M-6, M-7, M-10, M-12, M-25 и всё из раздела «Сознательно не закрывается этим планом».

- [ ] **Шаг 8: Коммит**

```bash
git add bot/db/queries.py tests/test_queries_api.py
git commit -m "refactor(db): drop the superseded quota and subscription helpers"
```

- [ ] **Шаг 9: Передать владельцу список ручных проверок**

Ниже — сводка того, что автоматические тесты проверить не могут. Пройти её после выкладки.

---

## Что проверять руками

Автотесты закрывают логику, но не то, как это выглядит у пользователя в Telegram. Ниже — по экранам, а не по задачам: так проверять быстрее.

### Пользователь

| Что проверить | Откуда взялось | На что смотреть |
|---|---|---|
| `/start` и возврат в главное меню | Task 28 | Число бесплатных скачиваний — фактический остаток, а не всегда «3». У подписчика — не про бесплатные, а про подписку. |
| Экран «📊 Мой статус» | Task 28 | Формулировка «Осталось бесплатных: N из M», а не «N/M». |
| Экран «📖 Помощь» | Task 28 | Нет обещаний «за секунды». Число бесплатных — из настроек. |
| Экран «💳 Показать реквизиты» | Task 27 | Реквизиты с `&` или `<` в значении отображаются целиком и не роняют кнопку. Проверить, временно поставив в `.env` значение вида `NGUYEN VAN A & CO`. |
| Отправка ссылки в подписи к фото | Task 28 | Бот качает. Пересланное фото без ссылки — молчит, а не отвечает меню. |
| Загрузка `.gif` | Task 13, Task 29 | Приходит анимацией, а не статичным кадром; подпись не говорит «Фото». |
| Доска Pinterest | Task 12 | Приходит до десяти разных файлов, а не три. |
| Ссылки `pinterest.de`, `m.pinterest.com`, `pin.it/abc` без схемы, ссылка с точкой на конце | Task 15 | Принимаются. |
| Квота | Task 25 | С одним оставшимся скачиванием второе уходит в пейволл. При провале загрузки единица возвращается — проверить по экрану статуса. |
| Ошибка загрузки | Task 11 | Текст соответствует причине. Приватный пост — «приватное», удалённый — «не найдено», а не «cookies устарели» на всё подряд. |
| Быстрое долбление кнопкой | Task 10 | Приходит «⏳ Слишком часто», крутилка не висит. Обычная навигация по меню не отбивается. |
| Кнопки на сообщении старше 48 часов | Task 26 | Бот отвечает новым сообщением, а не молчит. |
| Блокировка бота во время загрузки | Task 26 | В логе INFO про ушедшего пользователя, а не ERROR с трейсбеком. |
| Рестарт бота с ссылкой, присланной во время простоя | Task 5 | Ответа нет, квота не списана. |

### Администратор

| Что проверить | Откуда взялось | На что смотреть |
|---|---|---|
| Поиск пользователя с именем вида `Ann <3` | Task 30 | Карточка открывается и показывает имя целиком. |
| Поиск по `@Ivan` при сохранённом `ivan` | Task 20 | Находит. |
| Даты подписки в карточке | Task 30 | С явной пометкой зоны. |
| Двойной клик по «+30 дней» | Task 31 | Появляется экран подтверждения; после подтверждения повторный клик по той же кнопке ничего не добавляет. |
| Кнопки «−7 дней» и «Снять подписку» | Task 31 | Работают, отражаются в карточке. |
| Старая карточка из истории чата | Task 31 | Кнопка `+7`/`+30` месячной давности сообщает, что устарела, и подписку не выдаёт. |
| Кнопка на пользователе, которого больше нет в БД | Task 32 | Честный отказ, а не «✅ Подписка выдана» и не зависшая крутилка. |
| `/start` в режиме поиска | Task 33 | Открывается главное меню, а не «Неверный формат». |
| `/admin` от обычного пользователя | Task 33 | Приходит главное меню, а не тишина. Про существование панели ничего не сообщается. |

### Выкладка

| Что проверить | Откуда взялось | На что смотреть |
|---|---|---|
| Версии в образе | Task 6 | `gallery-dl 1.32.12`, `aiogram 3.31.0`. |
| Подписи к медиа после апгрейда aiogram | Task 6 | Разметка не сломалась: bot-level дефолты теперь применяются там, где раньше игнорировались. |
| Загрузка по всем пяти платформам после снятия `--no-check-certificates` | Task 14 | Ничего не отвалилось на верификации TLS. |
| Права на том с базой перед первым запуском от uid 1000 | Task 24 | `docker run --rm -v jw_downloader_bot_data:/data alpine chown -R 1000:1000 /data` выполнен. Иначе контейнер не поднимется. |
| Потолок памяти | Task 23 | Крупная загрузка не приводит к OOM-kill (`docker inspect` → `State.OOMKilled`). |
| Индексы и уникальность ника в боевой БД | Task 21 | Миграция прошла, бэкап на месте, число пользователей и строк журнала не изменилось. |

---
