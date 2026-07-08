# Setup

Fish examples use universal vars so `usage` sees them in future shells.

## Claude Code

Recommended: use Claude CLI credentials.

```fish
npm install -g @anthropics/claude
claude setup-token
usage show --provider claude
```

Optional token override:

```fish
set -Ux CLAUDE_CODE_OAUTH_TOKEN sk-ant-oat01-...
```

## OpenAI Codex Subscription 1

Recommended: use default Codex CLI credentials from `~/.codex/auth.json`.

```fish
npm install -g @openai/codex
codex
usage show --provider codex
```

Optional token override:

```fish
set -Ux CODEX_ACCESS_TOKEN eyJ...
```

## OpenAI Codex Subscription 2

Authenticate second account into separate Codex home, then tell `usage` where it lives.

```fish
env CODEX_HOME=$HOME/.codex-2 codex
set -Ux CODEX_HOME_2 $HOME/.codex-2
usage show --provider codex2
```

Optional token override:

```fish
set -Ux CODEX_ACCESS_TOKEN_2 eyJ...
```

## OpenAI Codex Subscription 3

Authenticate third account into separate Codex home, then tell `usage` where it lives.

```fish
env CODEX_HOME=$HOME/.codex-3 codex
set -Ux CODEX_HOME_3 $HOME/.codex-3
usage show --provider codex3
```

Optional token override:

```fish
set -Ux CODEX_ACCESS_TOKEN_3 eyJ...
```

## Checks

```fish
usage show
usage show --provider codex2
usage show --provider codex3
usage doctor
usage tui
```
