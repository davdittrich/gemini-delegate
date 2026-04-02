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
    HeartbeatWatchdog, BridgeTimeoutError, TimeoutType
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


if __name__ == "__main__":
    unittest.main()
