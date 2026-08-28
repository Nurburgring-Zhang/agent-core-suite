#!/usr/bin/env bash
# Agent Core Suite v1.1.0 —— Linux / macOS 安装脚本
#
# 用法（在套件目录下执行）：
#   ./install.sh --target /path/to/workspace
#   ./install.sh --target . --mode generic
#   ./install.sh --target . --dry-run
#
# 参数：
#   --target DIR      目标工作区目录（默认当前目录）
#   --mode MODE       auto（默认，由 acs_doctor 按文件系统标记探测）| qoder | qoderwork
#                     | claude | cursor | windsurf | codex | generic（落点见 spec/terminals.json）
#   --skill-home DIR  覆盖技能安装目录
#   --python BIN      python 可执行名（默认自动探测 python3/python/py）
#   --node BIN        node 可执行名（无 Python 时的兜底运行时，硬门禁覆盖 4/5 门）
#   --dry-run         只打印计划，不写盘
#   --force           覆盖已存在的同名文件
#   --with-hook       另装 pre-commit 外部强制点到 <target>/.git/hooks（目标须是 git 仓库）
#
# 退出码：0=成功；1=失败；2=用法错误。说明：不做哈希校验。
set -u

SUITE="$(cd "$(dirname "$0")" && pwd)"
TARGET="."
MODE="auto"
SKILL_HOME=""
PY=""
NODE=""
RUNTIME="python"
MODE_LABEL=""
MODE_NOTE=""
RULES_SRC=""
RULES_DST=""
DRY=0
FORCE=0
WITH_HOOK=0
FAILS=0

die_usage() { echo "USAGE_ERROR $1"; sed -n '3,20p' "$0"; exit 2; }
say() { echo "$1"; }
fail() { echo "FAIL  $1"; FAILS=$((FAILS + 1)); }

while [ $# -gt 0 ]; do
  case "$1" in
    --target) [ $# -ge 2 ] || die_usage "--target 缺少取值"; TARGET="$2"; shift 2 ;;
    --mode) [ $# -ge 2 ] || die_usage "--mode 缺少取值"; MODE="$2"; shift 2 ;;
    --skill-home) [ $# -ge 2 ] || die_usage "--skill-home 缺少取值"; SKILL_HOME="$2"; shift 2 ;;
    --python) [ $# -ge 2 ] || die_usage "--python 缺少取值"; PY="$2"; shift 2 ;;
    --node) [ $# -ge 2 ] || die_usage "--node 缺少取值"; NODE="$2"; shift 2 ;;
    --dry-run) DRY=1; shift ;;
    --force) FORCE=1; shift ;;
    --with-hook) WITH_HOOK=1; shift ;;
    *) die_usage "无法识别的参数：$1" ;;
  esac
done

# --mode 的合法取值由 spec/terminals.json 决定，故不在此处硬写一份清单：
# 多一份清单就多一条漂移路径。非法取值会在向 acs_doctor 取落点时被拒（exit 2）。

# ---- 第 0 步：运行时探测（Python 优先，Node 兜底）----
# 不得把 python3 固定为唯一默认值：存在大量只有 `python`（conda / miniforge / Windows Git Bash）
# 或只有 `py`（Windows launcher）的环境，固定默认值会直接导致安装失败。
py_works() { command -v "$1" >/dev/null 2>&1 && "$1" -c "import sys;sys.exit(0 if sys.version_info[0]==3 else 1)" >/dev/null 2>&1; }
node_works() { command -v "$1" >/dev/null 2>&1 && "$1" -e "process.exit(0)" >/dev/null 2>&1; }
if [ -n "$PY" ]; then
  py_works "$PY" || die_usage "--python $PY 不可用或非 Python 3"
else
  for cand in python3 python py; do
    if py_works "$cand"; then PY="$cand"; break; fi
  done
