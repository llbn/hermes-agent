"""Registry for platform-specific outbound text senders."""

from __future__ import annotations

from typing import Dict, List, Optional

from gateway.config import Platform

from .base import BaseOutboundTextSender

_SENDERS: Dict[Platform, BaseOutboundTextSender] = {}
_NAME_MAP: Dict[str, Platform] = {}
_registered = False


def register_sender(sender: BaseOutboundTextSender) -> None:
    """Register a platform sender exactly once."""
    _SENDERS[sender.platform] = sender
    _NAME_MAP[sender.platform.value] = sender.platform


def _ensure_registered() -> None:
    """Import all sender modules on first access to trigger registration."""
    global _registered
    if _registered:
        return
    _registered = True

    from . import discord as _discord_sender  # noqa: F401
    from . import email as _email_sender  # noqa: F401
    from . import homeassistant as _homeassistant_sender  # noqa: F401
    from . import signal as _signal_sender  # noqa: F401
    from . import slack as _slack_sender  # noqa: F401
    from . import telegram as _telegram_sender  # noqa: F401
    from . import whatsapp as _whatsapp_sender  # noqa: F401


def get_sender(platform: Platform) -> BaseOutboundTextSender:
    """Return the registered sender for a platform."""
    _ensure_registered()
    try:
        return _SENDERS[platform]
    except KeyError as exc:
        raise KeyError(f"No outbound sender registered for {platform.value}") from exc


def resolve_platform_name(name: str) -> Optional[Platform]:
    """Resolve a user-facing platform name to a Platform enum."""
    _ensure_registered()
    return _NAME_MAP.get((name or "").strip().lower())


def get_supported_platform_names() -> List[str]:
    """Return the user-facing names of all registered outbound platforms."""
    _ensure_registered()
    return sorted(_NAME_MAP.keys())
