"""Launch the configured Saki renderer and Python application together."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "d_sakiko_config.json"


def renderer_mode() -> str:
    override = os.environ.get("DSAKIKO_RENDERER", "").strip().lower()
    if override in {"electron", "pygame"}:
        return override
    try:
        with CONFIG_PATH.open("r", encoding="utf-8") as stream:
            config = json.load(stream)
        configured = str(config.get("ui_state", {}).get("live2d_renderer", "electron")).lower()
        return configured if configured in {"electron", "pygame"} else "electron"
    except (OSError, json.JSONDecodeError, AttributeError):
        return "electron"


def main() -> int:
    python_executable = Path(sys.executable)
    main_script = ROOT / "GPT_SoVITS" / "main2.py"
    electron_root = ROOT / "electron_frontend"
    electron_process: subprocess.Popen[bytes] | None = None

    if renderer_mode() == "electron":
        electron_command = electron_root / "node_modules" / ".bin" / "electron.cmd"
        if not electron_command.is_file():
            print(f"Electron 依赖不存在：{electron_command}", file=sys.stderr)
            print("请先运行 sync_real_environment.ps1 -InstallElectronDependencies -BuildElectron", file=sys.stderr)
            return 1
        electron_process = subprocess.Popen([str(electron_command), "."], cwd=electron_root)

    try:
        return subprocess.call([str(python_executable), str(main_script)], cwd=main_script.parent)
    finally:
        if electron_process is not None and electron_process.poll() is None:
            electron_process.terminate()


if __name__ == "__main__":
    raise SystemExit(main())
