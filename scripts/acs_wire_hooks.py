"""把 ACS 两个 hook 保强、幂等地并入终端 settings.json 的 hooks 对象（零第三方依赖）。

设计对齐「真实 live 形状」（已只读核对 C:/Users/<user>/.qoderwork/settings.json）：
    settings.hooks 是顶层 dict，键为事件名（8 个），值为 matcher-group 列表：
        [{"matcher"?: str, "hooks": [{"type":"command","command": str, "timeout": int}]}, ...]
    Stop / UserPromptSubmit 的现存组不带 matcher（裸 {"hooks":[...]}），本脚本新增组沿用此形状。
    企业托管钩子（_yunke_managed ... hook_entry.exe）在每个事件上都并存，必须原样保留。

保强（PRESERVE-STRONG）三条硬线：
    1. 只「追加」新 matcher-group，绝不删除 / 重排 / 改写任何既存条目；
    2. 写回后 READ-BACK 再解析，逐事件校验「既有条目按原序原样在前缀」且「ACS 条目已在位」；
    3. 幂等：某事件已含 acs-stop.sh / acs-prompt.sh 命令（子串识别）则不重复添加，报「already wired」。

用法：
    python -X utf8 acs_wire_hooks.py --settings <path> --suite <path> \
        [--events Stop,UserPromptSubmit] [--timeout 5] [--dry-run]

退出码：0=成功/空操作/dry-run，1=读回校验失败，2=USAGE_ERROR。
"""

import copy
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _acs_common import usage_exit, EXIT_PASS, EXIT_BLOCK, EXIT_USAGE  # noqa: E402

# 事件 → ACS hook 文件名。Stop 用 acs-stop.sh（post-turn 刷新载体），
# UserPromptSubmit 用 acs-prompt.sh（提醒先读交接）。其余事件本脚本无对应 hook。
EVENT_HOOK = {
    "Stop": "acs-stop.sh",
    "UserPromptSubmit": "acs-prompt.sh",
}
DEFAULT_EVENTS = ["Stop", "UserPromptSubmit"]
DEFAULT_TIMEOUT = 5

_VALUE_FLAGS = {"--settings": "settings", "--suite": "suite", "--events": "events", "--timeout": "timeout"}
_BOOL_FLAGS = {"--dry-run": "dry_run"}

USAGE = """\
acs_wire_hooks.py —— 保强、幂等地把 ACS hook 并入 settings.json 的 hooks 对象

必需：
  --settings <path>  目标终端 settings.json（不存在时：--dry-run 可预览，实写则 USAGE_ERROR）
  --suite <path>     ACS 套件根目录（其 hooks/acs-stop.sh + hooks/acs-prompt.sh 将被绝对路径引用）
可选：
  --events <list>    逗号分隔，默认 Stop,UserPromptSubmit
  --timeout <int>    新增 hook 条目的 timeout，默认 5
  --dry-run          只打印将要发生的改动，不写任何文件
  -h, --help         本帮助

退出码：0=成功/空操作/dry-run  1=读回校验失败  2=USAGE_ERROR
"""


def _parse(argv):
    out = {}
    i = 0
    while i < len(argv):
        tok = argv[i]
        if tok in ("-h", "--help"):
            out["help"] = "1"
            i += 1
        elif tok in _BOOL_FLAGS:
            out[_BOOL_FLAGS[tok]] = "1"
            i += 1
        elif tok in _VALUE_FLAGS:
            if i + 1 >= len(argv):
                usage_exit("参数 %s 缺少取值" % tok)
            out[_VALUE_FLAGS[tok]] = argv[i + 1]
            i += 2
        else:
            usage_exit("未知参数：%s（可用：--settings --suite --events --timeout --dry-run -h/--help）" % tok)
    return out


def _fwd(path):
    """绝对路径 + 正斜杠归一（Windows Git Bash 会吃掉反斜杠，故统一成 /）。"""
    ap = os.path.abspath(path)
    return ap.replace("\\", "/")


