#!/usr/bin/env python3
"""G-Skill 技能规范门禁：按官方 Agent Skills 规范校验 SKILL.md。

用法：
    python -X utf8 gate_skill_spec.py --root . [--skills-dir skills]

规则来源：anthropics/skills 的 skill-creator/scripts/quick_validate.py
（frontmatter 键白名单、name kebab-case、description 长度与禁用字符、正文行数上限），
外加本套件补充的两条：description 必须写触发条件、必须写 SKIP/排除边界。

为什么补后两条：官方明确 description 是唯一触发机制，且模型倾向欠触发；
只写「何时用」不写「何时不用」会让入口技能过触发，把 T0 闲聊也拖进重流水线。

退出码：0=PASS，1=BLOCK，2=USAGE_ERROR（视为未验证=未完成）
"""

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _acs_common import Report, load_json, parse_args, read_text, usage_exit  # noqa: E402

SPEC_NAME = os.path.join("spec", "skill-spec.json")


def resolve_spec(root):
    env = os.environ.get("ACS_SKILL_SPEC_PATH")
    if env:
        return env
    return os.path.join(root, SPEC_NAME)


def load_spec(root):
    path = resolve_spec(root)
    data = load_json(path, "技能规范真相源 %s" % SPEC_NAME)
    for key in ("spec_version", "frontmatter", "description", "body", "entry_skills"):
        if key not in data:
            usage_exit("技能规范真相源缺顶层字段 %s：%s" % (key, path))
    return data


def split_frontmatter(text):
    """返回 (frontmatter 原文, 正文)；没有合法 frontmatter 时 frontmatter 为 None。"""
    if not text.startswith("---"):
        return None, text
    parts = text.split("---", 2)
    if len(parts) < 3:
        return None, text
    return parts[1], parts[2]


def parse_keys(front):
    """只取顶层键名。故意不引 YAML 库：本套件零第三方依赖。"""
    keys = []
    for line in front.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if line[:1] in (" ", "\t", "-"):
            continue
        match = re.match(r"^([A-Za-z0-9_-]+)\s*:", line)
        if match:
            keys.append(match.group(1))
    return keys


def field_value(front, name):
    match = re.search(r"^%s\s*:\s*(.*)$" % re.escape(name), front, re.MULTILINE)
    if not match:
        return None
    return match.group(1).strip().strip("'\"")


def check_skill(rel, text, spec, report, is_entry):
    front, body = split_frontmatter(text)
    where = rel
    if front is None:
        report.error(where, "缺少 --- 包裹的 YAML frontmatter，技能无法被发现")
        return

    fm = spec["frontmatter"]
    keys = parse_keys(front)
    unknown = [k for k in keys if k not in fm["allowed_keys"]]
    if unknown:
        report.error(where, "frontmatter 含非白名单键：%s（允许：%s）"
                     % (", ".join(unknown), ", ".join(fm["allowed_keys"])))
    for req in fm["required_keys"]:
        if req not in keys:
            report.error(where, "frontmatter 缺必填键 %s" % req)

    name = field_value(front, "name")
    if name is not None:
        if len(name) > fm["max_name_chars"]:
            report.error(where, "name 长度 %d > %d" % (len(name), fm["max_name_chars"]))
        if not re.fullmatch(fm["name_pattern"], name):
            report.error(where, "name %r 不符合 kebab-case 规范" % name)
        expect = os.path.basename(os.path.dirname(rel))
        if expect and name != expect:
            report.error(where, "name %r 与目录名 %r 不一致，会导致调用名错配" % (name, expect))

    desc = field_value(front, "description")
    dcfg = spec["description"]
    if desc is not None:
        if len(desc) > dcfg["max_chars"]:
            report.error(where, "description 长度 %d > %d" % (len(desc), dcfg["max_chars"]))
        if len(desc) < dcfg["min_chars"]:
            report.error(where, "description 长度 %d < %d，触发信息不足" % (len(desc), dcfg["min_chars"]))
        for ch in dcfg["forbidden_chars"]:
            if ch in desc:
                report.error(where, "description 含禁用字符 %r" % ch)
        if not any(kw in desc for kw in dcfg["trigger_keywords"]):
            report.error(where, "description 未写触发条件（需含任一：%s）"
                         % ", ".join(dcfg["trigger_keywords"]))
        if is_entry and not any(kw in desc for kw in dcfg["skip_keywords"]):
            report.error(where, "入口技能 description 未写 SKIP/排除边界（需含任一：%s）"
                         % ", ".join(dcfg["skip_keywords"]))

    bcfg = spec["body"]
    lines = body.strip().splitlines()
    if len(lines) > bcfg["max_lines"]:
        report.error(where, "正文 %d 行 > %d，应把细节下沉到 reference 文件"
                     % (len(lines), bcfg["max_lines"]))
    if not lines:
        report.error(where, "正文为空")

    # 引用的本地文件必须真实存在：断链等于「维护没发生」。
    base = os.path.dirname(os.path.join(spec["_root"], rel))
    for target in re.findall(r"\[[^\]]*\]\(([^)]+)\)", body):
        if target.startswith(("http://", "https://", "mailto:", "#")):
            continue
        clean = target.split("#")[0].strip()
        if clean and not os.path.exists(os.path.join(base, clean)):
            report.error(where, "引用的本地文件不存在：%s" % target)


def main(argv):
    args = parse_args(argv, {"--root": "root", "--skills-dir": "skills_dir"}, ["root"])
    root = args["root"]
    if not os.path.isdir(root):
        usage_exit("root 不是目录：%s" % root)
    spec = load_spec(root)
    spec["_root"] = root
    skills_dir = os.path.join(root, args.get("skills_dir") or spec.get("skills_dir", "skills"))
    if not os.path.isdir(skills_dir):
        usage_exit("技能目录不存在：%s" % skills_dir)

    report = Report("gate_skill_spec (技能规范)")
    entries = set(spec["entry_skills"])
    found = 0
    for name in sorted(os.listdir(skills_dir)):
        skill_md = os.path.join(skills_dir, name, "SKILL.md")
        if not os.path.isfile(skill_md):
            continue
        found += 1
        rel = os.path.relpath(skill_md, root).replace("\\", "/")
        check_skill(rel, read_text(skill_md), spec, report, name in entries)
    report.note("skills_dir=%s 已校验 %d 个技能" % (skills_dir, found))
    if found == 0:
        report.error("skills", "未找到任何 SKILL.md，校验对象为空即无法证明合规")
    missing_entry = [e for e in entries
                     if not os.path.isfile(os.path.join(skills_dir, e, "SKILL.md"))]
    if missing_entry:
        report.error("skills", "spec 声明的入口技能缺失：%s" % ", ".join(missing_entry))
    return report.finish()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
