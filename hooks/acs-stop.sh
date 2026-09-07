#!/usr/bin/env bash
# Agent Core Suite —— Stop 事件（post-turn）钩子：交接载体加速/提醒器
#
# 【定位：加速/提醒，不是强制点】
#   本钩子只做「turn 结束后尽力刷新 + 校验 .acs/handoff.md」，属 audit-safe 加速器。
#   真正的强制点是机检门 scripts/gate_checklist.py 的 STEP_handoff —— 不是本钩子。
#   本平台已实测确认：做副作用 + exit 0 的钩子不打断当前 turn；
#   但「非零退出 / decision JSON 能否阻断或改写 turn」在本平台 **未验证**（known_gap），
#   故本钩子一律 exit 0，绝不假装能靠退出码执法。python 或套件缺失时静默降级为软提醒。
#
# 【平台事实】Stop 事件 payload 末条助手消息在 .last_assistant_message 或 .message；
#   stdin 是「一行紧凑 JSON + \n 后仍保持打开」，故用带超时的单次 read，绝不等待 EOF。
#
# 可调环境变量：
#   ACS_SUITE_DIR  套件根目录（最高优先）；缺省按「本钩子所在套件 → 已知落点」解析
#   ACS_WORKSPACE  工作区根（当 cwd 下没有 .acs/task-state.json 时回退到此）
#
# 退出码：恒为 0（加速器语义；执法交给门）。

# 注意：刻意不用 `set -e`——任一步失败都必须继续走到 exit 0，不得让 turn 受影响。
set -u

# --- 尽力读一行 stdin（带超时，不阻塞、不等 EOF）；读不到也无所谓 ---
PAYLOAD=""
IFS= read -r -t 3 PAYLOAD || true

# --- 解析套件根目录：ACS_SUITE_DIR → 本钩子所在套件 → 已知落点（第一个可用者）---
HOOK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd)"
HOOK_SUITE=""
if [ -n "${HOOK_DIR:-}" ]; then
  HOOK_SUITE="$(cd "$HOOK_DIR/.." 2>/dev/null && pwd)"
fi

CANDIDATES=()
[ -n "${ACS_SUITE_DIR:-}" ] && CANDIDATES+=("$ACS_SUITE_DIR")
[ -n "${HOOK_SUITE:-}" ]    && CANDIDATES+=("$HOOK_SUITE")
CANDIDATES+=("$HOME/.qoderwork/agent-core-suite")
CANDIDATES+=("$HOME/.qwenworkcn/agent-core-suite")
CANDIDATES+=("$HOME/.qoder/agent-core-suite")

SUITE=""
for c in "${CANDIDATES[@]}"; do
  if [ -n "$c" ] && [ -f "$c/scripts/acs_compress_handoff.py" ]; then
    SUITE="$c"
    break
  fi
done
# 找不到套件 → 静默降级（加速器缺席不等于 turn 失败）
[ -z "$SUITE" ] && exit 0

# --- 解析运行时（python3/python/py 里第一个真 py3）---
PY=""
for cand in python3 python py; do
  if command -v "$cand" >/dev/null 2>&1 &&
     "$cand" -c "import sys;sys.exit(0 if sys.version_info[0]==3 else 1)" >/dev/null 2>&1; then
    PY="$cand"
    break
  fi
done
# 没有 python → 静默降级
[ -z "$PY" ] && exit 0

# --- 定位 task-state.json：cwd 优先，其次 ACS_WORKSPACE ---
STATE=""
for base in "$PWD" ${ACS_WORKSPACE:-}; do
  if [ -n "$base" ] && [ -f "$base/.acs/task-state.json" ]; then
    STATE="$base/.acs/task-state.json"
    break
  fi
done
# 没有状态文件 → 无从渲染，静默退出（不制造空载体）
[ -z "$STATE" ] && exit 0

STATE_DIR="$(dirname "$STATE")"
HANDOFF="$STATE_DIR/handoff.md"
COMPRESS="$SUITE/scripts/acs_compress_handoff.py"

# --- 尽力刷新载体，再尽力校验；两步的退出码都只用于日志，绝不外溢到 turn ---
PYTHONUTF8=1 "$PY" -X utf8 "$COMPRESS" --render --state "$STATE" >/dev/null 2>&1 || true
if [ -f "$HANDOFF" ]; then
  PYTHONUTF8=1 "$PY" -X utf8 "$COMPRESS" --validate --handoff "$HANDOFF" --state "$STATE" >/dev/null 2>&1 || true
fi

# 恒 exit 0：加速器不打断 turn；阻断/decision 语义在本平台未验证，不假装执法。
exit 0
