"""Signal outbound text sender."""

from __future__ import annotations

import time
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


class SignalOutboundTextSender(BaseOutboundTextSender):
    """Outbound sender for Signal text messages."""

    platform = Platform.SIGNAL
    capabilities = OutboundCapabilities(supports_direct_send=True)

    def parse_target_ref(self, target_ref: str) -> tuple[str | None, str | None, bool]:
        normalized = (target_ref or "").strip()
        if normalized.startswith("+") or normalized.startswith("group:"):
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
        await adapter._stop_typing_indicator(request.chat_id)
        result = await adapter._rpc("send", _build_signal_params(adapter.account, request.chat_id, chunks[0].body))
        if result is not None:
            return SendResult(success=True)
        return SendResult(success=False, error="RPC send failed")

    async def send_direct(
        self,
        config: Any,
        request: OutboundRequest,
        chunks: list[PreparedChunk],
    ) -> dict[str, Any]:
        try:
            import httpx
        except ImportError:
            return {"error": "httpx not installed"}

        try:
            http_url = config.extra.get("http_url", "http://127.0.0.1:8080").rstrip("/")
            account = config.extra.get("account", "")
            if not account:
                return {"error": "Signal account not configured"}

            payload = {
                "jsonrpc": "2.0",
                "method": "send",
                "params": _build_signal_params(account, request.chat_id, chunks[0].body),
                "id": f"send_{int(time.time() * 1000)}",
            }

            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(f"{http_url}/api/v1/rpc", json=payload)
                response.raise_for_status()
                data = response.json()
                if "error" in data:
                    return {"error": f"Signal RPC error: {data['error']}"}

            return {
                "success": True,
                "platform": self.platform.value,
                "chat_id": request.chat_id,
            }
        except Exception as exc:
            return {"error": f"Signal send failed: {exc}"}


def _build_signal_params(account: str, chat_id: str, message: str) -> dict[str, Any]:
    params: dict[str, Any] = {
        "account": account,
        "message": message,
    }
    if chat_id.startswith("group:"):
        params["groupId"] = chat_id[6:]
    else:
        params["recipient"] = [chat_id]
    return params


SENDER = SignalOutboundTextSender()
register_sender(SENDER)
