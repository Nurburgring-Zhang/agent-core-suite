#!/usr/bin/env python3
"""Bootstrap 注入层：把常驻规则按各终端约定的格式吐出来，供 hook 或人工注入。

用法：
    python -X utf8 acs_bootstrap.py --root . [--profile qwenworkcn] [--format text|json]
    python -X utf8 acs_bootstrap.py --root . --verify          # 只自检可注入性，不输出正文

为什么需要它（来源：superpowers docs/porting-to-a-new-harness.md）：
    「The bootstrap is the entire integration」—— 没有注入，技能就是磁盘上的死文件。
    本套件原先只靠终端被动加载 AGENTS.md，没有 hook 保底，也没有任何断言能证明
    「规则真的到了模型面前」。本脚本补的正是这一环。

它只做一件事：读规则、拼注入载荷、按目标终端的 JSON 形状输出。
它不写文件、不改配置、不调模型 —— 注入动作由调用方（hook / CI / 人工）执行。

退出码：0=PASS，1=BLOCK（规则缺失或为空，无法注入），2=USAGE_ERROR
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _acs_common import EXIT_BLOCK, EXIT_PASS, load_json, parse_args, read_text, usage_exit  # noqa: E402

ADAPTERS_NAME = os.path.join("spec", "adapters.json")
BANNER = "EXTREMELY_IMPORTANT_AGENT_CORE_SUITE"


def load_adapters(root):
    path = os.environ.get("ACS_ADAPTERS_PATH") or os.path.join(root, ADAPTERS_NAME)
    data = load_json(path, "适配层真相源 %s" % ADAPTERS_NAME)
    for key in ("portable_core", "capability_adapters", "enforcement_honesty"):
        if key not in data:
            usage_exit("适配层真相源缺顶层字段 %s：%s" % (key, path))
    return data


def collect_rules(root, adapters):
    """收集常驻规则正文。任一必需规则缺失/为空即不可注入。"""
    payload = []
    missing = []
    for rel in adapters["portable_core"]["rules"]:
        path = os.path.join(root, rel.replace("/", os.sep))
        if not os.path.isfile(path):
            missing.append(rel)
            continue
        text = read_text(path).strip()
        if not text:
            missing.append(rel + "（空文件）")
            continue
        payload.append((rel, text))
    return payload, missing


def detect_enforcement(adapters):
    """如实判定本机强制力等级：能跑什么就说什么。"""
    import shutil

    if shutil.which("python") or shutil.which("python3") or sys.executable:
        return "full", adapters["enforcement_honesty"]["levels"]["full"]
    if shutil.which("node"):
        return "partial", adapters["enforcement_honesty"]["levels"]["partial"]
    return "soft_only", adapters["enforcement_honesty"]["levels"]["soft_only"]


def build_payload(root, adapters, profile_id):
    profiles = adapters["capability_adapters"]["profiles"]
    if profile_id not in profiles:
        usage_exit("未知 profile：%s（可用：%s）" % (profile_id, ", ".join(sorted(profiles))))
    profile = profiles[profile_id]
    rules, missing = collect_rules(root, adapters)
    level, level_note = detect_enforcement(adapters)

    # 能力缺口如实列出：缺什么就按 fallback 走，并要求在交付报告写明降级方式。
    gaps = []
    for cap in adapters["capability_adapters"]["required_capabilities"]:
        if not profile.get(cap["id"]):
            gaps.append({"capability": cap["id"], "purpose": cap["purpose"], "fallback": cap["fallback"]})

    body = ["<%s>" % BANNER,
            "以下为常驻工作标准，适用于本会话的每一个非闲聊任务。",
            "本机强制力等级：%s —— %s" % (level, level_note)]
    if gaps:
        body.append("本终端能力缺口（按 fallback 执行，并须在交付报告写明降级方式）：")
        for gap in gaps:
            body.append("  - %s（%s）→ %s" % (gap["capability"], gap["purpose"], gap["fallback"]))
    for rel, text in rules:
        body.append("")
        body.append("----- %s -----" % rel)
        body.append(text)
    body.append("</%s>" % BANNER)
    return {
        "profile": profile_id,
        "label": profile.get("label", profile_id),
        "enforcement_level": level,
        "enforcement_note": level_note,
        "capability_gaps": gaps,
        "missing_rules": missing,
        "rule_files": [rel for rel, _ in rules],
        "context": "\n".join(body),
    }


def emit(payload, fmt, profile_id):
    """按目标终端的 JSON 形状输出。

    不同终端读不同字段名（这是移植时最容易错的一步，故显式分支而非猜测）：
      claude  → hookSpecificOutput.additionalContext
      cursor  → additional_context
      其它/SDK → additionalContext
    """
    if fmt == "text":
        sys.stdout.write(payload["context"] + "\n")
        return
    if profile_id == "claude":
        shaped = {"hookSpecificOutput": {"hookEventName": "SessionStart",
                                         "additionalContext": payload["context"]}}
    elif profile_id == "cursor":
        shaped = {"additional_context": payload["context"]}
    else:
        shaped = {"additionalContext": payload["context"]}
    shaped["_acs"] = {k: payload[k] for k in
                      ("profile", "enforcement_level", "capability_gaps", "rule_files")}
    sys.stdout.write(json.dumps(shaped, ensure_ascii=False) + "\n")


def main(argv):
    verify_only = "--verify" in argv
    rest = [a for a in argv if a != "--verify"]
    args = parse_args(rest, {"--root": "root", "--profile": "profile", "--format": "format"}, ["root"])
    root = args["root"]
    if not os.path.isdir(root):
        usage_exit("root 不是目录：%s" % root)
    fmt = (args.get("format") or "json").strip()
    if fmt not in ("text", "json"):
        usage_exit("--format 只支持 text 或 json")

    adapters = load_adapters(root)
    profile_id = (args.get("profile") or os.environ.get("ACS_PROFILE") or "generic").strip()
    payload = build_payload(root, adapters, profile_id)

    if payload["missing_rules"]:
        sys.stderr.write("=== acs_bootstrap ===\n")
        for rel in payload["missing_rules"]:
            sys.stderr.write("  BLOCK 常驻规则缺失，无法注入：%s\n" % rel)
        sys.stderr.write("结果：BLOCK（规则不可注入 = 增强层未生效）\n")
        return EXIT_BLOCK

    if verify_only:
        sys.stdout.write("=== acs_bootstrap --verify ===\n")
        sys.stdout.write("  note  profile=%s(%s) 强制力=%s\n"
                         % (payload["profile"], payload["label"], payload["enforcement_level"]))
        sys.stdout.write("  note  可注入规则 %d 份，载荷 %d 字符\n"
                         % (len(payload["rule_files"]), len(payload["context"])))
        for gap in payload["capability_gaps"]:
            sys.stdout.write("  note  能力缺口 %s → fallback：%s\n" % (gap["capability"], gap["fallback"]))
        sys.stdout.write("结果：PASS（规则可注入；注入动作由 hook/CI/人工执行，本脚本不写文件）\n")
        return EXIT_PASS

    emit(payload, fmt, profile_id)
    return EXIT_PASS


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
