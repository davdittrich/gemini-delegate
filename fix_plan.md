# Fix Plan: Critical Code Review Issues

**Date**: 2026-04-02 | **Scope**: 9 fixes to gemini_bridge.py and test_acp.py
**Source**: Critical code review of commit 50ad05a

---

## Fix 1: Defensive guard for `prompt_text is None`

**File**: `scripts/gemini_bridge.py`
**Location**: After line 956 (`prompt_text = args.prompt`), before the parallel/single dispatch (line 958).
**Issue**: Pyright correctly flags that `prompt_text` can be `None` when `args.prompt` is None. Runtime is safe due to the validation block above, but a defensive guard is cheap insurance.

**Exact change**: Insert after line 956:

```python
    if prompt_text is None:
        print(json.dumps({"success": False, "error": "No prompt text provided."}))
        return
```

**Test**: Existing validation test covers this path. No new test needed.

---

## Fix 2: Strip pipe characters in `_sanitize_log_field`

**File**: `scripts/gemini_bridge.py`
**Location**: `_sanitize_log_field` function (line ~243).
**Issue**: Feedback log is pipe-delimited. `model` field passes through `_sanitize_log_field` but pipes aren't stripped, so `--model "flash|injected"` corrupts column structure.

**Exact change**: Replace:
```python
    return value.replace("\n", " ").replace("\r", " ")
```
With:
```python
    return value.replace("\n", " ").replace("\r", " ").replace("|", "-")
```

**Test**: Add to `TestFeedbackLog`:
```python
    def test_write_feedback_sanitizes_pipes_in_model(self):
        """Pipe characters in model field must be stripped to prevent column corruption."""
        _write_feedback(self.tmpdir, "accepted|review|1k|test", "flash|injected")
        log_file = self.tmpdir / ".gemini-bridge" / "feedback.log"
        content = log_file.read_text()
        # Model field should have pipe replaced with dash
        self.assertIn("flash-injected", content)
        self.assertNotIn("flash|injected", content)
```

---

## Fix 3: Replace `getattr(args, ...)` with direct attribute access

**File**: `scripts/gemini_bridge.py`
**Issue**: `--cache`, `--clear-cache`, `--log-feedback` are all added by `_parse_args()` unconditionally. `getattr` hides typos.

**Locations and exact changes**:

1. Line ~560: `if getattr(args, "cache", False):` → `if args.cache:`
2. Line ~561: `cache_ttl = getattr(args, "cache_ttl", DEFAULT_CACHE_TTL)` → `cache_ttl = args.cache_ttl`
3. Line ~920 (validation block): `getattr(args, "clear_cache", False)` → `args.clear_cache`
4. Line ~921: `getattr(args, "log_feedback", None)` → `args.log_feedback`
5. Line ~930: `if getattr(args, "clear_cache", False):` → `if args.clear_cache:`

**Test**: Existing tests cover all these paths. No new test needed.

---

## Fix 4: Hoist duplicate `model_name` in `_run_acp`

**File**: `scripts/gemini_bridge.py`
**Issue**: `model_name = args.model or "default"` appears at line ~565 (cache check) and line ~708 (token estimation). Same expression, 143 lines apart.

**Exact change**:
1. Move the first `model_name = args.model or "default"` to immediately after `project_path = cd.as_posix()` (line 555), before the cache check.
2. Remove the second `model_name = args.model or "default"` at line ~708.
3. Both the cache block and token estimation block now reference the single `model_name`.

---

## Fix 5: Fix timing-dependent cache expiry test

**File**: `scripts/test_acp.py`
**Location**: `TestResultCache.test_cache_expired_returns_none`
**Issue**: `cache_ttl=0` means `file_age > 0` must be true, but on 1-second granularity filesystems, store+lookup in the same second yields `file_age=0.0`, making `0.0 > 0` False. Test passes by accident.

**Exact change**: Replace:
```python
    def test_cache_expired_returns_none(self):
        key = "expired-key"
        _cache_store(self.cache_dir, key, {"success": True})
        # TTL of 0 means immediately expired
        result = _cache_lookup(self.cache_dir, key, cache_ttl=0)
        self.assertIsNone(result)
```
With:
```python
    def test_cache_expired_returns_none(self):
        key = "expired-key"
        _cache_store(self.cache_dir, key, {"success": True})
        # Backdate the file to guarantee expiry
        cache_file = self.cache_dir / f"{key}.json"
        old_time = time.time() - 100  # 100 seconds in the past
        os.utime(cache_file, (old_time, old_time))
        result = _cache_lookup(self.cache_dir, key, cache_ttl=10)
        self.assertIsNone(result)
```

