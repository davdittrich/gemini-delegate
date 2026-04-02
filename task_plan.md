# Implementation Plan: Gemini Delegate Cost & Quality Improvements (B-I)

**Date**: 2026-04-02 | **Revision**: 4 (post-review-gate iteration 3 — final)
**Scope**: 8 improvements to SKILL.md, scripts/gemini_bridge.py, and assets/prompt-template.md
**Goal**: Reduce delegation cost by 50-80% and improve output quality through model routing, context pruning, caching, cost visibility, and feedback loops.

### Revision 2 Changelog

Addresses 11 blocking issues from 8 independent reviewers (5 design + 3 adversarial):
- **B1→fixed**: Cache key now includes model name + `git diff --stat` hash for dirty tree detection
- **B2→fixed**: Eliminated `global CACHE_MAX_AGE_SECONDS`; TTL passed as parameter
- **B3→fixed**: Dropped `--no-cache`; `--cache` is opt-in only
- **B4→fixed**: H.1 rewritten as single complete function with `try/finally` cleanup
- **B5→fixed**: `--cd` defaults to `Path(".")` instead of `required=True`
- **B6→fixed**: Feedback log input sanitized (newline/CR stripping)
- **B7→fixed**: Cache dir creation uses atomic `os.makedirs(mode=0o700)` + `chmod`
- **B8→fixed**: Model routing table now has explicit content in new SKILL.md section
- **B9→fixed**: prompt_group validation consolidated into single canonical expression
- **B10→fixed**: Test import block shows complete merged statement
- **B11→fixed**: H.6 test rewritten to test actual bridge code via `_parse_args()`
- **Non-blocking**: Dead code removed, `_cache_dir` simplified, feedback log includes EST_TOKENS, file permissions on feedback.log, `--cache-ttl` validation, parallel cap at 5, tests co-located with implementation phases, C.3 anchor by heading not line number

**Revision 3** addresses 3 remaining blockers from iteration 2 (8 reviewers):
- **A→fixed**: D.4 validation now emits JSON error + `return` instead of broken `_parse_args()` re-invocation
- **B→fixed**: E.4 tests removed local imports; use merged top-level import block from D.6
- **C→fixed**: H.1 uses `asyncio.create_task()` (not bare coroutines), `finally` cancels pending tasks; `model` param in `_write_feedback` now sanitized via `_sanitize_log_field`

**Revision 4** addresses 2 remaining items from iteration 3 (8 reviewers):
- `--clear-cache` output now includes `"success": true` for JSON contract consistency
- `_MAX_PARALLEL_MODELS` and `_sanitize_log_field` added to D.6 merged import block; local import removed from H.6 `test_max_parallel_cap`

---

## Current State Inventory

| File | Lines | Purpose |
|---|---|---|
| `SKILL.md` | 131 | Skill definition loaded by Claude Code; rules, quick start, CLI docs |
| `scripts/gemini_bridge.py` | 655 | ACP transport client; session mgmt, heartbeat watchdog, CLI |
| `scripts/test_acp.py` | 141 | Unit tests (heartbeat, session isolation, output file) |
| `assets/prompt-template.md` | 143 | 7 reusable prompt templates for different delegation types |
| `requirements.txt` | 1 line | `agent-client-protocol==0.9.0` |

**Key constraint**: The bridge communicates via ACP JSON-RPC. Gemini can read files within `--cd` scope via `read_text_file`. The bridge currently has no caching, no token reporting, and no parallel execution. SKILL.md has no model routing guidance and no delegation fitness criteria.

---

## Improvement B: Pre-Delegation Cost Gate

**What**: Add mandatory checklist to SKILL.md that Claude must follow before every delegation. Prevents waste by catching oversized prompts, redundant re-analysis, and missing scope constraints.

**Why**: Currently nothing stops Claude from pasting a 2000-line file into a prompt when Gemini could read it via ACP, or re-analyzing unchanged files.

### B.1: Add section to SKILL.md

**File**: `SKILL.md`
**Location**: Insert new `## Pre-delegation checklist` section after the existing `## Core rules` section (after line 27).

**Exact content to insert**:

```markdown
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
```

### B.2: No code changes required

This is purely a SKILL.md documentation change that governs Claude's behavior.

### B.3: Add model routing table to SKILL.md

**File**: `SKILL.md`
**Location**: Insert immediately after the `## Pre-delegation checklist` section (from B.1), before `## When to delegate` (from F):

**Exact content to insert**:

```markdown
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
```

### B.4: Test

- Verify the section renders correctly in the skill loader.
- Manual test: invoke the skill and confirm Claude follows the checklist (evidenced by model flag usage and output scoping in prompts).

---

## Improvement C: Pointer-Based Prompt Templates

**What**: Rewrite `assets/prompt-template.md` templates to send file pointers instead of pasted content. Add `DO NOT` scope constraints to every template.

**Why**: Pasting file contents into prompts is the #1 token waste. Gemini already has ACP `read_text_file` capability -- it can pull files itself. Additionally, unconstrained output (essays instead of JSON) wastes Gemini's output tokens AND Claude's verification tokens.

### C.1: Rewrite "Review" template

**File**: `assets/prompt-template.md`
**Action**: Replace the existing `## Review (audit an existing diff)` section (lines 37-49) with:

```markdown
## Review (audit an existing diff)

```
Task:
- Review the following unified diff for correctness, edge cases, and missing tests.

Constraints:
- Analyze ONLY the changed lines and their immediate context.
- DO NOT suggest style, naming, or formatting improvements.
- DO NOT restate the code. Reference by file:line.
- Keep response under 300 words.

Input diff:
<paste `git diff` output -- hunks only, not full files>

