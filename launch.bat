@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

REM 自动探测 Python：优先 py（Windows Store/launcher），其次 python
set "PY="
where py >nul 2>&1 && set "PY=py"
if not defined PY (where python >nul 2>&1 && set "PY=python")
if not defined PY (
    echo 错误：未找到 Python。请安装 Python 3.10+ 并加入 PATH 后重试。
    pause
    exit /b 1
)

echo ============================================
echo  Dota2 AI 复盘中心 · 一键启动（Windows）
echo ============================================
echo 启动解析服务(5600) + Web 服务(8642) ...
"%PY%" scripts\start_all.py %*
endlocal
