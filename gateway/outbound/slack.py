"""Slack outbound text sender and formatting helpers."""

from __future__ import annotations

import re
from typing import Any, Optional

from gateway.config import Platform
from gateway.platforms.base import SendResult

from .base import (
    BaseOutboundTextSender,
    OutboundCapabilities,
    OutboundRequest,
    PreparedChunk,
    build_prepared_chunks,
    thread_id_from_metadata,
)
from .registry import register_sender

MAX_SLACK_MESSAGE_LENGTH = 39000
_SLACK_ID_RE = re.compile(r"^[A-Z][A-Z0-9]{8,}$")


def format_slack_message(content: str) -> str:
    """Convert standard markdown to Slack mrkdwn format."""
    if not content:
        return content

    placeholders: dict[str, str] = {}
    counter = [0]

    def _placeholder(value: str) -> str:
        key = f"\x00SL{counter[0]}\x00"
        counter[0] += 1
        placeholders[key] = value
        return key

    text = content
    text = re.sub(r"(```(?:[^\n]*\n)?[\s\S]*?```)", lambda match: _placeholder(match.group(0)), text)
    text = re.sub(r"(`[^`]+`)", lambda match: _placeholder(match.group(0)), text)
    text = re.sub(
        r"\[([^\]]+)\]\(([^)]+)\)",
        lambda match: _placeholder(f"<{match.group(2)}|{match.group(1)}>"),
        text,
    )

    def _convert_header(match: re.Match[str]) -> str:
        inner = re.sub(r"\*\*(.+?)\*\*", r"\1", match.group(1).strip())
        return _placeholder(f"*{inner}*")

    text = re.sub(r"^#{1,6}\s+(.+)$", _convert_header, text, flags=re.MULTILINE)
    text = re.sub(r"\*\*(.+?)\*\*", lambda match: _placeholder(f"*{match.group(1)}*"), text)
    text = re.sub(r"(?<!\*)\*([^*\n]+)\*(?!\*)", lambda match: _placeholder(f"_{match.group(1)}_"), text)
    text = re.sub(r"~~(.+?)~~", lambda match: _placeholder(f"~{match.group(1)}~"), text)

    for placeholder in reversed(list(placeholders.keys())):
        text = text.replace(placeholder, placeholders[placeholder])

    return text


def resolve_slack_thread_ts(
    reply_to: Optional[str] = None,
    metadata: Optional[dict[str, Any]] = None,
) -> Optional[str]:
    """Resolve the correct Slack thread_ts for an outbound call."""
    thread_id = thread_id_from_metadata(metadata)
    if thread_id:
        return thread_id
    return reply_to


class SlackOutboundTextSender(BaseOutboundTextSender):
    """Outbound sender for Slack text messages."""

    platform = Platform.SLACK
    capabilities = OutboundCapabilities(
        supports_direct_send=True,
        supports_reply_to=True,
        supports_threading=True,
    )

    def parse_target_ref(self, target_ref: str) -> tuple[str | None, str | None, bool]:
        normalized = (target_ref or "").strip()
        if _SLACK_ID_RE.fullmatch(normalized):
            return normalized, None, True
        return super().parse_target_ref(target_ref)

    def prepare_text(self, content: str) -> list[PreparedChunk]:
        return build_prepared_chunks(
            content,
            formatter=format_slack_message,
            max_length=MAX_SLACK_MESSAGE_LENGTH,
        )

    async def send_connected(
        self,
        adapter: Any,
        request: OutboundRequest,
        chunks: list[PreparedChunk],
    ) -> SendResult:
        app = getattr(adapter, "_app", None)
        if not app:
            return SendResult(success=False, error="Not connected")

        try:
            thread_ts = resolve_slack_thread_ts(request.reply_to, request.metadata)
            broadcast = adapter.config.extra.get("reply_broadcast", False)
            last_result = None

            for index, chunk in enumerate(chunks):
                kwargs = {
                    "channel": request.chat_id,
                    "text": chunk.body,
                }
                if thread_ts:
                    kwargs["thread_ts"] = thread_ts
                    if broadcast and index == 0:
                        kwargs["reply_broadcast"] = True
                last_result = await app.client.chat_postMessage(**kwargs)

            return SendResult(
                success=True,
                message_id=last_result.get("ts") if last_result else None,
                raw_response=last_result,
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
            url = "https://slack.com/api/chat.postMessage"
            headers = {
                "Authorization": f"Bearer {config.token}",
                "Content-Type": "application/json",
            }
            thread_ts = resolve_slack_thread_ts(request.reply_to, request.metadata)
            last_result = None
            async with aiohttp.ClientSession() as session:
                for index, chunk in enumerate(chunks):
                    payload: dict[str, Any] = {
                        "channel": request.chat_id,
                        "text": chunk.body,
                    }
                    if thread_ts:
                        payload["thread_ts"] = thread_ts
                    async with session.post(url, headers=headers, json=payload) as response:
                        data = await response.json()
                        if not data.get("ok"):
                            return {"error": f"Slack API error: {data.get('error', 'unknown')}"}
                        last_result = data

            return {
                "success": True,
                "platform": self.platform.value,
                "chat_id": request.chat_id,
                "message_id": last_result.get("ts") if last_result else None,
            }
        except Exception as exc:
            return {"error": f"Slack send failed: {exc}"}


SENDER = SlackOutboundTextSender()
register_sender(SENDER)
