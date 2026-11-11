#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Step 0 · Pre-flight check for the wecom-group-report pipeline.

    python check_env.py

Verifies: Python version, required packages, WeCom data directory, WXWork.exe
process, CJK fonts. Exit code 0 = ready; 1 = something must be fixed first.
"""
from __future__ import annotations

import importlib.util
import os
import subprocess
import sys

OK, BAD, WARN = "[OK]  ", "[FAIL]", "[WARN]"


def has(mod: str) -> bool:
    return importlib.util.find_spec(mod) is not None


def main() -> int:
    problems = []
    print("== 企业微信群报告管线 · 环境自检 ==\n")

    v = sys.version_info
    if v >= (3, 10):
        print(OK, f"Python {v.major}.{v.minor}.{v.micro}")
    else:
        problems.append("需要 Python 3.10+")
        print(BAD, f"Python {v.major}.{v.minor}（需 3.10+）")

    for mod, pkg in (("cryptography", "cryptography"), ("PIL", "pillow")):
        if has(mod):
            print(OK, f"依赖 {pkg}")
        else:
            problems.append(f"缺少依赖 {pkg}（pip install {pkg}）")
            print(BAD, f"依赖 {pkg} 缺失")

    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    try:
        from wxwork_reader.db import find_db_dir
        db_dir = find_db_dir(None)
        print(OK, f"企业微信数据目录 {db_dir}")
    except Exception as e:
        problems.append(f"未找到企业微信数据目录：{e}")
        print(BAD, f"企业微信数据目录：{e}")

    try:
        out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq WXWork.exe"],
                             capture_output=True, timeout=15).stdout.decode("gbk", "replace")
        if "WXWork.exe" in out:
            print(OK, "WXWork.exe 正在运行（已登录客户端）")
        else:
            print(WARN, "WXWork.exe 未在运行——请先启动并登录企业微信客户端，否则取钥会失败")
            problems.append("请先启动并登录企业微信客户端")
    except Exception as e:
        print(WARN, f"无法检测 WXWork.exe 进程：{e}")

    windir = os.environ.get("WINDIR", "C:/Windows")
    for f in ("msyh.ttc", "msyhbd.ttc"):
        p = os.path.join(windir, "Fonts", f)
        if os.path.exists(p):
            print(OK, f"字体 {f}")
        else:
            print(WARN, f"字体缺失 {p}（渲染 PNG 时可用 --font 指定其他中文字体）")

    print()
    if problems:
        print("需要先处理：")
        for p in problems:
            print(" -", p)
        return 1
    print("环境就绪，可以开始抽取。")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