Output:
- JSON: {"verdict": "ok"|"issues", "blocking": [{"file": "...", "line": N, "issue": "..."}], "minor": [...]}
```
```

### C.2: Rewrite "Tool-assisted review" template

**File**: `assets/prompt-template.md`
**Action**: Replace the existing `## Tool-assisted review` section (lines 51-68) with:

```markdown
## Tool-assisted review (ACP -- Gemini reads files directly)

```
Task:
- Review the implementation of <feature/function> for correctness and edge cases.

Files to read (use your file reading capability):
- <file path>:<start_line>-<end_line>  (primary focus)
- <related file>:<start_line>-<end_line>  (integration point, if any)

Context:
- <1-2 sentence description of what this code does and what changed>

Constraints:
- Read the files yourself using the workspace. Do not ask for code to be pasted.
- Check: off-by-one errors, null/empty handling, error paths, thread safety.
- Cross-reference any claims in comments/docs against the actual code.
- DO NOT suggest unrelated refactors or style improvements.
- DO NOT restate code back. Reference by file:line.

Output:
- JSON: {"verdict": "ok"|"issues", "blocking": [...], "minor": [...]}
```
```

### C.3: Rewrite "Analysis / Plan" template

**File**: `assets/prompt-template.md`
**Action**: Replace the `## Analysis / Plan (no code changes)` section (lines 2-18, from the `##` heading through the closing code fence) with:

```markdown
## Analysis / Plan (no code changes)

```
Task:
- <what to analyze>

Files to read:
- <file path>:<approximate line range>
- <file path>:<approximate line range>

Constraints:
- Read the files yourself. I am not pasting content.
- Keep it concise and actionable. Under 400 words.
- Reference files/lines, do not paste large snippets.
- DO NOT propose code changes unless explicitly asked.

Output:
- Bullet list of findings and a proposed plan.
```
```

### C.4: Rewrite "Plan review" template

**File**: `assets/prompt-template.md`
**Action**: Replace lines 126-143 (the last template) with:

```markdown
## Plan review (adversarial, structured JSON)

```
Task:
- Review the following implementation plan as an adversarial reviewer.
  Find ways it could fail, miss edge cases, or produce incorrect results.

Plan:
<paste plan text -- this is the ONE case where pasting is acceptable, since plans are not files>

Constraints:
- Check all technical claims against actual source files (read them yourself).
- Find at least 3 potential failure modes.
- For each issue: state mechanism, evidence, and severity.
- DO NOT suggest improvements beyond the scope of the plan.
- Keep total response under 500 words.

Output:
- JSON: {"verdict": "PASS"|"FAIL", "blocking_issues": [{"issue": "...", "evidence": "...", "severity": "blocking"}], "non_blocking": [...]}
```
```

### C.5: Leave "Patch" and "Web search" templates as-is

- **Patch template**: Already sends minimal context (task + pointers). No change needed.
- **Web search template**: No files involved. No change needed.
- **Fix regression-safety check**: Already concise. No change needed.

### C.6: Test

- Verify all templates render correctly (no broken markdown fences).
- Manual test: delegate a review using the new pointer-based template and confirm Gemini reads files via ACP instead of needing pasted content.

---

## Improvement D: Result Caching

**What**: Add a content-addressed cache to `gemini_bridge.py` that stores Gemini's JSON responses keyed by `(prompt_hash, git_HEAD)`. Cache hits skip the Gemini call entirely.

**Why**: During iterative development, the same files get reviewed multiple times. If the file hasn't changed (same git HEAD) and the prompt is identical, the result is deterministic. Paying Gemini again is pure waste.

### D.1: Add cache module constants and helpers

**File**: `scripts/gemini_bridge.py`
**Location**: After the existing `DEFAULT_SESSIONS_DIR` definition (line 110), add a new section:

```python
# ---------------------------------------------------------------------------
# Result Caching
# ---------------------------------------------------------------------------
import subprocess as _subprocess  # Add to top-level imports section

DEFAULT_CACHE_DIR = Path(os.getenv("XDG_CACHE_HOME", Path.home() / ".cache")) / "gemini-bridge" / "result-cache"
DEFAULT_CACHE_TTL = 86400  # 24 hours


def _ensure_dir(path: Path, mode: int = 0o700) -> Path:
    """Create directory with correct permissions, handling umask race."""
    os.makedirs(path, mode=mode, exist_ok=True)
    os.chmod(path, mode)  # Override umask
    return path


def _cache_key(prompt: str, cwd: str, model: str) -> str:
    """Content-addressed key: hash of (git HEAD + dirty state + model + prompt).

    Auto-invalidates when:
    - The prompt changes (different task)
    - Any commit is made (git HEAD changes)
    - Any tracked file is modified without committing (dirty tree)
    - A different model is requested (Flash vs Pro produce different results)
    """
    try:
        head = _subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=cwd, text=True, stderr=_subprocess.DEVNULL
        ).strip()
    except (_subprocess.CalledProcessError, FileNotFoundError):
        head = "no-git"
    # Include dirty-tree signal so uncommitted edits bust the cache
    try:
        dirty = _subprocess.check_output(
            ["git", "diff", "--stat"],
            cwd=cwd, text=True, stderr=_subprocess.DEVNULL
        ).strip()
    except (_subprocess.CalledProcessError, FileNotFoundError):
        dirty = ""
    dirty_hash = hashlib.sha256(dirty.encode("utf-8")).hexdigest()[:8] if dirty else "clean"
    composite = f"{head}\n{dirty_hash}\n{model}\n{prompt}"
    return hashlib.sha256(composite.encode("utf-8")).hexdigest()[:32]


def _cache_lookup(cache_dir: Path, key: str, cache_ttl: int) -> Optional[Dict[str, Any]]:
    """Return cached result if it exists and is not expired.

    Args:
        cache_ttl: Max age in seconds. Passed as parameter to avoid global state.
    """
    cache_file = cache_dir / f"{key}.json"
    if not cache_file.exists():
        return None
    # Wall-clock age check (timezone-immune)
    file_age = time.time() - cache_file.stat().st_mtime
    if file_age > cache_ttl:
        cache_file.unlink(missing_ok=True)
        return None
    try:
        data = json.loads(cache_file.read_text(encoding="utf-8"))
        data["_cache_hit"] = True
        return data
    except (json.JSONDecodeError, OSError):
        return None


def _cache_store(cache_dir: Path, key: str, result: Dict[str, Any]) -> None:
    """Store result in cache. Atomic write with 0600 permissions.

    Non-fatal: logs to stderr and continues on failure (e.g., disk full).
    """
    try:
        _ensure_dir(cache_dir)
        cache_file = cache_dir / f"{key}.json"
        # Strip internal fields before caching
        to_cache = {k: v for k, v in result.items() if not k.startswith("_")}
        with tempfile.NamedTemporaryFile(
            mode="w", dir=cache_dir, delete=False,
            encoding="utf-8", prefix=".tmp-cache-"
        ) as tf:
            json.dump(to_cache, tf, indent=2)
            temp_name = tf.name
        os.chmod(temp_name, 0o600)
        os.replace(temp_name, cache_file)
    except Exception as e:
        print(f"[bridge] Cache store failed (non-fatal): {e}", file=sys.stderr)
        if 'temp_name' in locals() and os.path.exists(temp_name):
            os.unlink(temp_name)
```

