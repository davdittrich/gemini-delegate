---
name: gemini-delegate
description: Delegate coding tasks to Gemini CLI via ACP (Agent Client Protocol) for prototyping, debugging, code review, and web search. Structured JSON output with tool calls, thoughts, and plans. Permission-controlled file access. Multi-turn sessions. Proactive safety net before expensive operations.
metadata:
  short-description: Delegate to Gemini CLI (ACP)
---

# Gemini Delegate (ACP)

Use Gemini CLI as a collaborator via the Agent Client Protocol. Claude owns the final result and must verify changes locally.

## Prerequisites

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

```bash
echo "Review src/auth.py around login() and propose fixes." | \
  python3 ~/.agents/skills/gemini-delegate/scripts/gemini_bridge.py \
  --cd "." --prompt-stdin --output-file AUTO
```

Output: JSON with `success`, `SESSION_ID`, `agent_messages`, `tool_calls`, `stop_reason`, `token_estimate`.

## Pre-delegation checklist (mandatory)

Before EVERY delegation:

1. **Input size**: >200 lines → send file paths, not content. >500 lines → split the task.
2. **Staleness**: `git diff --stat <file>`. Unchanged + prior result exists → reuse, don't re-delegate. With `--cache`: auto-invalidates on commits and dirty tree; omit during active editing.
3. **Scope output**: Every prompt MUST include an output format (JSON preferred), a length constraint, and at least one `DO NOT` clause.
4. **Select model**: Always pass `--model` explicitly (see routing table below).

## Task routing

| Task | Template | Model | Est. tokens |
|---|---|---|---|
| Diff review (<100 lines changed) | Focused diff review | `gemini-3-flash-preview` | 500-2k |
| Code review (single file, <200 lines) | Review | `gemini-3-flash-preview` | 1k-3k |
| Multi-file / architectural review | Tool-assisted review | `gemini-3.1-pro-preview` | 2k-8k |
| Architecture analysis | Analysis / Plan |  `gemini-3.1-pro-preview` | 3k-10k |
| Bug diagnosis (>3 files) | Tool-assisted review |  `gemini-3.1-pro-preview` | 2k-8k |
| Web search / current info | Web search |  `gemini-3-flash-preview` | 1k-3k |
| Generate a patch (single file) | Patch |  `gemini-3-flash-preview` | 1k-4k |
| Adversarial plan critique | Plan review |  `gemini-3.1-pro-preview` | 2k-5k |
| Novel algorithm design | Analysis / Plan |  `gemini-3.1-pro-preview` | 3k-10k |
| File reads, grep, enumeration | Analysis / Plan |  `gemini-3-flash-preview` | 500-2k |

## When to delegate

ALL must hold: task reads >200 lines Claude hasn't seen, task is self-contained, output is verifiable.

**High-value**: web searches, large file analysis, parallel reviews, adversarial second opinions, tedious enumeration.

## When NOT to delegate

ANY disqualifies: <30s of Claude reasoning, needs conversation context, file <50 lines, need to edit immediately after, security-sensitive, already done this session.

## Feedback log

After verifying output, log the outcome via `--log-feedback "VERDICT|TASK_TYPE|EST_TOKENS|NOTE"`. Before delegating, scan last 10 entries for this task type — if rejection rate >50%, escalate model tier. Log immediately after verification; do not batch or skip. See `assets/reference.md` for format details.

## CLI flags

| Flag | Description | Default |
|---|---|---|
| `--prompt` | Prompt text | |
| `--prompt-file` | Read prompt from file | |
| `--prompt-stdin` | Read prompt from stdin (mandatory for automation) | |
| `--session-id` | Resume a specific session | |
| `--new-session` | Force fresh session | |
| `--sessions-dir` | Override session storage directory | |
| `--cd` | Workspace root directory | `.` |
| `--timeout` | Total max wall-clock seconds | 600 |
| `--idle-timeout` | Max seconds between chunks | 120 |
| `--first-chunk-timeout` | Max seconds for first chunk | 300 |
| `--verbose` | Print heartbeat markers to stderr | |
| `--output-file` | Write JSON to file (`AUTO` for unique temp) | |
| `--approve-edits` | Allow Gemini to write files within `--cd` scope | |
| `--cache` | Enable result caching. Opt-in. | off |
| `--cache-ttl` | Cache TTL in seconds (1-2592000) | 86400 |
| `--clear-cache` | Clear cache and exit | |
| `--parallel-models` | Comma-separated models for parallel runs | |
| `--log-feedback` | Append feedback entry (VERDICT\|TASK_TYPE\|EST_TOKENS\|NOTE) | |
| `--model` | Gemini model to use | |

## Output format

```json
{
  "success": true,
  "SESSION_ID": "abc-123",
  "agent_messages": "Gemini's response text",
  "tool_calls": [...],
  "stop_reason": "end_turn",
  "token_estimate": {
    "input_tokens": 1250,
    "output_tokens": 340,
    "model": "gemini-3-flash-preview",
    "estimated_cost_usd": 0.000391,
    "note": "Estimate only. Actual billing may differ."
  }
}
```

## Permission control

By default, Gemini can **read** files within `--cd` scope but **cannot write**. `--approve-edits` enables scoped writes. Terminal execution is always blocked.

## References
- `assets/prompt-template.md` — prompt templates for each task type
- `assets/reference.md` — sessions, concurrency, timeouts, feedback log format, parallel review, collaboration capsule
