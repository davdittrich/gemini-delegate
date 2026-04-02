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

## Pre-delegation checklist (mandatory)

Before EVERY delegation call, verify:

1. **Estimate input size**: Count lines of context you intend to send in the prompt.
   - If >200 lines of code: DO NOT paste into prompt. Instead, send file paths and let Gemini read them via ACP (`read_text_file`).
   - If >500 lines: Consider whether this task should be split into smaller delegations.

2. **Check staleness**: Has the target file changed since the last Gemini analysis?
   - Run `git diff --stat <file>` or check modification time.
   - If unchanged AND a prior Gemini result exists for the same task type: reuse the prior result. Do not re-delegate.
   - **If using `--cache`**: The cache auto-invalidates on commits and uncommitted edits (dirty working tree). However, if you have staged changes that you then unstage, the cache may not detect this. When in doubt, omit `--cache` during active editing. Cache is most reliable in CI/review workflows.

3. **Scope the output**: Every prompt MUST include:
   - An explicit output format (JSON preferred for machine-readable results).
   - An explicit length constraint ("Keep under 200 words" or "JSON only, no prose").
   - At least one `DO NOT` clause to prevent scope creep (e.g., "DO NOT suggest unrelated refactors").

4. **Select model**: Choose the cheapest sufficient model (see Model Routing table below).
   - Always pass `--model <model-id>` explicitly. Never rely on Gemini CLI defaults.

## Gemini model routing

| Task type | Model flag | Rationale |
|---|---|---|
| File reads, grep, structure exploration | `--model gemini-2.5-flash` | No reasoning needed |
| Code review (single file, <200 lines changed) | `--model gemini-2.5-flash` | Sufficient quality |
| Code review (multi-file, architectural) | `--model gemini-2.5-pro` | Needs cross-file reasoning |
| Bug diagnosis (>3 files involved) | `--model gemini-2.5-pro` | Complex reasoning |
| Web search / current info | `--model gemini-2.5-flash` | Search quality is model-independent |
| Novel algorithm design | `--model gemini-2.5-pro` | Needs deep reasoning |
| Generate a patch (single file) | `--model gemini-2.5-flash` | Mechanical transformation |
| Adversarial plan review | `--model gemini-2.5-pro` | Needs nuanced judgment |

Always pass `--model` explicitly. Never rely on Gemini CLI defaults.

## When to delegate (fitness criteria)

Delegate to Gemini when ALL of these hold:
- Task involves reading >200 lines of code that Claude hasn't already read in this conversation.
- Task is self-contained (Gemini doesn't need Claude's conversation context).
- Task output is verifiable (code review verdict, search results, structured analysis).

**High-value delegations** (Gemini ROI is highest):
- Web searches (Gemini has real-time access via Google Search).
- Large file analysis (>200 lines) for specific patterns or bugs.
- Parallel independent reviews (launch 2-3 concurrent sessions).
- Second-opinion adversarial review (cross-model verification).
- Tedious enumeration (list all API endpoints, find all TODOs, count error paths).

## When NOT to delegate

Do NOT delegate when ANY of these hold:
- Task requires <30 seconds of Claude's reasoning. The delegation overhead (prompt construction + ACP round-trip + verification) exceeds the cost of doing it directly.
- Task requires Claude's conversation context. Gemini starts with zero context about the user's goals, prior discussion, or constraints. Summarizing this into a prompt often loses critical nuance.
- Target file is <50 lines. Reading it yourself is faster than delegating.
- You need to edit the file immediately after. Read-then-edit is one Claude tool call; delegate-then-read-then-edit is three steps.
- Task is security-sensitive. Claude must own the reasoning chain for auth, crypto, or permission logic.
- The same analysis was done in this session. Check your conversation context before delegating.

## Template selection guide

| Situation | Template | Model | Est. tokens |
|---|---|---|---|
| Quick diff review (<100 lines changed) | Focused diff review | `gemini-2.5-flash` | 500-2k |
| Multi-file review (need cross-file reasoning) | Tool-assisted review | `gemini-2.5-pro` | 2k-8k |
| Architecture analysis | Analysis / Plan | `gemini-2.5-pro` | 3k-10k |
| Current information lookup | Web search | `gemini-2.5-flash` | 1k-3k |
| Adversarial plan critique | Plan review | `gemini-2.5-pro` | 2k-5k |
| Generate a patch | Patch | `gemini-2.5-flash` | 1k-4k |

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
| `--cd` | Workspace root directory | `.` |
| `--timeout` | Total max wall-clock seconds | 600 |
| `--idle-timeout` | Max seconds between chunks | 120 |
| `--first-chunk-timeout` | Max seconds for first chunk | 300 |
| `--verbose` | Print heartbeat markers to stderr | |
| `--output-file` | Write JSON to file (or `AUTO` for unique temp) | |
| `--approve-edits` | Allow Gemini to write files within `--cd` scope | |
| `--cache` | Enable result caching (skip Gemini on cache hit). Opt-in. | off |
| `--cache-ttl` | Cache TTL in seconds (1-2592000) | 86400 |
| `--clear-cache` | Clear cache and exit (no prompt required) | |
| `--parallel-models` | Comma-separated models for parallel runs | |
| `--log-feedback` | Append feedback entry (VERDICT\|TASK_TYPE\|EST_TOKENS\|NOTE) | |

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
    "model": "gemini-2.5-flash",
    "estimated_cost_usd": 0.000391,
    "note": "Estimate only. Actual billing may differ."
  }
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

## Parallel / Cross-model review

For highest-quality adversarial review, run the same prompt against two models simultaneously:

```bash
echo "Review src/auth.py for security issues." | \
  python3 ~/.agents/skills/gemini-delegate/scripts/gemini_bridge.py \
  --cd "." --prompt-stdin \
  --parallel-models "gemini-2.5-flash,gemini-2.5-pro"
```

Output is a JSON array of results, one per model. Compare verdicts -- disagreements indicate areas needing human review.

## Delegation feedback log

After verifying Gemini's output, record the outcome. This builds institutional memory about what works.

**Location**: `.gemini-bridge/feedback.log` in the project root (add `.gemini-bridge/` to `.gitignore`).

**Format** (one line per delegation, pipe-delimited):
```
YYYY-MM-DD HH:MM | MODEL  | TASK_TYPE    | VERDICT  | EST_TOK | NOTE
```

**CLI shortcut** (write entry via bridge):
```bash
python3 gemini_bridge.py --cd "." --model flash \
  --log-feedback "accepted|diff-review|1.2k|clean, no issues found"
```

**Examples**:
```
2026-04-02 14:30 | flash  | diff-review  | accepted | 1.2k   | clean, no issues found
2026-04-02 15:10 | pro    | arch-review  | partial  | 8.4k   | good findings but hallucinated a nonexistent API method
2026-04-02 16:00 | flash  | web-search   | accepted | 2.1k   | found current pricing info
2026-04-02 16:45 | pro    | debug        | rejected | 12k    | completely wrong diagnosis, wasted tokens
```

**Verdicts**: `accepted` (used as-is), `partial` (useful but needed correction), `rejected` (wrong, discarded).

**How to use the log**:
- Before delegating, scan the last 10 entries for this task type.
- If rejection rate >50% for a task type + model combo, escalate to the next model tier.
- If a specific failure pattern repeats (e.g., "hallucinated API"), add a constraint to the prompt: "Verify all API/function names exist in the codebase before referencing them."

**Claude's responsibility**: Write the log entry immediately after verification. Do not batch. Do not skip.

## References
- `assets/prompt-template.md` (prompt patterns)
