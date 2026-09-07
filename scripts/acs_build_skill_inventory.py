#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""技能清单构建器：把多终端技能库分类，只 verbatim 打包真·可迁移纯文档技能。

为什么要这个脚本（实测事实驱动，运行时自己重新计量，不把结论固化进源码）：
  本机三个 agent 终端各带 ~108-112 个技能，重叠严重。以 .qoderwork/skills 实测为例：
  110 个技能目录 / 12223 个文件 / 190.0 MB，其中只有 14 个是纯 .md（0.31 MB），
  96 个 env-bound（189.7 MB，光 qoderwork-ppt 的 node_modules 就 124 MB / 10757 文件）。
  纯 .md 也不等于可迁移：qoderwork-guidance 引 qw_query/qw_action、tinyfish-web-automation
  引 tinyfish MCP、team-monthly-assessment 引 dingtalk/dws —— prose 里就绑死了运行时。
  另有一批阿里内部专有技能（recruit/qingbaoju/price360 等），永不 verbatim 分发。

约束（决定一切）：ACS 的 pack.py 只打 manifest.files 里逐个列出的文件（无 glob/目录），
  且拒绝任何清单外 zip 条目。所以不能把 12223 文件塞进去。诚实模型：只 verbatim 打包
  「纯 .md + prose 不绑运行时 + 非 ACS 自有 + 非内部专有」的小集合（<~150 KB），
  其余全部只做清单化（分类 + 安装/fallback 指引），不带任何 blob/二进制。

产物：
  spec/skill-inventory.json —— 全部技能的权威分类（本脚本 --out）。
  bundled-skills/<name>/    —— 仅 portable_doc 技能的 .md 逐字节副本（本脚本 --bundle-dir）。

