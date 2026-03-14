"""Tests for the shared outbound delivery service."""

import asyncio

from gateway.config import Platform
from gateway.outbound import get_sender, get_supported_platform_names, resolve_platform_name
from gateway.outbound.service import send_direct_text, send_result_to_legacy_dict
from gateway.platforms.base import SendResult


class TestOutboundRegistry:
    def test_registry_resolves_each_supported_platform(self):
        expected = {
            Platform.TELEGRAM,
            Platform.DISCORD,
            Platform.SLACK,
            Platform.WHATSAPP,
            Platform.SIGNAL,
            Platform.EMAIL,
            Platform.HOMEASSISTANT,
        }

        resolved = {resolve_platform_name(name) for name in get_supported_platform_names()}
        assert expected.issubset(resolved)
        for platform in expected:
            assert get_sender(platform).platform == platform

    def test_direct_send_reports_connected_only_platforms(self):
        result = asyncio.run(
            send_direct_text(
                Platform.WHATSAPP,
                type("Cfg", (), {"token": None, "extra": {}})(),
                "chat-id",
                "hello",
            )
        )
        assert "error" in result
        assert "requires a connected gateway adapter" in result["error"]

    def test_send_result_to_legacy_dict_includes_chunk_ids(self):
        result = SendResult(
            success=True,
            message_id="1",
            raw_response={"message_ids": ["1", "2"]},
        )
        payload = send_result_to_legacy_dict(Platform.DISCORD, "123", result)
        assert payload == {
            "success": True,
            "platform": "discord",
            "chat_id": "123",
            "message_id": "1",
            "message_ids": ["1", "2"],
        }