### D.2: Add CLI flags for cache control

**File**: `scripts/gemini_bridge.py`
**Location**: In `_parse_args()` function, add after the `--approve-edits` argument (line 625):

```python
    parser.add_argument("--cache", action="store_true", default=False,
                        help="Enable result caching (skip Gemini if cache hit). "
                             "Opt-in only. Omit for fresh results every time.")
    parser.add_argument("--cache-ttl", type=int, default=86400,
                        help="Cache time-to-live in seconds (default: 86400 = 24h). "
                             "Must be between 1 and 2592000 (30 days).")
```

**Note**: `--no-cache` has been removed. Caching is opt-in via `--cache`. If `--cache` is absent, no caching occurs. This eliminates the ambiguous 4-state problem from the original design.

### D.3: Integrate cache into `_run_acp()`

**File**: `scripts/gemini_bridge.py`
**Location**: At the top of `_run_acp()`, before session resolution (line 378), add cache check:

```python
async def _run_acp(args: argparse.Namespace, prompt_text: str) -> Dict[str, Any]:
    cd: Path = args.cd.resolve()
    project_path = cd.as_posix()

    # --- Cache check (before any ACP work) ---
    cache_d: Optional[Path] = None
    cache_key_str: Optional[str] = None
    if getattr(args, "cache", False):
        cache_ttl = getattr(args, "cache_ttl", DEFAULT_CACHE_TTL)
        if cache_ttl < 1 or cache_ttl > 2592000:
            cache_ttl = DEFAULT_CACHE_TTL
        cache_d = _ensure_dir(DEFAULT_CACHE_DIR)
        model_name = args.model or "default"
        cache_key_str = _cache_key(prompt_text, project_path, model_name)
        cached = _cache_lookup(cache_d, cache_key_str, cache_ttl)
        if cached is not None:
            return cached

    # ... existing session resolution and ACP code (unchanged) ...
```

**Location**: At the end of `_run_acp()`, before `return result` (line 525), add cache store:

```python
    # --- Cache store (only on success, non-fatal on failure) ---
    if cache_d and cache_key_str and result.get("success"):
        _cache_store(cache_d, cache_key_str, result)

    return result
```

### D.4: Add `--clear-cache` utility flag

**File**: `scripts/gemini_bridge.py`
**Location**: In `main()`, before `asyncio.run()`, add:

```python
    if getattr(args, "clear_cache", False):
        import shutil
        cache_path = DEFAULT_CACHE_DIR
        if cache_path.exists():
            count = len(list(cache_path.glob("*.json")))
            shutil.rmtree(cache_path)
            print(json.dumps({"success": True, "cleared": count, "path": str(cache_path)}))
        else:
            print(json.dumps({"success": True, "cleared": 0, "path": str(cache_path)}))
        return
```

And the corresponding CLI flag in `_parse_args()`:

```python
    parser.add_argument("--clear-cache", action="store_true",
                        help="Clear the result cache and exit.")
```

**Implementation detail — Consolidated prompt_group + --cd relaxation (addresses D.4, I.3, and BLOCKER 5+9)**:

Three changes in `_parse_args()`:

1. Relax `prompt_group` from `required=True` to `required=False`:

```python
    prompt_group = parser.add_mutually_exclusive_group(required=False)  # Changed from True
    # ... existing --prompt, --prompt-file, --prompt-stdin args unchanged ...
```

2. Make `--cd` default to current directory instead of `required=True`:

```python
    parser.add_argument("--cd", type=Path, default=Path("."),
                        help="Workspace root directory (default: current directory).")
```

3. Add a **single** canonical validation block in `main()`, immediately after `args = _parse_args()`. This block covers D.4 (`--clear-cache`), I.3 (`--log-feedback`), and the normal prompt requirement. It is shown once and must be implemented exactly as written:

