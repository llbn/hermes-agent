"""Tests for Telegram HTML rendering and outbound delivery."""

import asyncio
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from gateway.config import Platform, PlatformConfig


def _ensure_telegram_mock():
    if "telegram" in sys.modules and hasattr(sys.modules["telegram"], "__file__"):
        return

    mod = MagicMock()
    mod.Bot = MagicMock()
    mod.Message = type("Message", (), {})
    mod.Update = type("Update", (), {})
    mod.ext.ContextTypes.DEFAULT_TYPE = type(None)
    mod.constants.ParseMode.HTML = "HTML"
    mod.constants.ParseMode.MARKDOWN_V2 = "MarkdownV2"
    mod.constants.ChatType.GROUP = "group"
    mod.constants.ChatType.SUPERGROUP = "supergroup"
    mod.constants.ChatType.CHANNEL = "channel"
    mod.constants.ChatType.PRIVATE = "private"

    for name in ("telegram", "telegram.ext", "telegram.constants"):
        sys.modules.setdefault(name, mod)


_ensure_telegram_mock()

from gateway.outbound.service import send_connected_text, send_direct_text  # noqa: E402
from gateway.platforms.telegram import ParseMode, TelegramAdapter  # noqa: E402
from gateway.platforms.telegram_format import (  # noqa: E402
    DEFAULT_TELEGRAM_TEXT_LIMIT,
    TelegramFormattedChunk,
    markdown_to_telegram_chunks,
    markdown_to_telegram_html,
    split_telegram_html_chunks,
    telegram_html_to_plain_text,
)


@pytest.fixture()
def adapter():
    return TelegramAdapter(PlatformConfig(enabled=True, token="fake-token"))


class TestTelegramHtmlRendering:
    def test_empty_string(self, adapter):
        assert adapter.format_message("") == ""

    def test_none_input(self, adapter):
        assert adapter.format_message(None) is None

    def test_plain_text_is_html_escaped(self, adapter):
        assert adapter.format_message("2 < 3 & 4 > 1") == "2 &lt; 3 &amp; 4 &gt; 1"

    def test_bold_and_italic_render_to_html(self, adapter):
        result = adapter.format_message("This is **bold** and *italic*")
        assert "<b>bold</b>" in result
        assert "<i>italic</i>" in result

    def test_inline_and_fenced_code_render_to_html(self, adapter):
        result = adapter.format_message("Use `code`.\n\n```python\nprint('hi')\n```")
        assert "<code>code</code>" in result
        assert "<pre><code>print('hi')</code></pre>" in result

    def test_headings_render_as_bold_lines(self, adapter):
        result = adapter.format_message("# Title\n\n## Subtitle")
        assert "<b>Title</b>" in result
        assert "<b>Subtitle</b>" in result
        assert "# " not in result

    def test_markdown_links_render_to_anchors(self, adapter):
        result = adapter.format_message("[Click here](https://example.com/path_(1))")
        assert result == '<a href="https://example.com/path_(1)">Click here</a>'

    def test_blockquotes_render_to_telegram_html(self, adapter):
        result = adapter.format_message("> quoted")
        assert result == "<blockquote>quoted</blockquote>"

    def test_lists_render_as_textual_bullets(self, adapter):
        result = adapter.format_message("- first\n- second\n  - nested")
        assert "• first" in result
        assert "• second" in result
        assert "  • nested" in result

    def test_tables_render_as_preformatted_text(self, adapter):
        result = adapter.format_message("| Name | Role |\n| ---- | ---- |\n| Ada | Eng |")
        assert result.startswith("<pre><code>")
        assert "| Name | Role |" in result
        assert "| Ada" in result

    def test_file_references_are_wrapped_in_code_tags(self, adapter):
        result = adapter.format_message("See README.md and scripts/build.sh")
        assert "<code>README.md</code>" in result
        assert "<code>scripts/build.sh</code>" in result

    def test_markdown_image_falls_back_to_text(self, adapter):
        result = adapter.format_message("![diagram](https://example.com/a.png)")
        assert "diagram" in result
        assert "https://example.com/a.png" in result

    def test_raw_html_is_not_trusted(self, adapter):
        result = adapter.format_message("<b>not trusted</b>")
        assert result == "&lt;b&gt;not trusted&lt;/b&gt;"


