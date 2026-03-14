"""Telegram outbound text sender."""

from __future__ import annotations

import logging
import os
import re
from typing import Any

from gateway.config import Platform
from gateway.platforms.base import SendResult
from gateway.platforms.telegram import ParseMode
from gateway.platforms.telegram_format import (
    DEFAULT_TELEGRAM_TEXT_LIMIT,
    markdown_to_telegram_chunks,
    markdown_to_telegram_html,
)

from .base import (
    BaseOutboundTextSender,
    OutboundCapabilities,
    OutboundRequest,
    PreparedChunk,
    thread_id_from_metadata,
)
from .registry import register_sender

logger = logging.getLogger(__name__)

_TOPIC_TARGET_RE = re.compile(r"^\s*(-?\d+)(?::(\d+))?\s*$")
_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
_VIDEO_EXTS = {".mp4", ".mov", ".avi", ".mkv", ".3gp"}
_AUDIO_EXTS = {".ogg", ".opus", ".mp3", ".wav", ".m4a"}
_VOICE_EXTS = {".ogg", ".opus"}


class TelegramOutboundTextSender(BaseOutboundTextSender):
    """Outbound sender that renders Telegram text as safe HTML."""

    platform = Platform.TELEGRAM
    capabilities = OutboundCapabilities(
        supports_direct_send=True,
        supports_reply_to=True,
        supports_threading=True,
    )

    def parse_target_ref(self, target_ref: str) -> tuple[str | None, str | None, bool]:
        match = _TOPIC_TARGET_RE.fullmatch((target_ref or "").strip())
        if match:
            return match.group(1), match.group(2), True
        return super().parse_target_ref(target_ref)

    def prepare_text(self, content: str) -> list[PreparedChunk]:
        return [
            PreparedChunk(body=chunk.html, fallback_body=chunk.text)
            for chunk in markdown_to_telegram_chunks(content, DEFAULT_TELEGRAM_TEXT_LIMIT)
        ]

    async def send_connected(
        self,
        adapter: Any,
        request: OutboundRequest,
        chunks: list[PreparedChunk],
    ) -> SendResult:
        bot = getattr(adapter, "_bot", None)
        if not bot:
            return SendResult(success=False, error="Not connected")
        return await _send_text_chunks(bot, request, chunks)

    async def send_direct(
        self,
        config: Any,
        request: OutboundRequest,
        chunks: list[PreparedChunk],
    ) -> dict[str, Any]:
        try:
            from telegram import Bot
        except ImportError:
            return {
                "error": "python-telegram-bot not installed. Run: pip install python-telegram-bot"
            }

        bot = Bot(token=config.token)
        warnings: list[str] = []
        message_ids: list[str] = []
        last_message_id: str | None = None

        if request.content.strip():
            result = await _send_text_chunks(bot, request, chunks)
            if not result.success:
                return {"error": result.error or "Telegram send failed"}
            raw_ids = (result.raw_response or {}).get("message_ids", [])
            message_ids.extend(str(message_id) for message_id in raw_ids)
            last_message_id = message_ids[-1] if message_ids else result.message_id

        media_result = await _send_media_files(bot, request, warnings)
        if media_result["message_ids"]:
            message_ids.extend(media_result["message_ids"])
            last_message_id = media_result["message_ids"][-1]

        if last_message_id is None:
            error = "No deliverable text or media remained after processing MEDIA tags"
            if warnings:
                return {"error": error, "warnings": warnings}
            return {"error": error}

        payload: dict[str, Any] = {
            "success": True,
            "platform": self.platform.value,
            "chat_id": request.chat_id,
            "message_id": last_message_id,
        }
        if message_ids:
            payload["message_ids"] = message_ids
        if warnings:
            payload["warnings"] = warnings
        return payload


async def _send_text_chunks(bot: Any, request: OutboundRequest, chunks: list[PreparedChunk]) -> SendResult:
    """Send prepared Telegram text chunks via a bot instance."""
    message_ids: list[str] = []
    thread_id = thread_id_from_metadata(request.metadata)

    try:
        for index, chunk in enumerate(chunks):
            msg = await _send_single_chunk(
                bot,
                chat_id=request.chat_id,
                chunk=chunk,
                reply_to=request.reply_to if index == 0 else None,
                thread_id=thread_id,
            )
            message_ids.append(str(msg.message_id))
        return SendResult(
            success=True,
            message_id=message_ids[0] if message_ids else None,
            raw_response={"message_ids": message_ids},
        )
    except Exception as exc:
        logger.error("[Telegram] Failed to send message: %s", exc, exc_info=True)
        return SendResult(success=False, error=str(exc))


