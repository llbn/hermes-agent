"""Compatibility wrappers for callers that still import standalone senders."""

from gateway.config import PlatformConfig
from gateway.outbound.service import (
    send_discord_text,
    send_email_text,
    send_result_to_legacy_dict,
    send_signal_text,
    send_slack_text,
    send_telegram_text,
)
from gateway.config import Platform


async def send_telegram(token, chat_id, message, thread_id=None):
    config = PlatformConfig(enabled=True, token=token)
    result = await send_telegram_text(
        config,
        chat_id,
        message,
        metadata={"thread_id": thread_id} if thread_id is not None else None,
    )
    return send_result_to_legacy_dict(Platform.TELEGRAM, chat_id, result)


async def send_discord(token, chat_id, message):
    config = PlatformConfig(enabled=True, token=token)
    result = await send_discord_text(config, chat_id, message)
    return send_result_to_legacy_dict(Platform.DISCORD, chat_id, result)


async def send_slack(token, chat_id, message):
    config = PlatformConfig(enabled=True, token=token)
    result = await send_slack_text(config, chat_id, message)
    return send_result_to_legacy_dict(Platform.SLACK, chat_id, result)


async def send_signal(extra, chat_id, message):
    config = PlatformConfig(enabled=True, extra=extra or {})
    result = await send_signal_text(config, chat_id, message)
    return send_result_to_legacy_dict(Platform.SIGNAL, chat_id, result)


async def send_email(extra, chat_id, message):
    config = PlatformConfig(enabled=True, extra=extra or {})
    result = await send_email_text(config, chat_id, message)
    return send_result_to_legacy_dict(Platform.EMAIL, chat_id, result)
