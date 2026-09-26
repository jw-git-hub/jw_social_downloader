from __future__ import annotations

from types import SimpleNamespace

from bot.services import disk_space


def _usage(free_bytes: int):
    return SimpleNamespace(free=free_bytes)


def test_has_free_space_true_when_plenty(tmp_path, monkeypatch):
    monkeypatch.setattr(
        disk_space, "disk_usage", lambda path: _usage(10 * disk_space.BYTES_PER_GB)
    )
    assert disk_space.has_free_space(tmp_path, 5) is True


def test_has_free_space_false_when_not_enough(tmp_path, monkeypatch):
    monkeypatch.setattr(
        disk_space, "disk_usage", lambda path: _usage(int(3.9 * disk_space.BYTES_PER_GB))
    )
    assert disk_space.has_free_space(tmp_path, 5) is False


def test_has_free_space_true_exactly_at_threshold(tmp_path, monkeypatch):
    monkeypatch.setattr(
        disk_space, "disk_usage", lambda path: _usage(5 * disk_space.BYTES_PER_GB)
    )
    assert disk_space.has_free_space(tmp_path, 5) is True


def test_has_free_space_fails_open_on_oserror(tmp_path, monkeypatch):
    def _raise(path):
        raise OSError("no such device")

    monkeypatch.setattr(disk_space, "disk_usage", _raise)
    assert disk_space.has_free_space(tmp_path, 5) is True
