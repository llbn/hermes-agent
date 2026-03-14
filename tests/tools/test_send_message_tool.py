"""Tests for tools/send_message_tool.py."""

import asyncio
import json
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

def _ensure_telegram_mock():
    if "telegram" in sys.modules and hasattr(sys.modules["telegram"], "__file__"):
        return

    mod = MagicMock()
    mod.Bot = MagicMock()
    mod.ext.ContextTypes.DEFAULT_TYPE = type(None)
    mod.constants.ParseMode.HTML = "HTML"
    mod.constants.ChatType.GROUP = "group"
    mod.constants.ChatType.SUPERGROUP = "supergroup"
    mod.constants.ChatType.CHANNEL = "channel"
    mod.constants.ChatType.PRIVATE = "private"
    for name in ("telegram", "telegram.ext", "telegram.constants"):
        sys.modules.setdefault(name, mod)


_ensure_telegram_mock()

from gateway.config import Platform
from gateway.platforms.telegram import ParseMode
from tools.send_message_tool import _send_discord, _send_slack, _send_telegram, send_message_tool


def _run_async_immediately(coro):
    return asyncio.run(coro)


def _make_config():
    telegram_cfg = SimpleNamespace(enabled=True, token="fake-token", extra={})
    return SimpleNamespace(
        platforms={Platform.TELEGRAM: telegram_cfg},
        get_home_channel=lambda _platform: None,
    ), telegram_cfg


class TestSendMessageTool:
    def test_sends_to_explicit_telegram_topic_target(self):
        config, telegram_cfg = _make_config()

        with patch("gateway.config.load_gateway_config", return_value=config), \
             patch("tools.interrupt.is_interrupted", return_value=False), \
             patch("model_tools._run_async", side_effect=_run_async_immediately), \
             patch("tools.send_message_tool._send_to_platform", new=AsyncMock(return_value={"success": True})) as send_mock, \
             patch("gateway.mirror.mirror_to_session", return_value=True) as mirror_mock:
            result = json.loads(
                send_message_tool(
                    {
                        "action": "send",
                        "target": "telegram:-1001:17585",
                        "message": "hello",
                    }
                )
            )

        assert result["success"] is True
        send_mock.assert_awaited_once_with(Platform.TELEGRAM, telegram_cfg, "-1001", "hello", thread_id="17585")
        mirror_mock.assert_called_once_with("telegram", "-1001", "hello", source_label="cli", thread_id="17585")

    def test_resolved_telegram_topic_name_preserves_thread_id(self):
        config, telegram_cfg = _make_config()

        with patch("gateway.config.load_gateway_config", return_value=config), \
             patch("tools.interrupt.is_interrupted", return_value=False), \
             patch("gateway.channel_directory.resolve_channel_name", return_value="-1001:17585"), \
             patch("model_tools._run_async", side_effect=_run_async_immediately), \
             patch("tools.send_message_tool._send_to_platform", new=AsyncMock(return_value={"success": True})) as send_mock, \
             patch("gateway.mirror.mirror_to_session", return_value=True):
            result = json.loads(
                send_message_tool(
                    {
                        "action": "send",
                        "target": "telegram:Coaching Chat / topic 17585",
                        "message": "hello",
                    }
                )
            )

        assert result["success"] is True
        send_mock.assert_awaited_once_with(Platform.TELEGRAM, telegram_cfg, "-1001", "hello", thread_id="17585")


class TestSharedOutboundBehavior:
    @staticmethod
    def _mock_aiohttp_json_session(payloads, response_factory):
        mock_session = MagicMock()

        def _post(*args, **kwargs):
            payloads.append(kwargs["json"])
            response = response_factory()
            response.__aenter__ = AsyncMock(return_value=response)
            response.__aexit__ = AsyncMock(return_value=False)
            return response

        mock_session.post = MagicMock(side_effect=_post)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        return mock_session

    def test_send_slack_uses_adapter_formatting(self):
        payloads = []

        def _response():
            resp = MagicMock()
            resp.json = AsyncMock(return_value={"ok": True, "ts": "123.456"})
            return resp

        mock_session = self._mock_aiohttp_json_session(payloads, _response)

        with patch("aiohttp.ClientSession", return_value=mock_session):
            result = asyncio.run(_send_slack("xoxb-token", "C123", "**hello**"))

        assert result["success"] is True
        assert payloads[0]["text"] == "*hello*"

    def test_send_discord_uses_code_fence_aware_chunking(self):
        payloads = []
        message_ids = iter(["m1", "m2", "m3", "m4"])

        def _response():
            resp = MagicMock()
            resp.status = 200
            resp.text = AsyncMock(return_value="OK")
            resp.json = AsyncMock(return_value={"id": next(message_ids, "mx")})
            return resp

        mock_session = self._mock_aiohttp_json_session(payloads, _response)
        long_code = "Before\n```python\n" + ("x = 1\n" * 500) + "```\nAfter"

        with patch("aiohttp.ClientSession", return_value=mock_session):
            result = asyncio.run(_send_discord("bot-token", "123", long_code))

        assert result["success"] is True
        assert len(payloads) > 1
        assert all(payload["content"].count("```") % 2 == 0 for payload in payloads)

    def test_send_telegram_uses_html_chunking(self):
        bot_messages = []
        message_ids = iter([101, 102, 103])
        bold_text = "**" + ("hello " * 999) + "hello**"

        async def _send_message(**kwargs):
            bot_messages.append(kwargs)
            return SimpleNamespace(message_id=next(message_ids))

        mock_bot = MagicMock()
        mock_bot.send_message = AsyncMock(side_effect=_send_message)

        with patch("telegram.Bot", return_value=mock_bot):
            result = asyncio.run(
                _send_telegram(
                    "bot-token",
                    "-1001",
                    bold_text,
                    thread_id="17585",
                )
            )

        assert result["success"] is True
        assert len(bot_messages) > 1
        assert all(msg["parse_mode"] == ParseMode.HTML for msg in bot_messages)
        assert all(msg["message_thread_id"] == 17585 for msg in bot_messages)
        assert all("(1/" not in msg["text"] for msg in bot_messages)
