from bot import __main__ as entrypoint


class FakeCallback:
    def __init__(self):
        self.answers = []

    async def answer(self, text=None, show_alert=None):
        self.answers.append((text, show_alert))


class FakeMessage:
    def __init__(self):
        self.answers = []

    async def answer(self, text, **kwargs):
        self.answers.append(text)


class FakeUpdate:
    update_id = 4242

    def __init__(self, callback_query=None, message=None):
        self.callback_query = callback_query
        self.message = message


class FakeErrorEvent:
    def __init__(self, update, exception):
        self.update = update
        self.exception = exception


async def test_callback_gets_answered_so_the_spinner_stops():
    callback = FakeCallback()
    event = FakeErrorEvent(FakeUpdate(callback_query=callback), RuntimeError("бум"))

    await entrypoint.on_unhandled_error(event)

    assert len(callback.answers) == 1
    assert callback.answers[0][0]


async def test_message_gets_a_reply():
    message = FakeMessage()
    event = FakeErrorEvent(FakeUpdate(message=message), RuntimeError("бум"))

    await entrypoint.on_unhandled_error(event)

    assert len(message.answers) == 1


async def test_handler_never_raises_when_answering_fails():
    class DeadCallback:
        async def answer(self, text=None, show_alert=None):
            raise RuntimeError("юзер заблокировал бота")

    event = FakeErrorEvent(FakeUpdate(callback_query=DeadCallback()), ValueError("бум"))

    await entrypoint.on_unhandled_error(event)  # не должно бросить


async def test_handler_survives_update_without_message_or_callback():
    event = FakeErrorEvent(FakeUpdate(), KeyError("бум"))

    await entrypoint.on_unhandled_error(event)  # не должно бросить
