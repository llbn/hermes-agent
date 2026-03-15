"""Shared outbound text sender contracts and helpers."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from gateway.config import Platform
from gateway.platforms.base import SendResult

from .chunking import chunk_text_preserving_code_blocks


@dataclass(frozen=True)
class OutboundCapabilities:
    """Delivery capabilities supported by an outbound sender."""

    supports_direct_send: bool
    supports_reply_to: bool = False
    supports_threading: bool = False


@dataclass(frozen=True)
class PreparedChunk:
    """A platform-ready outbound text chunk."""

    body: str
    fallback_body: str | None = None


@dataclass(frozen=True)
class OutboundRequest:
    """Normalized outbound text request."""

    platform: Platform
    chat_id: str
    content: str
    reply_to: str | None = None
    metadata: dict[str, Any] | None = None
    media_files: list[tuple[str, bool]] = field(default_factory=list)


class BaseOutboundTextSender(ABC):
    """Stateless outbound text sender for a platform."""

    platform: Platform
    capabilities: OutboundCapabilities

    def parse_target_ref(self, target_ref: str) -> tuple[str | None, str | None, bool]:
        """Parse a target reference into chat/thread identifiers."""
        normalized = (target_ref or "").strip()
        if normalized.lstrip("-").isdigit():
            return normalized, None, True
        return None, None, False

    @abstractmethod
    def prepare_text(self, content: str) -> list[PreparedChunk]:
        """Convert user/model text into platform-ready chunks."""

    @abstractmethod
    async def send_connected(
        self,
        adapter: Any,
        request: OutboundRequest,
        chunks: list[PreparedChunk],
    ) -> SendResult:
        """Send using a connected runtime adapter."""

    @abstractmethod
    async def send_direct(
        self,
        config: Any,
        request: OutboundRequest,
        chunks: list[PreparedChunk],
    ) -> dict[str, Any]:
        """Send directly without a running gateway adapter."""


def build_prepared_chunks(
    content: str,
    *,
    formatter: Callable[[str], str] | None = None,
    max_length: int | None = None,
) -> list[PreparedChunk]:
    """Build prepared chunks using a formatter and the shared chunker."""
    formatted = formatter(content) if formatter else content
    if max_length is None:
        return [PreparedChunk(body=formatted)]
    return [
        PreparedChunk(body=chunk)
        for chunk in chunk_text_preserving_code_blocks(formatted, max_length)
    ]


def thread_id_from_metadata(metadata: Optional[dict[str, Any]]) -> str | None:
    """Return the most likely thread identifier from metadata."""
    if not metadata:
        return None
    return metadata.get("thread_id") or metadata.get("thread_ts")
