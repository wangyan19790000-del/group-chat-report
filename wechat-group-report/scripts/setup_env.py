#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""一次性环境准备：在技能目录之外建虚拟环境并装依赖。

    python setup_env.py
    python setup_env.py --venv-dir D:/py/wx
    python setup_env.py --mirror https://mirrors.aliyun.com/pypi/simple

结束时打印后续命令要用的解释器路径。
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def run(cmd: list[str]) -> int:
    print("→", " ".join(cmd))
    return subprocess.call(cmd)


def main() -> int:
    if os.name != "nt":
        print("提示：本技能读取的是 Windows 版微信本地库，需在 Windows 上运行。")
        return 2

    ap = argparse.ArgumentParser()
    default_dir = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "wechat-group-report" / ".venv"
    ap.add_argument("--venv-dir", default=str(default_dir), help="虚拟环境目录")
    ap.add_argument("--mirror", default="https://pypi.tuna.tsinghua.edu.cn/simple",
                    help="pip 镜像源（网络良好可传空字符串用官方源）")
    args = ap.parse_args()

    venv = Path(args.venv_dir)
    py = venv / "Scripts/python.exe"

    if not py.exists():
        print(f"创建虚拟环境：{venv}")
        code = run([sys.executable, "-m", "venv", str(venv)])
        if code != 0:
            print("虚拟环境创建失败。也可跳过本脚本，直接 pip install -r requirements.txt 使用系统 Python。")
            return code

    run([str(py), "-m", "pip", "install", "-q", "--upgrade", "pip"])
    install = [str(py), "-m", "pip", "install", "-r", str(HERE / "requirements.txt")]
    if args.mirror:
        install += ["-i", args.mirror]
    if run(install) != 0:
        print("\n依赖安装失败。换个镜像重试，例如：")
        print(f'  python setup_env.py --venv-dir "{venv}" --mirror https://mirrors.aliyun.com/pypi/simple')
        return 1

    print("\n" + "=" * 62)
    print("环境就绪。之后所有命令都用这个解释器（注意：要管理员身份运行终端）：")
    print(f'  "{py}" check_env.py')
    print("=" * 62)
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
