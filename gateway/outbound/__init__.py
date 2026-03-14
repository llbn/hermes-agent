"""Shared outbound helpers for gateway-adjacent delivery paths."""

from gateway.outbound.service import (
    get_supported_platform_names,
    resolve_outbound_platform,
    send_result_to_legacy_dict,
    send_text,
)

__all__ = [
    "get_supported_platform_names",
    "resolve_outbound_platform",
    "send_result_to_legacy_dict",
    "send_text",
]
