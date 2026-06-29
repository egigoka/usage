"""Textual TUI for usage metrics."""

import json
from datetime import datetime, timezone

from rich.markup import escape
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, VerticalScroll
from textual.widgets import (
    Footer,
    Static,
)

from usage_tui.cache import ResultCache
from usage_tui.config import ENV_FILE_PATH, config
from usage_tui.providers import (
    ClaudeOAuthProvider,
    CodexProvider,
    CopilotProvider,
    OpenAIUsageProvider,
    OpenRouterUsageProvider,
)
from usage_tui.providers.base import (
    BaseProvider,
    ProviderName,
    ProviderResult,
    WindowPeriod,
)


class ProviderCard(Static):
    """A card displaying metrics for a single provider."""

    AGE_WIDTH = 15
    RESET_WIDTH = 20
    RESET_AT_WIDTH = 12
    SUCCESS_WIDTH = 18

    DEFAULT_CSS = """
    ProviderCard {
        width: 100%;
        height: auto;
        padding: 0 1;
        color: $text;
    }
    """

    def __init__(
        self,
        provider_name: ProviderName,
        windows: tuple[WindowPeriod, ...],
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self.provider_name = provider_name
        self.windows = windows
        self.provider_info = config.get_provider_status(provider_name)
        self._results: dict[WindowPeriod, ProviderResult] = {}
        self._last_successful: dict[WindowPeriod, ProviderResult] = {}

    def on_mount(self) -> None:
        """Render the initial state once the widget is attached."""
        self._render_card()

    def set_result(
        self,
        window: WindowPeriod,
        result: ProviderResult,
        last_successful: ProviderResult | None = None,
    ) -> None:
        """Store a result for one window and re-render."""
        self._results[window] = result
        if last_successful and not last_successful.is_error:
            self._last_successful[window] = last_successful
        if not result.is_error:
            self._last_successful[window] = result
        self._render_card()

    def refresh_display(self) -> None:
        """Re-render time-relative fields without fetching new data."""
        self._render_card()

    def _render_card(self) -> None:
        """Render the provider name with one compact line per window."""
        name = self.provider_info["name"]

        if not self.provider_info["configured"]:
            self.set_class(True, "unconfigured")
            self.update(f"[b]{name}[/] | not configured (set {self.provider_info['env_var']})")
            return

        self.set_class(False, "unconfigured")

        lines = [f"[b]{name}[/]"]
        for window in self.windows:
            lines.append("  " + self._window_line(window, self._results.get(window)))
        self.update("\n".join(lines))

    def _window_line(self, window: WindowPeriod, result: ProviderResult | None) -> str:
        """Build the compact metrics line for a single window."""
        label = window.value

        if result is None:
            return f"{label} | loading…"

        if result.is_error:
            age = datetime.now(timezone.utc) - result.updated_at.replace(tzinfo=timezone.utc)
            age_text = f"updated {self._format_age(max(0, age.total_seconds()))} ago"
            segments = [f"{age_text:<{self.AGE_WIDTH}}"]

            if last_successful := self._last_successful.get(window):
                success_age = datetime.now(timezone.utc) - last_successful.updated_at.replace(
                    tzinfo=timezone.utc
                )
                success_age_text = self._format_age(max(0, success_age.total_seconds()))
                success_text = f"successful {success_age_text} ago"
                segments.append(f"{success_text:<{self.SUCCESS_WIDTH}}")

            error_color = self._theme_color("error", "red")
            segments.append(f"[{error_color}]error: {escape(result.error or '')}[/]")
            return f"{label} | " + " | ".join(segments)

        segments = []

        age = datetime.now(timezone.utc) - result.updated_at.replace(tzinfo=timezone.utc)
        age_text = f"updated {self._format_age(max(0, age.total_seconds()))} ago"
        segments.append(f"{age_text:<{self.AGE_WIDTH}}")

        m = result.metrics

        if m.usage_percent is not None:
            pct = m.usage_percent
            color = self._usage_color(pct)
            segments.append(f"[{color}]{pct:5.1f}% used[/]")

        if m.reset_at:
            now = datetime.now(timezone.utc)
            reset_delta = m.reset_at - now
            if reset_delta.total_seconds() > 0:
                reset_text = f"resets in {self._format_duration(reset_delta.total_seconds())}"
                segments.append(f"{reset_text:<{self.RESET_WIDTH}}")

                local_reset = m.reset_at.astimezone()
                now_local = datetime.now(local_reset.tzinfo)
                reset_at_time = local_reset.strftime("%H:%M")
                if local_reset.date() != now_local.date():
                    date_prefix = local_reset.strftime("%d.%m")
                    reset_at_text = f"{date_prefix} {reset_at_time}"
                else:
                    reset_at_text = reset_at_time
                segments.append(f"{reset_at_text:<{self.RESET_AT_WIDTH}}")

        if m.cost is not None and self.provider_name != ProviderName.CODEX:
            segments.append(f"${m.cost:.4f}")

        if m.requests is not None:
            segments.append(f"{m.requests:,} reqs")

        if m.total_tokens is not None:
            segments.append(f"{m.total_tokens:,} tokens")

        return f"{label} | " + " | ".join(segments)

    def _theme_color(self, name: str, fallback: str) -> str:
        """Return an active theme color usable in Rich markup."""
        try:
            color = getattr(self.app.current_theme, name, None)
        except Exception:
            return fallback
        return color or fallback

    def _usage_color(self, pct: float) -> str:
        """Return theme-aware usage status color."""
        if pct < 80:
            return self._theme_color("success", "green")
        if pct < 95:
            return self._theme_color("warning", "yellow")
        return self._theme_color("error", "red")

    def _format_age(self, seconds: float) -> str:
        """Format age in human-readable form."""
        if seconds < 60:
            return f"{int(seconds)}s"
        elif seconds < 3600:
            return f"{int(seconds / 60)}m"
        else:
            return f"{int(seconds / 3600)}h"

    def _format_duration(self, seconds: float) -> str:
        """Format duration in human-readable form."""
        total_minutes = int(seconds // 60)
        days = total_minutes // (24 * 60)
        hours = (total_minutes // 60) % 24
        minutes = total_minutes % 60
        parts = []
        if days > 0:
            parts.append(f"{days}d")
        if hours > 0 or days > 0:
            parts.append(f"{hours}h")
        parts.append(f"{minutes}m")
        return " ".join(parts)


class UsageTUI(App):
    """Main TUI application for usage metrics."""

    CSS = """
    Screen {
        background: $background;
    }

    #main-container {
        width: 100%;
        height: 100%;
        padding: 0 0;
    }

    #cards-container {
        width: 100%;
        height: 1fr;
    }
    """

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("r", "refresh", "Refresh"),
    ]

    DISPLAY_REFRESH_SECONDS = 1
    DATA_REFRESH_SECONDS = 10

    # Windows shown per provider. Copilot only reports a fixed 30-day window.
    DEFAULT_WINDOWS = (WindowPeriod.HOUR_5, WindowPeriod.DAY_7)
    PROVIDER_WINDOWS = {
        ProviderName.COPILOT: (WindowPeriod.DAY_30,),
    }

    def __init__(self) -> None:
        self._theme_settings_ready = False
        self._settings_path = ENV_FILE_PATH.parent / "settings.json"
        super().__init__()
        self.cache = ResultCache()
        self.providers: dict[ProviderName, BaseProvider] = {
            ProviderName.CLAUDE: ClaudeOAuthProvider(),
            ProviderName.OPENAI: OpenAIUsageProvider(),
            ProviderName.OPENROUTER: OpenRouterUsageProvider(),
            ProviderName.COPILOT: CopilotProvider(),
            ProviderName.CODEX: CodexProvider(),
        }
        self._refreshing = False

        saved_theme = self._load_saved_theme()
        self._theme_settings_ready = True
        if saved_theme in self.available_themes:
            self.theme = saved_theme

    def _watch_theme(self, theme_name: str) -> None:
        """Apply and persist theme changes."""
        super()._watch_theme(theme_name)
        if self._theme_settings_ready:
            self._save_theme(theme_name)

    def _load_settings(self) -> dict[str, object]:
        """Load TUI settings from disk."""
        try:
            data = json.loads(self._settings_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return data if isinstance(data, dict) else {}

    def _load_saved_theme(self) -> str | None:
        """Return the saved theme name if one exists."""
        theme = self._load_settings().get("theme")
        return theme if isinstance(theme, str) else None

    def _save_theme(self, theme_name: str) -> None:
        """Persist the selected theme."""
        settings = self._load_settings()
        settings["theme"] = theme_name
        try:
            self._settings_path.parent.mkdir(parents=True, exist_ok=True)
            self._settings_path.write_text(
                json.dumps(settings, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
        except OSError:
            pass

    def _windows_for(self, provider: ProviderName) -> tuple[WindowPeriod, ...]:
        """Time windows to display for a provider."""
        return self.PROVIDER_WINDOWS.get(provider, self.DEFAULT_WINDOWS)

    def compose(self) -> ComposeResult:
        with Container(id="main-container"):
            yield VerticalScroll(
                *(
                    ProviderCard(name, self._windows_for(name), id=f"card-{name.value}")
                    for name in self._ordered_providers()
                ),
                id="cards-container",
            )
        yield Footer()

    async def on_mount(self) -> None:
        """Initialize and fetch data on mount."""
        self.set_interval(self.DISPLAY_REFRESH_SECONDS, self._refresh_display)
        self.set_interval(self.DATA_REFRESH_SECONDS, self._refresh_from_timer)
        await self._refresh_data(use_cache=True)

    async def action_refresh(self) -> None:
        """Force-refresh all provider data for every displayed window."""
        await self._refresh_data(use_cache=False)

    async def _refresh_from_timer(self) -> None:
        """Refresh provider data when cached results have expired."""
        await self._refresh_data(use_cache=True)

    def _refresh_display(self) -> None:
        """Update relative timestamps and reset countdowns every second."""
        for provider_name in self.providers:
            card = self._get_card(provider_name)
            if card:
                card.refresh_display()

    async def _refresh_data(self, *, use_cache: bool) -> None:
        """Refresh all provider data for every displayed window."""
        if self._refreshing:
            return

        self._refreshing = True
        try:
            await self._fetch_provider_data(use_cache=use_cache)
        finally:
            self._refreshing = False

    async def _fetch_provider_data(self, *, use_cache: bool) -> None:
        """Fetch provider data, optionally using cached results."""
        for provider_name, provider in self.providers.items():
            if not provider.is_configured():
                continue

            card = self._get_card(provider_name)

            for window in self._windows_for(provider_name):
                cached = self.cache.get(provider_name, window) if use_cache else None
                if cached:
                    result = cached
                else:
                    try:
                        result = await provider.fetch(window)
                        self.cache.set(result)
                    except Exception as e:
                        result = provider._make_error_result(window, str(e))

                if card:
                    last_successful = None
                    if result.is_error:
                        last_successful = self.cache.get_last_good(provider_name, window)
                    card.set_result(window, result, last_successful=last_successful)

    def _ordered_providers(self) -> list[ProviderName]:
        """Configured (logged-in) providers first, unconfigured at the bottom.

        Original declaration order is preserved within each group.
        """
        configured = [n for n, p in self.providers.items() if p.is_configured()]
        unconfigured = [n for n, p in self.providers.items() if not p.is_configured()]
        return configured + unconfigured

    def _get_card(self, provider: ProviderName) -> ProviderCard | None:
        """Get the card widget for a provider."""
        card_id = f"card-{provider.value}"
        try:
            return self.query_one(f"#{card_id}", ProviderCard)
        except Exception:
            return None


def run_tui() -> None:
    """Run the TUI application."""
    app = UsageTUI()
    app.run()


if __name__ == "__main__":
    run_tui()
