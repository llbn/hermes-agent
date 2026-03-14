"""Send Message Tool -- cross-channel messaging via platform APIs.

Sends a message to a user or channel on any connected messaging platform
(Telegram, Discord, Slack). Supports listing available targets and resolving
human-friendly channel names to IDs. Works in both CLI and gateway contexts.
"""

import json
import logging
import os
import re

from gateway.config import Platform, PlatformConfig
from gateway.outbound.service import (
    get_supported_platform_names,
    resolve_outbound_platform,
    send_discord_text as _shared_send_discord_text,
    send_email_text as _shared_send_email_text,
    send_result_to_legacy_dict,
    send_signal_text as _shared_send_signal_text,
    send_slack_text as _shared_send_slack_text,
    send_telegram_text as _shared_send_telegram_text,
    send_text,
)

logger = logging.getLogger(__name__)

_TELEGRAM_TOPIC_TARGET_RE = re.compile(r"^\s*(-?\d+)(?::(\d+))?\s*$")


SEND_MESSAGE_SCHEMA = {
    "name": "send_message",
    "description": (
        "Send a message to a connected messaging platform, or list available targets.\n\n"
        "IMPORTANT: When the user asks to send to a specific channel or person "
        "(not just a bare platform name), call send_message(action='list') FIRST to see "
        "available targets, then send to the correct one.\n"
        "If the user just says a platform name like 'send to telegram', send directly "
        "to the home channel without listing first."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["send", "list"],
                "description": "Action to perform. 'send' (default) sends a message. 'list' returns all available channels/contacts across connected platforms."
            },
            "target": {
                "type": "string",
                "description": "Delivery target. Format: 'platform' (uses home channel), 'platform:#channel-name', 'platform:chat_id', or Telegram topic 'telegram:chat_id:thread_id'. Examples: 'telegram', 'telegram:-1001234567890:17585', 'discord:#bot-home', 'slack:#engineering', 'signal:+15551234567'"
            },
            "message": {
                "type": "string",
                "description": "The message text to send"
            }
        },
        "required": []
    }
}


def send_message_tool(args, **kw):
    """Handle cross-channel send_message tool calls."""
    action = args.get("action", "send")

    if action == "list":
        return _handle_list()

    return _handle_send(args)


def _handle_list():
    """Return formatted list of available messaging targets."""
    try:
        from gateway.channel_directory import format_directory_for_display
        return json.dumps({"targets": format_directory_for_display()})
    except Exception as e:
        return json.dumps({"error": f"Failed to load channel directory: {e}"})


def _handle_send(args):
    """Send a message to a platform target."""
    target = args.get("target", "")
    message = args.get("message", "")
    if not target or not message:
        return json.dumps({"error": "Both 'target' and 'message' are required when action='send'"})

    parts = target.split(":", 1)
    platform_name = parts[0].strip().lower()
    target_ref = parts[1].strip() if len(parts) > 1 else None
    chat_id = None
    thread_id = None

    if target_ref:
        chat_id, thread_id, is_explicit = _parse_target_ref(platform_name, target_ref)
    else:
        is_explicit = False

    # Resolve human-friendly channel names to numeric IDs
    if target_ref and not is_explicit:
        try:
            from gateway.channel_directory import resolve_channel_name
            resolved = resolve_channel_name(platform_name, target_ref)
            if resolved:
                chat_id, thread_id, _ = _parse_target_ref(platform_name, resolved)
            else:
                return json.dumps({
                    "error": f"Could not resolve '{target_ref}' on {platform_name}. "
                    f"Use send_message(action='list') to see available targets."
                })
        except Exception:
            return json.dumps({
                "error": f"Could not resolve '{target_ref}' on {platform_name}. "
                f"Try using a numeric channel ID instead."
            })

    from tools.interrupt import is_interrupted
    if is_interrupted():
        return json.dumps({"error": "Interrupted"})

    try:
        from gateway.config import load_gateway_config
        config = load_gateway_config()
    except Exception as e:
        return json.dumps({"error": f"Failed to load gateway config: {e}"})

    platform = resolve_outbound_platform(platform_name)
    if not platform:
        avail = ", ".join(get_supported_platform_names())
        return json.dumps({"error": f"Unknown platform: {platform_name}. Available: {avail}"})

    pconfig = config.platforms.get(platform)
    if not pconfig or not pconfig.enabled:
        return json.dumps({"error": f"Platform '{platform_name}' is not configured. Set up credentials in ~/.hermes/gateway.json or environment variables."})

    used_home_channel = False
    if not chat_id:
        home = config.get_home_channel(platform)
        if home:
            chat_id = home.chat_id
            used_home_channel = True
        else:
            return json.dumps({
                "error": f"No home channel set for {platform_name} to determine where to send the message. "
                f"Either specify a channel directly with '{platform_name}:CHANNEL_NAME', "
                f"or set a home channel via: hermes config set {platform_name.upper()}_HOME_CHANNEL <channel_id>"
            })

    try:
        from model_tools import _run_async
        result = _run_async(_send_to_platform(platform, pconfig, chat_id, message, thread_id=thread_id))
        if used_home_channel and isinstance(result, dict) and result.get("success"):
            result["note"] = f"Sent to {platform_name} home channel (chat_id: {chat_id})"

        # Mirror the sent message into the target's gateway session
        if isinstance(result, dict) and result.get("success"):
            try:
                from gateway.mirror import mirror_to_session
                source_label = os.getenv("HERMES_SESSION_PLATFORM", "cli")
                if mirror_to_session(platform_name, chat_id, message, source_label=source_label, thread_id=thread_id):
                    result["mirrored"] = True
            except Exception:
                pass

        return json.dumps(result)
    except Exception as e:
        return json.dumps({"error": f"Send failed: {e}"})