```python
    # --- Canonical argument validation (covers all early-exit flags) ---
    has_prompt = bool(args.prompt or args.prompt_file or args.prompt_stdin)
    has_early_exit = (
        getattr(args, "clear_cache", False)
        or getattr(args, "log_feedback", None)
    )
    if not has_prompt and not has_early_exit:
        print(json.dumps({
            "success": False,
            "error": "A prompt source (--prompt, --prompt-file, or --prompt-stdin) "
                     "is required unless using --clear-cache or --log-feedback.",
        }))
        return
```

**Why a single block**: The original plan split this validation across D.4 and I.3, producing conflicting expressions. An implementer reading only D.4 would miss `log_feedback`. This consolidated version is the only source of truth.

**Why not re-invoke `_parse_args()`**: With `prompt_group` changed to `required=False`, re-calling `_parse_args()` would silently return the same args object without raising an error. The JSON error + `return` pattern is consistent with all other bridge error paths.

### D.5: Update SKILL.md

**File**: `SKILL.md`
**Location**: Add to the CLI flags table:

```markdown
| `--cache` | Enable result caching (skip Gemini on cache hit). Opt-in. | off |
| `--cache-ttl` | Cache TTL in seconds (1-2592000) | 86400 |
| `--clear-cache` | Clear cache and exit (no prompt required) | |
```

### D.6: Tests

**File**: `scripts/test_acp.py`
**Add new test class**:

```python
class TestResultCache(unittest.TestCase):
    """Test content-addressed result caching."""

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self.cache_dir = self.tmpdir / "cache"

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def test_cache_miss_returns_none(self):
        result = _cache_lookup(self.cache_dir, "nonexistent-key", cache_ttl=86400)
        self.assertIsNone(result)

    def test_cache_store_and_hit(self):
        key = "test-key-abc"
        data = {"success": True, "agent_messages": "cached response"}
        _cache_store(self.cache_dir, key, data)
        
        hit = _cache_lookup(self.cache_dir, key, cache_ttl=86400)
        self.assertIsNotNone(hit)
        self.assertTrue(hit["_cache_hit"])
        self.assertEqual(hit["agent_messages"], "cached response")

    def test_cache_expired_returns_none(self):
        key = "expired-key"
        _cache_store(self.cache_dir, key, {"success": True})
        # TTL of 0 means immediately expired
        result = _cache_lookup(self.cache_dir, key, cache_ttl=0)
        self.assertIsNone(result)

    def test_cache_permissions(self):
        key = "perm-test"
        _cache_store(self.cache_dir, key, {"success": True})
        cache_file = self.cache_dir / f"{key}.json"
        self.assertEqual(os.stat(cache_file).st_mode & 0o777, 0o600)

    def test_cache_key_changes_with_prompt(self):
        key1 = _cache_key("prompt A", "/tmp/project", "flash")
        key2 = _cache_key("prompt B", "/tmp/project", "flash")
        self.assertNotEqual(key1, key2)

    def test_cache_key_changes_with_model(self):
        key1 = _cache_key("same prompt", "/tmp/project", "gemini-2.5-flash")
        key2 = _cache_key("same prompt", "/tmp/project", "gemini-2.5-pro")
        self.assertNotEqual(key1, key2)

    def test_cache_key_deterministic(self):
        key1 = _cache_key("same prompt", "/tmp/project", "flash")
        key2 = _cache_key("same prompt", "/tmp/project", "flash")
        self.assertEqual(key1, key2)
```

**Replace the existing import block** at the top of the test file (line 20-24) with this complete merged statement:

```python
from gemini_bridge import (
    BridgeClient, _parse_args, extract_json, _get_session_path,
    _load_session, _save_session, _get_sessions_dir,
    HeartbeatWatchdog, BridgeTimeoutError, TimeoutType,
    _cache_key, _cache_lookup, _cache_store, _ensure_dir,
    DEFAULT_CACHE_DIR, DEFAULT_CACHE_TTL,
    _estimate_tokens, _estimate_cost,
    _write_feedback, _sanitize_log_field,
    _MAX_PARALLEL_MODELS,
)
```

This single import block covers all new symbols from D, E, and I. It replaces the existing import — do not add a second import block.

---

## Improvement E: Token Counting & Cost Reporting

**What**: Add estimated token counts and cost to the bridge's JSON output. This gives Claude and users visibility into what each delegation costs.

**Why**: You can't optimize what you can't measure. Currently delegations are a black box -- no one knows whether a review cost 500 tokens or 50,000.

### E.1: Add token estimation function

**File**: `scripts/gemini_bridge.py`
**Location**: After the cache section, add:

```python
# ---------------------------------------------------------------------------
# Token Estimation
# ---------------------------------------------------------------------------
# Rough heuristic: 1 token ~ 4 characters for English text.
# This is an estimate, not a billing-accurate count.

def _estimate_tokens(text: str) -> int:
    """Rough token count estimate. Not billing-accurate."""
    return max(1, len(text) // 4)


# Pricing per 1M tokens (USD) -- update when prices change.
# Source: https://ai.google.dev/pricing as of 2026-04.
_MODEL_PRICING = {
    "gemini-2.5-flash": {"input": 0.15, "output": 0.60},
    "gemini-2.5-pro":   {"input": 1.25, "output": 10.00},
    # Fallback for unknown models
    "default":          {"input": 1.25, "output": 10.00},
}


def _estimate_cost(input_tokens: int, output_tokens: int, model: str) -> dict:
    """Estimate USD cost based on token counts and model."""
    pricing = _MODEL_PRICING.get(model, _MODEL_PRICING["default"])
    input_cost = (input_tokens / 1_000_000) * pricing["input"]
    output_cost = (output_tokens / 1_000_000) * pricing["output"]
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "model": model or "default",
        "estimated_cost_usd": round(input_cost + output_cost, 6),
        "note": "Estimate only. Actual billing may differ.",
    }
```

