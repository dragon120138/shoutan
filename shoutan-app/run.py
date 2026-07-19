"""手谈 · 一键启动脚本
自动：建虚拟环境 → 装依赖 → 建索引 → 起服务
"""
import os
import subprocess
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent
BACKEND = BASE / "backend"
VENV = BASE / ".venv"

IS_WIN = os.name == "nt"


def run(cmd, cwd=None, check=True, shell=False):
    print(f"\n$ {' '.join(cmd) if isinstance(cmd, list) else cmd}")
    subprocess.run(cmd, cwd=cwd, check=check, shell=shell)


def main():
    python = sys.executable

    # 1. 建虚拟环境
    if not VENV.exists():
        print("→ 创建虚拟环境 …")
        run([python, "-m", "venv", str(VENV)])

    pip = str(VENV / ("Scripts" if IS_WIN else "bin") / "pip")
    py = str(VENV / ("Scripts" if IS_WIN else "bin") / "python")

    # 2. 装依赖
    print("→ 安装依赖（首次较慢）…")
    req = str(BACKEND / "requirements.txt")
    run([pip, "install", "-r", req], check=False)

    # 3. 建索引
    print("→ 构建知识库索引 …")
    run([py, "-m", "app.indexer"], cwd=str(BACKEND))

    # 4. 起服务
    print("\n→ 启动服务 …")
    print("   前端地址: http://127.0.0.1:8000\n")
    run([py, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8000"],
        cwd=str(BACKEND))


if __name__ == "__main__":
    main()
