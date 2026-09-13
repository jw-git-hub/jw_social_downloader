import asyncio
import re

from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker

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


async def test_reservation_is_a_single_guarded_update_statement(db_session, make_user):
    """Структурный guard на суть фикса C-1: reserve_free_download обязан
    оставаться ОДНИМ оператором UPDATE с условием free_downloads_left>0
    внутри него самого (не отдельным SELECT для проверки + отдельным
    безусловным UPDATE для записи). Перехватываем before_cursor_execute на
    движке db_session и смотрим на форму запроса, а не только на результат:
    check-then-act даёт два оператора и UPDATE без предиката — этот тест
    должен покраснеть, даже если бы у наивной реализации по случайности
    совпал результат.
    """
    async with db_session.begin():
        db_session.add(make_user(free_downloads_left=1))

    seen: list[str] = []
    engine = db_session.get_bind()

    def _record(conn, cur, statement, params, context, executemany):
        seen.append(" ".join(statement.split()))

    event.listen(engine, "before_cursor_execute", _record)
    try:
        async with db_session.begin():
            assert await reserve_free_download(db_session, 1000000001) is True
    finally:
        event.remove(engine, "before_cursor_execute", _record)

    assert len(seen) == 1, seen  # один оператор, а не «прочитать» + «записать»
    assert seen[0].upper().startswith("UPDATE USERS")
    assert re.search(r"free_downloads_left\s*>\s*\?", seen[0])  # предикат ВНУТРИ UPDATE


async def test_concurrent_reservations_yield_exactly_one_winner(
    db_session, make_user, tmp_path, sqlite_engine_factory
):
    """C-1, подтверждающий тест на реальной гонке (не последовательный вызов
    в одной транзакции, как test_last_unit_can_be_taken_only_once, — два
    независимых соединения к одному и тому же файлу БД).

    Две детали обязательны, без них тест вырождается в холостой:
    - asyncio.Barrier ставится НЕПОСРЕДСТВЕННО перед reserve_free_download —
      иначе первая попытка успевает закоммититься до старта второй, и
      реальной гонки не будет;
    - каждая попытка сначала делает SELECT (session.get) в той же
      транзакции — это форма будущего вызова из bot/handlers/user.py
      (get_or_create_user читает пользователя первым), и без этого шага
      даже сломанная реализация может выглядеть безопасной на этом движке.
    """
    async with db_session.begin():
        db_session.add(make_user(free_downloads_left=1))

    engine_b = sqlite_engine_factory(tmp_path / "test.db")
    maker_b = async_sessionmaker(engine_b, expire_on_commit=False)
    barrier = asyncio.Barrier(2)

    async def attempt_a():
        async with db_session.begin():
            await db_session.get(User, 1000000001)
            await barrier.wait()
            return await reserve_free_download(db_session, 1000000001)

    async def attempt_b():
        async with maker_b() as session:
            async with session.begin():
                await session.get(User, 1000000001)
                await barrier.wait()
                return await reserve_free_download(session, 1000000001)

    try:
        results = await asyncio.gather(attempt_a(), attempt_b())
    finally:
        await engine_b.dispose()

    assert sorted(results) == [False, True]  # ровно один победитель
    assert await _left(db_session, 1000000001) == 0  # наивная реализация уводит в -1