fi
if [ -z "$PY" ]; then
  # 无 Python 不等于直接判死：先看 Node。有 Node 就还能拿到 4/5 门的退出码，
  # 缺口（reality_scan 需 Python AST）必须当场说清，而不是让人以为装完就是全门。
  if [ -n "$NODE" ]; then
    node_works "$NODE" || die_usage "--node $NODE 不可用"
  else
    for cand in node nodejs; do
      if node_works "$cand"; then NODE="$cand"; break; fi
    done
  fi
  if [ -z "$NODE" ]; then
    echo "FAIL  未探测到 Python 3（试过 python3 / python / py），也没有可用 Node。"
    echo "      硬门禁靠这两种运行时之一跑，都缺则只剩软约束 —— 这是显式失败，不静默降级。"
    echo "      装好 Python 3（推荐）或 Node ≥16 后重跑，或用 --python / --node 显式指定。"
    exit 1
  fi
  RUNTIME="node"
fi
say "=== Agent Core Suite 安装 ==="
say "  套件目录 : $SUITE"
if [ "$RUNTIME" = "python" ]; then
  say "  运行时   : python -> $PY ($("$PY" -c 'import sys;print(sys.version.split()[0])' 2>/dev/null))"
else
  say "  运行时   : node -> $NODE ($("$NODE" --version 2>/dev/null))"
  say "  WARN     : 未探测到 Python 3，改用 Node 兜底 —— 硬门禁覆盖 4/5 门"
  say "             （reality_scan 需 Python AST）。这是显式的部分覆盖，不是静默降级。"
fi

if [ ! -d "$TARGET" ]; then
  if [ "$DRY" -eq 1 ]; then say "  [dry-run] 将创建目标目录 $TARGET"; else mkdir -p "$TARGET"; fi
fi
TARGET_FULL="$(cd "$TARGET" 2>/dev/null && pwd || echo "$TARGET")"
say "  目标工作区: $TARGET_FULL"
say "  模式      : $MODE"

# ---- 第 1 步：安装前自检 ----
say ""
say "[1/4] 安装前自检"
if [ "$RUNTIME" = "python" ]; then
  CHECK="$SUITE/scripts/install_check.py"
  if [ ! -f "$CHECK" ]; then
    fail "缺少 scripts/install_check.py，套件不完整，终止安装"
    exit 1
  fi
  export PYTHONUTF8=1
  "$PY" -X utf8 "$CHECK" --root "$SUITE" || { fail "install_check 未通过，终止安装"; exit 1; }
else
  NODE_GATE="$SUITE/scripts/node/acs_gates.mjs"
  if [ ! -f "$NODE_GATE" ]; then
    fail "缺少 scripts/node/acs_gates.mjs，Node 兜底路径不完整，终止安装"
    exit 1
  fi
  "$NODE" "$NODE_GATE" check --root "$SUITE" || { fail "清单自检未通过，终止安装"; exit 1; }
fi

# ---- 第 2 步：确定安装路径 ----
# 落点（技能目录 / 规则文件）一律向 acs_doctor 取值：定义只存 spec/terminals.json，
# 安装脚本只当解释器。自己再抄一份 case 分支就等于给漂移开了第二条路。
doctor_paths() {
  if [ "$RUNTIME" = "python" ]; then
    "$PY" -X utf8 "$SUITE/scripts/acs_doctor.py" --paths --posix --mode "$MODE" --target "$TARGET_FULL"
  else
    "$NODE" "$SUITE/scripts/node/acs_gates.mjs" doctor --paths --posix --mode "$MODE" --target "$TARGET_FULL"
  fi
}
PATHS_OUT="$(doctor_paths)" || die_usage "--mode $MODE 无法解析（acs_doctor 拒绝了它，可用值见 spec/terminals.json）"
while IFS='=' read -r key value; do
  # Windows 上 Python/Node 的文本流输出 CRLF，\r 若跟进变量会拼出「路径末尾带回车」的
  # 幽灵文件名：cp 报「源文件不存在」，而看起来路径完全正确。必须先剥掉。
  value="${value%$'\r'}"
  case "$key" in
    TERMINAL) MODE="$value" ;;
    LABEL) MODE_LABEL="$value" ;;
    SKILLS_DIR) [ -n "$SKILL_HOME" ] || SKILL_HOME="$value" ;;
    RULES_SOURCE) RULES_SRC="$value" ;;
    RULES_TARGET) RULES_DST="$value" ;;
    NOTE) MODE_NOTE="$value" ;;
  esac
