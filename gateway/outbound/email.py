"""Email outbound text sender."""

from __future__ import annotations

import asyncio
import os
import smtplib
import ssl
import uuid
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any

from gateway.config import Platform
from gateway.platforms.base import SendResult

from .base import (
    BaseOutboundTextSender,
    OutboundCapabilities,
    OutboundRequest,
    PreparedChunk,
)
from .registry import register_sender


class EmailOutboundTextSender(BaseOutboundTextSender):
    """Outbound sender for email text messages."""

    platform = Platform.EMAIL
    capabilities = OutboundCapabilities(
        supports_direct_send=True,
        supports_reply_to=True,
    )

    def parse_target_ref(self, target_ref: str) -> tuple[str | None, str | None, bool]:
        normalized = (target_ref or "").strip()
        if "@" in normalized and " " not in normalized:
            return normalized, None, True
        return super().parse_target_ref(target_ref)

    def prepare_text(self, content: str) -> list[PreparedChunk]:
        return [PreparedChunk(body=content)]

    async def send_connected(
        self,
        adapter: Any,
        request: OutboundRequest,
        chunks: list[PreparedChunk],
    ) -> SendResult:
        try:
            loop = asyncio.get_running_loop()
            message_id = await loop.run_in_executor(
                None,
                adapter._send_email,
                request.chat_id,
                chunks[0].body,
                request.reply_to,
            )
            return SendResult(success=True, message_id=message_id)
        except Exception as exc:
            return SendResult(success=False, error=str(exc))

    async def send_direct(
        self,
        config: Any,
        request: OutboundRequest,
        chunks: list[PreparedChunk],
    ) -> dict[str, Any]:
        address = config.extra.get("address") or os.getenv("EMAIL_ADDRESS", "")
        password = os.getenv("EMAIL_PASSWORD", "")
        smtp_host = config.extra.get("smtp_host") or os.getenv("EMAIL_SMTP_HOST", "")
        smtp_port = int(os.getenv("EMAIL_SMTP_PORT", "587"))

        if not all([address, password, smtp_host]):
            return {
                "error": "Email not configured (EMAIL_ADDRESS, EMAIL_PASSWORD, EMAIL_SMTP_HOST required)"
            }

        try:
            message = MIMEMultipart()
            message["From"] = address
            message["To"] = request.chat_id
            message["Subject"] = "Hermes Agent"
            if request.reply_to:
                message["In-Reply-To"] = request.reply_to
                message["References"] = request.reply_to
            message_id = f"<hermes-{uuid.uuid4().hex[:12]}@{address.split('@')[1]}>"
            message["Message-ID"] = message_id
            message.attach(MIMEText(chunks[0].body, "plain", "utf-8"))

            server = smtplib.SMTP(smtp_host, smtp_port)
            server.starttls(context=ssl.create_default_context())
            server.login(address, password)
            server.send_message(message)
            server.quit()

            return {
                "success": True,
                "platform": self.platform.value,
                "chat_id": request.chat_id,
                "message_id": message_id,
            }
        except Exception as exc:
            return {"error": f"Email send failed: {exc}"}


SENDER = EmailOutboundTextSender()
register_sender(SENDER)
