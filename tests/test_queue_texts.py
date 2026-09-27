"""Тесты на bot.services.queue_texts: тексты — точная копия плана
(`.superpowers/sdd/2026-09-27-ecosystem-style/texts-plan.md`, раздел
«4. Готовые тексты для бота»).
"""

from __future__ import annotations

from bot.services.download_queue import Admission
from bot.services.progress_texts import PREPARING_TEXT
from bot.services.queue_texts import interrupted_text, queue_status_text, refusal_text
from bot.utils.text import LINE_MARKER


def test_queue_status_text_at_head_is_preparing_text():
    assert queue_status_text(0) == PREPARING_TEXT


def test_queue_status_text_for_a_waiting_link():
    assert queue_status_text(1) == (
        "<b>В очереди: 1-я</b>\n"
        "Качаю ваши ссылки по одной. До этой дойду сам — присылать заново не нужно."
    )
    assert queue_status_text(3) == (
        "<b>В очереди: 3-я</b>\n"
        "Качаю ваши ссылки по одной. До этой дойду сам — присылать заново не нужно."
    )


def test_refusal_text_single_duplicate_has_no_full_paragraph():
    text = refusal_text([("https://youtu.be/abc", Admission.DUPLICATE)], max_links=5)
    assert text == f"<b>Не поставил в очередь:</b>\n{LINE_MARKER}youtu.be/abc — уже в очереди"


def test_refusal_text_includes_full_paragraph_only_when_full_is_among_refusals():
    text = refusal_text(
        [
            ("https://youtu.be/abc", Admission.DUPLICATE),
            ("https://youtu.be/def", Admission.FULL),
        ],
        max_links=5,
    )
    assert text == (
        "<b>Не поставил в очередь:</b>\n"
        f"{LINE_MARKER}youtu.be/abc — уже в очереди\n"
        f"{LINE_MARKER}youtu.be/def — очередь заполнена\n\n"
        "В очереди может быть не больше 5 ссылок. Когда текущие скачаются, пришлите остальные ещё раз."
    )


def test_refusal_text_shows_tail_when_more_than_ten_refusals():
    refused = [(f"https://youtu.be/{i:03d}", Admission.DUPLICATE) for i in range(12)]
    text = refusal_text(refused, max_links=5)
    lines = text.splitlines()

    assert lines[0] == "<b>Не поставил в очередь:</b>"
    assert len(lines) == 12  # заголовок + 10 показанных ссылок + строка хвоста
    assert lines[-1] == "…и ещё 2"


def test_refusal_text_escapes_html_special_characters_in_the_link():
    text = refusal_text([("https://youtu.be/<a>&b", Admission.DUPLICATE)], max_links=5)
    assert f"{LINE_MARKER}youtu.be/&lt;a&gt;&amp;b — уже в очереди" in text


def test_refusal_text_strips_scheme_and_www():
    text = refusal_text(
        [("https://www.youtube.com/watch?v=abc", Admission.DUPLICATE)], max_links=5
    )
    assert f"{LINE_MARKER}youtube.com/watch?v=abc — уже в очереди" in text


def test_refusal_text_truncates_a_long_link():
    url = "https://youtube.com/watch?v=" + "a" * 80
    text = refusal_text([(url, Admission.DUPLICATE)], max_links=5)
    bullet_line = text.splitlines()[1]
    link_part = bullet_line[len(LINE_MARKER):bullet_line.index(" — ")]

    assert len(link_part) == 60
    assert link_part.endswith("…")


def test_interrupted_text_without_a_refund():
    text = interrupted_text(["https://youtu.be/a", "https://youtu.be/b"], refunded=False)
    assert text == (
        "<b>Бот перезапускался, и эти ссылки не скачались:</b>\n"
        f"{LINE_MARKER}youtu.be/a\n"
        f"{LINE_MARKER}youtu.be/b\n\n"
        "Пришлите их ещё раз."
    )


def test_interrupted_text_with_a_refund():
    text = interrupted_text(["https://youtu.be/a"], refunded=True)
    assert text == (
        "<b>Бот перезапускался, и эти ссылки не скачались:</b>\n"
        f"{LINE_MARKER}youtu.be/a\n\n"
        "Пришлите их ещё раз — бесплатные скачивания за них возвращены."
    )
