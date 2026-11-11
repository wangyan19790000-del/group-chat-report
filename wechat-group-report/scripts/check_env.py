#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Step 0 · 环境自检（个人微信版）。

    python check_env.py

检查：Python 版本、依赖包、管理员权限、微信进程、数据目录、中文字体、
SQLCipher 解密自测。退出码 0 = 就绪，1 = 有必须先解决的问题。
"""
from __future__ import annotations

import ctypes
import importlib.util
import os
import sys

OK, BAD, WARN = "[OK]  ", "[FAIL]", "[WARN]"


def has(mod: str) -> bool:
    return importlib.util.find_spec(mod) is not None


def is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def main() -> int:
    problems = []
    print("== 个人微信群报告管线 · 环境自检 ==\n")

    v = sys.version_info
    if v >= (3, 10):
        print(OK, f"Python {v.major}.{v.minor}.{v.micro}")
    else:
        problems.append("需要 Python 3.10+")
        print(BAD, f"Python {v.major}.{v.minor}（需 3.10+）")

    for mod, pkg in (("Crypto", "pycryptodome"), ("zstandard", "zstandard"),
                     ("PIL", "pillow"), ("numpy", "numpy"), ("frida", "frida")):
        if has(mod):
            print(OK, f"依赖 {pkg}")
        elif pkg == "frida":
            print(WARN, "frida 未安装：取钥将退化为慢速内存扫描（强烈建议 pip install frida）")
        else:
            problems.append(f"缺少依赖 {pkg}（pip install {pkg}）")
            print(BAD, f"依赖 {pkg} 缺失")

    if os.name != "nt":
        problems.append("仅支持 Windows（取密钥依赖 Windows 进程 API）")
        print(BAD, "非 Windows 平台")
        return 1

    if is_admin():
        print(OK, "当前进程有管理员权限（可读取微信进程内存）")
    else:
        problems.append("需要以管理员身份运行，否则读不到微信进程内存、取不到密钥")
        print(BAD, "当前不是管理员权限")

    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    try:
        from wxread.keyfind import list_weixin_pids
        pids = list_weixin_pids()
        if pids:
            print(OK, f"微信客户端正在运行（pid {', '.join(map(str, pids[:5]))}）")
        else:
            problems.append("未检测到微信客户端进程：请先启动并登录微信（保持运行）")
            print(BAD, "Weixin.exe 未运行")
    except Exception as e:
        print(WARN, f"进程检测失败：{e}")

    try:
        from wxread.source import find_data_root, list_accounts, pick_account
        root = find_data_root("")
        acc = pick_account(root, "")
        print(OK, f"数据目录 {root}")
        print(OK, f"当前账号 {acc['wxid']}（{os.path.basename(acc['dir'])}）")
        n = len(list_accounts(root))
        if n > 1:
            print(WARN, f"本机有 {n} 个账号目录，默认取最近写入的那个，可用 --account 指定")
    except Exception as e:
        problems.append(f"未找到微信数据目录：{e}")
        print(BAD, f"数据目录：{e}")

    try:
        from wxread.crypto import selftest
        if selftest():
            print(OK, "SQLCipher 解密自测通过")
        else:
            problems.append("SQLCipher 解密自测失败（依赖安装异常？）")
            print(BAD, "SQLCipher 解密自测失败")
    except Exception as e:
        print(WARN, f"解密自测跳过：{e}")

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
