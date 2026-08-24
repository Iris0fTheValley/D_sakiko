"""Launch the configured Saki renderer and Python application together."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
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


def wait_for_bridge(timeout: float = 30.0) -> bool:
    """Wait until both the WebSocket and model/audio HTTP ports accept connections."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        ready = True
        for port in (9876, 9877):
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                    pass
            except OSError:
                ready = False
                break
        if ready:
            return True
        time.sleep(0.2)
    return False


def main() -> int:
    python_executable = Path(sys.executable)
    main_script = ROOT / "GPT_SoVITS" / "main2.py"
    electron_root = ROOT / "electron_frontend"
    python_process = subprocess.Popen([str(python_executable), str(main_script)], cwd=main_script.parent)
    electron_process: subprocess.Popen[bytes] | None = None

    try:
        if renderer_mode() == "electron":
            if not wait_for_bridge():
                print("Bridge 在 30 秒内未就绪，未启动 Electron。", file=sys.stderr)
                return 1
            electron_command = electron_root / "node_modules" / ".bin" / "electron.cmd"
            if not electron_command.is_file():
                print(f"Electron 依赖不存在：{electron_command}", file=sys.stderr)
                print("请先运行 sync_real_environment.ps1 -InstallElectronDependencies -BuildElectron", file=sys.stderr)
                return 1
            electron_process = subprocess.Popen([str(electron_command), "."], cwd=electron_root)

        return python_process.wait()
    finally:
        if python_process.poll() is None:
            python_process.terminate()
        if electron_process is not None and electron_process.poll() is None:
            electron_process.terminate()


if __name__ == "__main__":
    raise SystemExit(main())
