"""Home Assistant outbound text sender."""

from __future__ import annotations

import logging
import uuid
from typing import Any

logger = logging.getLogger(__name__)

from gateway.config import Platform
from gateway.platforms.base import SendResult

from .base import (
    BaseOutboundTextSender,
    OutboundCapabilities,
    OutboundRequest,
    PreparedChunk,
)
from .registry import register_sender

MAX_HOMEASSISTANT_MESSAGE_LENGTH = 4096


class HomeAssistantOutboundTextSender(BaseOutboundTextSender):
    """Outbound sender for Home Assistant notifications."""

    platform = Platform.HOMEASSISTANT
    capabilities = OutboundCapabilities(supports_direct_send=True)

    def parse_target_ref(self, target_ref: str) -> tuple[str | None, str | None, bool]:
        normalized = (target_ref or "").strip()
        if normalized:
            return normalized, None, True
        return super().parse_target_ref(target_ref)

    def prepare_text(self, content: str) -> list[PreparedChunk]:
        return [PreparedChunk(body=content)]

    async def send_connected(
        self,
        adapter: Any,
        request: OutboundRequest,
        chunks: list[PreparedChunk],
    ) -> SendResult:
        return await _send_via_homeassistant(
            request.chat_id,
            chunks[0].body,
            session=getattr(adapter, "_rest_session", None),
            hass_url=adapter._hass_url,
            hass_token=adapter._hass_token,
        )

    async def send_direct(
        self,
        config: Any,
        request: OutboundRequest,
        chunks: list[PreparedChunk],
    ) -> dict[str, Any]:
        result = await _send_via_homeassistant(
            request.chat_id,
            chunks[0].body,
            session=None,
            hass_url=(config.extra.get("url") or "http://homeassistant.local:8123").rstrip("/"),
            hass_token=config.token,
        )
        if not result.success:
            return {"error": result.error or "Home Assistant send failed"}
        return {
            "success": True,
            "platform": self.platform.value,
            "chat_id": request.chat_id,
            "message_id": result.message_id,
        }


async def _send_via_homeassistant(
    chat_id: str,
    content: str,
    *,
    session: Any,
    hass_url: str,
    hass_token: str,
) -> SendResult:
    from gateway.platforms import homeassistant as ha_module

    aiohttp = ha_module.aiohttp
    if aiohttp is None:
        return SendResult(success=False, error="aiohttp not installed. Run: pip install aiohttp")

    url = f"{hass_url}/api/services/persistent_notification/create"
    headers = {
        "Authorization": f"Bearer {hass_token}",
        "Content-Type": "application/json",
    }
    if len(content) > MAX_HOMEASSISTANT_MESSAGE_LENGTH:
        logger.warning(
            "[HomeAssistant] Message truncated from %d to %d chars",
            len(content),
            MAX_HOMEASSISTANT_MESSAGE_LENGTH,
        )
    payload = {
        "title": "Hermes Agent",
        "message": content[:MAX_HOMEASSISTANT_MESSAGE_LENGTH],
        "notification_id": chat_id,
    }

    try:
        if session:
            async with session.post(
                url,
                headers=headers,
                json=payload,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as response:
                if response.status < 300:
                    return SendResult(success=True, message_id=uuid.uuid4().hex[:12])
                body = await response.text()
                return SendResult(success=False, error=f"HTTP {response.status}: {body}")

        async with aiohttp.ClientSession() as local_session:
            async with local_session.post(
                url,
                headers=headers,
                json=payload,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as response:
                if response.status < 300:
                    return SendResult(success=True, message_id=uuid.uuid4().hex[:12])
                body = await response.text()
                return SendResult(success=False, error=f"HTTP {response.status}: {body}")
    except Exception as exc:
        return SendResult(success=False, error=str(exc))


SENDER = HomeAssistantOutboundTextSender()
register_sender(SENDER)
