import asyncio
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from bot.services.media_probe import (
    REJECT_NO_AUDIO,
    REJECT_NO_DIMENSIONS,
    REJECT_NO_METADATA,
    REJECT_NO_VIDEO,
    REJECT_ZERO_DURATION,
    MediaInfo,
    _ffprobe_cmd,
    parse_ffprobe_json,
    probe_media,
    video_reject_code,
    video_reject_reason,
)


def _ffprobe_output(streams, duration="12.5"):
    return json.dumps({"streams": streams, "format": {"duration": duration}})


def test_parses_normal_video_with_audio():
    raw = _ffprobe_output([
        {"codec_type": "video", "width": 1920, "height": 1080},
        {"codec_type": "audio"},
    ])
    info = parse_ffprobe_json(raw)
    assert info == MediaInfo(
        has_video=True, has_audio=True, duration=12.5, width=1920, height=1080
    )


def test_detects_video_only_dash_fragment():
    raw = _ffprobe_output([{"codec_type": "video", "width": 1280, "height": 720}])
    info = parse_ffprobe_json(raw)
    assert info.has_video is True
    assert info.has_audio is False


def test_duration_falls_back_to_stream_when_format_lacks_it():
    raw = json.dumps({
        "streams": [
            {"codec_type": "video", "width": 640, "height": 480, "duration": "7.0"},
            {"codec_type": "audio"},
        ],
        "format": {},
    })
    assert parse_ffprobe_json(raw).duration == 7.0


def test_returns_none_on_garbage():
    assert parse_ffprobe_json("not json at all") is None
    assert parse_ffprobe_json("") is None


def test_rotated_video_swaps_dimensions():
    # Вертикальные ролики TikTok/Reels часто приходят с матрицей поворота.
    raw = json.dumps({
        "streams": [
            {
                "codec_type": "video",
                "width": 1920,
                "height": 1080,
                "side_data_list": [{"rotation": -90}],
            },
            {"codec_type": "audio"},
        ],
        "format": {"duration": "5.0"},
    })
    info = parse_ffprobe_json(raw)
    assert (info.width, info.height) == (1080, 1920)


# Находка 4: структурно неожиданный, но синтаксически валидный JSON не должен
# приводить к исключению — только к None.
@pytest.mark.parametrize(
    "raw",
    [
        json.dumps({"streams": "not-a-list", "format": {"duration": "1.0"}}),
        json.dumps({"streams": ["not-a-dict"], "format": {"duration": "1.0"}}),
        json.dumps({
            "streams": [{"codec_type": "video", "width": 1, "height": 1}],
            "format": "not-a-dict",
        }),
        json.dumps({
            "streams": [{
                "codec_type": "video", "width": 1, "height": 1,
                "side_data_list": "not-a-list",
            }],
            "format": {"duration": "1.0"},
        }),
        json.dumps({
            "streams": [{
                "codec_type": "video", "width": 1, "height": 1,
                "side_data_list": [1, 2, 3],
            }],
            "format": {"duration": "1.0"},
        }),
        json.dumps({
            "streams": [{"codec_type": "video", "width": "inf", "height": 1}],
            "format": {"duration": "1.0"},
        }),
        json.dumps({
            "streams": [{"codec_type": "video", "width": "nan", "height": 1}],
            "format": {"duration": "1.0"},
        }),
    ],
)
def test_parse_ffprobe_json_never_raises_on_structural_surprises(raw):
    # Тут не важно, вернётся None или разумный MediaInfo — важно, что не бросит.
    parse_ffprobe_json(raw)


@pytest.mark.parametrize(
    "info, expected_reason",
    [
        (None, "Не удалось прочитать метаданные файла"),
        (MediaInfo(False, True, 5.0, 0, 0), "В файле нет видеодорожки"),
        (MediaInfo(True, False, 5.0, 640, 480), "В файле нет звука"),
        (MediaInfo(True, True, 0.0, 640, 480), "У файла нулевая длительность"),
        (MediaInfo(True, True, 5.0, 0, 0), "У файла не определяется разрешение"),
    ],
)
def test_reject_reasons(info, expected_reason):
    # Находка 7: точное совпадение строки, а не подстроки — эти тексты уходят
    # пользователю дословно, и Задача 11 полагается на их точный вид.
    assert video_reject_reason(info) == expected_reason


