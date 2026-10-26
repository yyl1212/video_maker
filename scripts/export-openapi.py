#!/usr/bin/env python3
"""导出或只读校验API契约；不启动服务、不初始化数据库。"""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.main import create_app  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="只检查契约是否同步")
    parser.add_argument("--output", type=Path, default=ROOT / "frontend/openapi.json")
    args = parser.parse_args()
    schema = create_app().openapi()
    if args.check:
        try:
            current = json.loads(args.output.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            print(f"无法读取API契约：{exc}", file=sys.stderr)
            return 1
        if current != schema:
            print("API契约已变化，请重新导出并生成前端类型。", file=sys.stderr)
            return 1
        print("API契约已同步。")
        return 0
    try:
        args.output.write_text(
            json.dumps(schema, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    except OSError as exc:
        print(f"无法写入API契约：{exc}", file=sys.stderr)
        return 1
    print(f"已导出本地API契约：{args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
