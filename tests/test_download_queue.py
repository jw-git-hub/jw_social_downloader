"""Тесты на bot.services.download_queue.

Модуль чистый (без aiogram и БД), поэтому тесты проверяют FIFO-логику линий
напрямую: кто держит очередь `turn`, как считается `waiting_ahead` и что
происходит с линией при переполнении/дубликатах/снятии билетов.
"""

from __future__ import annotations

import asyncio

from bot.services.download_queue import Admission, DownloadQueue


def test_first_ticket_gets_turn_immediately_second_does_not():
    queue = DownloadQueue()
    _, first = queue.admit(1, "a")
    _, second = queue.admit(1, "b")

    assert first.turn.is_set()
    assert not second.turn.is_set()


def test_waiting_ahead_matches_position_in_line():
    queue = DownloadQueue()
    _, first = queue.admit(1, "a")
    _, second = queue.admit(1, "b")
    _, third = queue.admit(1, "c")

    assert queue.waiting_ahead(first) == 0
    assert queue.waiting_ahead(second) == 1
    assert queue.waiting_ahead(third) == 2


def test_withdrawing_head_passes_turn_to_next_ticket_only():
    queue = DownloadQueue()
    _, first = queue.admit(1, "a")
    _, second = queue.admit(1, "b")
    _, third = queue.admit(1, "c")

    queue.withdraw(first)

    assert second.turn.is_set()
    assert not third.turn.is_set()
    assert queue.waiting_ahead(second) == 0
    assert queue.waiting_ahead(third) == 1


def test_withdrawing_middle_ticket_keeps_head_and_shifts_tail():
    queue = DownloadQueue()
    _, first = queue.admit(1, "a")
    _, second = queue.admit(1, "b")
    _, third = queue.admit(1, "c")

    queue.withdraw(second)

    assert first.turn.is_set()
    assert not third.turn.is_set()
    assert queue.waiting_ahead(first) == 0
    assert queue.waiting_ahead(third) == 1


def test_sixth_ticket_is_full_then_accepted_after_a_withdrawal():
    queue = DownloadQueue()
    tickets = [queue.admit(1, f"url{i}")[1] for i in range(5)]

    admission, ticket = queue.admit(1, "url5")
    assert admission is Admission.FULL
    assert ticket is None

    queue.withdraw(tickets[0])
    admission, ticket = queue.admit(1, "url5")
    assert admission is Admission.ACCEPTED
    assert ticket is not None


def test_duplicate_of_head_and_of_a_waiting_ticket_is_rejected_then_accepted():
    queue = DownloadQueue()
    _, head = queue.admit(1, "a")
    queue.admit(1, "b")

    admission, ticket = queue.admit(1, "a")
    assert admission is Admission.DUPLICATE
    assert ticket is None

    admission, ticket = queue.admit(1, "b")
    assert admission is Admission.DUPLICATE
    assert ticket is None

    queue.withdraw(head)
    admission, ticket = queue.admit(1, "a")
    assert admission is Admission.ACCEPTED
    assert ticket is not None


def test_duplicate_on_a_full_line_is_duplicate_not_full():
    queue = DownloadQueue()
    for i in range(5):
        queue.admit(1, f"url{i}")

    admission, ticket = queue.admit(1, "url0")
    assert admission is Admission.DUPLICATE
    assert ticket is None


def test_different_users_do_not_affect_each_others_lines():
    queue = DownloadQueue()
    for i in range(5):
        queue.admit(1, f"url{i}")

    admission, ticket = queue.admit(2, "url0")
    assert admission is Admission.ACCEPTED
    assert ticket.turn.is_set()
    assert queue.line_length(2) == 1


def test_double_withdraw_is_safe_and_leaves_no_trace():
    queue = DownloadQueue()
    _, first = queue.admit(1, "a")
    _, second = queue.admit(1, "b")

    queue.withdraw(first)
    queue.withdraw(first)  # повторный withdraw — no-op
    queue.withdraw(second)

    assert queue.line_length(1) == 0
    assert queue._lines == {}


async def test_fifo_order_is_preserved_under_concurrent_wait_turn():
    queue = DownloadQueue()
    order: list[str] = []

    async def worker(ticket) -> None:
        await queue.wait_turn(ticket)
        order.append(ticket.url)
        queue.withdraw(ticket)

    tickets = [queue.admit(1, url)[1] for url in ("a", "b", "c")]

    await asyncio.gather(*(worker(ticket) for ticket in tickets))

    assert order == ["a", "b", "c"]
