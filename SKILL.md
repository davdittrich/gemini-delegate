---
name: gemini-delegate
description: Use when analyzing large codebases (>200 lines), performing web searches for current info, or needing adversarial plan reviews.
metadata:
  triggers: web search, adversarial review, code analysis, plan critique, current info, large file analysis
---

# Gemini Delegate (ACP)

Delegate tasks to Gemini CLI via ACP bridge. Claude owns final result; verify locally.

## Core Mandates
- **Use Bridge**: NEVER call `gemini` directly. ALWAYS use `scripts/gemini_bridge.py`.
- **Async Safety**: Use `--prompt-stdin`. Prevent deadlocks.
- **Persistence**: Use `--output-file AUTO` for unique result files.
- **Sessions**: Capture `SESSION_ID` from JSON; reuse for follow-ups.
- **Timeout**: Set Bash `timeout_ms` to 600000 (10m).

## Web Search (Grounding)
- **Prefix**: Queries MUST start with `WebSearch:` (e.g., `WebSearch: Python 3.13 features`). Forces web grounding.
- **Fallback**: If bridge fails (crash/timeout), use built-in `WebSearch` tool. Report in one line.

## Quick Start
```bash
echo "WebSearch: Kubernetes networking 2026" | \
  python3 scripts/gemini_bridge.py --prompt-stdin --output-file AUTO
```

## Task Routing
| Task | Model | Template |
|---|---|---|
| Review (<200 lines) | `gemini-3-flash-preview` | Focused review |
| Bug / Arch (>3 files) | `gemini-3.1-pro-preview` | Tool-assisted |
| Web Search / Info | `gemini-3-flash-preview` | Web search |
| Plan Critique / Algorithm | `gemini-3.1-pro-preview` | Adversarial |

## Delegation Criteria
- **Use for**: >200 lines unseen code, web searches, parallel reviews, adversarial critique.
- **Avoid**: <30s reasoning, needs conversation context, file <50 lines, security-sensitive.

## CLI Flags
| Flag | Description | Default |
|---|---|---|
| `--prompt` | Prompt text | |
| `--prompt-file` | Read prompt from file | |
| `--prompt-stdin` | Read from stdin (mandatory for automation) | |
| `--session-id` | Resume specific session | |
| `--new-session` | Force fresh session | |
| `--sessions-dir` | Override session storage dir | |
| `--cd` | Workspace root directory | `.` |
| `--timeout` | Total max wall-clock seconds | 600 |
| `--idle-timeout` | Max seconds between chunks | 120 |
| `--first-chunk-timeout` | Max seconds for first chunk | 300 |
| `--verbose` | Print heartbeat markers to stderr | |
| `--output-file` | Write JSON to file (`AUTO` for unique temp) | |
| `--approve-edits` | Allow file writes within `--cd` scope | |
| `--cache` | Enable result caching. Opt-in. | off |
| `--cache-ttl` | Cache TTL in seconds (1-2592000) | 86400 |
| `--clear-cache` | Clear cache and exit | |
| `--parallel-models` | Comma-separated models for parallel runs | |
| `--log-feedback` | Append feedback entry | |
| `--model` | Gemini model to use | |

## Output Format
```json
{
  "success": true,
  "SESSION_ID": "abc-123",
  "agent_messages": "Gemini's response",
  "tool_calls": [...],
  "stop_reason": "end_turn",
  "token_estimate": {
    "input_tokens": 1250,
    "output_tokens": 340,
    "model": "gemini-3-flash-preview",
    "estimated_cost_usd": 0.000391
  }
}
```

## Permission Control
- **Default**: Read-only within `--cd` scope. Terminal blocked.
- **Write**: Use `--approve-edits` to allow scoped file writes.

## Feedback Log
- **Mandate**: Log outcome after verification via `--log-feedback`.
- **Format**: `"VERDICT|TASK_TYPE|EST_TOKENS|NOTE"`
- **Escalation**: If rejection rate >50%, escalate model tier.

## Red Flags (STOP)
- Call `gemini` directly → Use bridge.
- Search without `WebSearch:` prefix → Grounding fails.
- Ignore bridge errors → Use fallback.

## References
- `assets/prompt-template.md`: Templates for each task.
- `assets/reference.md`: Feedback logs, parallel runs, capsules.
