"""WhatsApp outbound text sender."""

from __future__ import annotations

from typing import Any

from gateway.config import Platform
from gateway.platforms.base import SendResult

from .base import BaseOutboundTextSender, OutboundCapabilities, OutboundRequest, PreparedChunk
from .registry import register_sender


class WhatsAppOutboundTextSender(BaseOutboundTextSender):
    """Outbound sender for WhatsApp bridge traffic."""

    platform = Platform.WHATSAPP
    capabilities = OutboundCapabilities(
        supports_direct_send=False,
        supports_reply_to=True,
    )

    def parse_target_ref(self, target_ref: str) -> tuple[str | None, str | None, bool]:
        normalized = (target_ref or "").strip()
        if normalized.startswith("+") or normalized.endswith("@c.us") or normalized.endswith("@g.us"):
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
        if not adapter.is_connected:
            return SendResult(success=False, error="Not connected")

        try:
            import aiohttp
        except ImportError:
            return SendResult(success=False, error="aiohttp not installed. Run: pip install aiohttp")

        try:
            async with aiohttp.ClientSession() as session:
                payload = {
                    "chatId": request.chat_id,
                    "message": chunks[0].body,
                }
                if request.reply_to:
                    payload["replyTo"] = request.reply_to
                async with session.post(
                    f"http://localhost:{adapter._bridge_port}/send",
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as response:
                    if response.status == 200:
                        data = await response.json()
                        return SendResult(
                            success=True,
                            message_id=data.get("messageId"),
                            raw_response=data,
                        )
                    error = await response.text()
                    return SendResult(success=False, error=error)
        except Exception as exc:
            return SendResult(success=False, error=str(exc))

    async def send_direct(
        self,
        config: Any,
        request: OutboundRequest,
        chunks: list[PreparedChunk],
    ) -> dict[str, Any]:
        return {
            "error": "Direct sending is not supported for whatsapp; this platform requires a connected gateway adapter"
        }


SENDER = WhatsAppOutboundTextSender()
register_sender(SENDER)
