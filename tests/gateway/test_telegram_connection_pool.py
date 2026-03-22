"""Regression test: Telegram adapter must use separate connection pools
for regular API requests and long-poll get_updates to prevent
httpx.ReadError when streaming edits saturate the shared pool."""

import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from gateway.config import PlatformConfig


def _ensure_telegram_mock():
    if "telegram" in sys.modules and hasattr(sys.modules["telegram"], "__file__"):
        return

    telegram_mod = MagicMock()
    telegram_mod.ext.ContextTypes.DEFAULT_TYPE = type(None)
    telegram_mod.constants.ParseMode.MARKDOWN_V2 = "MarkdownV2"
    telegram_mod.constants.ChatType.GROUP = "group"
    telegram_mod.constants.ChatType.SUPERGROUP = "supergroup"
    telegram_mod.constants.ChatType.CHANNEL = "channel"
    telegram_mod.constants.ChatType.PRIVATE = "private"

    for name in ("telegram", "telegram.ext", "telegram.constants", "telegram.request"):
        sys.modules.setdefault(name, telegram_mod)


_ensure_telegram_mock()

from gateway.platforms.telegram import TelegramAdapter  # noqa: E402


@pytest.mark.asyncio
async def test_connect_uses_separate_connection_pools(monkeypatch):
    """connect() must configure separate HTTPXRequest instances for
    regular API traffic and get_updates polling."""
    adapter = TelegramAdapter(PlatformConfig(enabled=True, token="test-token"))

    monkeypatch.setattr(
        "gateway.status.acquire_scoped_lock",
        lambda scope, identity, metadata=None: (True, None),
    )
    monkeypatch.setattr(
        "gateway.status.release_scoped_lock",
        lambda scope, identity: None,
    )

    # Track HTTPXRequest instantiations
    created_requests = []

    class FakeHTTPXRequest:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            created_requests.append(self)

    monkeypatch.setattr(
        "gateway.platforms.telegram.HTTPXRequest", FakeHTTPXRequest,
    )

    async def fake_start_polling(**kwargs):
        pass

    updater = SimpleNamespace(
        start_polling=AsyncMock(side_effect=fake_start_polling),
        stop=AsyncMock(),
        running=True,
    )
    bot = SimpleNamespace(set_my_commands=AsyncMock())
    app = SimpleNamespace(
        bot=bot,
        updater=updater,
        add_handler=MagicMock(),
        initialize=AsyncMock(),
        start=AsyncMock(),
    )

    builder = MagicMock()
    builder.token.return_value = builder
    builder.request.return_value = builder
    builder.get_updates_request.return_value = builder
    builder.get_updates_read_timeout.return_value = builder
    builder.build.return_value = app
    monkeypatch.setattr(
        "gateway.platforms.telegram.Application",
        SimpleNamespace(builder=MagicMock(return_value=builder)),
    )

    ok = await adapter.connect()
    assert ok is True

    # Two separate HTTPXRequest instances must have been created
    assert len(created_requests) == 2, (
        f"Expected 2 HTTPXRequest instances (api + polling), got {len(created_requests)}"
    )

    # The main request should have a larger pool
    api_req = created_requests[0]
    assert api_req.kwargs.get("connection_pool_size", 0) > 1

    # The get_updates request should be a separate instance
    polling_req = created_requests[1]
    assert polling_req is not api_req

    # Builder must have been called with both request instances
    builder.request.assert_called_once_with(api_req)
    builder.get_updates_request.assert_called_once_with(polling_req)
    builder.get_updates_read_timeout.assert_called_once()
