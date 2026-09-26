from datetime import timedelta

from bot.utils.text import esc, format_wait


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


async def test_db_session_fixture_gives_a_working_schema(db_session, make_user):
    from sqlalchemy import select

    from bot.db.models import User

    async with db_session.begin():
        db_session.add(make_user(username="Ann"))

    found = await db_session.scalar(select(User).where(User.username == "Ann"))
    assert found is not None


def test_format_wait_rounds_up_to_minutes():
    assert format_wait(timedelta(0)) == "1 мин"
    assert format_wait(timedelta(seconds=59)) == "1 мин"
    assert format_wait(timedelta(seconds=61)) == "2 мин"
    assert format_wait(timedelta(minutes=12)) == "12 мин"


def test_format_wait_hours():
    assert format_wait(timedelta(hours=3)) == "3 ч"
    assert format_wait(timedelta(hours=5, minutes=12)) == "5 ч 12 мин"
    assert format_wait(timedelta(hours=24)) == "24 ч"
    assert format_wait(timedelta(hours=5, minutes=11, seconds=1)) == "5 ч 12 мин"
