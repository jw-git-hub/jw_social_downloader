from types import SimpleNamespace

from bot import __main__ as entrypoint


def _update(update_id, *, payment=False):
    message = SimpleNamespace(successful_payment=object() if payment else None)
    return SimpleNamespace(update_id=update_id, message=message)


class FakeBot:
    def __init__(self, batches, fail=False):
        self.batches = list(batches)
        self.fail = fail
        self.offsets = []
        self.webhook_drops = []

    async def delete_webhook(self, drop_pending_updates):
        self.webhook_drops.append(drop_pending_updates)

    async def get_updates(self, offset=None, timeout=None, limit=None, allowed_updates=None):
        if self.fail:
            raise RuntimeError("network down")
        self.offsets.append(offset)
        return self.batches.pop(0) if self.batches else []


class FakeDispatcher:
    def __init__(self):
        self.fed = []
        self.polling_kwargs = None

    def resolve_used_update_types(self):
        return ["message", "callback_query", "pre_checkout_query"]

    async def feed_update(self, bot, update):
        self.fed.append(update.update_id)

    async def start_polling(self, bot, **kwargs):
        self.polling_kwargs = kwargs


async def test_backlog_replays_only_payments_and_confirms_offset():
    bot = FakeBot([[_update(5, payment=True), _update(6)], [_update(7)]])
    dp = FakeDispatcher()

    await entrypoint.replay_pending_payments(bot, dp)

    assert dp.fed == [5]  # ссылка из простоя (6, 7) не качается
    assert bot.offsets == [None, 7, 8]  # последний запрос подтверждает выброс
    assert bot.webhook_drops == [False]


async def test_polling_starts_without_dropping_after_replay():
    dp = FakeDispatcher()
    await entrypoint.run_polling(dp, FakeBot([]))
    assert dp.polling_kwargs.get("drop_pending_updates") in (None, False)


async def test_replay_failure_falls_back_to_dropping_backlog():
    dp = FakeDispatcher()
    await entrypoint.run_polling(dp, FakeBot([], fail=True))
    assert dp.polling_kwargs.get("drop_pending_updates") is True
    assert dp.fed == []


def test_public_commands_include_payment_requirements():
    names = {c.command for c in entrypoint.PUBLIC_COMMANDS}
    assert {"start", "terms", "support", "paysupport"} <= names
