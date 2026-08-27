from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
LAUNCHER_PATH = ROOT / "tools" / "launch_runtime.py"


def load_launcher():
    spec = importlib.util.spec_from_file_location("test_launch_runtime_module", LAUNCHER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class RendererSelectionTest(unittest.TestCase):
    def test_config_and_environment_override_select_renderer(self):
        launcher = load_launcher()
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "config.json"
            config_path.write_text(
                json.dumps({"ui_state": {"live2d_renderer": "pygame"}}),
                encoding="utf-8",
            )
            launcher.CONFIG_PATH = config_path
            with patch.dict(os.environ, {}, clear=True):
                self.assertEqual(launcher.renderer_mode(), "pygame")
            with patch.dict(os.environ, {"DSAKIKO_RENDERER": "electron"}, clear=True):
                self.assertEqual(launcher.renderer_mode(), "electron")

    def test_missing_or_invalid_config_uses_historical_electron_default(self):
        launcher = load_launcher()
        with tempfile.TemporaryDirectory() as directory:
            launcher.CONFIG_PATH = Path(directory) / "missing.json"
            with patch.dict(os.environ, {}, clear=True):
                self.assertEqual(launcher.renderer_mode(), "electron")
            launcher.CONFIG_PATH.write_text(
                json.dumps({"ui_state": {"live2d_renderer": "invalid"}}),
                encoding="utf-8",
            )
            with patch.dict(os.environ, {}, clear=True):
                self.assertEqual(launcher.renderer_mode(), "electron")


if __name__ == "__main__":
    unittest.main()
