#!/usr/bin/env bash
# PageGate OpenClaw Skill 安装脚本
#
# 用法：
#   1. 本地安装（从代码仓库）:
#      bash openclaw-skill/install.sh
#
#   2. 远程一键安装（从 GitHub）:
#      curl -fsSL https://raw.githubusercontent.com/littlechild9/pagegate/main/openclaw-skill/install.sh | bash
#
# 安装完成后由主 agent 在聊天中继续 onboarding。

set -euo pipefail

# ── 颜色 ──────────────────────────────────────────────────────────
if [ -t 1 ]; then
    GREEN='\033[32m'; YELLOW='\033[33m'; RED='\033[31m'
    CYAN='\033[36m'; BOLD='\033[1m'; DIM='\033[2m'; RESET='\033[0m'
else
    GREEN=''; YELLOW=''; RED=''; CYAN=''; BOLD=''; DIM=''; RESET=''
fi

ok()   { printf "  ${GREEN}✓${RESET} %s\n" "$1"; }
warn() { printf "  ${YELLOW}⚠${RESET} %s\n" "$1"; }
fail() { printf "  ${RED}✗${RESET} %s\n" "$1"; }
info() { printf "  ${DIM}→${RESET} %s\n" "$1"; }

# ── 常量 ──────────────────────────────────────────────────────────
SKILL_NAME="pagegate-client"
GITHUB_REPO="littlechild9/pagegate"
GITHUB_BRANCH="main"
SKILL_SUBDIR="openclaw-skill"

# 安装目标目录（OpenClaw 标准路径）
INSTALL_DIR="${HOME}/.openclaw/workspace/skills/${SKILL_NAME}"
ONBOARDING_MARKER="${INSTALL_DIR}/.onboarding-pending"

# ── Banner ────────────────────────────────────────────────────────
printf "\n${CYAN}──────────────────────────────────────────────────${RESET}\n"
printf "${CYAN}  PageGate Client — OpenClaw Skill 安装程序${RESET}\n"
printf "${CYAN}──────────────────────────────────────────────────${RESET}\n\n"

# ── 检测 Python ───────────────────────────────────────────────────
PYTHON=""
for cmd in python3 python; do
    if command -v "$cmd" &>/dev/null; then
        PYTHON="$cmd"
        break
    fi
done
if [ -z "$PYTHON" ]; then
    fail "未找到 Python 3，请先安装 Python 3.9+"
    exit 1
fi
ok "Python: $($PYTHON --version 2>&1)"

# ── 确定安装来源 ──────────────────────────────────────────────────
SCRIPT_SOURCE="${BASH_SOURCE[0]-}"
SCRIPT_DIR=""
if [ -n "$SCRIPT_SOURCE" ]; then
    SCRIPT_DIR="$(cd "$(dirname "$SCRIPT_SOURCE")" && pwd 2>/dev/null || echo "")"
fi
SOURCE_DIR=""

# 情况 1：从本地代码仓库执行（install.sh 所在目录就是 skill 源）
if [ -n "$SCRIPT_DIR" ] && [ -f "$SCRIPT_DIR/SKILL.md" ]; then
    SOURCE_DIR="$SCRIPT_DIR"
    info "从本地目录安装: $SOURCE_DIR"
fi

# 情况 2：通过 curl | bash 执行，需要从 GitHub 下载
if [ -z "$SOURCE_DIR" ]; then
    info "从 GitHub 下载 skill..."

    TMPDIR_DL="$(mktemp -d)"
    trap 'rm -rf "$TMPDIR_DL"' EXIT

    # 优先用 git clone（浅克隆），回退到 curl 下载 tar
    if command -v git &>/dev/null; then
        git clone --depth 1 --branch "$GITHUB_BRANCH" \
            "https://github.com/${GITHUB_REPO}.git" \
            "$TMPDIR_DL/repo" 2>/dev/null
        SOURCE_DIR="$TMPDIR_DL/repo/$SKILL_SUBDIR"
    elif command -v curl &>/dev/null; then
        curl -fsSL "https://github.com/${GITHUB_REPO}/archive/refs/heads/${GITHUB_BRANCH}.tar.gz" \
            | tar xz -C "$TMPDIR_DL"
        # tar 解压后目录名类似 pagegate-main/
        EXTRACTED="$(ls -d "$TMPDIR_DL"/*/)"
        SOURCE_DIR="${EXTRACTED}${SKILL_SUBDIR}"
    else
        fail "需要 git 或 curl 来下载 skill"
        exit 1
    fi

    if [ ! -f "$SOURCE_DIR/SKILL.md" ]; then
        fail "下载失败：找不到 SKILL.md"
        exit 1
    fi
    ok "下载完成"
fi

# ── 检查已有安装 ──────────────────────────────────────────────────
if [ -d "$INSTALL_DIR" ]; then
    warn "已存在旧安装: $INSTALL_DIR"
    printf "  是否覆盖？(Y/n): "
    read -r REPLY </dev/tty || REPLY="y"
    REPLY="${REPLY:-y}"
    if [[ ! "$REPLY" =~ ^[Yy是]$ ]]; then
        info "取消安装"
        exit 0
    fi
    # 保留旧 .env 配置
    if [ -f "$INSTALL_DIR/.env" ]; then
        cp "$INSTALL_DIR/.env" "$SOURCE_DIR/.env.backup" 2>/dev/null || true
        ok "已备份旧的 .env 配置"
    fi
    rm -rf "$INSTALL_DIR"
fi

# ── 安装 ──────────────────────────────────────────────────────────
info "安装到 $INSTALL_DIR ..."
mkdir -p "$(dirname "$INSTALL_DIR")"
cp -r "$SOURCE_DIR" "$INSTALL_DIR"

# 恢复旧 .env
if [ -f "$INSTALL_DIR/.env.backup" ]; then
    mv "$INSTALL_DIR/.env.backup" "$INSTALL_DIR/.env"
    ok "已恢复之前的 .env 配置"
fi

# 标记是否需要继续 onboarding
if [ -f "$INSTALL_DIR/.env" ]; then
    rm -f "$ONBOARDING_MARKER"
else
    : > "$ONBOARDING_MARKER"
fi

# 设置可执行权限
chmod +x "$INSTALL_DIR/scripts/start-watcher.sh" 2>/dev/null || true
chmod +x "$INSTALL_DIR/scripts/check-watcher.sh" 2>/dev/null || true
chmod +x "$INSTALL_DIR/scripts/register_watch_cron.py" 2>/dev/null || true
chmod +x "$INSTALL_DIR/scripts/pagegate_onboard.py" 2>/dev/null || true

ok "Skill 安装完成！"
printf "\n"
info "安装位置: $INSTALL_DIR"
info "Skill 名称: $SKILL_NAME"

# ── 下一步：由主 agent 在聊天中完成 onboarding ───────────────────
printf "\n"
printf "${BOLD}安装完成！${RESET}\n"
info "接下来请直接在 OpenClaw 聊天里完成 PageGate onboarding。"
info "安装脚本只负责安装，真正的向导由主 agent 在聊天里继续。"
printf "\n"
