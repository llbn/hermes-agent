"""Unified outbound text delivery shared by adapters, cron, and send_message."""

from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, Optional

from gateway.config import Platform, PlatformConfig
from gateway.platforms.base import SendResult

logger = logging.getLogger(__name__)

TextSender = Callable[
    [PlatformConfig, str, str, Optional[str], Optional[Dict[str, Any]], Optional[Any]],
    Awaitable[SendResult],
]


@dataclass(frozen=True)
class OutboundTextSender:
    """Registered text sender for a platform."""

    send: TextSender


_OUTBOUND_PLATFORM_MAP: Dict[str, Platform] = {
    "telegram": Platform.TELEGRAM,
    "discord": Platform.DISCORD,
    "slack": Platform.SLACK,
    "whatsapp": Platform.WHATSAPP,
    "signal": Platform.SIGNAL,
    "email": Platform.EMAIL,
}

_TEXT_SENDERS: Dict[Platform, OutboundTextSender] = {}


def register_text_sender(platform: Platform, sender: TextSender) -> None:
    """Register a platform-specific outbound text sender."""
    _TEXT_SENDERS[platform] = OutboundTextSender(send=sender)


def resolve_outbound_platform(name: str) -> Optional[Platform]:
    """Resolve a user-facing outbound platform name to a Platform enum."""
    return _OUTBOUND_PLATFORM_MAP.get((name or "").strip().lower())


def get_supported_platform_names() -> list[str]:
    """Return the supported outbound platform names."""
    return list(_OUTBOUND_PLATFORM_MAP.keys())


async def send_text(
    platform: Platform,
    config: PlatformConfig,
    chat_id: str,
    content: str,
    reply_to: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
    adapter: Optional[Any] = None,
) -> SendResult:
    """Send text through the registered outbound sender for a platform."""
    sender = _TEXT_SENDERS.get(platform)
    if not sender:
        return SendResult(success=False, error=f"Direct sending not yet implemented for {platform.value}")
    return await sender.send(config, chat_id, content, reply_to, metadata, adapter)


