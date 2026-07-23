"""Tests for OpenAI Codex reset-credit display."""

from datetime import datetime

import pytest

from usage_tui.providers.base import ProviderName
from usage_tui.providers.codex import CodexCredentials, CodexProvider


def test_display_name_includes_available_reset_expirations() -> None:
    provider = CodexProvider(
        credentials=CodexCredentials(
            access_token="test-token",
            account_email="user@example.com",
        )
    )
    provider._parse_reset_credits(
        {
            "available_count": 2,
            "credits": [
                {"status": "used", "expires_at": "2026-07-31T12:00:00Z"},
                {"status": "available", "expires_at": "2026-08-03T12:00:00Z"},
                {"status": "available", "expires_at": "2026-08-01T12:00:00Z"},
            ],
        }
    )

    expected_dates = ", ".join(
        datetime.fromisoformat(value).astimezone().strftime("%b %d").replace(" 0", " ")
        for value in ("2026-08-01T12:00:00+00:00", "2026-08-03T12:00:00+00:00")
    )
    assert provider.display_name == (
        f"OpenAI Codex (user@example.com) Usage resets available x2 - {expected_dates}"
    )


def test_display_name_omits_reset_suffix_when_none_are_available() -> None:
    provider = CodexProvider(
        credentials=CodexCredentials(
            access_token="test-token",
            account_email="user@example.com",
        )
    )
    provider._parse_reset_credits({"available_count": 0, "credits": []})

    assert provider.display_name == "OpenAI Codex (user@example.com)"


@pytest.mark.parametrize(
    ("name", "base_name"),
    [
        (ProviderName.CODEX2, "OpenAI Codex 2"),
        (ProviderName.CODEX3, "OpenAI Codex 3"),
    ],
)
def test_additional_subscriptions_include_reset_credits(
    name: ProviderName, base_name: str
) -> None:
    provider = CodexProvider(
        credentials=CodexCredentials(access_token="test-token"),
        name=name,
    )
    provider._parse_reset_credits({"available_count": 1, "credits": []})

    assert provider.display_name == f"{base_name} Usage resets available x1"