done <<EOF
$PATHS_OUT
EOF
if [ -z "$SKILL_HOME" ] || [ -z "$RULES_SRC" ] || [ -z "$RULES_DST" ]; then
  fail "acs_doctor 未给出完整落点（mode=${MODE}），终止安装以免装到错误位置"
  exit 1
fi
ACS_DIR="$TARGET_FULL/.acs"
ACS_SCRIPTS="$ACS_DIR/scripts"
ACS_TEMPLATES="$ACS_DIR/templates"
ACS_SPEC="$ACS_DIR/spec"

say ""
say "[2/4] 安装路径"
say "  终端   : ${MODE}（${MODE_LABEL}）"
say "  技能   -> $SKILL_HOME"
say "  规则   -> ${RULES_DST}（源：${RULES_SRC}）"
say "  门禁   -> $ACS_SCRIPTS"
say "  模板   -> $ACS_TEMPLATES"
[ -z "$MODE_NOTE" ] || say "  注意   : $MODE_NOTE"

copy_one() {
  src="$1"; dst="$2"
  if [ ! -f "$src" ]; then fail "源文件不存在：$src"; return; fi
  if [ "$DRY" -eq 1 ]; then say "  [dry-run] $src -> $dst"; return; fi
  if [ -f "$dst" ]; then
    # 内容相同就无需做事；内容不同却不覆盖是真升级失败，不能静默 skip，
    # 否则旧版本会直接通过「文件存在」式的装后验证，得到一个假成功。
    if cmp -s "$src" "$dst"; then say "  same  已是最新：$dst"; return; fi
    if [ "$FORCE" -eq 0 ]; then
      fail "已存在且内容不同（旧版本）未覆盖：$dst —— 请加 --force 重跑以升级"
      return
    fi
  fi
  mkdir -p "$(dirname "$dst")"
  cp -f "$src" "$dst" && say "  ok    $dst" || fail "拷贝失败：$dst"
}

