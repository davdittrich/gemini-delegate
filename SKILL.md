---
name: gemini-delegate
description: Delegate coding tasks to Gemini CLI via ACP (Agent Client Protocol) for prototyping, debugging, code review, and web search. Structured JSON output with tool calls, thoughts, and plans. Permission-controlled file access. Multi-turn sessions. Proactive safety net before expensive operations.
metadata:
  short-description: Delegate to Gemini CLI (ACP)
---

# Gemini Delegate (ACP)

Use Gemini CLI as a collaborator via the Agent Client Protocol. Claude owns the final result and must verify changes locally.

## Prerequisites

Install the ACP SDK (one-time):
```bash
pip install --user -r ~/.agents/skills/gemini-delegate/requirements.txt
```

Requires Gemini CLI >= 0.36.0 with `--acp` support.

## Core rules
- Gemini is a collaborator; you own the final result.
- Always use the bridge script (`scripts/gemini_bridge.py`).
- **Use `--prompt-stdin`** for all automated calls to prevent deadlock and temp-file collisions.
- **Use `--output-file AUTO`** when result persistence is needed (produces unique `0600` file).
- Capture `SESSION_ID` from output and reuse it for follow-ups.
- Default timeout: set Bash tool `timeout_ms` to **600000 (10 minutes)**.

## Quick start

Shell quoting is no longer a concern. Prompts are transmitted via JSON-RPC, never as shell arguments.

```bash
echo "Review src/auth.py around login() and propose fixes." | \
  python3 ~/.agents/skills/gemini-delegate/scripts/gemini_bridge.py \
  --cd "." --prompt-stdin --output-file AUTO
```

**Output:** JSON with `success`, `SESSION_ID`, `agent_messages`, `tool_calls`, `stop_reason`, and `AUTO_OUTPUT_FILE`.

## Multi-turn sessions

```bash
# Start a session
echo "Analyze the bug in foo(). Keep it short." | \
  python3 ~/.agents/skills/gemini-delegate/scripts/gemini_bridge.py \
  --cd "." --prompt-stdin

# Continue the same session (use SESSION_ID from previous output)
echo "Now propose a minimal fix." | \
  python3 ~/.agents/skills/gemini-delegate/scripts/gemini_bridge.py \
  --cd "." --session-id "<SESSION_ID>" --prompt-stdin
```

Sessions are auto-persisted per project using hashed identifiers in `~/.cache/gemini-bridge/sessions/`.

## Concurrency & Isolation

When running multiple independent agents in the same project, isolate their session states using `GEMINI_BRIDGE_SESSIONS_DIR`:

```bash
export GEMINI_BRIDGE_SESSIONS_DIR="/tmp/agent-alpha-cache"
python3 gemini_bridge.py --cd "." --prompt "..."
```

Precedence: **Flag > Environment Variable > Default Path**.

## Progress-Aware Timeouts (Heartbeat)

The bridge monitors Gemini's activity to handle reasoning model latency:

- **Connect (60s)**: Handshake and session loading.
- **Initial Idle (300s)**: Time allowed for Gemini to start its first response chunk.
- **Subsequent Idle (120s)**: Max time allowed between response chunks.
- **Total (600s)**: Absolute hard cap.

For massive codebases or complex web searches, increase `--first-chunk-timeout` and `--timeout`.

## CLI flags

| Flag | Description | Default |
|---|---|---|
| `--prompt` | Prompt text | |
| `--prompt-file` | Read prompt from file | |
| `--prompt-stdin` | Read prompt from stdin (Mandatory for automation) | |
| `--session-id` | Resume a specific session | |
| `--new-session` | Force fresh session | |
| `--sessions-dir` | Override session storage directory | |
| `--cd` | Workspace root directory (required) | |
| `--timeout` | Total max wall-clock seconds | 600 |
| `--idle-timeout` | Max seconds between chunks | 120 |
| `--first-chunk-timeout` | Max seconds for first chunk | 300 |
| `--verbose` | Print heartbeat markers to stderr | |
| `--output-file` | Write JSON to file (or `AUTO` for unique temp) | |
| `--approve-edits` | Allow Gemini to write files within `--cd` scope | |

## Output format

```json
{
  "success": true,
  "SESSION_ID": "abc-123",
  "agent_messages": "Gemini's response text",
  "tool_calls": [{"id": "tc-001", "title": "Read file", "type": "read_file", "status": "completed", "path": "src/auth.py"}],
  "thoughts": "Agent reasoning (if emitted)",
  "stop_reason": "end_turn",
  "AUTO_OUTPUT_FILE": "/tmp/gemini_bridge_res_xyz.json",
  "error": null
}
```

## Permission control

By default, Gemini can **read** files within `--cd` scope but **cannot write**. The `--approve-edits` flag enables scoped writes. Terminal execution is always blocked.

## Collaboration State Capsule
Keep this short block updated near the end of your reply while collaborating:

```text
[Gemini Collaboration Capsule]
Goal:
Gemini SESSION_ID:
Files/lines handed off:
Tool calls: (from tool_calls output)
Last ask:
Gemini summary:
Next ask:
```

## References
- `assets/prompt-template.md` (prompt patterns)
