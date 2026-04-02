# gemini-delegate

[![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-%E2%89%A53.10-blue.svg)](https://python.org)

A Claude Code skill that delegates coding tasks to Google's Gemini CLI via the [Agent Client Protocol (ACP)](https://agentclientprotocol.com/) -- structured JSON-RPC communication instead of shell subprocess piping.

## What This Does

Claude Code sends prompts to Gemini CLI over a typed JSON-RPC channel. Gemini's responses come back as structured data -- tool calls, reasoning, and plans as JSON fields, not raw text to parse. File access is permission-controlled and path-scoped.

## Features

- **ACP transport** -- JSON-RPC 2.0 over stdio via `gemini --acp`. No shell quoting hazards.
- **Progress-Aware Heartbeat** -- Uses a multi-tiered watchdog to handle long-running reasoning tasks without false timeouts.
- **Structured output** -- `tool_calls`, `thoughts`, `plan`, `stop_reason` as typed JSON fields.
- **Permission control** -- Read-only by default. Writes require explicit `--approve-edits` flag, scoped to workspace root.
- **Path containment** -- All file operations restricted to `--cd` directory. Traversal attempts rejected.
- **Multi-turn sessions** -- Session IDs auto-persisted per project using hashed identifiers for total isolation.
- **Concurrency & Isolation** -- Shredded session storage and explicit isolation flags for multi-agent workflows.
- **Crash recovery** -- Process crashes produce valid JSON output (`stop_reason: "crash"`), never raw tracebacks.
- **Secure Defaults** -- Enforces `0o700` on cache directories and `0o600` on temporary results.

## Prerequisites

- **Python >= 3.10**
- **Gemini CLI >= 0.36.0** with `--acp` support ([install](https://github.com/google-gemini/gemini-cli))
- **Gemini API key** configured for the Gemini CLI

## Installation

```bash
# Clone
git clone https://github.com/YOUR_USERNAME/gemini-delegate.git

# Install the ACP SDK
pip install --user -r gemini-delegate/requirements.txt

# Copy to Claude Code skills directory
cp -r gemini-delegate ~/.agents/skills/gemini-delegate
```

## Configuration

The bridge inherits your shell environment. Ensure your Gemini API key is configured:

```bash
gemini --version   # Should be >= 0.36.0
gemini -p "Hello"  # Should get a response
```

Session state is stored in `~/.cache/gemini-bridge/sessions/` (XDG compliant).

## Usage

### Automation & Concurrency (Recommended)
Use `--prompt-stdin` and `--output-file AUTO` to avoid temporary file collisions and deadlocks:

```bash
echo "Review the architecture of src/" | \
  python3 ~/.agents/skills/gemini-delegate/scripts/gemini_bridge.py \
  --cd "." --prompt-stdin --output-file AUTO
```

**Note**: `AUTO` generates a unique, private (`0600`) JSON file in `/tmp`.

### Isolation for Multiple Agents
When running independent agents in the same project, isolate their states:

```bash
export GEMINI_BRIDGE_SESSIONS_DIR="/tmp/agent-alpha-cache"
python3 gemini_bridge.py --cd "." --prompt "..."
```

## Understanding Timeouts (Heartbeat)

The bridge uses a **Heartbeat Watchdog** to handle reasoning model latency:

- **Connect Timeout (60s)**: Strict limit for handshake and session loading.
- **Initial Idle (300s)**: Allows for long initial "reasoning" pauses before the first token.
- **Subsequent Idle (120s)**: Ensures the model keeps talking once it starts.
- **Total Timeout (600s)**: Hard safety cap for the entire operation.

Adjust these via `--first-chunk-timeout`, `--idle-timeout`, and `--timeout`.

## CLI Flags

| Flag | Description | Default |
|---|---|---|
| `--prompt` | Prompt text | |
| `--prompt-file` | Read prompt from file | |
| `--prompt-stdin` | Read prompt from stdin | |
| `--session-id` | Resume a specific session | |
| `--new-session` | Force fresh session | |
| `--sessions-dir` | Override session storage directory | |
| `--cd` | Workspace root directory (required) | |
| `--timeout` | Total max wall-clock seconds | 600 |
| `--idle-timeout` | Max seconds between message chunks | 120 |
| `--first-chunk-timeout` | Max seconds for the first response chunk | 300 |
| `--verbose` | Print heartbeat markers to stderr | |
| `--output-file` | Write JSON to file (or `AUTO` for unique temp) | |
| `--approve-edits` | Allow scoped file writes | |

## Output Format

```json
{
  "success": true,
  "SESSION_ID": "abc-123",
  "agent_messages": "Gemini's response text",
  "tool_calls": [
    {"id": "tc-001", "title": "Read file", "type": "read_file", "status": "completed", "path": "src/auth.py"}
  ],
  "thoughts": "Agent reasoning (if emitted)",
  "stop_reason": "end_turn",
  "AUTO_OUTPUT_FILE": "/tmp/gemini_bridge_res_xyz.json"
}
```

## License

Apache 2.0. See [LICENSE](LICENSE).
