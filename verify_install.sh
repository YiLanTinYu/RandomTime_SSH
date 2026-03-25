#!/bin/bash
# =============================================================================
# 通用安装验证脚本（命令行版）
# 适用：在根目录通用 Python 环境下验证依赖是否就绪
# 麒麟 V10 环境请使用 kylin/verify_install_kylin.sh
# =============================================================================

PYTHON_BIN=$(command -v python3 2>/dev/null || command -v python 2>/dev/null || echo "")

echo "============================================"
echo " SSH 交换机工具 — 安装验证"
echo "============================================"

# ── Python 环境 ──────────────────────────────────
echo ""
echo "=== 1. Python 环境 ==="
if [ -z "$PYTHON_BIN" ]; then
    echo "[FAIL] 未找到 python3，请先安装 Python 3.x"
    exit 1
fi
echo "[PASS] $($PYTHON_BIN --version)"

PIP_VER=$("$PYTHON_BIN" -m pip --version 2>&1)
if [ $? -eq 0 ]; then
    echo "[PASS] $PIP_VER"
else
    echo "[FAIL] pip 未安装"
    exit 1
fi

# ── 第三方依赖 ───────────────────────────────────
echo ""
echo "=== 2. 第三方依赖 ==="
check_pkg() {
    local mod=$1
    local name=${2:-$1}
    ver=$("$PYTHON_BIN" -c "import $mod; print(getattr($mod,'__version__','ok'))" 2>/dev/null)
    if [ $? -eq 0 ]; then
        echo "[PASS] $name: $ver"
    else
        echo "[FAIL] $name — 未安装（运行 pip install paramiko 或使用离线包）"
        FAIL=1
    fi
}

FAIL=0
check_pkg "paramiko"     "paramiko"
check_pkg "cryptography" "cryptography"
check_pkg "cffi"         "cffi"
check_pkg "nacl"         "pynacl"
check_pkg "bcrypt"       "bcrypt"

# ── 主程序文件 ───────────────────────────────────
echo ""
echo "=== 3. 主程序文件 ==="
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
MAIN_PY="${SCRIPT_DIR}/random_ssh_switch_connector.py"

if [ -f "$MAIN_PY" ]; then
    echo "[PASS] 主程序文件存在"
    if "$PYTHON_BIN" -m py_compile "$MAIN_PY" 2>/dev/null; then
        echo "[PASS] 语法检查通过"
    else
        echo "[FAIL] 主程序语法错误"
        FAIL=1
    fi
else
    echo "[FAIL] 主程序文件不存在: $MAIN_PY"
    FAIL=1
fi

# ── 结果汇总 ─────────────────────────────────────
echo ""
echo "============================================"
if [ ${FAIL:-0} -eq 0 ]; then
    echo " [OK] 验证通过，可以运行程序："
    echo ""
    echo "  # 交互向导模式（推荐）"
    echo "  python3 random_ssh_switch_connector.py"
    echo ""
    echo "  # 命令行参数模式"
    echo "  python3 random_ssh_switch_connector.py -H <IP> -u <用户名> -p <密码> -c commands.txt"
    echo ""
    echo "  # 查看帮助"
    echo "  python3 random_ssh_switch_connector.py --help"
else
    echo " [FAIL] 存在未通过项，请先安装缺失依赖："
    echo "  pip3 install --no-index --find-links=python_dependencies/ -r requirements.txt"
    exit 1
fi
echo "============================================"
