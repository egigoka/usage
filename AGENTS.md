
Focus: rules for usage and testing of features.

Manual smoke checks
- usage show
- usage show --provider claude
- usage show --provider codex --window 5h
- usage show --provider copilot
- usage show --json
- usage doctor
- usage env
- usage tui
- usage login --provider copilot
- usage login --provider claude

Testing rules
- Run usage show and usage tui for UI/CLI changes.

Versioning rules
- ALWAYS read VERSIONLOG.md before making version changes or creating releases
- Update VERSIONLOG.md with changes whenever bumping versions
- usage-tui uses standard semantic versioning (0.1.0, 0.1.1, etc.)

Commit rules
- Before committing, run the secret-scanner skill to check for env files and secrets, then follow the git-workflow skill.
