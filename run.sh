#!/usr/bin/env bash
# Dota2 AI 复盘中心 · 一键启动（Linux / macOS / WSL）
set -e
cd "$(dirname "$0")"

PY="$(command -v python3 || command -v python || true)"
if [ -z "$PY" ]; then
    echo "错误：未找到 Python。请安装 Python 3.10+ 后重试。"
    exit 1
fi

echo "============================================"
echo " Dota2 AI 复盘中心 · 一键启动 (Linux/macOS)"
echo "============================================"
echo "启动解析服务(5600) + Web 服务(8642) ..."
"$PY" scripts/start_all.py "$@"