def _parse_target_ref(platform_name: str, target_ref: str):
    """Parse a tool target into chat_id/thread_id and whether it is explicit."""
    if platform_name == "telegram":
        match = _TELEGRAM_TOPIC_TARGET_RE.fullmatch(target_ref)
        if match:
            return match.group(1), match.group(2), True
    if target_ref.lstrip("-").isdigit():
        return target_ref, None, True
    return None, None, False


async def _send_to_platform(platform, pconfig, chat_id, message, thread_id=None):
    """Route a message to the appropriate platform sender."""
    result = await send_text(
        platform,
        pconfig,
        chat_id,
        message,
        metadata={"thread_id": thread_id} if thread_id is not None else None,
    )
    return send_result_to_legacy_dict(platform, chat_id, result)


async def _send_telegram(token, chat_id, message, thread_id=None):
    """Compatibility wrapper for the shared standalone Telegram sender."""
    config = PlatformConfig(enabled=True, token=token)
    result = await _shared_send_telegram_text(
        config,
        chat_id,
        message,
        metadata={"thread_id": thread_id} if thread_id is not None else None,
    )
    return send_result_to_legacy_dict(Platform.TELEGRAM, chat_id, result)


async def _send_discord(token, chat_id, message):
    """Compatibility wrapper for the shared standalone Discord sender."""
    config = PlatformConfig(enabled=True, token=token)
    result = await _shared_send_discord_text(config, chat_id, message)
    return send_result_to_legacy_dict(Platform.DISCORD, chat_id, result)


async def _send_slack(token, chat_id, message):
    """Compatibility wrapper for the shared standalone Slack sender."""
    config = PlatformConfig(enabled=True, token=token)
    result = await _shared_send_slack_text(config, chat_id, message)
    return send_result_to_legacy_dict(Platform.SLACK, chat_id, result)


async def _send_signal(extra, chat_id, message):
    """Compatibility wrapper for the shared standalone Signal sender."""
    config = PlatformConfig(enabled=True, extra=extra or {})
    result = await _shared_send_signal_text(config, chat_id, message)
    return send_result_to_legacy_dict(Platform.SIGNAL, chat_id, result)


async def _send_email(extra, chat_id, message):
    """Compatibility wrapper for the shared standalone Email sender."""
    config = PlatformConfig(enabled=True, extra=extra or {})
    result = await _shared_send_email_text(config, chat_id, message)
    return send_result_to_legacy_dict(Platform.EMAIL, chat_id, result)


def _check_send_message():
    """Gate send_message on gateway running (always available on messaging platforms)."""
    platform = os.getenv("HERMES_SESSION_PLATFORM", "")
    if platform and platform != "local":
        return True
    try:
        from gateway.status import is_gateway_running
        return is_gateway_running()
    except Exception:
        return False


# --- Registry ---
from tools.registry import registry

registry.register(
    name="send_message",
    toolset="messaging",
    schema=SEND_MESSAGE_SCHEMA,
    handler=send_message_tool,
    check_fn=_check_send_message,
)
