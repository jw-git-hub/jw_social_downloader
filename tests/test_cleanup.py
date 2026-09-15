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


def test_sweep_skips_unrelated_subdirectories(tmp_path, monkeypatch):
    """Каталоги без префикса jw_cookies_ подметальщик не трогает вовсе."""
    monkeypatch.setattr(cleanup, "DOWNLOAD_DIR", tmp_path)
    (tmp_path / "some_other_dir").mkdir()

    removed = cleanup.sweep_once(max_age_minutes=10, now=time.time() + 3600)

    assert removed == 0
    assert (tmp_path / "some_other_dir").is_dir()


def test_old_cookie_dir_is_removed(tmp_path, monkeypatch):
    """Осиротевший (пережил жёсткое убийство процесса) каталог кук подчищается."""
    monkeypatch.setattr(cleanup, "DOWNLOAD_DIR", tmp_path)
    monkeypatch.setattr(cleanup, "COOKIE_DIR_MAX_AGE_SEC", 1800)
    cookie_dir = tmp_path / "jw_cookies_abc"
    cookie_dir.mkdir()

    removed = cleanup.sweep_once(max_age_minutes=10, now=time.time() + 1800 + 60)

    assert removed == 1
    assert not cookie_dir.exists()


def test_fresh_cookie_dir_survives(tmp_path, monkeypatch):
    """Каталог кук идущей загрузки (моложе порога кук) не трогаем."""
    monkeypatch.setattr(cleanup, "DOWNLOAD_DIR", tmp_path)
    monkeypatch.setattr(cleanup, "COOKIE_DIR_MAX_AGE_SEC", 1800)
    cookie_dir = tmp_path / "jw_cookies_abc"
    cookie_dir.mkdir()

    removed = cleanup.sweep_once(max_age_minutes=10, now=time.time() + 60)

    assert removed == 0
    assert cookie_dir.is_dir()


def test_cookie_dir_between_thresholds_survives(tmp_path, monkeypatch):
    """H-регресс: между max_age_minutes (10 мин) и порогом кук (30 мин)
    каталог кук обязан пережить проход — иначе подметальщик срежет куки
    у ещё идущей загрузки (DOWNLOAD_TIMEOUT 15 мин)."""
    monkeypatch.setattr(cleanup, "DOWNLOAD_DIR", tmp_path)
    monkeypatch.setattr(cleanup, "COOKIE_DIR_MAX_AGE_SEC", 1800)
    cookie_dir = tmp_path / "jw_cookies_abc"
    cookie_dir.mkdir()

    # 15 минут: старше max_age_minutes*60 (600с), но моложе COOKIE_DIR_MAX_AGE_SEC (1800с).
    removed = cleanup.sweep_once(max_age_minutes=10, now=time.time() + 900)

    assert removed == 0
    assert cookie_dir.is_dir()


def test_unrelated_dir_survives_even_when_very_old(tmp_path, monkeypatch):
    """Посторонний каталог не подчищается ни по одному из порогов."""
    monkeypatch.setattr(cleanup, "DOWNLOAD_DIR", tmp_path)
    monkeypatch.setattr(cleanup, "COOKIE_DIR_MAX_AGE_SEC", 1800)
    other_dir = tmp_path / "some_other_dir"
    other_dir.mkdir()

    removed = cleanup.sweep_once(max_age_minutes=10, now=time.time() + TWO_MONTHS)

    assert removed == 0
    assert other_dir.is_dir()


def test_cookie_dir_threshold_is_derived_from_download_timeout():
    """Пин самой формулы, а не только ветвления sweep_once.

    Все тесты выше monkeypatch-ят COOKIE_DIR_MAX_AGE_SEC и потому не замечают,
    если реальную формулу в cleanup.py подменят на max_age_minutes*60 (10 мин,
    тот самый баг, которого всё ТЗ просило избежать). Порог кук обязан быть
    выведен из DOWNLOAD_TIMEOUT и быть строго больше 10 минут, иначе
    подметальщик срежет куки у ещё идущей загрузки (DOWNLOAD_TIMEOUT 15 мин).
    """
    assert cleanup.COOKIE_DIR_MAX_AGE_SEC == cleanup.settings.DOWNLOAD_TIMEOUT * 2
    assert cleanup.COOKIE_DIR_MAX_AGE_SEC > 10 * 60


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