async def _send_single_chunk(
    bot: Any,
    *,
    chat_id: str,
    chunk: PreparedChunk,
    reply_to: str | None,
    thread_id: str | None,
) -> Any:
    """Send one Telegram chunk with HTML fallback to plain text."""
    kwargs = {
        "chat_id": int(chat_id),
        "text": chunk.body,
        "parse_mode": ParseMode.HTML,
        "reply_to_message_id": int(reply_to) if reply_to else None,
        "message_thread_id": int(thread_id) if thread_id else None,
    }

    try:
        return await bot.send_message(**kwargs)
    except Exception as html_error:
        if not is_telegram_parse_error(html_error):
            raise
        logger.warning("[Telegram] HTML parse failed, falling back to plain text: %s", html_error)
        kwargs["text"] = chunk.fallback_body if chunk.fallback_body is not None else chunk.body
        kwargs["parse_mode"] = None
        return await bot.send_message(**kwargs)


async def _send_media_files(bot: Any, request: OutboundRequest, warnings: list[str]) -> dict[str, list[str]]:
    """Send Telegram media attachments for send_message direct delivery."""
    if not request.media_files:
        return {"message_ids": []}

    thread_id = thread_id_from_metadata(request.metadata)
    thread_kwargs = {}
    if thread_id:
        thread_kwargs["message_thread_id"] = int(thread_id)
    message_ids: list[str] = []

    for media_path, is_voice in request.media_files:
        if not os.path.exists(media_path):
            warning = f"Media file not found, skipping: {media_path}"
            logger.warning(warning)
            warnings.append(warning)
            continue

        ext = os.path.splitext(media_path)[1].lower()
        try:
            with open(media_path, "rb") as file_handle:
                if ext in _IMAGE_EXTS:
                    message = await bot.send_photo(
                        chat_id=int(request.chat_id),
                        photo=file_handle,
                        **thread_kwargs,
                    )
                elif ext in _VIDEO_EXTS:
                    message = await bot.send_video(
                        chat_id=int(request.chat_id),
                        video=file_handle,
                        **thread_kwargs,
                    )
                elif ext in _VOICE_EXTS and is_voice:
                    message = await bot.send_voice(
                        chat_id=int(request.chat_id),
                        voice=file_handle,
                        **thread_kwargs,
                    )
                elif ext in _AUDIO_EXTS:
                    message = await bot.send_audio(
                        chat_id=int(request.chat_id),
                        audio=file_handle,
                        **thread_kwargs,
                    )
                else:
                    message = await bot.send_document(
                        chat_id=int(request.chat_id),
                        document=file_handle,
                        **thread_kwargs,
                    )
        except Exception as exc:
            warning = f"Failed to send media {media_path}: {exc}"
            logger.error(warning)
            warnings.append(warning)
            continue

        message_ids.append(str(message.message_id))

    return {"message_ids": message_ids}


def render_telegram_html(content: str | None) -> str | None:
    """Render markdown-like content to Telegram-safe HTML."""
    return markdown_to_telegram_html(content)


def is_telegram_parse_error(exc: Exception) -> bool:
    """Return True when a Telegram API error looks like an HTML/parse failure."""
    error_text = str(exc).lower()
    return "parse" in error_text or "entity" in error_text or "html" in error_text


async def edit_telegram_html(bot: Any, chat_id: str, message_id: str, content: str) -> SendResult:
    """Edit a Telegram message with HTML formatting and plain-text fallback."""
    from gateway.platforms.telegram_format import telegram_html_to_plain_text

    formatted = render_telegram_html(content)
    try:
        await bot.edit_message_text(
            chat_id=int(chat_id),
            message_id=int(message_id),
            text=formatted,
            parse_mode=ParseMode.HTML,
        )
    except Exception as html_error:
        if not is_telegram_parse_error(html_error):
            raise
        logger.warning("[Telegram] HTML parse failed on edit, falling back to plain text: %s", html_error)
        await bot.edit_message_text(
            chat_id=int(chat_id),
            message_id=int(message_id),
            text=telegram_html_to_plain_text(formatted) or content,
        )
    return SendResult(success=True, message_id=message_id)


SENDER = TelegramOutboundTextSender()
register_sender(SENDER)