def test_good_video_is_accepted():
    assert video_reject_reason(MediaInfo(True, True, 5.0, 1920, 1080)) is None


@pytest.mark.parametrize(
    "info, expected_code",
    [
        (None, REJECT_NO_METADATA),
        (MediaInfo(False, True, 5.0, 0, 0), REJECT_NO_VIDEO),
        (MediaInfo(True, False, 5.0, 640, 480), REJECT_NO_AUDIO),
        (MediaInfo(True, True, 0.0, 640, 480), REJECT_ZERO_DURATION),
        (MediaInfo(True, True, 5.0, 0, 0), REJECT_NO_DIMENSIONS),
    ],
)
def test_reject_codes(info, expected_code):
    assert video_reject_code(info) == expected_code


def test_good_video_code_is_none():
    assert video_reject_code(MediaInfo(True, True, 5.0, 1920, 1080)) is None


@pytest.mark.parametrize(
    "info",
    [
        None,
        MediaInfo(False, True, 5.0, 0, 0),
        MediaInfo(True, False, 5.0, 640, 480),
        MediaInfo(True, True, 0.0, 640, 480),
        MediaInfo(True, True, 5.0, 0, 0),
        MediaInfo(True, True, 5.0, 1920, 1080),
    ],
)
def test_reject_code_and_reason_agree_on_acceptance(info):
    assert (video_reject_code(info) is None) == (video_reject_reason(info) is None)


# ---------------------------------------------------------------------------
# probe_media: находки 1, 5, 6, 8 — интеграционные проверки на настоящих
# файлах и настоящем ffprobe-процессе, без моков.
# ---------------------------------------------------------------------------


def _have_ffmpeg_tools() -> bool:
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


@pytest.fixture(scope="session")
def media_files(tmp_path_factory):
    if not _have_ffmpeg_tools():
        pytest.skip("ffmpeg/ffprobe недоступны в этом окружении")

    media_dir = tmp_path_factory.mktemp("media_probe_fixtures")
    ok_path = media_dir / "ok.mp4"
    video_only_path = media_dir / "videoonly.mp4"

    subprocess.run(
        [
            "ffmpeg", "-v", "error", "-y",
            "-f", "lavfi", "-i", "testsrc=duration=1:size=64x64:rate=5",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
            "-c:v", "libx264", "-c:a", "aac", "-shortest", str(ok_path),
        ],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        [
            "ffmpeg", "-v", "error", "-y",
            "-f", "lavfi", "-i", "testsrc=duration=1:size=64x64:rate=5",
            "-c:v", "libx264", str(video_only_path),
        ],
        check=True,
        capture_output=True,
    )
    return {"ok": ok_path, "video_only": video_only_path}


async def test_probe_media_accepts_real_video_with_audio(media_files):
    info = await probe_media(media_files["ok"])
    assert info is not None
    assert info.has_video is True
    assert info.has_audio is True
    assert video_reject_reason(info) is None


async def test_probe_media_flags_real_video_only_file(media_files):
    # Именно этот случай — весь смысл модуля: video-only DASH-фрагмент
    # не должен маскироваться под готовое к отправке видео.
    info = await probe_media(media_files["video_only"])
    assert info is not None
    assert info.has_video is True
    assert info.has_audio is False
    assert video_reject_code(info) == REJECT_NO_AUDIO


# Находка 1: ffprobe завершается с ненулевым кодом на всех этих входах, но
# печатает в stdout что-то похожее на валидный JSON (вплоть до "{}"). Раньше
# probe_media код возврата не проверял и получал MediaInfo(False, False, 0, 0, 0)
# вместо None.
async def test_probe_media_returns_none_for_nonexistent_path(tmp_path):
    missing = tmp_path / "does-not-exist.mp4"
    assert await probe_media(missing) is None


async def test_probe_media_returns_none_for_directory(tmp_path):
    assert await probe_media(tmp_path) is None


async def test_probe_media_returns_none_for_zero_byte_file(tmp_path):
    empty = tmp_path / "empty.mp4"
    empty.write_bytes(b"")
    assert await probe_media(empty) is None


