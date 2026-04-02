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
- **Multi-turn sessions** -- Session IDs auto-persisted per project. Expired sessions fall back gracefully.
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

No additional configuration needed. Session state is stored at `~/.cache/gemini-bridge/sessions.json`.

## Usage

### Simple prompt

```bash
python3 ~/.agents/skills/gemini-delegate/scripts/gemini_bridge.py \
  --cd "." --prompt "Review src/auth.py around login() and propose fixes."
```

### Prompt from file (recommended for complex prompts)

```bash
python3 ~/.agents/skills/gemini-delegate/scripts/gemini_bridge.py \
  --cd "." --prompt-file /tmp/review_prompt.txt
```

### Multi-turn session

```bash
# First turn
python3 ~/.agents/skills/gemini-delegate/scripts/gemini_bridge.py \
  --cd "." --prompt "Analyze the bug in foo()."

# Continue (use SESSION_ID from previous output)
python3 ~/.agents/skills/gemini-delegate/scripts/gemini_bridge.py \
  --cd "." --session-id "<SESSION_ID>" --prompt "Propose a fix."
```

### Allow file writes

```bash
python3 ~/.agents/skills/gemini-delegate/scripts/gemini_bridge.py \
  --cd "." --prompt "Fix the typo in README.md" --approve-edits
```

### Extract structured JSON from response

```bash
python3 ~/.agents/skills/gemini-delegate/scripts/gemini_bridge.py \
  --cd "." --prompt-file /tmp/review.txt --parse-json
```

## CLI Flags

| Flag | Description |
|---|---|
| `--prompt` / `--PROMPT` | Prompt text |
| `--prompt-file` | Read prompt from file (mutually exclusive with `--prompt`) |
| `--session-id` / `--SESSION_ID` | Resume a specific session |
| `--new-session` | Force fresh session (mutually exclusive with `--session-id`) |
| `--cd` | Workspace root directory (required) |
| `--sandbox` | Run Gemini in sandbox mode |
| `--model` | Override the Gemini model |
| `--timeout` | Max seconds (default: 300) |
| `--parse-json` | Extract JSON from response text |
| `--output-file` | Write result JSON to file |
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
  "parsed_json": {}
}
```

### stop_reason values

| Value | Source | Meaning |
|---|---|---|
| `end_turn` | SDK | Normal completion |
| `max_tokens` | SDK | Token limit reached -- continue session |
| `max_turn_requests` | SDK | Turn limit reached -- continue session |
| `cancelled` | SDK | Cancelled by client |
| `refusal` | SDK | Gemini refused the request |
| `timeout` | Bridge | `--timeout` exceeded |
| `crash` | Bridge | Gemini process exited unexpectedly |
| `error` | Bridge | ACP protocol or unexpected error |

## Permission Model

| Default | `--approve-edits` |
|---|---|
| Read files within `--cd` | Read + write files within `--cd` |
| Deny all writes | Allow scoped writes |
| Block terminal execution | Block terminal execution |

Path containment uses `Path.resolve()` + `is_relative_to()`. Symlink traversal is prevented.

## Prompt Templates

See `assets/prompt-template.md` for 8 ready-to-use templates:

- Analysis / Plan
- Patch (Unified Diff)
- Review (audit a diff)
- Tool-assisted review (ACP -- Gemini reads files directly)
- Pre-action audit (benchmark completeness)
- Fix regression-safety check
- Web search
- Plan review (adversarial, structured JSON)

## Running Tests

```bash
cd ~/.agents/skills/gemini-delegate
python3 -m pytest scripts/test_acp.py -v
```

Unit tests (T1-T10) run with mocked ACP -- no Gemini connection needed. Integration tests (T11-T12) require a live Gemini CLI and are gated:

```bash
GEMINI_INTEGRATION_TEST=1 python3 -m pytest scripts/test_acp.py -v
```

## Architecture

The bridge implements the ACP `Client` protocol with 12 methods, communicating with `gemini --acp` over JSON-RPC 2.0 on stdio:

- 3 core methods: `read_text_file`, `write_text_file`, `request_permission`
- 5 terminal stubs (all reject -- defense in depth)
- 2 extension stubs (`ext_method` rejects, `ext_notification` ignores)
- `session_update` (accumulates streaming chunks)
- `on_connect` (stores connection reference)

Session lifecycle: `spawn_agent_process` (async context manager) -> `initialize` -> `new_session` / `load_session` -> `prompt` -> streaming `session_update` notifications -> `PromptResponse`.

## License

Apache 2.0. See [LICENSE](LICENSE).
