#!/usr/bin/env python3
"""Tests for the ACP Gemini Bridge — unique files, isolation, and heartbeat."""

import asyncio
import io
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# Add parent to path for imports
SCRIPT_DIR = Path(__file__).parent
sys.path.insert(0, str(SCRIPT_DIR))

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
from acp import RequestError


class TestHeartbeatWatchdog(unittest.IsolatedAsyncioTestCase):
    """Test the HeartbeatWatchdog timing logic."""

    async def test_initial_idle_timeout(self):
        # 2s initial limit, very long subsequent/total
        dog = HeartbeatWatchdog(total_limit=100, initial_idle=2, subsequent_idle=100)
        
        with self.assertRaises(BridgeTimeoutError) as cm:
            # We must wrap in wait_for to ensure the test itself doesn't hang 
            # if the watchdog fails to raise
            await asyncio.wait_for(dog.monitor(), timeout=5)
        
        self.assertEqual(cm.exception.timeout_type, TimeoutType.INITIAL_IDLE)

    async def test_subsequent_idle_timeout(self):
        # Long initial, 2s subsequent
        dog = HeartbeatWatchdog(total_limit=100, initial_idle=100, subsequent_idle=2)
        
        # Trigger activity
        dog.activity()
        
        with self.assertRaises(BridgeTimeoutError) as cm:
            await asyncio.wait_for(dog.monitor(), timeout=5)
        
        self.assertEqual(cm.exception.timeout_type, TimeoutType.SUBSEQUENT_IDLE)

    async def test_total_timeout(self):
        # 2s total limit
        dog = HeartbeatWatchdog(total_limit=2, initial_idle=100, subsequent_idle=100)
        
        with self.assertRaises(BridgeTimeoutError) as cm:
            await asyncio.wait_for(dog.monitor(), timeout=5)
        
        self.assertEqual(cm.exception.timeout_type, TimeoutType.TOTAL)

    async def test_reset_prevents_timeout(self):
        # 2s subsequent limit
        dog = HeartbeatWatchdog(total_limit=100, initial_idle=100, subsequent_idle=2)
        dog.activity()
        
        monitor_task = asyncio.create_task(dog.monitor())
        
        # Reset every 1s for 3s
        for _ in range(3):
            await asyncio.sleep(1)
            dog.activity()
            
        # If we got here, it didn't time out yet
        monitor_task.cancel()
        try:
            await monitor_task
        except asyncio.CancelledError:
            pass


class TestSessionIsolation(unittest.TestCase):
    """Test hashed session naming and directory-based isolation."""

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self.sessions_dir = self.tmpdir / "sessions"

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def test_hashed_filename_consistency(self):
        project = "/tmp/test-project"
        path1 = _get_session_path(self.sessions_dir, project)
        path2 = _get_session_path(self.sessions_dir, project)
        self.assertEqual(path1, path2)
        self.assertIn("test-project", path1.name)

    def test_hashed_filename_uniqueness(self):
        path1 = _get_session_path(self.sessions_dir, "/tmp/project-a")
        path2 = _get_session_path(self.sessions_dir, "/tmp/project-b")
        self.assertNotEqual(path1, path2)

    def test_save_and_load_session(self):
        project = str(self.tmpdir / "proj")
        session_path = _get_session_path(self.sessions_dir, project)

        _save_session(session_path, project, "sess-123")

        # Verify permissions
        self.assertEqual(os.stat(session_path).st_mode & 0o777, 0o600)
        self.assertEqual(os.stat(self.sessions_dir).st_mode & 0o777, 0o700)

        # Load back
        loaded = _load_session(session_path, project)
        self.assertEqual(loaded, "sess-123")

    def test_hashed_filename_preserves_digits(self):
        """Digits 1-9 in project names should not be replaced with underscores."""
        path = _get_session_path(self.sessions_dir, "/tmp/project-v2")
        self.assertIn("project-v2", path.name)


class TestBridgeFeatures(unittest.TestCase):
    """Test CLI logic."""

    def test_output_file_auto_logic(self):
        from gemini_bridge import _emit
        class Args:
            cd = "."
            output_file = "AUTO"
            parse_json = False
        
        result = {"success": True, "agent_messages": "hi"}
        
        with patch("sys.stderr", new_callable=io.StringIO) as mock_stderr:
            _emit(result, Args())
            self.assertIn("AUTO_OUTPUT_FILE", result)
            auto_path = Path(result["AUTO_OUTPUT_FILE"])
            self.assertTrue(auto_path.exists())
            auto_path.unlink()


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
        # Backdate the file to guarantee expiry
        cache_file = self.cache_dir / f"{key}.json"
        old_time = time.time() - 100  # 100 seconds in the past
        os.utime(cache_file, (old_time, old_time))
        result = _cache_lookup(self.cache_dir, key, cache_ttl=10)
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

    def test_write_feedback_sanitizes_pipes_in_model(self):
        """Pipe characters in model field must be stripped to prevent column corruption."""
        _write_feedback(self.tmpdir, "accepted|review|1k|test", "flash|injected")
        log_file = self.tmpdir / ".gemini-bridge" / "feedback.log"
        content = log_file.read_text()
        self.assertIn("flash-injected", content)
        self.assertNotIn("flash|injected", content)

    def test_write_feedback_full_model_name_alignment(self):
        """Full model names (e.g., gemini-2.5-flash) should not corrupt column structure."""
        _write_feedback(self.tmpdir, "accepted|review|1k|test", "gemini-2.5-flash")
        log_file = self.tmpdir / ".gemini-bridge" / "feedback.log"
        content = log_file.read_text()
        self.assertIn("gemini-2.5-flash", content)
        self.assertIn("| accepted", content)


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
        """Verify copy.copy produces isolated args per model.
        Note: Tests the isolation mechanism directly rather than through _run_parallel,
        which requires a live ACP connection. See _run_parallel for the actual usage."""
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


if __name__ == "__main__":
    unittest.main()
