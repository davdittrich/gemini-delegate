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

For prompts with heavy special characters, use `--prompt-file`:
```bash
python3 ~/.agents/skills/gemini-delegate/scripts/gemini_bridge.py \
  --cd "." --prompt-file /tmp/review_prompt.txt
```

**Output:** JSON with `success`, `SESSION_ID`, `agent_messages`, `tool_calls`, `stop_reason`, and optional `error` / `thoughts` / `plan`.

## Multi-turn sessions

```bash
# Start a session
python3 ~/.agents/skills/gemini-delegate/scripts/gemini_bridge.py \
  --cd "." --prompt "Analyze the bug in foo(). Keep it short."

# Continue the same session (use SESSION_ID from previous output)
python3 ~/.agents/skills/gemini-delegate/scripts/gemini_bridge.py \
  --cd "." --session-id "<SESSION_ID>" --prompt "Now propose a minimal fix as Unified Diff Patch ONLY."
```

Sessions are auto-persisted per project directory. Expired sessions automatically fall back to a fresh session.

## CLI flags

| Flag | Description |
|---|---|
| `--prompt` / `--PROMPT` | Prompt text (both accepted) |
| `--prompt-file` | Read prompt from file (mutually exclusive with `--prompt`) |
| `--session-id` / `--SESSION_ID` | Resume a specific session |
| `--new-session` | Force fresh session (mutually exclusive with `--session-id`) |
| `--cd` | Workspace root directory (required) |
| `--sandbox` | Run Gemini in sandbox mode |
| `--model` | Override the Gemini model |
| `--timeout` | Max seconds (default: 300) |
| `--parse-json` | Extract JSON from `agent_messages` |
| `--output-file` | Write result JSON to file |
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
  "parsed_json": {}
}
```

### Key fields

- **`tool_calls`**: What Gemini read/wrote. Update the Collaboration State Capsule with paths. Flag `write_file` type for human review.
- **`stop_reason`**: `end_turn` (normal), `max_tokens`/`max_turn_requests` (continue session), `refusal` (rephrase), `timeout`/`crash`/`error` (bridge-synthesized failures).
- **`thoughts`**: Gemini's reasoning — include in the Capsule's "Gemini summary" if present.
- **`all_messages`**: Raw ACP `SessionNotification` objects (when `--return-all-messages` is set).

## Permission control

By default, Gemini can **read** files within `--cd` scope but **cannot write**. The `--approve-edits` flag enables scoped writes.

**When to pass `--approve-edits`:**
- **NEVER** for review, analysis, or audit tasks
- **NEVER** by default
- **ONLY** when the human user has explicitly requested Gemini to make file changes

**Path containment**: All file operations are restricted to the `--cd` workspace root. Paths outside scope are rejected. Terminal execution is always blocked.

## Prompting patterns

Use `assets/prompt-template.md` as a starter.

### 1) Ask Gemini to open files itself
Provide entry file(s) and approximate line numbers, objective and constraints, output format (diff vs analysis). Avoid pasting large code blocks.

### 2) Enforce safe output for code changes
Append to prompts: `OUTPUT: Unified Diff Patch ONLY. Strictly prohibit any actual modifications.`

### 3) Use Gemini for what it's good at
- Alternative solution paths and edge cases
- UI/UX and readability feedback
- Review of a proposed patch (risk spotting, missing tests)
- Search the web (Gemini has built-in Google Search)

## Proactive collaboration triggers

Gemini adds the most value as a pre-action safety net — catching gaps before expensive operations run. A 30-second Gemini check prevents 30-minute re-runs.

### Before running expensive benchmarks

```bash
python3 ~/.agents/skills/gemini-delegate/scripts/gemini_bridge.py \
  --cd "." --prompt "Read benchmarks/run_benchmarks.R and the benchmark script at /tmp/regression_head2head.R. List every estimator group present in run_benchmarks.R. Then check which groups are MISSING from the regression script. Also: does the script have any early quit()/stop() calls? OUTPUT: bullet list of missing groups and structural issues."
```

### Before proposing a code fix

```bash
python3 ~/.agents/skills/gemini-delegate/scripts/gemini_bridge.py \
  --cd "." --prompt "Read src/robust_core.h lines 76-145. I propose to [describe change]. The original bug was: [describe previous bug]. Question: does the proposed fix re-introduce that bug? OUTPUT: yes/no verdict with evidence from the source."
```

### Before modifying an existing script

```bash
python3 ~/.agents/skills/gemini-delegate/scripts/gemini_bridge.py \
  --cd "." --prompt "Read [script path]. Map the control flow: where are quit()/stop() calls? What functions are defined and where are they scoped? If I append code at the end, will it execute? OUTPUT: control flow summary with line numbers."
```

### As a cross-model adversarial reviewer

In the metaswarm plan-review-gate, one reviewer can be Gemini instead of a Claude subagent. Use the "Plan review (adversarial)" template from `assets/prompt-template.md` and pass `--parse-json`:

```bash
python3 ~/.agents/skills/gemini-delegate/scripts/gemini_bridge.py \
  --cd "." --prompt-file /tmp/review_brief.txt --parse-json --timeout 120
```

### Web search delegation

Gemini has built-in Google Search. Delegate current-information queries:

```bash
python3 ~/.agents/skills/gemini-delegate/scripts/gemini_bridge.py \
  --cd "." --prompt "Search the web for the latest version of package X. Cite sources with URLs. OUTPUT: bullet list."
```

### Background Gemini queries

For expensive queries, run in the background via Claude's Agent tool:

```python
Agent(
  description="Gemini codebase review",
  prompt="Run: python3 ~/.agents/skills/gemini-delegate/scripts/gemini_bridge.py "
         "--cd /path/to/repo --prompt 'Analyze architecture of src/'",
  run_in_background=True,
  model="haiku"
)
```

Model routing: use Tier 1 (haiku) for launching bridge invocations — the model selection happens inside Gemini, not in the calling Claude agent.

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