class TestTelegramChunking:
    def test_long_bold_message_keeps_balanced_tags(self):
        chunks = markdown_to_telegram_chunks("**" + ("hello " * 1199) + "hello**", limit=220)
        assert len(chunks) > 1
        assert all(chunk.html.count("<b>") == chunk.html.count("</b>") for chunk in chunks)
        assert all("(1/" not in chunk.html for chunk in chunks)

    def test_long_code_block_keeps_balanced_pre_and_code_tags(self):
        content = "```python\n" + ("x = 1\n" * 800) + "```"
        chunks = markdown_to_telegram_chunks(content, limit=220)
        assert len(chunks) > 1
        assert all(chunk.html.count("<pre>") == chunk.html.count("</pre>") for chunk in chunks)
        assert all(chunk.html.count("<code>") == chunk.html.count("</code>") for chunk in chunks)

    def test_long_link_keeps_balanced_anchor_tags(self):
        label = "a" * 500
        chunks = markdown_to_telegram_chunks(f"[{label}](https://example.com)", limit=180)
        assert len(chunks) > 1
        assert all(chunk.html.count("<a ") == chunk.html.count("</a>") for chunk in chunks)

    def test_entities_are_not_split_mid_entity(self):
        html_text = markdown_to_telegram_html("A & B " * 600)
        chunks = split_telegram_html_chunks(html_text, 80)
        assert len(chunks) > 1
        assert all("&amp" not in chunk[-4:] for chunk in chunks[:-1])

    def test_plaintext_fallback_is_derived_from_html_chunk(self):
        chunks = markdown_to_telegram_chunks("**Bold** and [link](https://example.com)")
        assert chunks == [
            TelegramFormattedChunk(
                html="<b>Bold</b> and <a href=\"https://example.com\">link</a>",
                text="Bold and link",
            )
        ]

    def test_default_limit_uses_safety_margin(self):
        chunks = markdown_to_telegram_chunks("x" * (DEFAULT_TELEGRAM_TEXT_LIMIT + 50))
        assert len(chunks) == 2
        assert all(len(chunk.html) <= DEFAULT_TELEGRAM_TEXT_LIMIT for chunk in chunks)


class TestTelegramPlainTextFallback:
    def test_html_to_plain_text_unescapes_entities(self):
        assert telegram_html_to_plain_text("A &amp; B &lt; C") == "A & B < C"

    def test_html_to_plain_text_strips_tags(self):
        rendered = "<blockquote><b>Hello</b> <code>world</code></blockquote>"
        assert telegram_html_to_plain_text(rendered) == "Hello world"


class TestTelegramOutboundSending:
    @pytest.fixture()
    def config(self):
        return PlatformConfig(enabled=True, token="fake-token")

    def test_connected_send_uses_html_chunks_and_preserves_reply_and_thread(self, adapter):
        bold_text = "**" + ("hello " * 1199) + "hello**"
        mock_messages = [
            SimpleNamespace(message_id=1),
            SimpleNamespace(message_id=2),
        ]

        async def _send_message(**kwargs):
            return mock_messages.pop(0)

        adapter._bot = MagicMock()
        adapter._bot.send_message = AsyncMock(side_effect=_send_message)

        result = asyncio.run(
            send_connected_text(
                adapter,
                "-100123",
                bold_text,
                reply_to="55",
                metadata={"thread_id": "77"},
            )
        )

        assert result.success is True
        assert adapter._bot.send_message.await_count >= 2
        first_call = adapter._bot.send_message.await_args_list[0].kwargs
        second_call = adapter._bot.send_message.await_args_list[1].kwargs
        assert first_call["parse_mode"] == ParseMode.HTML
        assert first_call["reply_to_message_id"] == 55
        assert first_call["message_thread_id"] == 77
        assert "<b>" in first_call["text"]
        assert second_call["reply_to_message_id"] is None
        assert second_call["message_thread_id"] == 77

    def test_direct_send_html_parse_failure_retries_with_plain_text(self, config):
        calls = []

        async def _send_message(**kwargs):
            calls.append(kwargs)
            if kwargs.get("parse_mode") == ParseMode.HTML:
                raise Exception("can't parse entities")
            return SimpleNamespace(message_id=42)

        mock_bot = MagicMock()
        mock_bot.send_message = AsyncMock(side_effect=_send_message)

        with patch("telegram.Bot", return_value=mock_bot):
            result = asyncio.run(
                send_direct_text(
                    Platform.TELEGRAM,
                    config,
                    "-100123",
                    "**hello** [link](https://example.com)",
                )
            )

        assert result["success"] is True
        assert calls[0]["parse_mode"] == ParseMode.HTML
        assert calls[1]["parse_mode"] is None
        assert calls[1]["text"] == "hello link"


class TestFileReferences:
    """Tests for _is_file_reference and _wrap_file_references."""

    def test_plain_filename_is_file_reference(self):
        from gateway.platforms.telegram_format import _is_file_reference

        assert _is_file_reference("src/main.py", "src/main.py") is True

    def test_bare_filename_is_file_reference(self):
        from gateway.platforms.telegram_format import _is_file_reference

        assert _is_file_reference("foo.py", "foo.py") is True

    def test_url_is_not_file_reference(self):
        from gateway.platforms.telegram_format import _is_file_reference

        assert _is_file_reference("https://example.com", "example.com") is False

    def test_mailto_is_not_file_reference(self):
        from gateway.platforms.telegram_format import _is_file_reference

        assert _is_file_reference("mailto:user@example.com", "user@example.com") is False

    def test_wrap_file_references_wraps_known_extension(self):
        from gateway.platforms.telegram_format import _wrap_file_references

        result = _wrap_file_references("See config.yaml for details")
        assert "<code>config.yaml</code>" in result

    def test_wrap_file_references_preserves_non_file_text(self):
        from gateway.platforms.telegram_format import _wrap_file_references

        result = _wrap_file_references("No files here")
        assert result == "No files here"

    def test_wrap_file_references_handles_path_with_dirs(self):
        from gateway.platforms.telegram_format import _wrap_file_references

        result = _wrap_file_references("Check gateway/outbound/base.py")
        assert "<code>gateway/outbound/base.py</code>" in result
