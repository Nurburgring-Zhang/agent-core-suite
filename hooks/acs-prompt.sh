#!/usr/bin/env bash
# Agent Core Suite —— UserPromptSubmit 事件钩子：交接提醒器（best-effort）
#
# 【诚实边界 / known_gap】
#   本钩子把一行提醒打到 stdout。但「终端是否会把 UserPromptSubmit 钩子的 stdout
#   注入模型上下文」在本平台 **未经验证**（known_gap）——本钩子因此只是 best-effort 提醒，
#   不承诺一定进模型眼。真正的强制点是机检门 scripts/gate_checklist.py 的 STEP_handoff，
#   不是本钩子。同理，非零退出 / decision JSON 能否改写或阻断 turn 亦未验证，故一律 exit 0。
#
# 【平台事实】stdin 是「一行紧凑 JSON + \n 后仍保持打开」，用带超时的单次 read，绝不等待 EOF。
#   纯 bash，无 jq、无 python 依赖，尽量轻。
#
# 可调环境变量：
#   ACS_WORKSPACE  工作区根（当 cwd 下没有 .acs/handoff.md 时回退到此）
#
# 退出码：恒为 0（提醒器语义；执法交给门）。

set -u

# --- 尽力读一行 stdin（带超时，不阻塞、不等 EOF）---
PAYLOAD=""
IFS= read -r -t 3 PAYLOAD || true

# --- 定位 .acs/handoff.md：cwd 优先，其次 ACS_WORKSPACE ---
HANDOFF=""
for base in "$PWD" ${ACS_WORKSPACE:-}; do
  if [ -n "$base" ] && [ -f "$base/.acs/handoff.md" ]; then
    HANDOFF="$base/.acs/handoff.md"
    break
  fi
done

# 有交接载体才提醒；没有就静默（不制造噪音）
if [ -n "$HANDOFF" ]; then
  echo "[ACS] 先读 .acs/handoff.md（当前目标/已完成/下一步），再定点读取，禁止回灌历史"
fi

# 恒 exit 0：提醒器不打断 turn；stdout 是否入模型上下文未验证，不假装执法。
exit 0