### E.2: Integrate into output assembly

**File**: `scripts/gemini_bridge.py`
**Location**: In `_run_acp()`, in the output assembly section (around line 511-525), after `result["tool_calls"] = client._tool_calls`, add:

```python
    # Token estimation
    model_name = args.model or "default"
    input_tokens = _estimate_tokens(prompt_text)
    output_tokens = _estimate_tokens(client._agent_messages)
    result["token_estimate"] = _estimate_cost(input_tokens, output_tokens, model_name)
```

### E.3: Update SKILL.md output format example

**File**: `SKILL.md`
**Location**: Update the JSON output example (lines 99-109) to include:

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

### E.4: Tests

**File**: `scripts/test_acp.py`
**Add**:

```python
class TestTokenEstimation(unittest.TestCase):
    """Uses _estimate_tokens and _estimate_cost from the merged top-level import block (D.6).
    Do NOT add local imports — they are already imported at module level."""

    def test_estimate_tokens_basic(self):
        # ~100 chars -> ~25 tokens
        text = "a" * 100
        self.assertEqual(_estimate_tokens(text), 25)

    def test_estimate_tokens_empty(self):
        self.assertEqual(_estimate_tokens(""), 1)  # min 1

    def test_estimate_cost_flash(self):
        cost = _estimate_cost(1000, 500, "gemini-2.5-flash")
        self.assertIn("estimated_cost_usd", cost)
        self.assertGreater(cost["estimated_cost_usd"], 0)
        # Flash should be cheap
        self.assertLess(cost["estimated_cost_usd"], 0.01)

    def test_estimate_cost_unknown_model_uses_default(self):
        cost = _estimate_cost(1000, 500, "unknown-model-xyz")
        self.assertIn("estimated_cost_usd", cost)
```

---

## Improvement F: Delegation Fitness Criteria

**What**: Add explicit "when to delegate" and "when NOT to delegate" criteria to SKILL.md.

**Why**: The biggest source of waste is delegating tasks that are cheaper for Claude to do itself. The overhead of prompting + waiting + verifying often exceeds the cost of Claude just reading the file.

### F.1: Add section to SKILL.md

**File**: `SKILL.md`
**Location**: Insert after the new `## Pre-delegation checklist` section (from Improvement B):

```markdown
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
```

### F.2: No code changes required

Pure SKILL.md documentation.

---

## Improvement G: Focused Diff Review Template

**What**: Add a new template to `assets/prompt-template.md` optimized for the most common delegation: reviewing a specific code change. Minimizes both input tokens (diff hunks only) and output tokens (JSON only).

**Why**: The existing "Review" template is generic. A purpose-built diff review template can cut tokens 60-80% by sending only changed hunks and demanding only JSON output.

### G.1: Add new template

**File**: `assets/prompt-template.md`
**Location**: Insert as a new section after the existing `## Review` template:

```markdown
## Focused diff review (cheapest review option)

Use this when reviewing a specific git diff. Sends only the changed hunks, not full files.
Claude should generate the diff via `git diff` and paste only the relevant hunks.

```
Task:
- Review ONLY the diff below for bugs, edge cases, and correctness issues.

Diff:
<paste `git diff -- <file>` output>

Constraints:
- Analyze ONLY what changed. Do not review surrounding unchanged code.
- DO NOT suggest style, naming, or refactoring improvements.
- DO NOT restate the diff. Reference changes by +/- line markers.
- If the diff is correct, respond with: {"verdict": "ok", "blocking": [], "minor": []}

Output:
- JSON only. No prose. No explanation unless a blocking issue exists.
- Schema: {"verdict": "ok"|"issues", "blocking": [{"hunk": "+/- line ref", "issue": "..."}], "minor": [...]}
```
```

### G.2: Add corresponding guidance to SKILL.md

**File**: `SKILL.md`
**Location**: Add to a new `## Template selection guide` section after the model routing table:

```markdown
## Template selection guide

| Situation | Template | Model | Est. tokens |
|---|---|---|---|
| Quick diff review (<100 lines changed) | Focused diff review | `gemini-2.5-flash` | 500-2k |
| Multi-file review (need cross-file reasoning) | Tool-assisted review | `gemini-2.5-pro` | 2k-8k |
| Architecture analysis | Analysis / Plan | `gemini-2.5-pro` | 3k-10k |
| Current information lookup | Web search | `gemini-2.5-flash` | 1k-3k |
| Adversarial plan critique | Plan review | `gemini-2.5-pro` | 2k-5k |
| Generate a patch | Patch | `gemini-2.5-flash` | 1k-4k |
```

---

## Improvement H: Parallel Session Support

**What**: Add a `--parallel N` flag to `gemini_bridge.py` that spawns N concurrent ACP connections, each with the same prompt but potentially different models. Returns an array of results.

**Why**: The highest-quality review pattern is cross-model adversarial review (send same code to Flash and Pro, compare results). Currently this requires two sequential shell calls. Parallel execution halves wall-clock time and enables new patterns.

### H.1: Add parallel execution function

**File**: `scripts/gemini_bridge.py`
**Location**: After the existing `_run_acp()` function, add:

