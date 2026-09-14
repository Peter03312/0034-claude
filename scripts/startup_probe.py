#!/usr/bin/env python3
"""一次性启动探测：启动 uvicorn 子进程，轮询 /health 后关闭。

供 docker compose verify 服务在测试通过后执行，确认镜像内 API 确实可启动。
"""

from __future__ import annotations

import subprocess
import sys
import time
import urllib.request
import urllib.error

PORT = 8000
URL = f"http://127.0.0.1:{PORT}/health"
DEADLINE_SECONDS = 30.0


def main() -> int:
    proc = subprocess.Popen(
        [
            sys.executable, "-m", "uvicorn", "app.main:app",
            "--host", "127.0.0.1", "--port", str(PORT),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    logs: list[str] = []
    deadline = time.monotonic() + DEADLINE_SECONDS
    try:
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                remaining = proc.stdout.read() if proc.stdout else ""
                logs.append(remaining)
                print("API 进程提前退出:\n" + "".join(logs), file=sys.stderr)
                return 1
            try:
                with urllib.request.urlopen(URL, timeout=2) as resp:
                    if resp.status == 200:
                        print(f"启动探测成功：GET {URL} -> 200 {resp.read().decode()}")
                        return 0
            except (urllib.error.URLError, ConnectionError, OSError):
                if proc.stdout is not None:
                    line = proc.stdout.readline()
                    if line:
                        logs.append(line)
                time.sleep(0.3)
        print(f"启动探测超时（{DEADLINE_SECONDS}s）:\n" + "".join(logs), file=sys.stderr)
        return 1
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


if __name__ == "__main__":
    raise SystemExit(main())
