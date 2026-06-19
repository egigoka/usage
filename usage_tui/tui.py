"""Textual TUI for usage metrics."""

from datetime import datetime, timezone

from textual import events, on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, VerticalScroll
from textual.widgets import (
    Button,
    Footer,
    Static,
)

from usage_tui.cache import ResultCache
from usage_tui.config import config
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

    DEFAULT_CSS = """
    ProviderCard {
        width: 100%;
        height: auto;
        padding: 0 1;
        color: $text;
    }

    ProviderCard.unconfigured {
        color: $text-muted;
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

    def on_mount(self) -> None:
        """Render the initial state once the widget is attached."""
        self._render_card()

    def set_result(self, window: WindowPeriod, result: ProviderResult) -> None:
        """Store a result for one window and re-render."""
        self._results[window] = result
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
        label = f"[dim]{window.value}[/]"

        if result is None:
            return f"{label} | loading…"

        if result.is_error:
            return f"{label} | [red]error: {result.error}[/]"

        segments = []

        age = datetime.now(timezone.utc) - result.updated_at.replace(tzinfo=timezone.utc)
        segments.append(f"updated {self._format_age(age.total_seconds())} ago")

        m = result.metrics

        if m.usage_percent is not None:
            pct = m.usage_percent
            color = "green" if pct < 80 else ("yellow" if pct < 95 else "red")
            segments.append(f"[{color}]{pct:.1f}% used[/]")

        if m.reset_at:
            reset_delta = m.reset_at - datetime.now(timezone.utc)
            if reset_delta.total_seconds() > 0:
                segments.append(f"resets in {self._format_duration(reset_delta.total_seconds())}")

        if m.cost is not None:
            segments.append(f"${m.cost:.4f}")

        if m.requests is not None:
            segments.append(f"{m.requests:,} reqs")

        if m.total_tokens is not None:
            segments.append(f"{m.total_tokens:,} tokens")

        return f"{label} | " + " | ".join(segments)

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
        padding: 1 2;
    }

    #cards-container {
        width: 100%;
        height: 1fr;
    }

    #refresh-btn {
        width: 100%;
        margin-top: 1;
    }
    """

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("r", "refresh", "Refresh"),
    ]

    # Minimum terminal height (rows) before the bottom Refresh button is shown.
    MIN_ROWS_FOR_REFRESH = 20

    # Windows shown per provider. Copilot only reports a fixed 30-day window.
    DEFAULT_WINDOWS = (WindowPeriod.HOUR_5, WindowPeriod.DAY_7)
    PROVIDER_WINDOWS = {
        ProviderName.COPILOT: (WindowPeriod.DAY_30,),
    }

    def __init__(self) -> None:
        super().__init__()
        self.cache = ResultCache()
        self.providers: dict[ProviderName, BaseProvider] = {
            ProviderName.CLAUDE: ClaudeOAuthProvider(),
            ProviderName.OPENAI: OpenAIUsageProvider(),
            ProviderName.OPENROUTER: OpenRouterUsageProvider(),
            ProviderName.COPILOT: CopilotProvider(),
            ProviderName.CODEX: CodexProvider(),
        }

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
            yield Button("Refresh", id="refresh-btn", variant="primary")
        yield Footer()

    async def on_mount(self) -> None:
        """Initialize and fetch data on mount."""
        self._update_refresh_visibility()
        await self.action_refresh()

    def on_resize(self, event: events.Resize) -> None:
        """Show the Refresh button only when the console is tall enough."""
        self._update_refresh_visibility()

    def _update_refresh_visibility(self) -> None:
        """Hide the bottom Refresh button on short terminals (use 'r' instead)."""
        try:
            btn = self.query_one("#refresh-btn", Button)
        except Exception:
            return
        btn.display = self.size.height >= self.MIN_ROWS_FOR_REFRESH

    @on(Button.Pressed, "#refresh-btn")
    async def on_refresh_pressed(self) -> None:
        """Handle refresh button press."""
        await self.action_refresh()

    async def action_refresh(self) -> None:
        """Refresh all provider data for every displayed window."""
        for provider_name, provider in self.providers.items():
            if not provider.is_configured():
                continue

            card = self._get_card(provider_name)

            for window in self._windows_for(provider_name):
                cached = self.cache.get(provider_name, window)
                if cached:
                    result = cached
                else:
                    try:
                        result = await provider.fetch(window)
                        self.cache.set(result)
                    except Exception as e:
                        result = provider._make_error_result(window, str(e))

                if card:
                    card.set_result(window, result)

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
