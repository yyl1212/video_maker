#!/usr/bin/env python3
"""为每个检查设置绝对截止，超时终止整个子进程组。"""

import argparse
import os
import signal
import subprocess
import sys


def main() -> int:
    parser = argparse.ArgumentParser(description="运行有界检查")
    parser.add_argument("--cwd")
    parser.add_argument("--timeout", type=float, default=540)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command or not 0 < args.timeout <= 540:
        parser.error("须提供命令，超时必须在0～540秒之间")
    try:
        process = subprocess.Popen(command, cwd=args.cwd, start_new_session=True)
    except OSError as error:
        print(f"无法启动检查：{error}", file=sys.stderr)
        return 127
    try:
        return process.wait(timeout=args.timeout)
    except (subprocess.TimeoutExpired, KeyboardInterrupt) as error:
        # 子进程可能再启动Node/测试服务，不能只终止最外层进程。
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            pass
        # 即便父进程先退出，也必须清理同组残留子进程。
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait()
        interrupted = isinstance(error, KeyboardInterrupt)
        print(
            "检查已中断" if interrupted else "检查超时，已停止进程组", file=sys.stderr
        )
        return 130 if interrupted else 124


if __name__ == "__main__":
    raise SystemExit(main())
