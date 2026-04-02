# gemini-delegate

[![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-%E2%89%A53.10-blue.svg)](https://python.org)

A Claude Code skill that delegates coding tasks to Google's Gemini CLI via the [Agent Client Protocol (ACP)](https://agentclientprotocol.com/) -- structured JSON-RPC communication instead of shell subprocess piping.

## What This Does

Claude Code sends prompts to Gemini CLI over a typed JSON-RPC channel. Gemini's responses come back as structured data -- tool calls, reasoning, and plans as JSON fields, not raw text to parse. File access is permission-controlled and path-scoped.

## Features

- **ACP transport** -- JSON-RPC 2.0 over stdio via `gemini --acp`. No shell quoting hazards.
- **Structured output** -- `tool_calls`, `thoughts`, `plan`, `stop_reason` as typed JSON fields
- **Permission control** -- Read-only by default. Writes require explicit `--approve-edits` flag, scoped to workspace root.
- **Path containment** -- All file operations restricted to `--cd` directory. Traversal attempts rejected.
- **Terminal blocked** -- Gemini cannot execute shell commands through the bridge.
- **Multi-turn sessions** -- Session IDs auto-persisted per project using hashed identifiers.
- **Concurrency & Isolation** -- Shredded session storage and explicit isolation flags for multi-agent workflows.
- **Crash recovery** -- Process crashes produce valid JSON output (`stop_reason: "crash"`), never raw tracebacks.
- **Timeout + cancellation** -- Graceful `session/cancel` with hard-kill fallback.

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
# On systems with writable site-packages (venv, conda): pip install -r requirements.txt

# Copy to Claude Code skills directory
cp -r gemini-delegate ~/.agents/skills/gemini-delegate
```

Or install directly:
```bash
mkdir -p ~/.agents/skills/gemini-delegate
cd ~/.agents/skills/gemini-delegate
# Download SKILL.md, requirements.txt, scripts/, assets/ from this repo
pip install --user -r requirements.txt
```

## Configuration

The bridge inherits your shell environment. Ensure your Gemini API key is configured:

```bash
# Verify Gemini CLI works
gemini --version   # Should be >= 0.36.0
gemini -p "Hello"  # Should get a response
```

No additional configuration needed. Session state is stored in `~/.cache/gemini-bridge/sessions/` (XDG compliant).

## Usage

### Simple prompt

```bash
python3 ~/.agents/skills/gemini-delegate/scripts/gemini_bridge.py \
  --cd "." --prompt "Review src/auth.py around login() and propose fixes."
```

### Automation & Concurrency (Recommended)
Use `--prompt-stdin` and `--output-file AUTO` to avoid temporary file collisions:

```bash
echo "Review the architecture of src/" | \
  python3 ~/.agents/skills/gemini-delegate/scripts/gemini_bridge.py \
  --cd "." --prompt-stdin --output-file AUTO
```

**Note**: `AUTO` generates a unique, private (`0600`) JSON file in `/tmp`.

### Multi-turn session

```bash
# First turn
python3 ~/.agents/skills/gemini-delegate/scripts/gemini_bridge.py \
  --cd "." --prompt "Analyze the bug in foo()."

# Continue (use SESSION_ID from previous output)
python3 ~/.agents/skills/gemini-delegate/scripts/gemini_bridge.py \
  --cd "." --session-id "<SESSION_ID>" --prompt "Propose a fix."
```

### Isolation for Multiple Agents
When running independent agents in the same project, isolate their states:

```bash
export GEMINI_BRIDGE_SESSIONS_DIR="/tmp/agent-alpha-cache"
python3 gemini_bridge.py --cd "." --prompt "..."
```

## CLI Flags

| Flag | Description |
|---|---|
| `--prompt` | Prompt text |
| `--prompt-file` | Read prompt from file |
| `--prompt-stdin` | Read prompt from stdin (Preferred for automation) |
| `--session-id` | Resume a specific session |
| `--new-session` | Force fresh session |
| `--sessions-dir` | Override session storage directory |
| `--cd` | Workspace root directory (required) |
| `--sandbox` | Run Gemini in sandbox mode |
| `--model` | Override the Gemini model |
| `--timeout` | Max seconds (default: 300) |
| `--parse-json` | Extract JSON from response text |
| `--output-file` | Write result JSON to file (or `AUTO` for unique temp file) |
| `--return-all-messages` | Include raw ACP events in output |
| `--approve-edits` | Allow scoped file writes |

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
  "plan": [{"content": "Read the file", "status": "completed"}],
  "error": null,
  "parsed_json": {},
  "AUTO_OUTPUT_FILE": "/tmp/gemini_bridge_res_xyz.json"
}
```

## Permission Model

| Default | `--approve-edits` |
|---|---|
| Read files within `--cd` | Read + write files within `--cd` |
| Deny all writes | Allow scoped writes |
| Block terminal execution | Block terminal execution |

Path containment uses `Path.resolve()` + `is_relative_to()`. Symlink traversal is prevented.

## Running Tests

```bash
cd ~/.agents/skills/gemini-delegate
python3 scripts/test_acp.py
```

## Architecture

The bridge implements the ACP `Client` protocol with 12 methods, communicating with `gemini --acp` over JSON-RPC 2.0 on stdio.

- **Shredded Sessions**: Uses hashed project paths to isolate session persistence.
- **Atomic Updates**: Uses `os.replace` + `tempfile` for thread-safe state persistence.
- **Secure Defaults**: Enforces `0o700` on cache directories and `0o600` on temporary results.

## License

Apache 2.0. See [LICENSE](LICENSE).
