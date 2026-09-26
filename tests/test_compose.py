from pathlib import Path

import yaml

from bot.config import Settings

ROOT = Path(__file__).resolve().parent.parent


def _compose() -> dict:
    return yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))


def _service(name: str) -> dict:
    return _compose()["services"][name]


def _bot_service() -> dict:
    return _service("bot")


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


def test_downloads_are_not_on_tmpfs():
    # Task 14: загрузки уехали с tmpfs (200М в RAM) на диск. Ключа может не
    # быть вовсе, а если по какой-то причине остался — в нём точно не должно
    # быть записи про jw_downloads.
    service = _bot_service()
    tmpfs = service.get("tmpfs")
    if tmpfs is None:
        return
    assert not any("jw_downloads" in entry for entry in tmpfs)


def test_downloads_are_bind_mounted_from_host_disk():
    mounts = _bot_service()["volumes"]
    matches = [m for m in mounts if m.startswith("/mnt/storage/jw_downloads:")]
    assert matches, "bind-mount загрузок на диск хоста пропал"
    source, target = matches[0].split(":", 1)
    assert source == "/mnt/storage/jw_downloads"
    # target не должен разъезжаться с дефолтом bot.config.DOWNLOAD_ROOT —
    # иначе бот и compose снова смогут разойтись по пути загрузок.
    # (Не settings.DOWNLOAD_ROOT: conftest.py переопределяет его для тестов
    # на /tmp/jw_test_downloads, чтобы тесты не писали в /srv/jw_downloads.)
    assert target == Settings.model_fields["DOWNLOAD_ROOT"].default


def test_telegram_bot_api_image_is_pinned():
    # :latest невоспроизводим: дайджест менялся без коммитов в апстриме.
    assert _service("telegram-bot-api")["image"] == "aiogram/telegram-bot-api:10.3"


def test_telegram_bot_api_uses_host_network_and_loopback_only():
    service = _service("telegram-bot-api")
    assert service["network_mode"] == "host"
    assert service["environment"]["TELEGRAM_HTTP_IP_ADDRESS"] == "127.0.0.1"
    assert "ports" not in service


def test_telegram_bot_api_credentials_come_from_env_not_literals():
    environment = _service("telegram-bot-api")["environment"]
    assert environment["TELEGRAM_API_ID"] == "${TELEGRAM_API_ID}"
    assert environment["TELEGRAM_API_HASH"] == "${TELEGRAM_API_HASH}"
    assert environment["TELEGRAM_LOCAL"]


def test_telegram_bot_api_has_a_memory_limit():
    assert _service("telegram-bot-api").get("mem_limit")


def test_telegram_bot_api_stat_port_is_not_enabled():
    # Стат-порт отдаёт полный токен открытым текстом.
    service = _service("telegram-bot-api")
    assert "TELEGRAM_STAT" not in service.get("environment", {})
    raw = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert "8082" not in raw


def test_telegram_bot_api_temp_dir_is_on_the_external_disk():
    mounts = _service("telegram-bot-api")["volumes"]
    matches = [m for m in mounts if m.split(":")[1] == "/tmp/telegram-bot-api"]
    assert matches, "temp-dir сервера не смонтирован"
    source = matches[0].split(":")[0]
    assert source.startswith("/mnt/storage/")


def test_telegram_bot_api_data_dir_is_outside_the_downloads_tree():
    mounts = _service("telegram-bot-api")["volumes"]
    matches = [m for m in mounts if m.split(":")[1] == "/var/lib/telegram-bot-api"]
    assert matches, "data-dir сервера (папка с токеном) не смонтирован"
    source = matches[0].split(":")[0]
    assert source.startswith("/mnt/storage/jw_tg_api")
    assert not source.startswith("/mnt/storage/jw_downloads")


def test_telegram_bot_api_does_not_mount_the_downloads_folder():
    mounts = _service("telegram-bot-api")["volumes"]
    assert not any("/mnt/storage/jw_downloads" in m for m in mounts)


def test_bot_service_does_not_declare_the_transport_switch():
    # Переключатель живёт только в .env, не в compose.
    service = _bot_service()
    assert "USE_LOCAL_BOT_API" not in service.get("environment", {})


def test_bot_service_does_not_depend_on_the_local_server():
    # В облачном режиме бот не должен зависеть от сервера.
    assert "depends_on" not in _bot_service()


def test_bot_smoke_is_opt_in_via_profile():
    assert _service("bot-smoke")["profiles"] == ["smoke"]


def test_bot_smoke_uses_a_separate_test_bot_token():
    # ":-", а не ":?": форма ":?" сломала бы любые compose-команды, если
    # переменной ещё нет в .env.
    assert _service("bot-smoke")["environment"]["BOT_TOKEN"] == "${TEST_BOT_TOKEN:-}"


def test_bot_smoke_runs_on_the_local_transport():
    assert _service("bot-smoke")["environment"]["USE_LOCAL_BOT_API"] == "true"


def test_bot_smoke_has_its_own_container_name():
    assert _service("bot-smoke")["container_name"] != _bot_service()["container_name"]


def test_bot_smoke_does_not_share_the_production_data_volume():
    mounts = _service("bot-smoke")["volumes"]
    data = [m for m in mounts if m.endswith(":/app/data")]
    assert data, "том с данными смоук-бота пропал"
    source = data[0].split(":", 1)[0]
    assert source != "bot_data"


def test_bot_smoke_uses_a_separate_database():
    assert "smoke" in _service("bot-smoke")["environment"]["DATABASE_URL"]
