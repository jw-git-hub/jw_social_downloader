from bot import __main__ as entrypoint


async def test_polling_drops_pending_updates():
    """M-19: очередь апдейтов за время простоя переигрываться не должна."""
    seen = {}

    class FakeDispatcher:
        async def start_polling(self, bot, **kwargs):
            seen["bot"] = bot
            seen["kwargs"] = kwargs

    sentinel = object()
    await entrypoint.run_polling(FakeDispatcher(), sentinel)

    assert seen["bot"] is sentinel
    assert seen["kwargs"].get("drop_pending_updates") is True
