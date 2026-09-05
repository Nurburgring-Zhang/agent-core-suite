#!/usr/bin/env python3
"""G-Rule 规则体系自洽门禁：查条数漂移、承重短语丢失、副本不同步。

用法：
    python -X utf8 gate_rule_consistency.py --root . [--external path/to/SOUL.md ...]

为什么需要它：套件原有门禁全部检查「任务状态」，没有一条检查「规则体系自身」。
实测教训是 reference-creed.md 长期写「十二条」而终端全局守则写「十五条」，
388 项测试全绿也没人报错 —— 因为没有断言看着它。

两段式（来源 ponytail check-rule-copies.js）：
    1. 条数与条目标记 —— 可精确比对，缺一即 BLOCK；
    2. 承重短语不变量 —— 长文档无法逐字节比对，改断言关键短语必须逐字存活。

退出码：0=PASS，1=BLOCK，2=USAGE_ERROR（视为未验证=未完成）
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _acs_common import Report, load_json, parse_args, read_text, usage_exit  # noqa: E402

# 本门禁的真相源是 spec/rule-consistency.json（与 thresholds.json 分开：
# 一个管任务执行阈值，一个管规则体系自洽，混在一起会让两类改动互相牵连）。
_HERE = os.path.dirname(os.path.abspath(__file__))
_DEFAULT_RULE_SPEC = os.path.join(os.path.dirname(_HERE), "spec", "rule-consistency.json")
RULE_SPEC_NAME = os.path.join("spec", "rule-consistency.json")


def resolve_rule_spec(root):
    """真相源必须来自被检查的那棵树。

    环境变量优先（供 CI 显式指定）；否则取 --root 下的 spec。
    绝不回退到脚本所在安装目录 —— 否则检查副本时会读到本机真 spec，
    副本里 spec 被删/被改也照样 PASS（首版负样本 5 实测到这个静默放行）。
    """
    env = os.environ.get("ACS_RULE_SPEC_PATH")
    if env:
        return env
    return os.path.join(root, RULE_SPEC_NAME)


def load_rule_spec(root):
    path = resolve_rule_spec(root)
    data = load_json(path, "规则自洽真相源 %s" % RULE_SPEC_NAME)
    for key in ("spec_version", "canonical_creed", "count_declarations",
                "invariants", "cross_copies"):
        if key not in data:
            usage_exit("规则自洽真相源缺顶层字段 %s：%s" % (key, path))
    return data


def load_file(root, rel, report, label):
    path = os.path.join(root, rel.replace("/", os.sep))
    if not os.path.isfile(path):
        report.error(label, "声明的规则文件不存在：%s" % rel)
        return None
    text = read_text(path)
    if not text.strip():
        report.error(label, "规则文件为空：%s" % rel)
        return None
    return text


def check_canonical(root, report, spec):
    cfg = spec["canonical_creed"]
    text = load_file(root, cfg["path"], report, "canonical")
    if text is None:
        return
    count_word = cfg["count_word"]
    if count_word not in text:
        report.error("canonical", "守则原文未声明条数词 %s" % count_word)
    missing = [m for m in cfg["article_markers"] if m not in text]
    if missing:
        report.error("canonical", "守则原文缺少条目标记：%s" % ", ".join(missing))
    found = sum(1 for m in cfg["article_markers"] if m in text)
    if found != cfg["expected_article_count"]:
        report.error("canonical", "条目数实测 %d != 期望 %d" % (found, cfg["expected_article_count"]))


def check_count_declarations(root, report, spec):
    """条数声明必须同步。

    只匹配「守则+条数」的组合短语：守则原文自带「第十二条」「第十五条」等条目标记，
    用裸条数词会把正文条目误报成过时声明（首版实测踩到，故收紧为组合匹配）。
    """
    cfg = spec["count_declarations"]
    want = spec["canonical_creed"]["count_word"]
    for rel in cfg["files"]:
        text = load_file(root, rel, report, "count")
        if text is None:
            continue
        stale = [p for p in cfg["stale_phrases"] if want not in p and p in text]
        if stale:
            report.error("count:%s" % rel, "出现过时条数声明 %s（应为 %s）" % (", ".join(stale), want))


def check_invariants(root, report, spec):
    cfg = spec["invariants"]
    for rel in cfg["sources"]:
        text = load_file(root, rel, report, "invariant")
        if text is None:
            continue
        for phrase in cfg["phrases"]:
            if phrase not in text:
                report.error("invariant:%s" % rel, "承重短语丢失：%r" % phrase)


def check_cross_copies(root, report, externals, spec):
    for group in spec["cross_copies"]["groups"]:
        for rel in group["files"]:
            text = load_file(root, rel, report, "copy")
            if text is None:
                continue
            missing = [p for p in group["phrases"] if p not in text]
            if missing:
                report.error("copy:%s" % rel,
                             "[%s] 副本缺少共同承载短语：%s" % (group["id"], ", ".join(missing)))
    if not externals:
        report.note("未传 --external：本次只校验套件内副本，终端全局规则文件未纳入本轮证据")
        return
    want = spec["canonical_creed"]["count_word"]
    phrases = spec["invariants"]["phrases"]
    for path in externals:
        if not os.path.isfile(path):
            report.error("external", "外部规则文件不存在：%s" % path)
            continue
        text = read_text(path)
        if want not in text:
            report.error("external:%s" % os.path.basename(path),
                         "未声明条数词 %s，与守则原文漂移" % want)
        missing = [p for p in phrases if p not in text]
        if missing:
            report.error("external:%s" % os.path.basename(path),
                         "承重短语丢失：%s" % ", ".join(missing))


def main(argv):
    externals = []
    rest = []
    idx = 0
    while idx < len(argv):
        if argv[idx] == "--external":
            if idx + 1 >= len(argv):
                usage_exit("--external 需要一个文件路径")
            externals.append(argv[idx + 1])
            idx += 2
            continue
        rest.append(argv[idx])
        idx += 1

    args = parse_args(rest, {"--root": "root"}, ["root"])
    root = args["root"]
    if not os.path.isdir(root):
        usage_exit("root 不是目录：%s" % root)

    spec = load_rule_spec(root)
    report = Report("gate_rule_consistency (规则体系自洽)")
    report.note("root=%s spec=%s external=%d 个" % (root, resolve_rule_spec(root), len(externals)))
    check_canonical(root, report, spec)
    check_count_declarations(root, report, spec)
    check_invariants(root, report, spec)
    check_cross_copies(root, report, externals, spec)
    return report.finish()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