```python
# ---------------------------------------------------------------------------
# Parallel Execution
# ---------------------------------------------------------------------------
import copy  # Add to top-level imports

_MAX_PARALLEL_MODELS = 5


async def _run_parallel(args: argparse.Namespace, prompt_text: str) -> Dict[str, Any]:
    """Run N parallel ACP sessions, potentially with different models.

    --parallel-models: comma-separated list of models.
    Example: --parallel-models gemini-2.5-flash,gemini-2.5-pro
    Each model gets its own ACP session with the same prompt.
    Temp session directories are cleaned up in a finally block.
    """
    models = [m.strip() for m in args.parallel_models.split(",")]
    if len(models) > _MAX_PARALLEL_MODELS:
        return {
            "success": False,
            "error": f"Max {_MAX_PARALLEL_MODELS} parallel models allowed, got {len(models)}.",
            "mode": "parallel",
            "results": [],
        }

    temp_dirs: List[Path] = []
    tasks: List[asyncio.Task] = []

    try:
        for i, model in enumerate(models):
            model_args = copy.copy(args)
            model_args.model = model
            model_args.new_session = True  # Force fresh sessions for isolation
            model_args.cache = False  # Disable cache in parallel to avoid racing writes
            # Isolated session dir per model
            model_dir = Path(tempfile.mkdtemp(prefix=f"gemini-parallel-{i}-"))
            temp_dirs.append(model_dir)
            model_args.sessions_dir = model_dir
            # create_task() schedules immediately and returns a cancellable Task
            tasks.append(asyncio.create_task(_run_acp(model_args, prompt_text)))

        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Assemble parallel output
        parallel_results = []
        for model, result in zip(models, results):
            if isinstance(result, Exception):
                parallel_results.append({
                    "model": model,
                    "success": False,
                    "error": str(result),
                })
            else:
                result["model_used"] = model
                parallel_results.append(result)

        return {
            "success": all(r.get("success", False) for r in parallel_results),
            "mode": "parallel",
            "results": parallel_results,
        }
    finally:
        # Cancel any tasks that were created but never gathered (mid-loop exception)
        for t in tasks:
            if not t.done():
                t.cancel()
        # Guaranteed cleanup of all temp session directories
        for d in temp_dirs:
            shutil.rmtree(d, ignore_errors=True)
```

### H.2: Add CLI flags

**File**: `scripts/gemini_bridge.py`
**Location**: In `_parse_args()`:

```python
    parser.add_argument("--parallel-models",
                        help="Comma-separated models for parallel execution (e.g., 'gemini-2.5-flash,gemini-2.5-pro').")
```

### H.3: Integrate into `main()`

**File**: `scripts/gemini_bridge.py`
**Location**: In `main()`, before `result = asyncio.run(...)`:

```python
    if args.parallel_models:
        result = asyncio.run(_run_parallel(args, prompt_text))
    else:
        result = asyncio.run(_run_acp(args, prompt_text))
```

### H.4: Update SKILL.md

**File**: `SKILL.md`
**Location**: Add new section:

```markdown
## Parallel / Cross-model review

For highest-quality adversarial review, run the same prompt against two models simultaneously:

```bash
echo "Review src/auth.py for security issues." | \
  python3 ~/.agents/skills/gemini-delegate/scripts/gemini_bridge.py \
  --cd "." --prompt-stdin \
  --parallel-models "gemini-2.5-flash,gemini-2.5-pro"
```

Output is a JSON array of results, one per model. Compare verdicts -- disagreements indicate areas needing human review.
```

### H.5: Add CLI flag to table

```markdown
| `--parallel-models` | Comma-separated models for parallel runs | |
```

### H.6: Tests

**File**: `scripts/test_acp.py`

```python
class TestParallelExecution(unittest.TestCase):
    """Test parallel execution setup and argument routing."""

    def test_parse_args_accepts_parallel_models(self):
        """Verify argparse accepts --parallel-models flag."""
        sys_argv_backup = sys.argv
        try:
            sys.argv = [
                "gemini_bridge.py",
                "--cd", "/tmp",
                "--prompt", "test",
                "--parallel-models", "gemini-2.5-flash,gemini-2.5-pro",
            ]
            args = _parse_args()
            self.assertEqual(args.parallel_models, "gemini-2.5-flash,gemini-2.5-pro")
        finally:
            sys.argv = sys_argv_backup

    def test_parallel_model_args_isolation(self):
        """Verify each model gets its own args copy with correct .model."""
        import copy
        class FakeArgs:
            parallel_models = "flash,pro"
            model = "original"
            new_session = False
            cache = True
            sessions_dir = None

        args = FakeArgs()
        models = [m.strip() for m in args.parallel_models.split(",")]
        for model in models:
            model_args = copy.copy(args)
            model_args.model = model
            model_args.new_session = True
            model_args.cache = False
            # Verify isolation: original unchanged, copy has new model
            self.assertEqual(model_args.model, model)
            self.assertTrue(model_args.new_session)
            self.assertFalse(model_args.cache)  # Cache disabled in parallel
        self.assertEqual(args.model, "original")  # Original unchanged

    def test_max_parallel_cap(self):
        """Verify _MAX_PARALLEL_MODELS constant exists and is 5.
        Uses top-level import from D.6 merged block — no local import."""
        self.assertEqual(_MAX_PARALLEL_MODELS, 5)
```

### H.7: Concurrency safety note

Each parallel session uses:
- Its own temporary sessions directory (tracked in `temp_dirs` list, cleaned up in `finally`)
- `--new-session` forced (no session reuse across parallel runs)
- `cache = False` forced (avoids cross-model cache key collision)
- No shared mutable state in `BridgeClient` (each gets its own instance)
- No global mutation (TTL passed as parameter, not via `global`)

The only shared resource is the Gemini API rate limit, which is external. If Gemini rate-limits, individual results will contain `success: false` with the error — callers must inspect each result's `success` field.

---

## Improvement I: Feedback Log

