def test_bot_config_imports():
    from bot.config import settings

    assert settings.BOT_TOKEN == "123456:TEST-TOKEN-NOT-REAL"
