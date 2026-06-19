# Version History

## usage-tui Python Package Versions

### v0.1.1 (2026-02-15)
**Fixed**
- Copilot provider now correctly extracts and displays actual credit numbers from API response
- Store actual credit values in `metrics.remaining` and `metrics.limit` instead of percentages

### v0.1.0 (Initial Release)
**Added**
- Multi-provider usage metrics TUI
- Support for Claude, OpenAI, OpenRouter, GitHub Copilot, and Codex
- Interactive TUI with multiple time windows (5h, 7d, 30d)
- JSON output for scripting
- Caching layer to reduce API calls
- OAuth device flow for Copilot authentication

---

## Version Numbering

- **usage-tui**: Uses semantic versioning (0.1.1)
