"""Tests for Firecrawl client configuration and singleton behavior.

Coverage:
  _get_firecrawl_client() — configuration matrix, singleton caching,
  constructor failure recovery, return value verification, edge cases.
"""

import os
import pytest
from unittest.mock import patch, MagicMock


class TestFirecrawlClientConfig:
    """Test suite for Firecrawl client initialization."""

    def setup_method(self):
        """Reset client and env vars before each test."""
        import tools.web_tools
        tools.web_tools._firecrawl_client = None
        tools.web_tools._firecrawl_client_config = None
        for key in (
            "FIRECRAWL_API_KEY",
            "FIRECRAWL_API_URL",
            "FIRECRAWL_GATEWAY_URL",
            "TOOL_GATEWAY_DOMAIN",
            "TOOL_GATEWAY_SCHEME",
            "TOOL_GATEWAY_USER_TOKEN",
        ):
            os.environ.pop(key, None)

    def teardown_method(self):
        """Reset client after each test."""
        import tools.web_tools
        tools.web_tools._firecrawl_client = None
        tools.web_tools._firecrawl_client_config = None
        for key in (
            "FIRECRAWL_API_KEY",
            "FIRECRAWL_API_URL",
            "FIRECRAWL_GATEWAY_URL",
            "TOOL_GATEWAY_DOMAIN",
            "TOOL_GATEWAY_SCHEME",
            "TOOL_GATEWAY_USER_TOKEN",
        ):
            os.environ.pop(key, None)

    # ── Configuration matrix ─────────────────────────────────────────

    def test_cloud_mode_key_only(self):
        """API key without URL → cloud Firecrawl."""
        with patch.dict(os.environ, {"FIRECRAWL_API_KEY": "fc-test"}):
            with patch("tools.web_tools.Firecrawl") as mock_fc:
                from tools.web_tools import _get_firecrawl_client
                result = _get_firecrawl_client()
                mock_fc.assert_called_once_with(api_key="fc-test")
                assert result is mock_fc.return_value

    def test_self_hosted_with_key(self):
        """Both key + URL → self-hosted with auth."""
        with patch.dict(os.environ, {
            "FIRECRAWL_API_KEY": "fc-test",
            "FIRECRAWL_API_URL": "http://localhost:3002",
        }):
            with patch("tools.web_tools.Firecrawl") as mock_fc:
                from tools.web_tools import _get_firecrawl_client
                result = _get_firecrawl_client()
                mock_fc.assert_called_once_with(
                    api_key="fc-test", api_url="http://localhost:3002"
                )
                assert result is mock_fc.return_value

    def test_self_hosted_no_key(self):
        """URL only, no key → self-hosted without auth."""
        with patch.dict(os.environ, {"FIRECRAWL_API_URL": "http://localhost:3002"}):
            with patch("tools.web_tools.Firecrawl") as mock_fc:
                from tools.web_tools import _get_firecrawl_client
                result = _get_firecrawl_client()
                mock_fc.assert_called_once_with(api_url="http://localhost:3002")
                assert result is mock_fc.return_value

    def test_no_config_raises_with_helpful_message(self):
        """Neither key nor URL → ValueError with guidance."""
        with patch("tools.web_tools.Firecrawl"):
            with patch("tools.web_tools._read_nous_access_token", return_value=None):
                from tools.web_tools import _get_firecrawl_client
                with pytest.raises(ValueError, match="FIRECRAWL_API_KEY"):
                    _get_firecrawl_client()

    def test_tool_gateway_domain_builds_firecrawl_gateway_origin(self):
        """Shared gateway domain should derive the Firecrawl vendor hostname."""
        with patch.dict(os.environ, {"TOOL_GATEWAY_DOMAIN": "nousresearch.com"}):
            with patch("tools.web_tools._read_nous_access_token", return_value="nous-token"):
                with patch("tools.web_tools.Firecrawl") as mock_fc:
                    from tools.web_tools import _get_firecrawl_client
                    result = _get_firecrawl_client()
                    mock_fc.assert_called_once_with(
                        api_key="nous-token",
                        api_url="https://firecrawl-gateway.nousresearch.com",
                    )
                    assert result is mock_fc.return_value

    def test_tool_gateway_scheme_can_switch_derived_gateway_origin_to_http(self):
        """Shared gateway scheme should allow local plain-http vendor hosts."""
        with patch.dict(os.environ, {
            "TOOL_GATEWAY_DOMAIN": "nousresearch.com",
            "TOOL_GATEWAY_SCHEME": "http",
        }):
            with patch("tools.web_tools._read_nous_access_token", return_value="nous-token"):
                with patch("tools.web_tools.Firecrawl") as mock_fc:
                    from tools.web_tools import _get_firecrawl_client
                    result = _get_firecrawl_client()
                    mock_fc.assert_called_once_with(
                        api_key="nous-token",
                        api_url="http://firecrawl-gateway.nousresearch.com",
                    )
                    assert result is mock_fc.return_value

    def test_invalid_tool_gateway_scheme_raises(self):
        """Unexpected shared gateway schemes should fail fast."""
        with patch.dict(os.environ, {
            "TOOL_GATEWAY_DOMAIN": "nousresearch.com",
            "TOOL_GATEWAY_SCHEME": "ftp",
        }):
            with patch("tools.web_tools._read_nous_access_token", return_value="nous-token"):
                from tools.web_tools import _get_firecrawl_client
                with pytest.raises(ValueError, match="TOOL_GATEWAY_SCHEME"):
                    _get_firecrawl_client()

    def test_explicit_firecrawl_gateway_url_takes_precedence(self):
        """An explicit Firecrawl gateway origin should override the shared domain."""
        with patch.dict(os.environ, {
            "FIRECRAWL_GATEWAY_URL": "https://firecrawl-gateway.localhost:3009/",
            "TOOL_GATEWAY_DOMAIN": "nousresearch.com",
        }):
            with patch("tools.web_tools._read_nous_access_token", return_value="nous-token"):
                with patch("tools.web_tools.Firecrawl") as mock_fc:
                    from tools.web_tools import _get_firecrawl_client
                    _get_firecrawl_client()
                    mock_fc.assert_called_once_with(
                        api_key="nous-token",
                        api_url="https://firecrawl-gateway.localhost:3009",
                    )

    def test_default_gateway_domain_targets_nous_production_origin(self):
        """Default gateway origin should point at the Firecrawl vendor hostname."""
        with patch("tools.web_tools._read_nous_access_token", return_value="nous-token"):
            with patch("tools.web_tools.Firecrawl") as mock_fc:
                from tools.web_tools import _get_firecrawl_client
                _get_firecrawl_client()
                mock_fc.assert_called_once_with(
                    api_key="nous-token",
                    api_url="https://firecrawl-gateway.nousresearch.com",
                )

    def test_direct_mode_is_preferred_over_tool_gateway(self):
        """Explicit Firecrawl config should win over the gateway fallback."""
        with patch.dict(os.environ, {
            "FIRECRAWL_API_KEY": "fc-test",
            "TOOL_GATEWAY_DOMAIN": "nousresearch.com",
        }):
            with patch("tools.web_tools._read_nous_access_token", return_value="nous-token"):
                with patch("tools.web_tools.Firecrawl") as mock_fc:
                    from tools.web_tools import _get_firecrawl_client
                    _get_firecrawl_client()
                    mock_fc.assert_called_once_with(api_key="fc-test")

    # ── Singleton caching ────────────────────────────────────────────

    def test_singleton_returns_same_instance(self):
        """Second call returns cached client without re-constructing."""
        with patch.dict(os.environ, {"FIRECRAWL_API_KEY": "fc-test"}):
            with patch("tools.web_tools.Firecrawl") as mock_fc:
                from tools.web_tools import _get_firecrawl_client
                client1 = _get_firecrawl_client()
                client2 = _get_firecrawl_client()
                assert client1 is client2
                mock_fc.assert_called_once()  # constructed only once

    def test_constructor_failure_allows_retry(self):
        """If Firecrawl() raises, next call should retry (not return None)."""
        import tools.web_tools
        with patch.dict(os.environ, {"FIRECRAWL_API_KEY": "fc-test"}):
            with patch("tools.web_tools.Firecrawl") as mock_fc:
                mock_fc.side_effect = [RuntimeError("init failed"), MagicMock()]
                from tools.web_tools import _get_firecrawl_client

                with pytest.raises(RuntimeError):
                    _get_firecrawl_client()

                # Client stayed None, so retry should work
                assert tools.web_tools._firecrawl_client is None
                result = _get_firecrawl_client()
                assert result is not None

    # ── Edge cases ───────────────────────────────────────────────────

    def test_empty_string_key_treated_as_absent(self):
        """FIRECRAWL_API_KEY='' should not be passed as api_key."""
        with patch.dict(os.environ, {
            "FIRECRAWL_API_KEY": "",
            "FIRECRAWL_API_URL": "http://localhost:3002",
        }):
            with patch("tools.web_tools.Firecrawl") as mock_fc:
                from tools.web_tools import _get_firecrawl_client
                _get_firecrawl_client()
                # Empty string is falsy, so only api_url should be passed
                mock_fc.assert_called_once_with(api_url="http://localhost:3002")

    def test_empty_string_key_no_url_raises(self):
        """FIRECRAWL_API_KEY='' with no URL → should raise."""
        with patch.dict(os.environ, {"FIRECRAWL_API_KEY": ""}):
            with patch("tools.web_tools.Firecrawl"):
                with patch("tools.web_tools._read_nous_access_token", return_value=None):
                    from tools.web_tools import _get_firecrawl_client
                    with pytest.raises(ValueError):
                        _get_firecrawl_client()
