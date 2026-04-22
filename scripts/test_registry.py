#!/usr/bin/env python3
"""Tests for the ModelRegistry logic."""

import json
import os
import shutil
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import sys
# Add parent to path for imports
SCRIPT_DIR = Path(__file__).parent
sys.path.insert(0, str(SCRIPT_DIR))

from gemini_bridge import ModelRegistry

class TestModelRegistry(unittest.TestCase):

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self.cache_dir = self.tmpdir / "cache"
        self.registry_file = self.tmpdir / "source-registry.json"
        
        # Initial dummy data
        self.dummy_data = {
            "gemini": {
                "tier_2": [
                    {
                        "model_id": "gemini-2.5-pro",
                        "pricing_per_1m_tokens": {"input": 1.25, "output": 10.0}
                    }
                ]
            }
        }
        self.registry_file.write_text(json.dumps(self.dummy_data))

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def test_cache_creation(self):
        reg = ModelRegistry(cache_dir=self.cache_dir, source_file=self.registry_file)
        models = reg.get_models()
        self.assertIn("gemini-2.5-pro", models)
        self.assertTrue((self.cache_dir / "models.json").exists())

    def test_cache_hit_within_ttl(self):
        reg = ModelRegistry(cache_dir=self.cache_dir, source_file=self.registry_file)
        reg.get_models() # Warm up cache
        
        # Update source but expect cache hit
        self.registry_file.write_text(json.dumps({"gemini": {"tier_1": [{"model_id": "new-model"}]}}))
        
        reg2 = ModelRegistry(cache_dir=self.cache_dir, source_file=self.registry_file)
        models = reg2.get_models()
        self.assertIn("gemini-2.5-pro", models)
        self.assertNotIn("new-model", models)

    def test_cache_expiry_triggers_refresh(self):
        reg = ModelRegistry(cache_dir=self.cache_dir, source_file=self.registry_file)
        reg.get_models()
        
        # Backdate cache file
        cache_file = self.cache_dir / "models.json"
        old_time = time.time() - 90000 # > 24h
        os.utime(cache_file, (old_time, old_time))
        
        # Update source
        self.registry_file.write_text(json.dumps({"gemini": {"tier_1": [{"model_id": "new-model", "pricing_per_1m_tokens": {}}]}}))
        
        reg2 = ModelRegistry(cache_dir=self.cache_dir, source_file=self.registry_file)
        models = reg2.get_models()
        self.assertIn("new-model", models)

    def test_pricing_retrieval(self):
        reg = ModelRegistry(cache_dir=self.cache_dir, source_file=self.registry_file)
        pricing = reg.get_pricing("gemini-2.5-pro")
        self.assertEqual(pricing["input"], 1.25)

if __name__ == "__main__":
    unittest.main()
