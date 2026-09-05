"""硬门禁总编排：一条命令跑完全部 gate 并汇总。

用法：
    python -X utf8 run_gates.py --state .acs/task-state.json --root . [--tier T2]
                                [--record .acs/verify-record.json] [--whitelist path]
                                [--external ~/.../SOUL.md]
                                [--skip reality,verify]

退出码：0=全部 PASS；1=存在 BLOCK；2=USAGE_ERROR（视为未验证=未完成）
"""

import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _acs_common import EXIT_BLOCK, EXIT_PASS, EXIT_USAGE, norm_tier, parse_args, usage_exit  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_WHITELIST = os.path.join(".acs", "reality-whitelist.txt")

LABELS = {EXIT_PASS: "PASS", EXIT_BLOCK: "BLOCK", EXIT_USAGE: "USAGE_ERROR"}


def run(name, script, argv):
    cmd = [sys.executable, "-X", "utf8", os.path.join(HERE, script)] + argv
    sys.stdout.write("\n$ %s\n" % " ".join(cmd[3:]))
    sys.stdout.flush()
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    text = proc.stdout.decode("utf-8", "replace") if proc.stdout else ""
    sys.stdout.write(text)
    sys.stdout.flush()
    return (name, proc.returncode)


def main(argv):
    # --external 可重复出现（终端全局规则文件可能有多份），故先摘出来再走极简解析器。
    rule_external = []
    rest = []
    idx = 0
    while idx < len(argv):
        if argv[idx] == "--external":
            if idx + 1 >= len(argv):
                usage_exit("--external 需要一个文件路径")
            rule_external += ["--external", argv[idx + 1]]
            idx += 2
            continue
        rest.append(argv[idx])
        idx += 1

    spec = {
        "--state": "state", "--root": "root", "--tier": "tier",
        "--record": "record", "--whitelist": "whitelist", "--skip": "skip",
    }
    args = parse_args(rest, spec, ["state"])
    if not os.path.isfile(args["state"]):
        usage_exit("task-state 文件不存在：%s" % args["state"])
    root = args.get("root") or "."
    if not os.path.isdir(root):
        usage_exit("root 不是目录：%s" % root)
    tier = norm_tier(args.get("tier"), default="")
    skip = set(x.strip() for x in (args.get("skip") or "").split(",") if x.strip())

    tier_argv = ["--tier", tier] if tier else []
    results = []

    if "state" not in skip:
        results.append(run("state", "gate_state_validate.py", ["--state", args["state"]] + tier_argv))
    if "loop" not in skip:
        results.append(run("loop", "gate_loop_guard.py", ["--state", args["state"]] + tier_argv))
    if "checklist" not in skip:
        results.append(run("checklist", "gate_checklist.py", ["--state", args["state"]] + tier_argv))
    if "reality" not in skip:
        extra = []
        wl = args.get("whitelist") or os.path.join(root, DEFAULT_WHITELIST)
        if os.path.isfile(wl):
            extra = ["--whitelist", wl]
        results.append(run("reality", "gate_reality_scan.py", ["--root", root] + extra))
    if "honesty" not in skip:
        results.append(run("honesty", "gate_honesty_ledger.py", ["--state", args["state"]] + tier_argv))

    # 规则自洽与技能规范只在「被检查的树自带 spec」时参与：
    # 业务工作区通常没有这两份 spec，此时它们不适用，跳过并如实说明，而不是假装检查过。
    if "rule" not in skip:
        if os.path.isfile(os.path.join(root, "spec", "rule-consistency.json")):
            results.append(run("rule", "gate_rule_consistency.py", ["--root", root] + rule_external))
        else:
            sys.stdout.write("\n=== gate_rule_consistency ===\n"
                             "  note  %s 无 spec/rule-consistency.json，本树不含规则体系，跳过（非通过）\n" % root)
    if "skillspec" not in skip:
        if os.path.isfile(os.path.join(root, "spec", "skill-spec.json")):
            results.append(run("skillspec", "gate_skill_spec.py", ["--root", root]))
        else:
            sys.stdout.write("\n=== gate_skill_spec ===\n"
                             "  note  %s 无 spec/skill-spec.json，本树不含技能定义，跳过（非通过）\n" % root)

    record = args.get("record")
    if "verify" not in skip:
        if record and os.path.isfile(record):
            results.append(run("verify", "gate_verify_rank.py", ["--record", record] + tier_argv))
        elif tier == "T3":
            sys.stdout.write("\n=== gate_verify_rank ===\n")
            sys.stdout.write("  BLOCK [record] T3 必须提供 verify-record.json（多候选竞标证据缺失）\n")
            results.append(("verify", EXIT_BLOCK))
        else:
            sys.stdout.write("\n=== gate_verify_rank ===\n  note  未提供 --record，本级不强制，跳过。\n")

    sys.stdout.write("\n================ 门禁汇总 ================\n")
    width = max(len(n) for n, _ in results) if results else 8
    for name, code in results:
        sys.stdout.write("  %-*s  %s (exit=%d)\n" % (width, name, LABELS.get(code, "UNKNOWN"), code))

    codes = [c for _, c in results]
    if EXIT_USAGE in codes:
        final = EXIT_USAGE
        verdict = "USAGE_ERROR：有门禁未能真实执行，按未验证=未完成处理"
    elif EXIT_BLOCK in codes:
        final = EXIT_BLOCK
        verdict = "BLOCK：存在违规，禁止进入下一门"
    else:
        final = EXIT_PASS
        verdict = "PASS：全部门禁通过"
    sys.stdout.write("总判定：%s\n" % verdict)
    sys.stdout.flush()
    return final


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
