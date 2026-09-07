#!/usr/bin/env python3
"""Handoff 压缩脚本：从 task-state.json 渲染 .acs/handoff.md（≤1000 字，目标锚点防跑偏）。

用法：
    python -X utf8 acs_handoff_compress.py --state .acs/task-state.json [--out .acs/handoff.md]

为什么需要它（v2.0 每轮压缩 + 保留目标防跑偏）：
    每个 step 结束写一份 ≤1000 字 handoff（current_goal/completed/next_step），下一步只读它、
    不重放历史对话。current_goal 是防跑偏锚点：长程任务每轮复述目标，下一步以此校准方向。
    无损压缩=只压过程与推理，不压结论/证据/当前目标。

可迁移性：本脚本是终端无关的纯 stdlib CLI，渲染的是终端无关的文本约定（.acs/handoff.md）。
    post-turn 自动触发依赖各终端 hook 机制（如 claude 的 SessionStart/UserPromptSubmit），
    未实测的事件集登记于 known_gaps，本脚本不声称全终端自动触发——它只负责「被调用时正确渲染」。

退出码：0=PASS（渲染成功且 ≤1000 字），1=BLOCK（缺段/超长/无 step），2=USAGE_ERROR
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _acs_common import Report, load_json, parse_args, spec_section, usage_exit  # noqa: E402

_HANDOFF = spec_section("handoff")
HO_MAX_CHARS = _HANDOFF["max_handoff_chars"]
HO_REQUIRED_SECTIONS = tuple(_HANDOFF["required_sections"])
HO_MIN_SECTION_CHARS = _HANDOFF["min_section_chars"]
DEFAULT_OUT = _HANDOFF["handoff_file"]


def render_handoff(steps):
    """取最后一个有 handoff 的 step，渲染成 markdown。返回 (text, errors)。"""
    errors = []
    latest = None
    for step in steps:
        if isinstance(step.get("handoff"), dict):
            latest = step
    if latest is None:
        return None, ["无任何 step 含 handoff：每步结束必须写目标/已完成/下一步交接"]
    ho = latest["handoff"]
    total = 0
    lines = ["# Handoff（≤%d 字，下一步只读本文件，不重放历史）" % HO_MAX_CHARS, ""]
    for sec in HO_REQUIRED_SECTIONS:
        val = ho.get(sec)
        if not isinstance(val, str) or len(val.strip()) < HO_MIN_SECTION_CHARS:
            errors.append("handoff 缺 %s 段或过短（≥%d 字）" % (sec, HO_MIN_SECTION_CHARS))
            val = val if isinstance(val, str) else ""
        total += len(val or "")
        label = {"current_goal": "当前目标（防跑偏锚点）",
                 "completed": "已完成项（无损保留结论/证据）",
                 "next_step": "下一步唯一动作"}.get(sec, sec)
        lines.append("## %s" % label)
        lines.append(val or "")
        lines.append("")
    if total > HO_MAX_CHARS:
        errors.append("handoff 三段合计 %d 字 > %d：压缩只压过程不压结论/证据/目标" % (total, HO_MAX_CHARS))
    lines.append("---")
    lines.append("step=%s 三段合计=%d 字（上限 %d）" % (latest.get("id"), total, HO_MAX_CHARS))
    return "\n".join(lines), errors


def main(argv):
    args = parse_args(argv, {"--state": "state", "--out": "out"}, ["state"])
    if not os.path.isfile(args["state"]):
        usage_exit("task-state 文件不存在：%s" % args["state"])
    state = load_json(args["state"], "task-state")
    steps = state.get("steps")
    if not isinstance(steps, list) or not steps:
        usage_exit("task-state 无 steps，无法渲染 handoff")

    text, errors = render_handoff(steps)
    report = Report("acs_handoff_compress (handoff 压缩渲染)")
    if text is None:
        for e in errors:
            report.error("handoff", e)
        return report.finish()

    out = args.get("out") or DEFAULT_OUT
    out_dir = os.path.dirname(out)
    if out_dir and not os.path.isdir(out_dir):
        os.makedirs(out_dir, exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        fh.write(text)
    report.note("已渲染 %s（%d 字符）" % (out, len(text)))
    for e in errors:
        report.error("handoff", e)
    return report.finish()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
