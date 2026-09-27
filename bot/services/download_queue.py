"""Очередь загрузок: не больше `MAX_LINKS_PER_USER` ссылок на пользователя.

Чистый модуль (без aiogram и БД): у каждого пользователя своя FIFO-линия
билетов — голова линии единственная качается, остальные ждут своей очереди
(Д1 плана `.superpowers/sdd/2026-09-27-queue/plan.md`). Модуль заменяет
`user_active_downloads`/`_try_take_user_slot` из `bot/handlers/user.py`:
правило «одна загрузка на пользователя» выполняется само — вызывающий код
качает только голову линии.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from enum import Enum

# Решение владельца 2026-09-27: не больше 5 ссылок на пользователя, считая
# ту, что качается, и те, что ждут (Д2 плана).
MAX_LINKS_PER_USER = 5


class Admission(Enum):
    """Итог попытки поставить ссылку в очередь пользователя."""

    ACCEPTED = "accepted"
    DUPLICATE = "duplicate"
    FULL = "full"


@dataclass(eq=False)
class QueueTicket:
    """Место одной ссылки в линии пользователя.

    `eq=False` — билеты сравниваются по идентичности объекта, а не по
    содержимому: два билета одного пользователя на один и тот же `url`
    (после снятия первого и повторной постановки) должны оставаться
    различимыми. `turn` взводится ровно тогда, когда билет становится
    головой линии — `wait_turn` просто ждёт это событие.
    """

    user_id: int
    url: str
    turn: asyncio.Event = field(default_factory=asyncio.Event, repr=False)


class DownloadQueue:
    """FIFO-линия билетов на каждого пользователя."""

    def __init__(self, max_per_user: int = MAX_LINKS_PER_USER) -> None:
        self._max_per_user = max_per_user
        self._lines: dict[int, list[QueueTicket]] = {}

    def admit(self, user_id: int, url: str) -> tuple[Admission, QueueTicket | None]:
        """Синхронно решает судьбу ссылки: без `await`, чтобы пачка ссылок
        из одного апдейта не проскочила лимит между двумя await-точками.
        Порядок проверок важен: повтор уже стоящей в линии ссылки — это
        дубликат, даже если линия уже заполнена, а не переполнение.
        """
        line = self._lines.setdefault(user_id, [])
        if any(ticket.url == url for ticket in line):
            return Admission.DUPLICATE, None
        if len(line) >= self._max_per_user:
            return Admission.FULL, None

        ticket = QueueTicket(user_id=user_id, url=url)
        line.append(ticket)
        if len(line) == 1:
            ticket.turn.set()
        return Admission.ACCEPTED, ticket

    def waiting_ahead(self, ticket: QueueTicket) -> int:
        """Сколько ссылок пользователя перед этим билетом, считая голову.

        0 — для головы линии и для билета, уже снятого через `withdraw`
        (такого билета в линии больше нет).
        """
        line = self._lines.get(ticket.user_id, [])
        try:
            return line.index(ticket)
        except ValueError:
            return 0

    def line_length(self, user_id: int) -> int:
        return len(self._lines.get(user_id, []))

    async def wait_turn(self, ticket: QueueTicket) -> None:
        """Ждёт, пока билет станет головой линии.

        Инвариант: не вызывать для билета, уже снятого через `withdraw`, —
        его `turn` больше никогда не взводится, и ожидание зависнет навсегда.
        """
        await ticket.turn.wait()

    def withdraw(self, ticket: QueueTicket) -> None:
        """Идемпотентно убирает билет из линии, где бы он ни стоял.

        Если снята голова — очередь переходит к новой голове (её `turn`
        взводится) и только к ней. Опустевшая линия удаляется из словаря
        целиком, а не остаётся пустым списком.
        """
        line = self._lines.get(ticket.user_id)
        if line is None or ticket not in line:
            return

        was_head = line[0] is ticket
        line.remove(ticket)

        if not line:
            del self._lines[ticket.user_id]
            return

        if was_head:
            line[0].turn.set()
