"""Shared outbound text chunking helpers."""

from __future__ import annotations

from typing import List, Optional


def chunk_text_preserving_code_blocks(
    content: str,
    max_length: int = 4096,
    *,
    add_chunk_indicators: bool = True,
) -> List[str]:
    """Split a long message into chunks, preserving triple-backtick fences."""
    if len(content) <= max_length:
        return [content]

    indicator_reserve = 10 if add_chunk_indicators else 0
    fence_close = "\n```"

    chunks: List[str] = []
    remaining = content
    carry_lang: Optional[str] = None

    while remaining:
        prefix = f"```{carry_lang}\n" if carry_lang is not None else ""
        headroom = max_length - indicator_reserve - len(prefix) - len(fence_close)
        if headroom < 1:
            headroom = max_length // 2

        if len(prefix) + len(remaining) <= max_length - indicator_reserve:
            chunks.append(prefix + remaining)
            break

        region = remaining[:headroom]
        split_at = region.rfind("\n")
        if split_at < headroom // 2:
            split_at = region.rfind(" ")
        if split_at < 1:
            split_at = headroom

        chunk_body = remaining[:split_at]
        remaining = remaining[split_at:].lstrip()

        full_chunk = prefix + chunk_body
        in_code = carry_lang is not None
        lang = carry_lang or ""
        for line in chunk_body.split("\n"):
            stripped = line.strip()
            if stripped.startswith("```"):
                if in_code:
                    in_code = False
                    lang = ""
                else:
                    in_code = True
                    tag = stripped[3:].strip()
                    lang = tag.split()[0] if tag else ""

        if in_code:
            full_chunk += fence_close
            carry_lang = lang
        else:
            carry_lang = None

        chunks.append(full_chunk)

    if add_chunk_indicators and len(chunks) > 1:
        total = len(chunks)
        chunks = [f"{chunk} ({index + 1}/{total})" for index, chunk in enumerate(chunks)]

    return chunks
