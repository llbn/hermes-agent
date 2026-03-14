"""Shared outbound text delivery helpers."""

from .registry import get_sender, get_supported_platform_names, resolve_platform_name
from .service import send_connected_text, send_direct_text, send_result_to_legacy_dict

__all__ = [
    "get_sender",
    "get_supported_platform_names",
    "resolve_platform_name",
    "send_connected_text",
    "send_direct_text",
    "send_result_to_legacy_dict",
]