退出码：0=成功，2=USAGE_ERROR（参数/路径非法，视为未完成）。
"""

import hashlib
import io
import json
import os
import re
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _acs_common import usage_exit, read_text, EXIT_PASS, EXIT_USAGE  # noqa: E402

# 套件根 = scripts/ 的父目录（所有默认相对路径都锚在这里，不锚当前工作目录）
_HERE = os.path.dirname(os.path.abspath(__file__))
SUITE_ROOT = os.path.dirname(_HERE)

GENERATED_BY = "scripts/acs_build_skill_inventory.py"
SPEC_VERSION = "2.1.0"

# 三个终端技能库默认扫描路径（只用真实存在的；forward-slash 便于跨工具读取）
DEFAULT_SKILLS_DIRS = [
    "C:/Users/Nurburgring/.qoderwork/skills",
    "C:/Users/Nurburgring/.qwenworkcn/skills",
    "C:/Users/Nurburgring/.qoder/skills",
]

# ACS 自有技能：已在套件内，不再重复 verbatim 打包（默认这 5 个）
DEFAULT_ACS_SKILLS = [
    "graph-engineering",
    "loop-engineering",
    "self-verify-scaling",
    "token-thrift",
    "universal-task-code",
]

# 运行时绑定信号：.md-only 但 prose 命中任一 → 仍不可迁移，降级 env_bound。
# 逐条 re.escape + IGNORECASE 做子串匹配（信号本身即字面量，不当正则元字符解释）。
# 除任务给定的样例信号外，补三个终端自有运行时前缀（实测证据）：
#   qwenwork_*（media-generation 引 qwenwork_image_generate、qw-pages-supabase 引 qwenwork_pages_*）、
#   qoder_* / ~/.qoder（qoder-native-integration 引 qoder_cron、mcp_list/get/call 三件套）、
#   mcp_list/mcp_get/mcp_call（MCP 惰性加载三件套，注意 mcp__ 双下划线匹配不到它们）。
# 判断口径：宁可多降级（=不打包，安全侧），也绝不把绑运行时的技能当可迁移 verbatim 分发。
RUNTIME_DEP_SIGNALS = [
    "mcp__", "qw_query", "qw_action", "qw_mcp", "tinyfish", "dingtalk", "钉钉",
    "dws", "odps", "aliyun", "阿里", "dataworks", "quickbi", "smartq", "sunfire",
    "aone", "bpms", "采蜜库", "computer_use", "browser_session",
    "qwenwork", "qoder", "mcp_list", "mcp_get", "mcp_call",
]

# 内部专有技能名单：按名字子串/精确匹配命中即 internal_proprietary，永不打包。
# 判断口径：宁可把边界技能误判为内部（=不打包，安全侧），也绝不 verbatim 分发内部资产。
# 故意不用裸 "internal"/"data"/"ata" 等短词做子串，避免误伤 deep-research-internal /
# analytics-data-analysis 之类（它们改由 non-md 或 dep 信号判为 env_bound）。
INTERNAL_NAME_SUBSTR = [
    "recruit", "resume-search", "internal-background-check", "qingbaoju", "price360",
    "talent-evaluation", "team-monthly-assessment", "okr", "it-asset", "apply-vacation",
    "travel", "aliren", "ata-all", "ei-internal", "fbi-skill", "lineage-analysis",
    "sls-query", "odps-skill", "aliyun-odps", "quickbi", "smartq",
    "organizational-normative", "internal-comms", "business-value-chain", "dws",
    "dingtalk", "material-search", "yoho-yuque", "techmigo", "query-my-packet",
    "daily-report", "weekly-report",
]
INTERNAL_NAME_EXACT = ["a1"]

# 体积/文件数统计时剪枝的 vendored 目录：这些是依赖树而非文档，进去既慢又无意义。
VENDORED_DIRS = {"node_modules", ".git", "venv", "__pycache__", "dist", "build"}

# 分类优先级：junk > already_in_acs > internal_proprietary > env_bound > portable_doc
PRECEDENCE = ["junk", "already_in_acs", "internal_proprietary", "env_bound", "portable_doc"]

INSTALL_GUIDANCE_PORTABLE = "已 verbatim 打包于 bundled-skills/{name}/，任意终端拷入其 skills 目录即用"
INSTALL_GUIDANCE_BOUND = "运行时/内部依赖，不打包；需在源终端按其 SKILL.md 的依赖说明安装，或走该能力 fallback"


# --------------------------------------------------------------------------
# 参数解析：_acs_common.parse_args 不支持可重复项与布尔 flag，这里写一个同等严格的小解析器。
# --------------------------------------------------------------------------

def parse_cli(argv):
    opts = {
        "skills_dirs": [],       # 可重复
        "acs_skills": [],        # 可重复
        "out": None,
        "bundle_dir": None,
        "dry_run": False,
        "no_copy": False,
        "help": False,
    }
    value_flags = {"--skills-dir": "skills_dirs", "--acs-skills": "acs_skills",
                   "--out": "out", "--bundle-dir": "bundle_dir"}
    bool_flags = {"--dry-run": "dry_run", "--no-copy": "no_copy",
                  "-h": "help", "--help": "help"}
    i = 0
    while i < len(argv):
        tok = argv[i]
        if tok in bool_flags:
            opts[bool_flags[tok]] = True
            i += 1
        elif tok in value_flags:
            if i + 1 >= len(argv) or argv[i + 1].startswith("--"):
                usage_exit("参数 %s 缺少取值" % tok)
            key = value_flags[tok]
            if isinstance(opts[key], list):
                opts[key].append(argv[i + 1])
            else:
                opts[key] = argv[i + 1]
            i += 2
        else:
            usage_exit("未知参数：%s（可用：%s）"
                       % (tok, " ".join(sorted(list(value_flags) + list(bool_flags)))))
    return opts


def usage_text():
    return (
        "用法：python -X utf8 scripts/acs_build_skill_inventory.py\n"
        "  --skills-dir <path>   可重复；省略则默认扫三个终端技能库（只取存在者）\n"
        "  --out <path>          清单 JSON 输出，默认 spec/skill-inventory.json\n"
        "  --bundle-dir <path>   verbatim 打包目录，默认 bundled-skills/\n"
        "  --acs-skills <name>   可重复；标记 already_in_acs，默认 5 个 ACS 自有技能\n"
        "  --dry-run             只算并打印计划与将写/将拷的确切文件清单，不落任何盘（exit 0）\n"
        "  --no-copy             写清单 JSON 但不拷 bundled-skills\n"
    )


def sha256_bytes(data):
    h = hashlib.sha256()
    h.update(data)
    return h.hexdigest()


def to_fwd(path):
    """统一成正斜杠，便于跨工具消费与 byte-stable 输出。"""
    return path.replace("\\", "/")


# --------------------------------------------------------------------------
# 单终端单技能扫描：剪枝 vendored，短路 has_non_md，累计近似体积与 md 清单。
# --------------------------------------------------------------------------

def scan_skill_dir(skill_dir):
    """返回一次扫描结果 dict。file_count/size_bytes 均排除 vendored（近似），
    size_excludes_vendored 标记是否发生过剪枝；junk 用未剪枝快速探测确认零文件。"""
    file_count = 0
    size_bytes = 0
    has_non_md = False
    saw_vendored = False
    md_files = []          # 相对 skill_dir 的正斜杠路径
    md_abs = []            # 对应绝对路径（供打包/信号读取）

    for cur_root, dirs, files in os.walk(skill_dir):
        # 剪枝：不下降进依赖树（既为速度，也为把 vendored 排除出体积/计数）
        pruned = [d for d in dirs if d in VENDORED_DIRS]
        if pruned:
            saw_vendored = True
            # 存在 vendored 依赖树本身即证明该技能非纯文档 → 直接判 non-md，无需进去逐个看
            has_non_md = True
        dirs[:] = [d for d in dirs if d not in VENDORED_DIRS]

        for fn in files:
            abs_p = os.path.join(cur_root, fn)
            try:
                sz = os.path.getsize(abs_p)
            except OSError:
                sz = 0
            file_count += 1
            size_bytes += sz
            ext = os.path.splitext(fn)[1].lower()
            if ext != ".md":
                has_non_md = True   # 短路语义：命中即置真，后续无需再判
            else:
                rel = to_fwd(os.path.relpath(abs_p, skill_dir))
                md_files.append(rel)
                md_abs.append(abs_p)

    # junk 确认：剪枝后零文件且无 vendored → 再用未剪枝快扫确认是否真的一个文件都没有
    is_junk = False
    if file_count == 0 and not saw_vendored:
        for cur_root, _dirs, files in os.walk(skill_dir):
            if files:
                break
        else:
            is_junk = True

    # 按 (rel, abs) 成对排序，保证 md_files 与 md_abs 顺序严格对齐（确定性）
    paired = sorted(zip(md_files, md_abs))
    md_files_sorted = [p[0] for p in paired]
    md_abs_sorted = [p[1] for p in paired]

    return {
        "file_count": file_count,
        "size_bytes": size_bytes,
        "has_non_md": has_non_md,
        "size_excludes_vendored": saw_vendored,
        "md_files": md_files_sorted,
        "md_abs": md_abs_sorted,
        "is_junk": is_junk,
    }


def detect_dep_signals(md_abs_paths):
    """把技能所有 .md 文本拼起来，逐信号 IGNORECASE 子串匹配，返回命中的信号（排序）。"""
    if not md_abs_paths:
        return []
    chunks = []
    for p in md_abs_paths:
        try:
            chunks.append(read_text(p))
        except OSError:
            continue
    blob = "\n".join(chunks)
    blob_lower = blob.lower()
    hits = []
    for sig in RUNTIME_DEP_SIGNALS:
        if sig.lower() in blob_lower:
            hits.append(sig)
    return sorted(hits)


def is_internal_name(name):
    low = name.lower()
    if low in INTERNAL_NAME_EXACT:
        return True
    for tok in INTERNAL_NAME_SUBSTR:
        if tok in low:
            return True
    return False


# --------------------------------------------------------------------------
# 合并多终端出现：同名技能合成一条记录，source_terminals 列全部来源。
# --------------------------------------------------------------------------

def merge_occurrences(name, occurrences):
    """occurrences: list of (terminal_path, scan_dict)。返回合并后的记录字段。
    代表终端取 file_count 最大者（并列按终端路径排序），打包/计数以它为准；
    has_non_md 取任一为真，dep_signals 取并集（更保守：任一终端绑运行时即视为绑）。"""
    terminals = sorted([t for t, _ in occurrences])
    # 代表出现：(-file_count, terminal) 排序取首个 → 文件最多、终端路径字典序最小
    rep = sorted(occurrences, key=lambda kv: (-kv[1]["file_count"], kv[0]))[0]
    rep_term, rep_scan = rep

    has_non_md = any(s["has_non_md"] for _, s in occurrences)
    size_excludes_vendored = any(s["size_excludes_vendored"] for _, s in occurrences)
    # junk：所有终端出现都为 junk 才算 junk（任一终端有实体内容即非 junk）
    is_junk = all(s["is_junk"] for _, s in occurrences)

    # dep_signals 并集：跨所有终端的 md 文本一起测（保守）
    all_md_abs = []
    for _, s in occurrences:
        all_md_abs.extend(s["md_abs"])
    dep_signals = detect_dep_signals(all_md_abs)

    return {
        "name": name,
        "source_terminals": terminals,
        "rep_terminal": rep_term,
        "rep_dir": os.path.join(rep_term, name),
        "file_count": rep_scan["file_count"],
        "size_bytes": rep_scan["size_bytes"],
        "size_excludes_vendored": size_excludes_vendored,
        "has_non_md": has_non_md,
        "dep_signals": dep_signals,
        "md_files": rep_scan["md_files"],
        "md_abs": rep_scan["md_abs"],
        "is_junk": is_junk,
    }


def classify(merged, acs_set):
    """按优先级给出 primary 分类 + also 命中的其它分类。"""
    name = merged["name"]
    applies = {}
    applies["junk"] = bool(merged["is_junk"])
    applies["already_in_acs"] = name in acs_set
    applies["internal_proprietary"] = is_internal_name(name)
    applies["env_bound"] = bool(merged["has_non_md"]) or bool(merged["dep_signals"])
    applies["portable_doc"] = (
        not merged["is_junk"]
        and name not in acs_set
        and not is_internal_name(name)
        and not merged["has_non_md"]
        and not merged["dep_signals"]
    )
    primary = None
    for cls in PRECEDENCE:
        if applies[cls]:
            primary = cls
            break
    if primary is None:
        # 理论不可达：非 junk 必落入 env_bound 或 portable_doc 之一；兜底 env_bound（安全侧）
        primary = "env_bound"
    also = [c for c in PRECEDENCE if c != primary and applies[c]]
    return primary, also


def build_record(merged, acs_set, bundle_dir_rel):
    primary, also = classify(merged, acs_set)
    name = merged["name"]

    if primary == "portable_doc":
        bundle_path = "%s/%s" % (bundle_dir_rel.rstrip("/"), name)
        install = INSTALL_GUIDANCE_PORTABLE.format(name=name)
        fallback = "纯文档技能，拷入即用；若目标终端已有同名技能，以终端自带版本为准，勿覆盖其运行时。"
    elif primary == "already_in_acs":
        bundle_path = None
        install = "ACS 已自带该技能（套件 skills/ 内），无需从终端再打包。"
        fallback = "随 ACS 安装即生效；不与终端同名技能重复分发。"
    elif primary == "junk":
        bundle_path = None
        install = "空目录/零文件（如 .temp 垃圾），不打包。"
        fallback = "无内容可迁移；可安全忽略。"
    else:  # env_bound / internal_proprietary
        bundle_path = None
        install = INSTALL_GUIDANCE_BOUND
        if primary == "internal_proprietary":
            fallback = ("阿里内部专有/合规敏感，绝不 verbatim 分发；仅在授权源终端按其 SKILL.md 依赖安装，"
                        "无该环境时走通用能力 fallback 或明确告知不可用。")
        else:
            fallback = ("绑定终端 MCP/运行时或含二进制依赖，不打包；无该环境时该能力不可用，"
                        "应显式降级并告知，不静默假装完成。")

    rec = {
        "name": name,
        "source_terminals": merged["source_terminals"],
        "classification": primary,
        "file_count": merged["file_count"],
        "size_bytes": merged["size_bytes"],
        "size_excludes_vendored": merged["size_excludes_vendored"],
        "has_non_md": merged["has_non_md"],
        "dep_signals": merged["dep_signals"],
        "md_files": merged["md_files"],
        "bundle_path": bundle_path,
        "install_guidance": install,
        "fallback": fallback,
    }
    if also:
        rec["also"] = also
    return rec, primary


# --------------------------------------------------------------------------
# 打包：把 portable_doc 的 .md 逐字节拷进 bundled-skills/<name>/，回读 sha256 断言一致。
# --------------------------------------------------------------------------

def plan_bundle_files(records, bundle_dir_rel):
    """返回 [(src_abs, dst_abs, rel_to_suite_fwd)]，按 rel 排序（确定性）。"""
    plan = []
    for rec in records:
        if rec["classification"] != "portable_doc":
            continue
        name = rec["name"]
        # 找回该记录代表终端的 md_abs：records 里不带 md_abs，改由 caller 传入的 merged 提供
        for rel_md, src_abs in rec["_md_pairs"]:
            dst_abs = os.path.join(SUITE_ROOT, bundle_dir_rel, name,
                                   rel_md.replace("/", os.sep))
            rel_fwd = "%s/%s/%s" % (bundle_dir_rel.rstrip("/"), name, rel_md)
            plan.append((src_abs, dst_abs, rel_fwd))
    plan.sort(key=lambda t: t[2])
    return plan


def do_copy(plan, dry_run):
    """执行拷贝 + 回读 sha256 断言。返回 (bundled_rel_list, bundled_bytes, mismatches)。"""
    bundled_rel = []
    bundled_bytes = 0
    mismatches = []
    for src_abs, dst_abs, rel_fwd in plan:
        if dry_run:
            bundled_rel.append(rel_fwd)
            try:
                bundled_bytes += os.path.getsize(src_abs)
            except OSError:
                pass
            continue
        dst_dir = os.path.dirname(dst_abs)
        if not os.path.isdir(dst_dir):
            os.makedirs(dst_dir)
        # 逐字节 verbatim：copy2 保留元数据；随后读回双方 bytes 做 sha256 对比
        shutil.copy2(src_abs, dst_abs)
        with io.open(src_abs, "rb") as fh:
            src_raw = fh.read()
        with io.open(dst_abs, "rb") as fh:
            dst_raw = fh.read()
        if sha256_bytes(src_raw) != sha256_bytes(dst_raw):
            mismatches.append(rel_fwd)
        bundled_rel.append(rel_fwd)
        bundled_bytes += len(dst_raw)
    return bundled_rel, bundled_bytes, mismatches


def note_stale_bundle_dirs(bundle_dir_abs, bundled_names):
    """非破坏刷新：报告 bundled-skills/ 下存在但本次未涉及的旧目录（不删）。"""
    stale = []
    if os.path.isdir(bundle_dir_abs):
        for entry in sorted(os.listdir(bundle_dir_abs)):
            if os.path.isdir(os.path.join(bundle_dir_abs, entry)) and entry not in bundled_names:
                stale.append(entry)
    return stale


# --------------------------------------------------------------------------
# 主流程
# --------------------------------------------------------------------------

def main(argv):
    opts = parse_cli(argv)
    if opts["help"]:
        sys.stdout.write(usage_text())
        return EXIT_PASS

    # 扫描目录：显式给了就用给的（必须存在），否则用默认三终端里真实存在者
    if opts["skills_dirs"]:
        skills_dirs = [to_fwd(os.path.abspath(d)) for d in opts["skills_dirs"]]
        for d in skills_dirs:
            if not os.path.isdir(d):
                usage_exit("--skills-dir 不是目录：%s" % d)
    else:
        skills_dirs = [d for d in DEFAULT_SKILLS_DIRS if os.path.isdir(d)]
        if not skills_dirs:
            usage_exit("默认三终端技能库均不存在，且未显式提供 --skills-dir")

    acs_set = set(opts["acs_skills"]) if opts["acs_skills"] else set(DEFAULT_ACS_SKILLS)

    out_rel = opts["out"] or os.path.join("spec", "skill-inventory.json")
    out_abs = out_rel if os.path.isabs(out_rel) else os.path.join(SUITE_ROOT, out_rel)
    out_abs = to_fwd(os.path.abspath(out_abs))

    bundle_dir_rel = to_fwd(opts["bundle_dir"] or "bundled-skills")
    if os.path.isabs(opts["bundle_dir"] or ""):
        bundle_dir_abs = to_fwd(os.path.abspath(opts["bundle_dir"]))
        # 绝对 bundle 目录：rel 用于 JSON 展示时退化为绝对路径
        bundle_dir_rel = bundle_dir_abs
    else:
        bundle_dir_abs = to_fwd(os.path.abspath(os.path.join(SUITE_ROOT, bundle_dir_rel)))

    dry_run = opts["dry_run"]
    no_copy = opts["no_copy"]

    sys.stdout.write("=== acs_build_skill_inventory (v%s) ===\n" % SPEC_VERSION)
    sys.stdout.write("  note  suite_root =%s\n" % to_fwd(SUITE_ROOT))
    sys.stdout.write("  note  scanned    =%s\n" % ", ".join(skills_dirs))
    sys.stdout.write("  note  out        =%s\n" % out_abs)
    sys.stdout.write("  note  bundle_dir =%s\n" % bundle_dir_abs)
    sys.stdout.write("  note  acs_skills =%s\n" % ", ".join(sorted(acs_set)))
    sys.stdout.write("  note  dry_run=%s no_copy=%s\n" % (dry_run, no_copy))

    # 逐终端逐技能扫描，按技能名归并出现
    by_name = {}   # name -> list[(terminal, scan)]
    for term in skills_dirs:
        for entry in sorted(os.listdir(term)):
            skill_dir = os.path.join(term, entry)
            if not os.path.isdir(skill_dir):
                continue    # 技能是目录；根部散落文件不算技能
            scan = scan_skill_dir(skill_dir)
            by_name.setdefault(entry, []).append((term, scan))

    merged_list = [merge_occurrences(n, occ) for n, occ in by_name.items()]
    merged_list.sort(key=lambda m: m["name"])

    records = []
    counts = {"portable_doc": 0, "already_in_acs": 0, "env_bound": 0,
              "internal_proprietary": 0, "junk": 0}
    for m in merged_list:
        rec, primary = build_record(m, acs_set, bundle_dir_rel)
        # 内部用：打包计划需要 (rel_md, src_abs) 成对；不写进最终 JSON
        rec["_md_pairs"] = list(zip(m["md_files"], m["md_abs"]))
        rec["_rep_dir"] = m["rep_dir"]
        records.append(rec)
        counts[primary] = counts.get(primary, 0) + 1

    plan = plan_bundle_files(records, bundle_dir_rel)
    bundled_names = sorted({rec["name"] for rec in records if rec["classification"] == "portable_doc"})
    stale = note_stale_bundle_dirs(bundle_dir_abs, set(bundled_names))

    # 执行拷贝（dry-run 只算不写；no-copy 完全不拷）
    if dry_run or no_copy:
        bundled_rel = [p[2] for p in plan] if not no_copy else []
        bundled_bytes = 0
        if not no_copy:
            for src_abs, _dst, _rel in plan:
                try:
                    bundled_bytes += os.path.getsize(src_abs)
                except OSError:
                    pass
        mismatches = []
    else:
        bundled_rel, bundled_bytes, mismatches = do_copy(plan, dry_run=False)

    # 剥离内部字段，得到可序列化记录
    out_records = []
    for rec in records:
        clean = {k: v for k, v in rec.items() if not k.startswith("_")}
        out_records.append(clean)

    summary = {
        "total_skills": len(out_records),
        "portable_doc": counts.get("portable_doc", 0),
        "already_in_acs": counts.get("already_in_acs", 0),
        "env_bound": counts.get("env_bound", 0),
        "internal_proprietary": counts.get("internal_proprietary", 0),
        "junk": counts.get("junk", 0),
        "bundled_files": len(bundled_rel),
        "bundled_bytes": bundled_bytes,
    }

    doc = {
        "spec_version": SPEC_VERSION,
        "_why": [
            "portable vs env_bound：纯 .md 不等于可迁移。实测 .qoderwork/skills 110 技能/12223 文件/190MB，"
            "仅 14 个纯 .md（0.31MB），96 个 env-bound（189.7MB，qoderwork-ppt 的 node_modules 就 124MB/10757 文件）。",
            "prose 绑定：.md-only 也可能在正文里绑死运行时（qw_query/qw_action/tinyfish/dingtalk/dws/odps/aone/阿里，"
            "以及 qoder/qwenwork/mcp_list 等终端自有工具前缀），命中即降级 env_bound，记录 dep_signals。",
            "pack.py 约束：ACS 只打 manifest.files 里逐个列出的文件（无 glob/目录）并拒绝清单外条目，"
            "故不能整体塞入万级文件；只 verbatim 打包 portable_doc 小集合，其余仅清单化。",
            "no blobs/binaries：本清单不含任何二进制/依赖树；file_count 与 size_bytes 均排除 vendored "
            "(node_modules/.git/venv/__pycache__/dist/build)，size_excludes_vendored=true 标记该剪枝。",
            "internal proprietary：阿里内部专有/合规敏感技能（recruit/qingbaoju/price360/dws/dingtalk 等）"
            "分类为 internal_proprietary，永不 verbatim 分发，只能在授权源终端安装或走 fallback。",
            "counts 以合并后代表终端为准（同名技能跨终端合成一条，source_terminals 列全部来源）；"
            "输出无时间戳、全排序，重复运行 byte-stable。",
        ],
        "generated_by": GENERATED_BY,
        "scanned_dirs": skills_dirs,
        "summary": summary,
        "skills": out_records,
    }

    payload = json.dumps(doc, ensure_ascii=False, indent=2)

    if dry_run:
        sys.stdout.write("--- DRY RUN：不落任何盘 ---\n")
        sys.stdout.write("[would write] %s (%d bytes)\n" % (out_abs, len(payload.encode("utf-8"))))
        sys.stdout.write("[would create/refresh bundle dir] %s\n" % bundle_dir_abs)
        sys.stdout.write("[would copy %d files, %d bytes] verbatim into bundled-skills/:\n"
                         % (len(plan), bundled_bytes))
        for _src, _dst, rel in plan:
            sys.stdout.write("  %s\n" % rel)
        if stale:
            sys.stdout.write("[stale bundle dirs NOT touched] %s\n" % ", ".join(stale))
        _print_plan_summary(summary, records, counts)
        sys.stdout.write("结果：DRY-RUN PASS（未写文件、未拷贝）\n")
        return EXIT_PASS

    # 写清单 JSON（父目录不存在则建）
    out_dir = os.path.dirname(out_abs)
    if out_dir and not os.path.isdir(out_dir):
        os.makedirs(out_dir)
    with io.open(out_abs, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(payload)
        fh.write("\n")

    if no_copy:
        sys.stdout.write("  note  --no-copy：已写清单，未拷贝 bundled-skills\n")

    if mismatches:
        sys.stdout.write("FAIL  回读 sha256 不一致的文件：%s\n" % ", ".join(mismatches))
        return EXIT_USAGE

    if stale:
        sys.stdout.write("  note  bundled-skills/ 下未涉及的旧目录（未删，非破坏刷新）：%s\n"
                         % ", ".join(stale))

    _print_plan_summary(summary, records, counts)
    sys.stdout.write("  bundled_files=%d bundled_bytes=%d\n" % (len(bundled_rel), bundled_bytes))
    if not no_copy:
        sys.stdout.write("  sha256 readback: %d/%d 一致，0 mismatch\n" % (len(bundled_rel), len(bundled_rel)))
    sys.stdout.write("  wrote %s\n" % out_abs)
    sys.stdout.write("结果：PASS（分类 %d 技能，打包 %d 文件，回读一致）\n"
                     % (summary["total_skills"], len(bundled_rel)))
    return EXIT_PASS


def _print_plan_summary(summary, records, counts):
    sys.stdout.write("--- 分类汇总 ---\n")
    sys.stdout.write("  %s\n" % json.dumps(summary, ensure_ascii=False))
    portable = sorted(r["name"] for r in records if r["classification"] == "portable_doc")
    sys.stdout.write("  portable_doc(%d): %s\n" % (len(portable), ", ".join(portable)))
    downgraded = sorted(
        (r["name"], r["dep_signals"]) for r in records
        if r["classification"] == "env_bound" and not r["has_non_md"] and r["dep_signals"]
    )
    sys.stdout.write("  .md-only 但 prose 绑运行时→env_bound(%d):\n" % len(downgraded))
    for nm, sig in downgraded:
        sys.stdout.write("    %s -> %s\n" % (nm, ",".join(sig)))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