def send_result_to_legacy_dict(platform: Platform, chat_id: str, result: SendResult) -> Dict[str, Any]:
    """Convert SendResult to the legacy dict shape used by tools/cron."""
    if not result.success:
        return {"error": result.error or "Unknown send error"}

    payload: Dict[str, Any] = {
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


def _telegram_render_adapter(config: PlatformConfig, adapter: Optional[Any]) -> Any:
    if adapter is not None:
        return adapter
    from gateway.platforms.telegram import TelegramAdapter

    return TelegramAdapter(config)


def _discord_render_adapter(config: PlatformConfig, adapter: Optional[Any]) -> Any:
    if adapter is not None:
        return adapter
    from gateway.platforms.discord import DiscordAdapter

    return DiscordAdapter(config)


def _slack_render_adapter(config: PlatformConfig, adapter: Optional[Any]) -> Any:
    if adapter is not None:
        return adapter
    from gateway.platforms.slack import SlackAdapter

    return SlackAdapter(config)


async def send_telegram_text(
    config: PlatformConfig,
    chat_id: str,
    content: str,
    reply_to: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
    adapter: Optional[Any] = None,
) -> SendResult:
    """Send a Telegram text message using adapter rendering semantics."""
    from gateway.platforms.telegram import ParseMode, _strip_mdv2, check_telegram_requirements

    render_adapter = _telegram_render_adapter(config, adapter)
    bot = getattr(adapter, "_bot", None) if adapter is not None else None
    if bot is None:
        if adapter is not None:
            return SendResult(success=False, error="Not connected")
        if not check_telegram_requirements():
            return SendResult(
                success=False,
                error="python-telegram-bot not installed. Run: pip install python-telegram-bot",
            )
        from telegram import Bot

        bot = Bot(token=config.token)

    try:
        formatted = render_adapter.format_message(content)
        chunks = render_adapter.truncate_message(formatted, render_adapter.MAX_MESSAGE_LENGTH)

        message_ids = []
        thread_id = metadata.get("thread_id") if metadata else None

        for i, chunk in enumerate(chunks):
            try:
                msg = await bot.send_message(
                    chat_id=int(chat_id),
                    text=chunk,
                    parse_mode=ParseMode.MARKDOWN_V2,
                    reply_to_message_id=int(reply_to) if reply_to and i == 0 else None,
                    message_thread_id=int(thread_id) if thread_id else None,
                )
            except Exception as md_error:
                if "parse" in str(md_error).lower() or "markdown" in str(md_error).lower():
                    logger.warning("[Telegram] MarkdownV2 parse failed, falling back to plain text: %s", md_error)
                    plain_chunk = _strip_mdv2(chunk)
                    msg = await bot.send_message(
                        chat_id=int(chat_id),
                        text=plain_chunk,
                        parse_mode=None,
                        reply_to_message_id=int(reply_to) if reply_to and i == 0 else None,
                        message_thread_id=int(thread_id) if thread_id else None,
                    )
                else:
                    raise
            message_ids.append(str(msg.message_id))

        return SendResult(
            success=True,
            message_id=message_ids[0] if message_ids else None,
            raw_response={"message_ids": message_ids},
        )
    except Exception as e:
        logger.error("[Telegram] Failed to send message: %s", e, exc_info=True)
        return SendResult(success=False, error=str(e))


async def send_discord_text(
    config: PlatformConfig,
    chat_id: str,
    content: str,
    reply_to: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
    adapter: Optional[Any] = None,
) -> SendResult:
    """Send a Discord text message using adapter rendering semantics."""
    render_adapter = _discord_render_adapter(config, adapter)
    formatted = render_adapter.format_message(content)
    chunks = render_adapter.truncate_message(formatted, render_adapter.MAX_MESSAGE_LENGTH)

    if adapter is not None:
        client = getattr(adapter, "_client", None)
        if not client:
            return SendResult(success=False, error="Not connected")
        try:
            channel = client.get_channel(int(chat_id))
            if not channel:
                channel = await client.fetch_channel(int(chat_id))
            if not channel:
                return SendResult(success=False, error=f"Channel {chat_id} not found")

            reference = None
            if reply_to:
                try:
                    reference = await channel.fetch_message(int(reply_to))
                except Exception as e:
                    logger.debug("Could not fetch Discord reply-to message: %s", e)

            message_ids = []
            for i, chunk in enumerate(chunks):
                msg = await channel.send(
                    content=chunk,
                    reference=reference if i == 0 else None,
                )
                message_ids.append(str(msg.id))

            return SendResult(
                success=True,
                message_id=message_ids[0] if message_ids else None,
                raw_response={"message_ids": message_ids},
            )
        except Exception as e:
            logger.error("[Discord] Failed to send message: %s", e, exc_info=True)
            return SendResult(success=False, error=str(e))

    try:
        import aiohttp
    except ImportError:
        return SendResult(success=False, error="aiohttp not installed. Run: pip install aiohttp")

    try:
        url = f"https://discord.com/api/v10/channels/{chat_id}/messages"
        headers = {"Authorization": f"Bot {config.token}", "Content-Type": "application/json"}
        message_ids = []
        async with aiohttp.ClientSession() as session:
            for i, chunk in enumerate(chunks):
                payload: Dict[str, Any] = {"content": chunk}
                if reply_to and i == 0:
                    payload["message_reference"] = {"message_id": str(reply_to)}
                async with session.post(url, headers=headers, json=payload) as resp:
                    if resp.status not in (200, 201):
                        body = await resp.text()
                        return SendResult(success=False, error=f"Discord API error ({resp.status}): {body}")
                    data = await resp.json()
                    message_ids.append(data.get("id"))

        return SendResult(
            success=True,
            message_id=message_ids[0] if message_ids else None,
            raw_response={"message_ids": message_ids},
        )
    except Exception as e:
        return SendResult(success=False, error=f"Discord send failed: {e}")


async def send_slack_text(
    config: PlatformConfig,
    chat_id: str,
    content: str,
    reply_to: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
    adapter: Optional[Any] = None,
) -> SendResult:
    """Send a Slack text message using adapter rendering semantics."""
    render_adapter = _slack_render_adapter(config, adapter)
    formatted = render_adapter.format_message(content)
    chunks = render_adapter.truncate_message(formatted, render_adapter.MAX_MESSAGE_LENGTH)
    thread_ts = render_adapter._resolve_thread_ts(reply_to, metadata)
    broadcast = config.extra.get("reply_broadcast", False)

    async def _post_message(client, chunk: str, chunk_index: int):
        kwargs: Dict[str, Any] = {
            "channel": chat_id,
            "text": chunk,
        }
        if thread_ts:
            kwargs["thread_ts"] = thread_ts
            if broadcast and chunk_index == 0:
                kwargs["reply_broadcast"] = True
        return await client.chat_postMessage(**kwargs)

    if adapter is not None:
        app = getattr(adapter, "_app", None)
        if not app:
            return SendResult(success=False, error="Not connected")
        try:
            last_result = None
            for i, chunk in enumerate(chunks):
                last_result = await _post_message(app.client, chunk, i)
            return SendResult(
                success=True,
                message_id=last_result.get("ts") if last_result else None,
                raw_response=last_result,
            )
        except Exception as e:
            logger.error("[Slack] Send error: %s", e, exc_info=True)
            return SendResult(success=False, error=str(e))

    try:
        import aiohttp
    except ImportError:
        return SendResult(success=False, error="aiohttp not installed. Run: pip install aiohttp")

    try:
        url = "https://slack.com/api/chat.postMessage"
        headers = {"Authorization": f"Bearer {config.token}", "Content-Type": "application/json"}
        last_result = None
        async with aiohttp.ClientSession() as session:
            for i, chunk in enumerate(chunks):
                payload: Dict[str, Any] = {
                    "channel": chat_id,
                    "text": chunk,
                }
                if thread_ts:
                    payload["thread_ts"] = thread_ts
                    if broadcast and i == 0:
                        payload["reply_broadcast"] = True
                async with session.post(url, headers=headers, json=payload) as resp:
                    data = await resp.json()
                    if not data.get("ok"):
                        return SendResult(
                            success=False,
                            error=f"Slack API error: {data.get('error', 'unknown')}",
                        )
                    last_result = data

        return SendResult(
            success=True,
            message_id=last_result.get("ts") if last_result else None,
            raw_response=last_result,
        )
    except Exception as e:
        return SendResult(success=False, error=f"Slack send failed: {e}")


async def send_signal_text(
    config: PlatformConfig,
    chat_id: str,
    content: str,
    reply_to: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
    adapter: Optional[Any] = None,
) -> SendResult:
    """Send a Signal text message."""
    if adapter is not None:
        try:
            await adapter._stop_typing_indicator(chat_id)
        except Exception:
            pass

        params: Dict[str, Any] = {
            "account": adapter.account,
            "message": content,
        }
        if chat_id.startswith("group:"):
            params["groupId"] = chat_id[6:]
        else:
            params["recipient"] = [chat_id]

        result = await adapter._rpc("send", params)
        if result is not None:
            return SendResult(success=True)
        return SendResult(success=False, error="RPC send failed")

    try:
        import httpx
    except ImportError:
        return SendResult(success=False, error="httpx not installed")

    try:
        http_url = config.extra.get("http_url", "http://127.0.0.1:8080").rstrip("/")
        account = config.extra.get("account", "")
        if not account:
            return SendResult(success=False, error="Signal account not configured")

        params = {"account": account, "message": content}
        if chat_id.startswith("group:"):
            params["groupId"] = chat_id[6:]
        else:
            params["recipient"] = [chat_id]

        payload = {
            "jsonrpc": "2.0",
            "method": "send",
            "params": params,
            "id": f"send_{int(time.time() * 1000)}",
        }

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(f"{http_url}/api/v1/rpc", json=payload)
            resp.raise_for_status()
            data = resp.json()
            if "error" in data:
                return SendResult(success=False, error=f"Signal RPC error: {data['error']}")
            return SendResult(success=True)
    except Exception as e:
        return SendResult(success=False, error=f"Signal send failed: {e}")


async def send_email_text(
    config: PlatformConfig,
    chat_id: str,
    content: str,
    reply_to: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
    adapter: Optional[Any] = None,
) -> SendResult:
    """Send an email message."""
    if adapter is not None:
        try:
            loop = asyncio.get_running_loop()
            message_id = await loop.run_in_executor(
                None, adapter._send_email, chat_id, content, reply_to
            )
            return SendResult(success=True, message_id=message_id)
        except Exception as e:
            logger.error("[Email] Send failed to %s: %s", chat_id, e)
            return SendResult(success=False, error=str(e))

    import smtplib
    from email.mime.text import MIMEText

    address = config.extra.get("address") or os.getenv("EMAIL_ADDRESS", "")
    password = os.getenv("EMAIL_PASSWORD", "")
    smtp_host = config.extra.get("smtp_host") or os.getenv("EMAIL_SMTP_HOST", "")
    smtp_port = int(os.getenv("EMAIL_SMTP_PORT", "587"))

    if not all([address, password, smtp_host]):
        return SendResult(
            success=False,
            error="Email not configured (EMAIL_ADDRESS, EMAIL_PASSWORD, EMAIL_SMTP_HOST required)",
        )

    try:
        msg = MIMEText(content, "plain", "utf-8")
        msg["From"] = address
        msg["To"] = chat_id
        msg["Subject"] = "Hermes Agent"

        server = smtplib.SMTP(smtp_host, smtp_port)
        server.starttls()
        server.login(address, password)
        server.send_message(msg)
        server.quit()
        return SendResult(success=True)
    except Exception as e:
        return SendResult(success=False, error=f"Email send failed: {e}")


register_text_sender(Platform.TELEGRAM, send_telegram_text)
register_text_sender(Platform.DISCORD, send_discord_text)
register_text_sender(Platform.SLACK, send_slack_text)
register_text_sender(Platform.SIGNAL, send_signal_text)
register_text_sender(Platform.EMAIL, send_email_text)
