"""Shared outbound text delivery service."""

from __future__ import annotations

from typing import Any, Dict, Optional

from gateway.config import Platform
from gateway.platforms.base import SendResult

from .base import OutboundRequest
from .registry import get_sender


async def send_connected_text(
    adapter: Any,
    chat_id: str,
    content: str,
    reply_to: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> SendResult:
    """Send outbound text through a connected runtime adapter."""
    sender = get_sender(adapter.platform)
    request = OutboundRequest(
        platform=adapter.platform,
        chat_id=str(chat_id),
        content=content,
        reply_to=reply_to,
        metadata=dict(metadata or {}),
    )
    chunks = sender.prepare_text(content)
    return await sender.send_connected(adapter, request, chunks)


async def send_direct_text(
    platform: Platform,
    config: Any,
    chat_id: str,
    content: str,
    reply_to: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
    media_files: Optional[list[tuple[str, bool]]] = None,
) -> dict[str, Any]:
    """Send outbound text directly without a running gateway adapter."""
    sender = get_sender(platform)
    request = OutboundRequest(
        platform=platform,
        chat_id=str(chat_id),
        content=content,
        reply_to=reply_to,
        metadata=dict(metadata or {}),
        media_files=list(media_files or []),
    )
    chunks = sender.prepare_text(content)
    if not sender.capabilities.supports_direct_send:
        return {
            "error": (
                f"Direct sending is not supported for {platform.value}; "
                "this platform requires a connected gateway adapter"
            )
        }
    return await sender.send_direct(config, request, chunks)


def send_result_to_legacy_dict(platform: Platform, chat_id: str, result: SendResult) -> dict[str, Any]:
    """Convert SendResult into the dict shape expected by tools and cron."""
    if not result.success:
        return {"error": result.error or "Unknown send error"}

    payload: dict[str, Any] = {
        "success": True,
        "platform": platform.value,
        "chat_id": str(chat_id),
    }
    if result.message_id:
        payload["message_id"] = str(result.message_id)

    raw_response = result.raw_response
    if isinstance(raw_response, dict) and "message_ids" in raw_response:
        payload["message_ids"] = raw_response["message_ids"]

    return payload
