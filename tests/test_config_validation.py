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
