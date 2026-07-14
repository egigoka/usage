"""Regression tests for TUI provider-card rendering."""

from datetime import datetime, timedelta, timezone

from usage_tui.providers.base import ProviderName, ProviderResult, UsageMetrics, WindowPeriod
from usage_tui.tui import ProviderCard


def _result(window: WindowPeriod, *, reset_at: datetime) -> ProviderResult:
    return ProviderResult(
        provider=ProviderName.CODEX2,
        window=window,
        metrics=UsageMetrics(remaining=83.0, limit=100.0, reset_at=reset_at),
        updated_at=datetime.now(timezone.utc),
    )


def test_shared_limit_allows_small_reset_timestamp_drift() -> None:
    """Separate API calls can estimate the same reset one second apart."""
    reset_at = datetime(2026, 7, 20, 2, 2, tzinfo=timezone.utc)
    card = ProviderCard(
        ProviderName.CODEX2,
        (WindowPeriod.HOUR_5, WindowPeriod.DAY_7),
    )
    card._results = {
        WindowPeriod.HOUR_5: _result(WindowPeriod.HOUR_5, reset_at=reset_at),
        WindowPeriod.DAY_7: _result(WindowPeriod.DAY_7, reset_at=reset_at - timedelta(seconds=1)),
    }

    assert card._limits_match(WindowPeriod.HOUR_5, WindowPeriod.DAY_7)
