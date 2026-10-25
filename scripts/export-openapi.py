#!/usr/bin/env python3
"""从应用契约导出类型源；不启动服务、不初始化数据库。"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.main import create_app  # noqa: E402

target = ROOT / "frontend" / "openapi.json"
target.write_text(
    json.dumps(create_app().openapi(), ensure_ascii=False, indent=2) + "\n",
    encoding="utf-8",
)
print(f"已导出本地API契约：{target}")