async def test_probe_media_returns_none_for_text_renamed_to_mp4(tmp_path):
    fake = tmp_path / "fake.mp4"
    fake.write_text("this is plain text, not a video, just wearing an mp4 name")
    assert await probe_media(fake) is None


async def test_probe_media_returns_none_for_unreadable_file(tmp_path):
    # В тестовом образе процесс работает от root, поэтому chmod 000 не
    # запрещает root'у чтение (CAP_DAC_OVERRIDE) — реальный код возврата тут
    # ненулевой из-за нечитаемого ffprobe'ом содержимого, а не EACCES. Это
    # всё равно валидная проверка находки 1: любой ненулевой код -> None.
    locked = tmp_path / "locked.mp4"
    locked.write_bytes(os.urandom(256))
    locked.chmod(0)
    try:
        assert await probe_media(locked) is None
    finally:
        locked.chmod(0o600)  # чтобы pytest мог убрать tmp_path за собой


async def test_probe_media_returns_none_for_dev_null():
    assert await probe_media(Path("/dev/null")) is None


async def test_probe_media_returns_none_when_ffprobe_missing_from_path(tmp_path, monkeypatch):
    empty_bin_dir = tmp_path / "empty-bin"
    empty_bin_dir.mkdir()
    monkeypatch.setenv("PATH", str(empty_bin_dir))
    some_file = tmp_path / "irrelevant.mp4"
    some_file.write_bytes(b"\x00")
    assert await probe_media(some_file) is None


# Находка 5: отмена задачи во время ожидания ffprobe должна убить и собрать
# дочерний процесс, а не оставить его висеть, и должна дать CancelledError
# распространиться дальше (не проглатывать).
async def test_probe_media_kills_child_process_on_cancellation(tmp_path):
    fifo_path = tmp_path / "blocking.mp4"
    os.mkfifo(fifo_path)
    task = asyncio.create_task(probe_media(fifo_path))
    # Дать ffprobe время реально открыть FIFO на чтение и заблокироваться —
    # открытие FIFO на чтение блокируется, пока не появится писатель.
    await asyncio.sleep(0.3)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.sleep(0.3)  # дать kill()/wait() отработать

    # Если бы дочерний ffprobe остался жив и держал FIFO открытым на чтение,
    # неблокирующее открытие на запись сразу бы удалось. Если процесс убит —
    # читателя нет, и открытие падает с ENXIO.
    with pytest.raises(OSError):
        fd = os.open(fifo_path, os.O_WRONLY | os.O_NONBLOCK)
        os.close(fd)


# ---------------------------------------------------------------------------
# Харденинг флагов: ffprobe не должен уметь ходить в сеть по ссылке,
# спрятанной внутри недоверенного контейнера. В trixie ~25 CVE ffmpeg висят
# в статусе "не патчим" (в т.ч. OOB-чтение в DASH-демуксере) — версией это
# не лечится, поэтому демуксеру запрещено открывать что-либо, кроме
# обычного файла.
# ---------------------------------------------------------------------------


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
    # Осознанное решение (см. media_probe.py): занижение probesize/
    # analyzeduration даёт ложное «нет звука» на файлах, где аудиодорожка
    # начинается не с нулевой отметки, а это отказ, видимый пользователю.
    cmd = _ffprobe_cmd(tmp_path / "clip.mp4")
    assert "-probesize" not in cmd
    assert "-analyzeduration" not in cmd


def test_ffprobe_command_matches_expected_shape(tmp_path):
    # Явная фиксация всей формы команды — чтобы будущая правка, случайно
    # потерявшая или переставившая флаг, была видна сразу, а не только через
    # частичные проверки выше.
    target = tmp_path / "clip.mp4"
    assert _ffprobe_cmd(target) == [
        "ffprobe", "-v", "error",
        "-protocol_whitelist", "file",
        "-show_streams", "-show_format",
        "-print_format", "json",
        str(target),
    ]


async def test_hardened_ffprobe_still_reads_a_real_file(media_files):
    # Регрессия: ограничение протоколов не должно мешать обычному локальному
    # файлу — он по-прежнему читается протоколом `file`.
    info = await probe_media(media_files["ok"])
    assert info is not None
    assert info.has_video is True
    assert info.has_audio is True
    assert info.duration > 0
    assert info.width > 0 and info.height > 0
