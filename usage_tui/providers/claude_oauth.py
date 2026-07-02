"""Claude provider for Claude Code subscription quota."""

import asyncio
import os
import re
import shutil
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx

from usage_tui.claude_cli_auth import extract_claude_cli_token
from usage_tui.providers.base import (
    AuthenticationError,
    BaseProvider,
    ProviderError,
    ProviderName,
    ProviderResult,
    UsageMetrics,
    WindowPeriod,
)


class ClaudeOAuthProvider(BaseProvider):
    """
    Provider for Claude Code usage metrics.

    Prefers `claude /usage` because the OAuth usage API is aggressively
    rate-limited. Falls back to the OAuth API when the CLI path is unavailable.

    Environment Variables:
        CLAUDE_CODE_OAUTH_TOKEN: OAuth token (sk-ant-oat...)

    Note: This endpoint is unofficial and may change. Code parses defensively.
    """

    name = ProviderName.CLAUDE
    USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
    TOKEN_ENV_VAR = "CLAUDE_CODE_OAUTH_TOKEN"
    CLI_CACHE_SECONDS = 120

    def __init__(self, token: str | None = None) -> None:
        """
        Initialize the Claude OAuth provider.

        Args:
            token: OAuth token. If not provided, reads from environment,
                   then falls back to Claude CLI credentials.
        """
        self._token = token or os.environ.get(self.TOKEN_ENV_VAR) or extract_claude_cli_token()
        self._cli_cache: dict[WindowPeriod, ProviderResult] | None = None
        self._cli_cache_at: datetime | None = None
        self._cli_fetch_lock: asyncio.Lock = asyncio.Lock()

    def is_configured(self) -> bool:
        """Check if Claude CLI or OAuth token is available."""
        return shutil.which("claude") is not None or (
            self._token is not None and self._token.startswith("sk-ant-")
        )

    def get_config_help(self) -> str:
        """Get configuration instructions."""
        return f"""Claude OAuth Provider Configuration:

1. Install and authenticate Claude Code:
   claude

2. Optional fallback: set environment variable:
   export {self.TOKEN_ENV_VAR}=sk-ant-oat01-...

Note: `claude /usage` is preferred for quota data."""

    async def fetch(self, window: WindowPeriod = WindowPeriod.DAY_7) -> ProviderResult:
        """
        Fetch Claude Code subscription quota.

        Note: The window parameter is ignored as Claude's OAuth endpoint
        returns current quota state, not historical data.
        """
        cli_result = None
        if shutil.which("claude") is not None:
            cli_result = await self._fetch_from_cli(window)
            if not cli_result.is_error:
                return cli_result

        if not self._token:
            if cli_result:
                return cli_result
            return self._make_error_result(
                window=window,
                error="Not configured. Run `claude` to authenticate Claude Code.",
            )

        api_result = await self._fetch_from_api(window)
        if api_result.is_error and cli_result:
            return self._make_combined_error_result(window, cli_result, api_result)
        return api_result

    def _make_combined_error_result(
        self,
        window: WindowPeriod,
        cli_result: ProviderResult,
        api_result: ProviderResult,
    ) -> ProviderResult:
        """Combine Claude CLI and API failures into one clear error."""
        api_limited = api_result.raw.get("status_code") == 429
        if api_limited and cli_result.error == "Could not parse Claude CLI /usage output":
            return self._make_error_result(
                window=window,
                error="both claude /usage and api is rate limited",
                raw={"source": "claude_cli_and_api", "api_status_code": 429},
            )

        return self._make_error_result(
            window=window,
            error=f"Claude CLI failed ({cli_result.error}); OAuth API failed ({api_result.error})",
            raw={
                "source": "claude_cli_and_api",
                "api_status_code": api_result.raw.get("status_code"),
            },
        )

    async def _fetch_from_api(self, window: WindowPeriod) -> ProviderResult:
        """Fetch Claude Code subscription quota from the OAuth API."""
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(
                    self.USAGE_URL,
                    headers={
                        "Authorization": f"Bearer {self._token}",
                        "anthropic-beta": "oauth-2025-04-20",
                        "Accept": "application/json",
                        "User-Agent": "usage-tui",
                    },
                )

                if response.status_code == 401:
                    raise AuthenticationError("Invalid or expired OAuth token")

                if response.status_code == 403:
                    error_body = response.text
                    if "user:profile" in error_body:
                        return self._make_error_result(
                            window=window,
                            error=(
                                "OAuth scope error. Fix: unset CLAUDE_CODE_OAUTH_TOKEN "
                                "&& claude setup-token"
                            ),
                            raw={"status_code": 403, "body": error_body},
                        )
                    return self._make_error_result(
                        window=window,
                        error=f"API forbidden: HTTP {response.status_code}",
                        raw={"status_code": 403, "body": error_body},
                    )

                if response.status_code == 429:
                    return self._make_error_result(
                        window=window,
                        error="Rate limited. Try again later.",
                        raw={"status_code": 429},
                    )

                if response.status_code != 200:
                    return self._make_error_result(
                        window=window,
                        error=f"API error: HTTP {response.status_code}",
                        raw={"status_code": response.status_code, "body": response.text},
                    )

                data = response.json()
                return self._parse_response(data, window)

        except AuthenticationError:
            raise
        except httpx.TimeoutException:
            return self._make_error_result(
                window=window,
                error="Request timed out",
            )
        except httpx.RequestError as e:
            return self._make_error_result(
                window=window,
                error=f"Network error: {e}",
            )
        except Exception as e:
            raise ProviderError(f"Unexpected error: {e}") from e

    async def _fetch_from_cli(self, window: WindowPeriod) -> ProviderResult:
        """Fetch Claude Code quota by running `claude /usage`."""
        cached = self._get_cli_cached(window)
        if cached:
            return cached

        async with self._cli_fetch_lock:
            cached = self._get_cli_cached(window)
            if cached:
                return cached

            return await self._run_cli_fetch(window)

    async def _run_cli_fetch(self, window: WindowPeriod) -> ProviderResult:
        """Run `claude /usage` subprocess (must be called under _cli_fetch_lock)."""
        env = os.environ.copy()
        # ClaudeBar found setup tokens can lack the scopes needed by /usage.
        env.pop(self.TOKEN_ENV_VAR, None)

        process = None
        try:
            process = await asyncio.create_subprocess_exec(
                "claude",
                "/usage",
                "--allowed-tools",
                "",
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
            stdout, stderr = await asyncio.wait_for(
                process.communicate(input=b"\n"),
                timeout=20,
            )
        except TimeoutError:
            if process and process.returncode is None:
                process.kill()
                await process.wait()
            return self._make_error_result(window=window, error="Claude CLI /usage timed out")
        except OSError as e:
            return self._make_error_result(window=window, error=f"Claude CLI unavailable: {e}")

        output = "\n".join(
            part.decode("utf-8", errors="replace") for part in (stdout, stderr) if part
        )
        if process.returncode not in (0, None):
            error = self._extract_cli_error(output) or (
                f"Claude CLI exited with {process.returncode}"
            )
            return self._make_error_result(window=window, error=error, raw={"source": "claude_cli"})

        try:
            results = self._parse_cli_output(output)
        except ValueError as e:
            return self._make_error_result(
                window=window,
                error=str(e),
                raw={"source": "claude_cli"},
            )

        self._cli_cache = results
        self._cli_cache_at = datetime.now(timezone.utc)
        return results.get(window) or self._make_error_result(
            window=window,
            error=f"Claude CLI did not report {window.value} usage",
            raw={"source": "claude_cli"},
        )

    def _get_cli_cached(self, window: WindowPeriod) -> ProviderResult | None:
        """Return a recent parsed CLI result for the requested window."""
        if not self._cli_cache or not self._cli_cache_at:
            return None
        age = datetime.now(timezone.utc) - self._cli_cache_at
        if age.total_seconds() >= self.CLI_CACHE_SECONDS:
            self._cli_cache = None
            self._cli_cache_at = None
            return None
        return self._cli_cache.get(window)

    def _parse_cli_output(self, output: str) -> dict[WindowPeriod, ProviderResult]:
        """Parse `claude /usage` output into per-window provider results."""
        clean = self._strip_terminal_sequences(output)
        if error := self._extract_cli_error(clean):
            raise ValueError(error)

        lines = [line.strip() for line in clean.splitlines() if line.strip()]
        captured_at = datetime.now(timezone.utc)
        parsed: dict[WindowPeriod, ProviderResult] = {}

        if session := self._parse_cli_window(
            lines,
            labels=("current session",),
            window=WindowPeriod.HOUR_5,
            captured_at=captured_at,
        ):
            parsed[WindowPeriod.HOUR_5] = session

        if weekly := self._parse_cli_window(
            lines,
            labels=("current week (all models)", "current week"),
            window=WindowPeriod.DAY_7,
            captured_at=captured_at,
        ):
            parsed[WindowPeriod.DAY_7] = weekly

        if not parsed:
            raise ValueError("Could not parse Claude CLI /usage output")
        return parsed

    def _parse_cli_window(
        self,
        lines: list[str],
        labels: tuple[str, ...],
        window: WindowPeriod,
        captured_at: datetime,
    ) -> ProviderResult | None:
        """Parse one Claude CLI usage window."""
        for label in labels:
            for index, line in enumerate(lines):
                if label not in line.lower():
                    continue

                window_lines = lines[index : index + 12]
                remaining = self._extract_remaining_percent(window_lines)
                if remaining is None:
                    continue

                reset_at = self._parse_cli_reset(self._extract_reset_text(window_lines))
                return ProviderResult(
                    provider=self.name,
                    window=window,
                    metrics=UsageMetrics(
                        remaining=remaining,
                        limit=100.0,
                        reset_at=reset_at,
                    ),
                    updated_at=captured_at,
                    raw={"source": "claude_cli"},
                )
        return None

    def _extract_remaining_percent(self, lines: list[str]) -> float | None:
        """Extract remaining quota percentage from nearby CLI output lines."""
        for line in lines:
            match = re.search(r"(\d{1,3}(?:\.\d+)?)\s*%\s*(used|left)", line, re.IGNORECASE)
            if not match:
                continue

            value = float(match.group(1))
            return max(0.0, 100.0 - value) if match.group(2).lower() == "used" else value
        return None

    def _extract_reset_text(self, lines: list[str]) -> str | None:
        """Extract the reset text from nearby CLI output lines."""
        for line in lines:
            if "reset" in line.lower():
                return line
        return None

    def _parse_cli_reset(self, text: str | None) -> datetime | None:
        """Parse relative or absolute reset text from Claude CLI output."""
        if not text:
            return None

        cleaned = self._normalize_reset_text(text)
        if not cleaned:
            return None

        if relative := self._parse_relative_reset(cleaned):
            return relative

        return self._parse_absolute_reset(cleaned)

    def _normalize_reset_text(self, text: str) -> str:
        """Normalize reset text to the portion after the last reset marker."""
        cleaned = self._strip_terminal_sequences(text)
        cleaned = re.sub(r"\s+\d{1,3}(?:\.\d+)?%\s*(?:used|left)\s*$", "", cleaned, flags=re.I)
        matches = list(re.finditer(r"resets?", cleaned, flags=re.I))
        if matches:
            cleaned = cleaned[matches[-1].end() :]
        cleaned = re.sub(r"^\s*in\s+", "", cleaned, flags=re.I)
        return cleaned.strip(" ·:-\t")

    def _parse_relative_reset(self, text: str) -> datetime | None:
        """Parse reset durations like `2h 15m` or `30m`."""
        total = timedelta()
        found = False
        duration_pattern = r"(\d+)\s*(d|day|days|h|hr|hour|hours|m|min|minute|minutes)"
        for value, unit in re.findall(duration_pattern, text, re.I):
            found = True
            amount = int(value)
            unit = unit.lower()
            if unit.startswith("d"):
                total += timedelta(days=amount)
            elif unit.startswith("h"):
                total += timedelta(hours=amount)
            else:
                total += timedelta(minutes=amount)

        if not found or total.total_seconds() <= 0:
            return None
        return datetime.now(timezone.utc) + total

    def _parse_absolute_reset(self, text: str) -> datetime | None:
        """Parse absolute reset times from Claude CLI output."""
        tz = datetime.now().astimezone().tzinfo or timezone.utc
        if tz_match := re.search(r"\(([^)]+)\)\s*$", text):
            try:
                tz = ZoneInfo(tz_match.group(1).strip())
            except ZoneInfoNotFoundError:
                pass
            text = text[: tz_match.start()].strip()

        text = re.sub(r"\s+at\s+", ", ", text, flags=re.I)
        text = re.sub(r"(?i)(\d)(am|pm)\b", lambda m: f"{m.group(1)}{m.group(2).upper()}", text)

        formats = (
            "%b %d, %Y, %I:%M%p",
            "%b %d, %Y, %I%p",
            "%b %d, %Y",
            "%b %d, %I:%M%p",
            "%b %d, %I%p",
            "%I:%M%p",
            "%I%p",
            "%b %d",
        )
        now = datetime.now(tz)

        for fmt in formats:
            try:
                parsed = datetime.strptime(text, fmt)
            except ValueError:
                continue

            has_year = "%Y" in fmt
            has_month = "%b" in fmt
            has_time = "%I" in fmt

            if has_year:
                candidate = parsed.replace(tzinfo=tz)
            elif has_month:
                candidate = parsed.replace(year=now.year, tzinfo=tz)
                if not has_time:
                    candidate = candidate.replace(hour=0, minute=0, second=0, microsecond=0)
                if candidate <= now:
                    candidate = candidate.replace(year=now.year + 1)
            else:
                candidate = now.replace(
                    hour=parsed.hour,
                    minute=parsed.minute,
                    second=0,
                    microsecond=0,
                )
                if candidate <= now:
                    candidate += timedelta(days=1)

            return candidate.astimezone(timezone.utc)

        return None

    def _extract_cli_error(self, output: str) -> str | None:
        """Return a concise CLI error if output indicates failure."""
        lower = output.lower()
        if "not logged in" in lower or "please log in" in lower or "authentication_error" in lower:
            return "Claude CLI is not authenticated. Run `claude` to log in."
        if "do you trust" in lower or "is this a project you created or one you trust" in lower:
            return "Claude CLI needs this workspace trusted before /usage can run."
        if "update required" in lower or "please update" in lower:
            return "Claude CLI update required."
        if "/usage is only available for subscription plans" in lower:
            return "Claude /usage is only available for subscription plans."
        is_rate_limited = "rate limited" in lower or "rate limit exceeded" in lower
        if is_rate_limited and "rate limits are" not in lower:
            return "Claude CLI rate limited."
        return None

    def _strip_terminal_sequences(self, output: str) -> str:
        """Remove ANSI escape/control sequences from terminal output."""
        output = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", output)
        output = output.replace("\r", "\n")
        return "\n".join(line.rstrip() for line in output.splitlines())

    def _parse_response(self, data: dict, window: WindowPeriod) -> ProviderResult:
        """
        Parse the API response defensively.

        Expected response format:
        {
            "five_hour": {"utilization": 61.0, "resets_at": "2026-01-28T07:59:59..."},
            "seven_day": {"utilization": 22.0, "resets_at": "2026-02-03T09:59:59..."},
            "extra_usage": {"is_enabled": false, ...}
        }

        utilization is a percentage (0-100) of quota used.
        """
        # Select the appropriate window based on the requested period
        window_key = "seven_day" if window == WindowPeriod.DAY_7 else "five_hour"
        window_data = data.get(window_key, {})

        if not window_data:
            # Fallback to seven_day if specific window not available
            window_data = data.get("seven_day", {})

        # Parse reset time if available
        reset_at = None
        if resets_at := window_data.get("resets_at"):
            try:
                reset_at = datetime.fromisoformat(resets_at.replace("Z", "+00:00"))
            except (ValueError, AttributeError):
                pass

        # Parse utilization percentage (0-100)
        utilization = window_data.get("utilization")
        remaining_percent = None
        if utilization is not None:
            remaining_percent = 100.0 - utilization

        metrics = UsageMetrics(
            remaining=remaining_percent,  # Store as percentage remaining
            limit=100.0,  # Total quota is 100%
            reset_at=reset_at,
            # Claude doesn't provide these in OAuth endpoint
            cost=None,
            requests=None,
            input_tokens=None,
            output_tokens=None,
        )

        return ProviderResult(
            provider=self.name,
            window=window,
            metrics=metrics,
            raw=data,
        )
