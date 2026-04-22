#!/usr/bin/env python3
"""Tests for model fallback and registry logic."""

import asyncio
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch, AsyncMock

import sys
# Add parent to path for imports
SCRIPT_DIR = Path(__file__).parent
sys.path.insert(0, str(SCRIPT_DIR))

from gemini_bridge import _run_acp, _estimate_cost
import gemini_bridge

class MockProc:
    def __init__(self):
        self.returncode = None
    def kill(self):
        pass
    async def wait(self):
        return 0

class TestFallback(unittest.IsolatedAsyncioTestCase):

    async def test_fallback_on_404(self):
        """Verify that a 404 ModelNotFoundError triggers a retry without --model."""
        
        # Mock args
        args = MagicMock()
        args.cd = Path(".")
        args.model = "invalid-model"
        args.cache = False
        args.new_session = True
        args.timeout = 10
        args.first_chunk_timeout = 5
        args.idle_timeout = 5
        args.verbose = False
        args.session_id = None
        args.sandbox = False

        # Mock spawn_agent_process
        # First call: raise error or return conn that fails initialize
        # Second call: succeed
        
        mock_conn_fail = AsyncMock()
        from acp import RequestError
        # Simulate 404 error during initialize
        mock_conn_fail.initialize.side_effect = RequestError(404, "Model not found")
        
        mock_conn_ok = AsyncMock()
        mock_conn_ok.initialize.return_value = None
        mock_conn_ok.new_session.return_value = MagicMock(session_id="new-sess")
        mock_conn_ok.prompt.return_value = MagicMock(stop_reason="end_turn")
        
        mock_proc = MockProc()

        call_count = 0
        def side_effect(client, *args_list, **kwargs):
            nonlocal call_count
            call_count += 1
            # Return a context manager
            cm = MagicMock()
            if "invalid-model" in args_list:
                cm.__aenter__.return_value = (mock_conn_fail, mock_proc)
            else:
                cm.__aenter__.return_value = (mock_conn_ok, mock_proc)
            cm.__aexit__.return_value = None
            return cm

        with patch("gemini_bridge.spawn_agent_process", side_effect=side_effect):
            result = await _run_acp(args, "test prompt")
            
            self.assertTrue(result["success"])
            self.assertEqual(call_count, 2)

    async def test_no_fallback_on_other_error(self):
        """Verify that other errors do NOT trigger a retry."""
        args = MagicMock()
        args.cd = Path(".")
        args.model = "some-model"
        args.cache = False
        args.new_session = True
        args.timeout = 10
        args.first_chunk_timeout = 5
        args.idle_timeout = 5
        args.verbose = False
        args.session_id = None
        args.sandbox = False

        mock_conn_fail = AsyncMock()
        from acp import RequestError
        # 500 error
        mock_conn_fail.initialize.side_effect = RequestError(500, "Internal Server Error")
        
        mock_proc = MockProc()
        cm = MagicMock()
        cm.__aenter__.return_value = (mock_conn_fail, mock_proc)
        cm.__aexit__.return_value = None

        with patch("gemini_bridge.spawn_agent_process", return_value=cm):
            result = await _run_acp(args, "test prompt")
            self.assertFalse(result["success"])
            self.assertIn("Internal Server Error", result["error"])

if __name__ == "__main__":
    unittest.main()