# ---- 第 3 步：拷贝 ----
say ""
say "[3/4] 拷贝文件"
SKILLS="universal-task-code loop-engineering graph-engineering self-verify-scaling token-thrift"
for name in $SKILLS; do
  src_dir="$SUITE/skills/$name"
  if [ ! -d "$src_dir" ]; then fail "缺少技能目录：$src_dir"; continue; fi
  for f in "$src_dir"/*; do
    [ -f "$f" ] || continue
    copy_one "$f" "$SKILL_HOME/$name/$(basename "$f")"
  done
done

# 规则落点由 spec/terminals.json 决定。唯一的例外语义：当源是 AGENTS.md 时，目标是一个
# 各终端共用的约定文件，很可能已被主人写过内容 —— 此时不覆盖，改为提示手工合并。
if [ "$RULES_SRC" = "AGENTS.md" ] && [ -f "$RULES_DST" ] && [ "$FORCE" -eq 0 ]; then
  say "  skip  已存在 ${RULES_DST}，请手工合并 $SUITE/AGENTS.md（用 --force 覆盖）"
  say "        注：这是唯一一处有意为之的跳过，可能留下一个旧版本。"
else
  copy_one "$SUITE/$RULES_SRC" "$RULES_DST"
fi

for f in _acs_common.py gate_state_validate.py gate_loop_guard.py gate_verify_rank.py \
         gate_reality_scan.py gate_checklist.py run_gates.py install_check.py acs_doctor.py; do
  copy_one "$SUITE/scripts/$f" "$ACS_SCRIPTS/$f"
done

for f in task-state.schema.json task-state.example.json verify-record.schema.json \
         verify-record.example.json handoff-summary.md; do
  copy_one "$SUITE/templates/$f" "$ACS_TEMPLATES/$f"
done

copy_one "$SUITE/spec/thresholds.json" "$ACS_SPEC/thresholds.json"
copy_one "$SUITE/spec/terminals.json" "$ACS_SPEC/terminals.json"

# Node 版门禁：给「有 Node 无 Python」的环境留一条硬强制路径（覆盖 4/5 道门，缺口显式声明）
copy_one "$SUITE/scripts/node/acs_gates.mjs" "$ACS_SCRIPTS/node/acs_gates.mjs"

copy_one "$SUITE/VERSION" "$ACS_DIR/VERSION"
copy_one "$SUITE/manifest.json" "$ACS_DIR/manifest.json"

# 外部强制点（L3）：默认不装。未经主人同意就往别人仓库塞 hook 属于接管，不是增强；
# 故只在显式 --with-hook 时安装，逃生口仍是 git 原生的 --no-verify。
if [ "$WITH_HOOK" -eq 1 ]; then
  GIT_HOOKS="$TARGET_FULL/.git/hooks"
  if [ ! -d "$TARGET_FULL/.git" ]; then
    fail "--with-hook 但目标不是 git 仓库：${TARGET_FULL}（无 .git 目录，挂不了 pre-commit）"
  else
    copy_one "$SUITE/hooks/pre-commit" "$GIT_HOOKS/pre-commit"
    if [ "$DRY" -eq 0 ] && [ -f "$GIT_HOOKS/pre-commit" ]; then
      # 没有可执行位的 hook 会被 git 直接忽略 —— 那是「装了但没生效」，比不装更危险。
      chmod +x "$GIT_HOOKS/pre-commit" || fail "chmod +x 失败：$GIT_HOOKS/pre-commit"
      [ -x "$GIT_HOOKS/pre-commit" ] || fail "hook 无可执行位，git 会忽略它：$GIT_HOOKS/pre-commit"
    fi
  fi
fi

# ---- 第 4 步：安装后生效验证 ----
say ""
say "[4/4] 安装后验证"
if [ "$DRY" -eq 1 ]; then
  say "  [dry-run] 跳过验证"
else
  for name in $SKILLS; do
    probe="$SKILL_HOME/$name/SKILL.md"
    if [ -f "$probe" ]; then say "  ok    技能可读：$probe"; else fail "技能未落盘：$probe"; fi
  done
  SAMPLE="$ACS_TEMPLATES/task-state.example.json"
  if [ "$RUNTIME" = "python" ]; then
    if [ -f "$ACS_SCRIPTS/run_gates.py" ] && [ -f "$SAMPLE" ]; then
      if "$PY" -X utf8 "$ACS_SCRIPTS/run_gates.py" --state "$SAMPLE" \
          --root "$ACS_SCRIPTS" --tier T2; then
        say "  ok    门禁在正样本上判定 PASS（exit=0），硬约束已生效"
      else
        fail "门禁在正样本上未通过，安装视为未生效"
      fi
    else
      fail "门禁或正样本缺失，无法验证生效"
    fi
  else
    if [ -f "$ACS_SCRIPTS/node/acs_gates.mjs" ] && [ -f "$SAMPLE" ]; then
      if "$NODE" "$ACS_SCRIPTS/node/acs_gates.mjs" run --state "$SAMPLE" --tier T2; then
        say "  ok    Node 门禁在正样本上判定 PASS（exit=0），硬约束已生效"
        say "  PARTIAL 覆盖 4/5 道门：reality_scan 需 Python AST，Node 侧不假装能跑。"
      else
        fail "Node 门禁在正样本上未通过，安装视为未生效"
      fi
    else
      fail "Node 门禁或正样本缺失，无法验证生效"
    fi
  fi
fi

say ""
if [ "$FAILS" -eq 0 ]; then
  say "结果：安装完成（0 项失败）"
  [ -z "$MODE_NOTE" ] || say "生效条件：$MODE_NOTE"
  say "开工前：cp $ACS_TEMPLATES/task-state.example.json $ACS_DIR/task-state.json 并按真实任务改写。"
  exit 0
else
  say "结果：安装失败（$FAILS 项失败），请修复后重跑"
  exit 1
fi