**What**: Add a lightweight feedback logging system that records delegation outcomes (accepted/rejected, tokens, model, task type). Stored in `.gemini-bridge/feedback.log` (gitignored).

**Why**: Without a feedback loop, the same mistakes repeat. If Gemini hallucinated an API last time, Claude should know to add "verify all API names exist" to the next prompt. This is the "closed-loop control system" from the lateral thinking analysis.

### I.1: Add feedback log section to SKILL.md

**File**: `SKILL.md`
**Location**: Add as the last section before `## References`:

```markdown
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
```

### I.2: Add `.gemini-bridge/` to `.gitignore`

**File**: `.gitignore`
**Action**: Append:

```
.gemini-bridge/
```

### I.3: Add `--log-feedback` CLI flag (optional automation)

**File**: `scripts/gemini_bridge.py`
**Location**: In `_parse_args()`:

```python
    parser.add_argument("--log-feedback",
                        help="Append a feedback entry. Format: 'VERDICT|TASK_TYPE|EST_TOKENS|NOTE'. "
                             "Example: 'accepted|review|1.2k|clean review'. "
                             "Writes to .gemini-bridge/feedback.log in --cd directory.")
```

**Location**: In `main()`, add early handler:

```python
    if args.log_feedback:
        _write_feedback(args.cd, args.log_feedback, args.model or "unknown")
        return
```

**Helper function** (add after token estimation section):

```python
# ---------------------------------------------------------------------------
# Feedback Logging
# ---------------------------------------------------------------------------
import datetime as _datetime  # Add to top-level imports


def _sanitize_log_field(value: str) -> str:
    """Strip newlines and carriage returns to prevent log injection."""
    return value.replace("\n", " ").replace("\r", " ")


def _write_feedback(cd: Path, feedback_str: str, model: str) -> None:
    """Append a feedback entry to .gemini-bridge/feedback.log.

    Format: VERDICT|TASK_TYPE|EST_TOKENS|NOTE
    All fields are sanitized to prevent newline injection.
    """
    parts = feedback_str.split("|", 3)
    if len(parts) != 4:
        print(json.dumps({
            "success": False,
            "error": "Format: VERDICT|TASK_TYPE|EST_TOKENS|NOTE  "
                     "(e.g., 'accepted|review|1.2k|clean review')"
        }))
        return

    verdict, task_type, est_tokens, note = [_sanitize_log_field(p.strip()) for p in parts]
    model = _sanitize_log_field(model)  # Sanitize caller-supplied model too
    log_dir = _ensure_dir(cd.resolve() / ".gemini-bridge")
    log_file = log_dir / "feedback.log"

    timestamp = _datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    entry = f"{timestamp} | {model:<6} | {task_type:<12} | {verdict:<8} | {est_tokens:<6} | {note}\n"

    # Open with restricted permissions
    fd = os.open(log_file, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        os.write(fd, entry.encode("utf-8"))
    finally:
        os.close(fd)

    print(json.dumps({"success": True, "logged": entry.strip()}))
```

Note: The `--log-feedback` flag needs to work without a prompt. The prompt_group relaxation and `--cd` default change are handled by the **consolidated validation block in D.4** — that single block is the source of truth for all early-exit flag handling. Do not duplicate validation here.

### I.4: Tests

**File**: `scripts/test_acp.py`

```python
class TestFeedbackLog(unittest.TestCase):

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def test_write_feedback_creates_log(self):
        _write_feedback(self.tmpdir, "accepted|review|1.2k|clean review", "flash")
        log_file = self.tmpdir / ".gemini-bridge" / "feedback.log"
        self.assertTrue(log_file.exists())
        content = log_file.read_text()
        self.assertIn("accepted", content)
        self.assertIn("flash", content)
        self.assertIn("1.2k", content)
        self.assertIn("clean review", content)

    def test_write_feedback_appends(self):
        _write_feedback(self.tmpdir, "accepted|review|1k|first", "flash")
        _write_feedback(self.tmpdir, "rejected|debug|8k|second", "pro")
        log_file = self.tmpdir / ".gemini-bridge" / "feedback.log"
        lines = log_file.read_text().strip().split("\n")
        self.assertEqual(len(lines), 2)

    def test_write_feedback_bad_format_3_fields(self):
        # Only 3 fields (missing EST_TOKENS) -- should print error, not crash
        _write_feedback(self.tmpdir, "accepted|review|clean review", "flash")
        log_file = self.tmpdir / ".gemini-bridge" / "feedback.log"
        self.assertFalse(log_file.exists())

    def test_write_feedback_sanitizes_newlines(self):
        """Newlines in note field must be stripped to prevent log injection."""
        _write_feedback(
            self.tmpdir,
            "accepted|review|1k|line1\nINJECTED|fake|0|evil",
            "flash"
        )
        log_file = self.tmpdir / ".gemini-bridge" / "feedback.log"
        lines = log_file.read_text().strip().split("\n")
        # Must be exactly 1 line -- the injected entry must not create a second line
        self.assertEqual(len(lines), 1)
        self.assertNotIn("INJECTED", lines[0].split("|")[1])  # Not in task_type position

    def test_write_feedback_file_permissions(self):
        _write_feedback(self.tmpdir, "accepted|review|1k|test", "flash")
        log_file = self.tmpdir / ".gemini-bridge" / "feedback.log"
        self.assertEqual(os.stat(log_file).st_mode & 0o777, 0o600)

    def test_write_feedback_dir_permissions(self):
        _write_feedback(self.tmpdir, "accepted|review|1k|test", "flash")
        log_dir = self.tmpdir / ".gemini-bridge"
        self.assertEqual(os.stat(log_dir).st_mode & 0o777, 0o700)
```

