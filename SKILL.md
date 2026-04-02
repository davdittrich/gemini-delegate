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
On systems with a writable site-packages (venv, conda), plain `pip install -r` works.

Requires Gemini CLI >= 0.36.0 with `--acp` support.

## Core rules
- Gemini is a collaborator; you own the final result.
- Always use the bridge script (`scripts/gemini_bridge.py`).
- Prefer file/line references over pasting snippets. Set `--cd` to the repo root.
- For code changes, request **Unified Diff Patch ONLY** and forbid direct file modification (unless `--approve-edits` is set).
- Capture `SESSION_ID` from output and reuse it for follow-ups.
- Keep a short **Collaboration State Capsule** updated while this skill is active.
- Default timeout: set Bash tool `timeout_ms` to **600000 (10 minutes)**.

## Quick start

Shell quoting is no longer a concern. Prompts are transmitted via JSON-RPC, never as shell arguments.

```bash
python3 ~/.agents/skills/gemini-delegate/scripts/gemini_bridge.py \
  --cd "." --prompt "Review src/auth.py around login() and propose fixes."
```

### Automation & Concurrency
For automated callers or concurrent agents, use `--prompt-stdin` to avoid temporary file collisions:

```bash
echo "Review the architecture of src/" | \
  python3 ~/.agents/skills/gemini-delegate/scripts/gemini_bridge.py \
  --cd "." --prompt-stdin --output-file AUTO
```

**Note**: `--output-file AUTO` generates a unique, private (`0600`) JSON file in `/tmp`.

## Multi-turn sessions

```bash
# Start a session
python3 ~/.agents/skills/gemini-delegate/scripts/gemini_bridge.py \
  --cd "." --prompt "Analyze the bug in foo(). Keep it short."

# Continue the same session (use SESSION_ID from previous output)
python3 ~/.agents/skills/gemini-delegate/scripts/gemini_bridge.py \
  --cd "." --session-id "<SESSION_ID>" --prompt "Now propose a minimal fix as Unified Diff Patch ONLY."
```

Sessions are auto-persisted per project directory using hashed identifiers in `~/.cache/gemini-bridge/sessions/`.

## Concurrency & Isolation

When running multiple independent agents in the same project, isolate their session states using `GEMINI_BRIDGE_SESSIONS_DIR`:

```bash
export GEMINI_BRIDGE_SESSIONS_DIR="/tmp/agent-alpha-cache"
python3 ~/.agents/skills/gemini-delegate/scripts/gemini_bridge.py --cd "." --prompt "..."
```

Alternatively, use the `--sessions-dir` flag. Precedence: **Flag > Environment Variable > Default Path**.

## CLI flags

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
| `--parse-json` | Extract JSON from `agent_messages` |
| `--output-file` | Write result JSON to file (or `AUTO` for unique temp file) |
| `--return-all-messages` | Include raw ACP events in output |
| `--approve-edits` | Allow Gemini to write files within `--cd` scope |

## Output format

```json
{
  "success": true,
  "SESSION_ID": "abc-123",
  "agent_messages": "Gemini's response text",
  "tool_calls": [{"id": "tc-001", "title": "Read file", "type": "read_file", "status": "completed", "path": "src/auth.py"}],
  "thoughts": "Agent reasoning (if emitted)",
  "stop_reason": "end_turn",
  "plan": [{"content": "Read the file", "status": "completed"}],
  "error": null,
  "parsed_json": {},
  "AUTO_OUTPUT_FILE": "/tmp/gemini_bridge_res_xyz.json"
}
```

## Permission control

By default, Gemini can **read** files within `--cd` scope but **cannot write**. The `--approve-edits` flag enables scoped writes.

**Path containment**: All file operations are restricted to the `--cd` workspace root. Paths outside scope are rejected. Terminal execution is always blocked.

## Proactive collaboration triggers

Gemini adds the most value as a pre-action safety net — catching gaps before expensive operations run.

### Before running expensive benchmarks

```bash
python3 ~/.agents/skills/gemini-delegate/scripts/gemini_bridge.py \
  --cd "." --prompt "Read benchmarks/run_benchmarks.R and list every estimator group present. Then check which groups are MISSING from the regression script at /tmp/regression_head2head.R."
```

### Web search delegation

Gemini has built-in Google Search. Delegate current-information queries:

```bash
python3 ~/.agents/skills/gemini-delegate/scripts/gemini_bridge.py \
  --cd "." --prompt "Search the web for the latest version of package X. Cite sources with URLs. OUTPUT: bullet list."
```

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