Note: `time` and `os` are already imported in test_acp.py.

---

## Fix 6: Widen model format field in feedback log

**File**: `scripts/gemini_bridge.py`
**Location**: `_write_feedback` function, the entry f-string (line ~270).
**Issue**: `{model:<6}` is 6 chars but `gemini-2.5-flash` is 16. Columns misalign.

**Exact change**: Replace:
```python
    entry = f"{timestamp} | {model:<6} | {task_type:<12} | {verdict:<8} | {est_tokens:<6} | {note}\n"
```
With:
```python
    entry = f"{timestamp} | {model:<20} | {task_type:<12} | {verdict:<8} | {est_tokens:<6} | {note}\n"
```

**Test**: Add to `TestFeedbackLog`:
```python
    def test_write_feedback_full_model_name_alignment(self):
        """Full model names (e.g., gemini-2.5-flash) should not corrupt column structure."""
        _write_feedback(self.tmpdir, "accepted|review|1k|test", "gemini-2.5-flash")
        log_file = self.tmpdir / ".gemini-bridge" / "feedback.log"
        content = log_file.read_text()
        # The model field should be present and the verdict should still be in its own column
        self.assertIn("gemini-2.5-flash", content)
        self.assertIn("| accepted", content)
```

**No SKILL.md update needed** — examples use abbreviated `flash`/`pro` which render correctly under both widths.

---

## Fix 7: Fix pre-existing regex bug `0-0` → `0-9`

**File**: `scripts/gemini_bridge.py`
**Location**: `_get_session_path` function (line ~138).
**Issue**: `[^a-zA-Z0-0_\-]` has degenerate range `0-0` (matches only `'0'`). Digits 1-9 in project names are incorrectly replaced with underscores.

**Exact change**: Replace:
```python
    safe_basename = re.sub(r"[^a-zA-Z0-0_\-]", "_", basename)
```
With:
```python
    safe_basename = re.sub(r"[^a-zA-Z0-9_\-]", "_", basename)
```

**Test**: Existing `TestSessionIsolation` tests use `/tmp/test-project` and `/tmp/project-a` which don't contain digits 1-9. Add:
```python
    def test_hashed_filename_preserves_digits(self):
        """Digits 1-9 in project names should not be replaced with underscores."""
        path = _get_session_path(self.sessions_dir, "/tmp/project-v2")
        self.assertIn("project-v2", path.name)
```

---

## ~~Fix 8: Guard `_ensure_dir` with existence check~~ — DROPPED

**Reason**: Dropped per plan review. The "optimization" saves one `chmod` syscall per invocation but introduces a permissions-correction regression: if the directory exists with wrong permissions (e.g., 0o755 from a different umask), the guarded version silently skips the correction. No profiling evidence justified the change. The existing `_ensure_dir` call with `exist_ok=True` is effectively free.

---

## Fix 9: Improve `test_parallel_model_args_isolation` (non-blocking, opportunistic)

**File**: `scripts/test_acp.py`
**Issue**: Test hand-rolls `copy.copy` logic instead of testing actual `_run_parallel`. Not blocking since other parallel tests cover real code.

**Change**: Add a docstring note acknowledging the limitation:
```python
    def test_parallel_model_args_isolation(self):
        """Verify copy.copy produces isolated args per model.
        Note: Tests the isolation mechanism directly rather than through _run_parallel,
        which requires a live ACP connection. See _run_parallel for the actual usage."""
```

This is a documentation fix, not a code fix. The real integration test requires mock ACP infrastructure that doesn't exist yet.

---

## Dependency Graph

All fixes are independent — no ordering constraints. Apply in numeric order for clarity.

## Implementation Order

1. Fix 7 (regex bug — pre-existing, standalone)
2. Fixes 2, 6 (feedback log: sanitizer + field width — same function area)
3. Fixes 3, 4 (gemini_bridge.py: getattr, model_name — scattered)
4. Fix 1 (prompt_text guard — main())
5. Fixes 5, 9 (test_acp.py: expiry test, parallel docstring)
6. Run full test suite

Note: Fix 8 dropped per review. 8 fixes remain.

## Acceptance Criteria

- All 28+ existing tests pass
- New pipe sanitization test passes
- New digit preservation test passes
- `python3 -c "import ast; ast.parse(open('scripts/gemini_bridge.py').read())"` succeeds
- `--log-feedback` with pipe in model produces dashes, not pipes, in log
- Full model name (`gemini-2.5-flash`) does not corrupt feedback log column structure