---

## Dependency Graph

```
B (Pre-delegation checklist + model routing table)  ─── no code deps ───> can implement first
C (Pointer-based templates)   ─── no code deps ───> can implement first
F (Fitness criteria)          ─── no code deps ───> can implement first
G (Focused diff template)     ─── no code deps ───> can implement first
I.1-I.2 (Feedback log docs)  ─── no code deps ───> can implement first

D (Result caching)            ─── modifies gemini_bridge.py ───> implement before H
E (Token reporting)           ─── modifies gemini_bridge.py ───> implement before H
I.3 (Feedback CLI flag)       ─── shares prompt_group relaxation with D.4 ───> implement WITH D

H (Parallel sessions)         ─── depends on D+E being merged first (avoids merge conflicts)
```

**Recommended implementation order** (tests co-located with code, not deferred):

1. **Phase 1** (SKILL.md + templates only, zero code risk): B (including B.3 model routing table), C, F, G, I.1-I.2
2. **Phase 2** (gemini_bridge.py core + their tests): D + D.6, E + E.4, I.3 + I.4. The consolidated prompt_group validation (D.4) must be implemented first since both D and I.3 depend on it.
3. **Phase 3** (gemini_bridge.py advanced + tests): H + H.6

---

## Risk Assessment

| Risk | Severity | Mitigation |
|---|---|---|
| Cache returns stale results after uncommitted edits | **Resolved** | Cache key now includes `git diff --stat` hash. Dirty working tree auto-invalidates cache. Edge case: staged-then-unstaged changes may not be detected; B.1 checklist warns about this. |
| Token estimation is inaccurate (3-3.5 chars/token for code vs 4 for prose) | Low | Labeled as "Estimate only" in output. Systematically undercounts code tokens by ~15-25%. Acceptable for cost awareness, not for billing. |
| Parallel sessions hit Gemini rate limits | Medium | Each result has its own `success` field. Callers must inspect per-result, not just top-level `success`. Rate-limited results will show `success: false`. |
| `prompt_group` relaxation allows invocation without prompt | **Resolved** | Single consolidated validation block in `main()` covers all early-exit flags. |
| `--parallel-models` with many models overwhelms system | **Resolved** | `_MAX_PARALLEL_MODELS = 5` enforced in `_run_parallel` with clear error JSON. |
| Feedback log grows unbounded | Low | Append-only text. 10,000 entries ≈ 500KB. Add `--trim-feedback N` if needed later. |
| Cache write fails (disk full) | Low | `_cache_store` is non-fatal: logs to stderr and continues. Live result still returned. |
| Cross-project cache sharing | **Not possible** | Cache key includes `cwd` (via `git diff --stat` and `git rev-parse HEAD` which are cwd-scoped). Different projects produce different keys. |
| Non-git repos and caching | Low | Falls back to `head="no-git"` + `dirty_hash="clean"`. Cache works but never auto-invalidates on file changes. Acceptable since `--cache` is opt-in. |

---

## File Change Summary

| File | Changes | Lines added (est.) |
|---|---|---|
| `SKILL.md` | B.1 (checklist), B.3 (model routing table), F.1 (fitness criteria), G.2 (template guide), H.4 (parallel docs), I.1 (feedback log), E.3 (output example update) | ~140 |
| `assets/prompt-template.md` | C.1-C.4 (rewrite 4 templates), G.1 (new focused diff template) | ~65 (replacing ~50) |
| `scripts/gemini_bridge.py` | D.1 (cache + _ensure_dir), D.2-D.4 (CLI flags + integration), E.1-E.2 (token estimation), H.1-H.3 (parallel execution), I.3 (feedback logging + sanitization), consolidated prompt_group validation | ~250 |
| `scripts/test_acp.py` | D.6, E.4, H.6, I.4 (merged import block + 4 new test classes) | ~130 |
| `.gitignore` | I.2 | 1 |

**Total estimated**: ~585 new/modified lines across 5 files.

**New top-level imports added to gemini_bridge.py** (add to existing import block at lines 7-21):
```python
import copy
import datetime as _datetime
import subprocess as _subprocess
```

---

## Acceptance Criteria

Each improvement is complete when:

- **B**: SKILL.md contains pre-delegation checklist; manual test shows Claude follows it.
- **C**: All 4 modified templates use pointer-based file references and include DO NOT clauses and JSON output specs.
- **D**: `--cache` flag works; repeated identical prompts return `_cache_hit: true`; different models produce different cache keys; dirty working tree busts cache; `--clear-cache` works without `--cd`; cache write failure is non-fatal; all D.6 tests pass.
- **E**: Every bridge output includes `token_estimate` object with `input_tokens`, `output_tokens`, `model`, and `estimated_cost_usd`. All E.4 tests pass.
- **F**: SKILL.md contains "when to delegate" and "when NOT to delegate" sections.
- **G**: New "Focused diff review" template exists in prompt-template.md; template selection guide exists in SKILL.md.
- **H**: `--parallel-models "flash,pro"` runs two concurrent sessions and returns a results array; temp dirs cleaned up via `finally` even on error; >5 models rejected with clear error; cache disabled in parallel mode; H.6 tests pass.
- **I**: `.gitignore` includes `.gemini-bridge/`; SKILL.md documents the feedback log format with EST_TOKENS column; `--log-feedback` accepts 4-field format (`VERDICT|TASK_TYPE|EST_TOKENS|NOTE`); newlines in fields are sanitized; feedback.log created with 0600 permissions; I.4 tests pass including injection test.
- **All tests pass**: `cd scripts && python -m pytest test_acp.py -v` exits 0.
