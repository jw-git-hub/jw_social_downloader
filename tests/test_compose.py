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
