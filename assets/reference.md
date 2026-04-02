# Gemini Delegate — Reference

Detailed usage patterns, session management, and operational procedures. The core rules live in `SKILL.md`; this file covers everything else.

## Multi-turn sessions

```bash
# Start a session
echo "Analyze the bug in foo(). Keep it short." | \
  python3 ~/.agents/skills/gemini-delegate/scripts/gemini_bridge.py \
  --cd "." --prompt-stdin

# Continue (use SESSION_ID from previous output)
echo "Now propose a minimal fix." | \
  python3 ~/.agents/skills/gemini-delegate/scripts/gemini_bridge.py \
  --cd "." --session-id "<SESSION_ID>" --prompt-stdin
```

Sessions are auto-persisted per project in `~/.cache/gemini-bridge/sessions/`.

## Concurrency & isolation

Isolate session states for multiple independent agents:

```bash
export GEMINI_BRIDGE_SESSIONS_DIR="/tmp/agent-alpha-cache"
python3 gemini_bridge.py --cd "." --prompt "..."
```

Precedence: **Flag > Environment Variable > Default Path**.

## Progress-aware timeouts (heartbeat)

| Phase | Timeout | Purpose |
|---|---|---|
| Connect | 60s | Handshake and session loading |
| Initial Idle | 300s | First response chunk (reasoning warmup) |
| Subsequent Idle | 120s | Between response chunks |
| Total | 600s | Absolute hard cap |

For massive codebases or complex web searches, increase `--first-chunk-timeout` and `--timeout`.

## Parallel / cross-model review

Run the same prompt against two models simultaneously for adversarial review:

```bash
echo "Review src/auth.py for security issues." | \
  python3 ~/.agents/skills/gemini-delegate/scripts/gemini_bridge.py \
  --cd "." --prompt-stdin \
  --parallel-models "gemini-3-flash-preview,gemini-3.1-pro-preview"
```

Output is a JSON array of results, one per model. Compare verdicts — disagreements indicate areas needing human review.

## Delegation feedback log

Record delegation outcomes to build institutional memory.

**Location**: `.gemini-bridge/feedback.log` in the project root (add `.gemini-bridge/` to `.gitignore`).

**Format** (one line per delegation, pipe-delimited):
```
YYYY-MM-DD HH:MM | MODEL                | TASK_TYPE    | VERDICT  | EST_TOK | NOTE
```

**CLI shortcut**:
```bash
python3 gemini_bridge.py --cd "." --model gemini-3-flash-preview \
  --log-feedback "accepted|diff-review|1.2k|clean, no issues found"
```

**Examples**:
```
2026-04-02 14:30 | gemini-3-flash-preview | diff-review  | accepted | 1.2k   | clean, no issues found
2026-04-02 15:10 | gemini-3.1-pro-preview | arch-review  | partial  | 8.4k   | hallucinated a nonexistent API method
2026-04-02 16:45 | gemini-3.1-pro-preview | debug        | rejected | 12k    | completely wrong diagnosis
```

**Verdicts**: `accepted` (used as-is), `partial` (useful but needed correction), `rejected` (wrong, discarded).

## Collaboration state capsule

Keep this block updated near the end of your reply while collaborating:

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