def _build_command(suite_fwd, hook_name):
    return 'bash "%s/hooks/%s"' % (suite_fwd, hook_name)


def _iter_commands(groups):
    """遍历一个事件下所有 matcher-group 里的 hook 命令字符串。"""
    if not isinstance(groups, list):
        return
    for g in groups:
        if not isinstance(g, dict):
            continue
        for h in g.get("hooks") or []:
            if isinstance(h, dict) and isinstance(h.get("command"), str):
                yield h["command"]


def _acs_present(groups, hook_name):
    """该事件是否已接线此 ACS hook（子串识别 acs-stop.sh / acs-prompt.sh）。"""
    for cmd in _iter_commands(groups):
        if hook_name in cmd:
            return True
    return False


def main(argv):
    args = _parse(argv)
    if args.get("help"):
        sys.stdout.write(USAGE)
        sys.stdout.flush()
        return EXIT_PASS

    settings_path = args.get("settings")
    suite_path = args.get("suite")
    if not settings_path:
        usage_exit("缺少必需参数 --settings")
    if not suite_path:
        usage_exit("缺少必需参数 --suite")

    # --events 解析 + 合法性校验
    raw_events = args.get("events")
    if raw_events:
        events = [e.strip() for e in raw_events.split(",") if e.strip()]
    else:
        events = list(DEFAULT_EVENTS)
    if not events:
        usage_exit("--events 解析后为空")
    for ev in events:
        if ev not in EVENT_HOOK:
            usage_exit("事件 %r 无对应 ACS hook（本脚本仅支持 %s）" % (ev, "/".join(sorted(EVENT_HOOK))))

    # --timeout 解析
    try:
        timeout = int(args.get("timeout") or DEFAULT_TIMEOUT)
    except ValueError:
        usage_exit("--timeout 必须是整数：%r" % args.get("timeout"))

    dry = bool(args.get("dry_run"))
    suite_fwd = _fwd(suite_path)

    # 引用的 hook 文件必须真实存在，否则接线即 broken（dry-run 也不例外）
    for ev in events:
        hook_file = os.path.join(suite_fwd, "hooks", EVENT_HOOK[ev])
        if not os.path.isfile(hook_file):
            usage_exit("套件里找不到 hook 文件：%s（--suite 指错或套件不完整）" % hook_file)

    # 读 settings
    exists = os.path.isfile(settings_path)
    if not exists and not dry:
        usage_exit("settings.json 不存在：%s（实写要求文件已在；如需预览用 --dry-run）" % settings_path)
    if exists:
        try:
            with open(settings_path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except ValueError as exc:
            usage_exit("settings.json 不是合法 JSON：%s（%s）" % (settings_path, exc))
    else:
        data = {}
    if not isinstance(data, dict):
        usage_exit("settings.json 顶层不是对象，无法安全合并")

    hooks = data.get("hooks")
    created_skeleton = False
    if not isinstance(hooks, dict):
        hooks = {}
        created_skeleton = True
    data["hooks"] = hooks

    # 规划（先只算，不改）
    plan = []  # (event, before_count, after_count, action, cmd)
    originals = {}  # event -> 原始 group 列表（深拷贝，供读回前缀校验）
    to_add = []  # (event, group)
    for ev in events:
        existing = hooks.get(ev)
        if existing is None:
            existing = []
        if not isinstance(existing, list):
            usage_exit("settings.hooks.%s 不是列表，形状异常，拒绝合并（避免破坏既存钩子）" % ev)
        originals[ev] = copy.deepcopy(existing)
        before = len(existing)
        hook_name = EVENT_HOOK[ev]
        cmd = _build_command(suite_fwd, hook_name)
        if _acs_present(existing, hook_name):
            plan.append((ev, before, before, "already wired", cmd))
        else:
            group = {"hooks": [{"type": "command", "command": cmd, "timeout": timeout}]}
            plan.append((ev, before, before + 1, "ADD", cmd))
            to_add.append((ev, group))

    # 打印计划（dry-run 与实写都打，diff 式）
    sys.stdout.write("=== acs_wire_hooks ===\n")
    sys.stdout.write("settings=%s（%s）\n" % (settings_path, "存在" if exists else "不存在→将建骨架"))
    sys.stdout.write("suite=%s\n" % suite_fwd)
    if created_skeleton:
        sys.stdout.write("  + 顶层无 hooks，将创建 {\"hooks\":{}} 骨架\n")
    for ev, before, after, action, cmd in plan:
        mark = "已接线，跳过" if action == "already wired" else "追加新组"
        sys.stdout.write("  [%s] %s：条目 %d → %d（%s）\n" % (ev, action, before, after, mark))
        sys.stdout.write("        command: %s\n" % cmd)

    if dry:
        sys.stdout.write("--dry-run：以上为将发生的改动，未写任何文件。\n")
        sys.stdout.flush()
        return EXIT_PASS

    if not to_add and not created_skeleton:
        sys.stdout.write("无改动（全部事件已接线）：幂等空操作。\n")
        sys.stdout.flush()
        return EXIT_PASS

    # 执行追加（只在末尾 append，既存条目一字不动）
    for ev, group in to_add:
        hooks.setdefault(ev, []).append(group)

    # 原子写：同目录临时文件 → os.replace 覆盖目标
    target_abs = os.path.abspath(settings_path)
    target_dir = os.path.dirname(target_abs) or "."
    if not os.path.isdir(target_dir):
        os.makedirs(target_dir)
    fd, tmp_path = tempfile.mkstemp(prefix=".acs_wire_", suffix=".json", dir=target_dir)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps(data, ensure_ascii=False, indent=2))
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_path, target_abs)
    except Exception:
        # 写失败要清掉临时文件，不留垃圾；然后如实报错退出
        try:
            if os.path.isfile(tmp_path):
                os.remove(tmp_path)
        except OSError:
            pass
        raise

    # 读回校验：ACS 条目在位 + 既有条目按原序原样保留在前缀（永不减少）
    try:
        with open(target_abs, "r", encoding="utf-8") as fh:
            back = json.load(fh)
    except ValueError as exc:
        sys.stdout.write("读回校验失败：写回后不是合法 JSON（%s）\n" % exc)
        sys.stdout.flush()
        return EXIT_BLOCK

    back_hooks = back.get("hooks") if isinstance(back.get("hooks"), dict) else {}
    ok = True
    for ev in events:
        nl = back_hooks.get(ev)
        ol = originals[ev]
        ev_ok = True
        if not isinstance(nl, list):
            sys.stdout.write("读回校验失败：[%s] 写回后不是列表\n" % ev)
            ev_ok = False
        else:
            if len(nl) < len(ol):
                sys.stdout.write("读回校验失败：[%s] 既有条目减少 %d → %d（保强被破坏）\n" % (ev, len(ol), len(nl)))
                ev_ok = False
            if nl[:len(ol)] != ol:
                sys.stdout.write("读回校验失败：[%s] 既有条目被改写或重排（前缀不一致）\n" % ev)
                ev_ok = False
            if not _acs_present(nl, EVENT_HOOK[ev]):
                sys.stdout.write("读回校验失败：[%s] 写回后找不到 ACS 条目 %s\n" % (ev, EVENT_HOOK[ev]))
                ev_ok = False
            if ev_ok:
                sys.stdout.write("  [%s] 读回 OK：既存 %d 条原样保留，现共 %d 条，ACS(%s) 在位\n"
                                 % (ev, len(ol), len(nl), EVENT_HOOK[ev]))
        if not ev_ok:
            ok = False

    if not ok:
        sys.stdout.write("结果：BLOCK（读回校验未过，请检查 %s）\n" % target_abs)
        sys.stdout.flush()
        return EXIT_BLOCK

    sys.stdout.write("结果：PASS（保强合并完成，既有条目零删改，ACS 条目已就位）\n")
    sys.stdout.flush()
    return EXIT_PASS


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
