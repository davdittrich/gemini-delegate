#!/usr/bin/env python3
"""Tests for the ACP Gemini Bridge — unique files and isolation."""

import asyncio
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# Add parent to path for imports
SCRIPT_DIR = Path(__file__).parent
sys.path.insert(0, str(SCRIPT_DIR))

from gemini_bridge import (
    BridgeClient, _parse_args, extract_json, _get_session_path,
    _load_session, _save_session, _get_sessions_dir
)
from acp import RequestError


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

    def test_legacy_migration(self):
        project = str(self.tmpdir / "legacy-proj")
        resolved_project = Path(project).resolve().as_posix()
        
        # Create legacy sessions.json
        legacy_file = self.tmpdir / "sessions.json"
        legacy_file.write_text(json.dumps({resolved_project: "legacy-id"}), encoding="utf-8")
        
        # sessions_dir is self.tmpdir / "sessions"
        # bridge looks for legacy file at sessions_dir.parent / "sessions.json"
        session_path = _get_session_path(self.sessions_dir, project)
        
        loaded = _load_session(session_path, project)
        self.assertEqual(loaded, "legacy-id")
        
        # Verify it was migrated
        self.assertTrue(session_path.exists())
        migrated_data = json.loads(session_path.read_text(encoding="utf-8"))
        self.assertEqual(migrated_data["session_id"], "legacy-id")

    def test_precedence_flag_env_default(self):
        class Args:
            sessions_dir = "/tmp/flag-dir"
        
        # Flag takes precedence
        with patch.dict(os.environ, {"GEMINI_BRIDGE_SESSIONS_DIR": "/tmp/env-dir"}):
            with patch("pathlib.Path.mkdir"):
                with patch("os.chmod"):
                    path = _get_sessions_dir(Args())
                    self.assertEqual(str(path), "/tmp/flag-dir")

        # Env takes precedence over default
        class ArgsNoFlag:
            sessions_dir = None
        
        with patch.dict(os.environ, {"GEMINI_BRIDGE_SESSIONS_DIR": "/tmp/env-dir"}):
            with patch("pathlib.Path.mkdir"):
                with patch("os.chmod"):
                    path = _get_sessions_dir(ArgsNoFlag())
                    self.assertEqual(str(path), "/tmp/env-dir")


class TestBridgeFeatures(unittest.TestCase):
    """Test --prompt-stdin and --output-file AUTO."""

    def test_prompt_stdin_parsing(self):
        # We test the arg parsing part
        from gemini_bridge import _parse_args
        with patch("sys.argv", ["gemini_bridge.py", "--cd", ".", "--prompt-stdin"]):
            args = _parse_args()
            self.assertTrue(args.prompt_stdin)

    def test_output_file_auto_logic(self):
        # We verify _emit handles AUTO correctly
        from gemini_bridge import _emit
        class Args:
            cd = "."
            output_file = "AUTO"
            parse_json = False
        
        result = {"success": True, "agent_messages": "hi"}
        
        # Use StringIO for text stream
        with patch("sys.stderr", new_callable=io.StringIO) as mock_stderr:
            _emit(result, Args())
            
            # The result dict should now have AUTO_OUTPUT_FILE
            self.assertIn("AUTO_OUTPUT_FILE", result)
            auto_path = Path(result["AUTO_OUTPUT_FILE"])
            self.assertTrue(auto_path.exists())
            self.assertEqual(os.stat(auto_path).st_mode & 0o777, 0o600)
            
            # Cleanup
            auto_path.unlink()


class TestBridgeClient(unittest.TestCase):
    """Basic unit tests for BridgeClient containment."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.client = BridgeClient(cwd=self.tmpdir, approve_edits=False)

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def test_path_containment_read_outside_scope(self):
        with self.assertRaises(RequestError):
            asyncio.run(self.client.read_text_file(
                path="/etc/passwd", session_id="test",
            ))


if __name__ == "__main__":
    unittest.main()
