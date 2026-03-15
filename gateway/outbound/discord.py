"""Discord outbound text sender."""

from __future__ import annotations

from typing import Any

from gateway.config import Platform
from gateway.platforms.base import SendResult

from .base import (
    BaseOutboundTextSender,
    OutboundCapabilities,
    OutboundRequest,
    PreparedChunk,
    build_prepared_chunks,
)
from .registry import register_sender

MAX_DISCORD_MESSAGE_LENGTH = 2000


class DiscordOutboundTextSender(BaseOutboundTextSender):
    """Outbound sender for Discord text messages."""

    platform = Platform.DISCORD
    capabilities = OutboundCapabilities(
        supports_direct_send=True,
        supports_reply_to=True,
    )

    def prepare_text(self, content: str) -> list[PreparedChunk]:
        return build_prepared_chunks(content, max_length=MAX_DISCORD_MESSAGE_LENGTH)

    async def send_connected(
        self,
        adapter: Any,
        request: OutboundRequest,
        chunks: list[PreparedChunk],
    ) -> SendResult:
        client = getattr(adapter, "_client", None)
        if not client:
            return SendResult(success=False, error="Not connected")

        try:
            channel = client.get_channel(int(request.chat_id))
            if not channel:
                channel = await client.fetch_channel(int(request.chat_id))
            if not channel:
                return SendResult(success=False, error=f"Channel {request.chat_id} not found")

            reference = None
            if request.reply_to:
                try:
                    reference = await channel.fetch_message(int(request.reply_to))
                except Exception:
                    reference = None

            message_ids: list[str] = []
            for index, chunk in enumerate(chunks):
                message = await channel.send(
                    content=chunk.body,
                    reference=reference if index == 0 else None,
                )
                message_ids.append(str(message.id))

            return SendResult(
                success=True,
                message_id=message_ids[0] if message_ids else None,
                raw_response={"message_ids": message_ids},
            )
        except Exception as exc:
            return SendResult(success=False, error=str(exc))

    async def send_direct(
        self,
        config: Any,
        request: OutboundRequest,
        chunks: list[PreparedChunk],
    ) -> dict[str, Any]:
        try:
            import aiohttp
        except ImportError:
            return {"error": "aiohttp not installed. Run: pip install aiohttp"}

        try:
            url = f"https://discord.com/api/v10/channels/{request.chat_id}/messages"
            headers = {
                "Authorization": f"Bot {config.token}",
                "Content-Type": "application/json",
            }
            message_ids: list[str] = []
            async with aiohttp.ClientSession() as session:
                for index, chunk in enumerate(chunks):
                    payload: dict[str, Any] = {"content": chunk.body}
                    if request.reply_to and index == 0:
                        payload["message_reference"] = {"message_id": str(request.reply_to)}
                    async with session.post(url, headers=headers, json=payload) as response:
                        if response.status not in (200, 201):
                            body = await response.text()
                            return {"error": f"Discord API error ({response.status}): {body}"}
                        data = await response.json()
                        message_ids.append(data.get("id"))

            return {
                "success": True,
                "platform": self.platform.value,
                "chat_id": request.chat_id,
                "message_ids": message_ids,
                "message_id": message_ids[0] if message_ids else None,
            }
        except Exception as exc:
            return {"error": f"Discord send failed: {exc}"}


SENDER = DiscordOutboundTextSender()
register_sender(SENDER)
