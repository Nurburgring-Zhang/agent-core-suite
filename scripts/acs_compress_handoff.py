"""交接载体 .acs/handoff.md 的机械渲染 / 校验器（零第三方依赖）。

诚实边界（务必先读）：
    本脚本只做「机械渲染 + 机械校验」，它 **不做语义级 LLM 压缩**——一个纯 stdlib
    脚本没有语义能力，假装能压缩就是造假。真正的「无损压缩」永远是 agent 的原生职责。
    本脚本的价值只有两条，且都可机检、可复现：
      (a) 从 task-state.json 最后一步的 steps[].handoff 结构化镜像，确定性地
          scaffold / refresh 出 .acs/handoff.md，使跨步载体「永不缺失」（同输入同字节，幂等）；
      (b) 机械校验载体的形状，抓出「超字数 / 三要素缺失 / 目标锚点空话 / 未做偏离自检」
          这类无损压缩违规——与 gate_checklist.py 的 STEP_handoff 同一口径、同一真相源。
    语义跑偏判定属原生能力，本层不假装能评定。

真实强制点是 gate_checklist.py 的 STEP_handoff（机检门），不是本脚本、更不是 hook。

用法：
    # 渲染（从状态文件最后一步的 handoff 镜像生成载体；默认落 <state_dir>/handoff.md）
    python -X utf8 acs_compress_handoff.py --render --state .acs/task-state.json [--out <path>]

    # 校验（载体形状 + 可选与状态镜像交叉核对）
    python -X utf8 acs_compress_handoff.py --validate --handoff .acs/handoff.md \
        [--state .acs/task-state.json] [--tier T1|T2|T3]

退出码：0=PASS，1=BLOCK，2=USAGE_ERROR（视为未验证=未完成）。
所有阈值来自 spec/thresholds.json 的 handoff / vague_phrases 节，本文件不自带数字。
"""

import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _acs_common import (  # noqa: E402
    Report,
    load_json,
    norm_tier,
    spec_section,
    usage_exit,
    EXIT_PASS,
    EXIT_BLOCK,
    EXIT_USAGE,
)

# 阈值单一真相源：handoff 节 + 空话表。缺字段由 _acs_common 直接 USAGE_ERROR，不给默认值。
_HO = spec_section("handoff")
HO_TARGET = _HO["target_chars"]
HO_MAX = _HO["max_chars_hard"]
HO_TOL = _HO["chars_tolerance"]
HO_MIN_GOAL = _HO["min_goal_anchor_chars"]
HO_MIN_NEXT = _HO["min_next_chars"]
HO_REQUIRED_SECTIONS = tuple(_HO["required_sections"])
HO_REQUIRE_GOAL = _HO["require_goal_anchor"]
HO_REQUIRE_DRIFT = _HO["require_drift_checked"]
HO_PATH = _HO["handoff_path"]
HO_APPLY_TIERS = tuple(_HO["apply_tiers"])

# 空话表：验收空话 + 证据空话合并（校验目标锚点是否为「已完成 / ok / 没问题」这类废话）
_VP = spec_section("vague_phrases")
VAGUE = tuple(list(_VP["acceptance"]) + list(_VP["evidence"]))

# 载体六段固定形状（与 templates/handoff-summary.md 逐段对齐，禁止增段）
SECTION_HEADINGS = [
    "## 0 当前目标（锚点，每步复述防跑偏）",
    "## 1 已确认事实（只写被证据支撑的，每条附证据）",
    "## 2 已完成产出（文件路径 + 一句话作用）",
    "## 3 未解决问题 / 已知风险（含已排除的错误路径）",
    "## 4 下一步入口（唯一目标 + 完成标准）",
    "## 5 状态指针",
]

# required_sections 的关键字 → 载体标题里可能出现的别名。
# spec 用「已完成项」，模板标题用「已完成产出」，两者视为同一段，避免误判缺失。
SECTION_ALIASES = {
    "已完成项": ["已完成项", "已完成产出", "已完成"],
}

# 值参数与布尔参数的分表：--render / --validate 是「出现即为该模式」的布尔开关，
# 不能走 _acs_common.parse_args（它要求每个 flag 都带取值），故这里写一个极小的本地解析，
# 但仍复用 usage_exit 保证「参数错 = 未验证 = 未完成」的统一口径。
_VALUE_FLAGS = {"--state": "state", "--out": "out", "--handoff": "handoff", "--tier": "tier"}
_BOOL_FLAGS = {"--render": "render", "--validate": "validate"}

USAGE = """\
acs_compress_handoff.py —— .acs/handoff.md 载体的机械渲染 / 校验器（不做语义压缩）

模式（二选一，不得同时给）：
  --render    从 task-state.json 最后一步的 steps[].handoff 镜像确定性渲染载体（幂等）
  --validate  机械校验载体形状：字数上限 / 三要素锚点 / 目标非空话 / 偏离自检

参数：
  --state <path>     task-state.json 路径（--render 必需；--validate 可选，用于交叉核对镜像）
  --handoff <path>   待校验的 .acs/handoff.md 路径（--validate 必需）
  --out <path>       --render 的输出路径（默认 <state_dir>/handoff.md）
  --tier <T1|T2|T3>  分级；不在 handoff.apply_tiers 内则本门不适用（仅提示，不阻断）
  -h, --help         显示本帮助

示例：
  python -X utf8 acs_compress_handoff.py --render --state .acs/task-state.json
  python -X utf8 acs_compress_handoff.py --validate --handoff .acs/handoff.md --state .acs/task-state.json --tier T2

退出码：0=PASS  1=BLOCK  2=USAGE_ERROR
"""


def _parse(argv):
    """本地极简解析：布尔开关置 '1'，值参数取下一个 token。未知/缺值一律 usage_exit。"""
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
            usage_exit("未知参数：%s（可用：--render --validate --state --out --handoff --tier -h/--help）" % tok)
    return out


def _is_vague(text):
    """与 gate_checklist 同口径：strip + lower 后与空话表精确相等才算空话。"""
    low = (text or "").strip().lower()
    return low in [p.lower() for p in VAGUE]


def _nonempty_strs(value):
    if not isinstance(value, list):
        return []
    return [x.strip() for x in value if isinstance(x, str) and x.strip()]


# ------------------------------------------------------------------ 渲染


def _last_step(state):
    steps = state.get("steps") or []
    if not steps:
        usage_exit("task-state.json 没有任何 steps[]：无最后一步可渲染，不编造内容。")
    last = steps[-1]
    if not isinstance(last, dict):
        usage_exit("steps[-1] 不是对象：无法从中取 handoff 镜像。")
    return last


def render_markdown(state, state_path):
    """从最后一步的 handoff 镜像 + 真实已记录字段，确定性拼出六段载体。

    只搬运 task-state.json 里真实存在的结构化数据，不生成任何新语义：
      第 0 段 ← handoff.goal_anchor + drift_checked 声明
      第 1 段 ← 本步 acceptance[].actual（真实实测值，附证据）
      第 2 段 ← handoff.done[]
      第 3 段 ← 本步 review 里未闭环的 issues_found + 顶层 known_gaps
      第 4 段 ← handoff.next
      第 5 段 ← 指向状态文件的指针行
    同输入 → 同字节（无时间戳、无随机、无环境依赖），故幂等。
    """
    last = _last_step(state)
    ho = last.get("handoff")
    if not isinstance(ho, dict):
        # 镜像缺失 = 从未记录 = 无法渲染。绝不凭空捏造交接内容（那会污染下一步）。
        usage_exit(
            "steps[-1] 没有 handoff 结构化镜像：无法渲染从未记录的内容（不编造）。\n"
            "  请先在 task-state.json 的最后一步写入 handoff="
            "{goal_anchor, done[], next, chars, drift_checked, path} 再渲染。"
        )

    goal = (ho.get("goal_anchor") or "").strip()
    done = _nonempty_strs(ho.get("done"))
    nxt = (ho.get("next") or "").strip()
    drift = ho.get("drift_checked")
    drift_txt = "true" if drift is True else ("false" if drift is False else "未声明")

    # 第 1 段：本步 acceptance 的实测值（每条附证据），全部来自真实记录
    facts = []
    for item in last.get("acceptance") or []:
        if not isinstance(item, dict):
            continue
        actual = (item.get("actual") or "").strip()
        if not actual:
            continue
        value = (item.get("value") or "").strip() or "验收项"
        facts.append("- %s｜证据：%s" % (value, actual))

    # 第 3 段：未闭环的审核发现 + 顶层已知缺口，全部来自真实记录
    issues = []
    rv = last.get("review") if isinstance(last.get("review"), dict) else {}
    found = _nonempty_strs(rv.get("issues_found"))
    closed = set(_nonempty_strs(rv.get("issues_closed")))
    for f in found:
        if f not in closed:
            issues.append("- 未闭环：%s" % f)
    for g in _nonempty_strs(state.get("known_gaps")):
        issues.append("- known_gap：%s" % g)

    tier = state.get("tier") or "?"
    step_id = last.get("id") or "?"
    node_id = last.get("node_id") or "?"

    L = []
    L.append(SECTION_HEADINGS[0])
    L.append("- 总目标：%s" % (goal or "（镜像 goal_anchor 为空——待 agent 补写唯一目标）"))
    L.append("- 偏离自检：drift_checked=%s（%s）" % (
        drift_txt,
        "已比对总目标，未跑偏" if drift is True else "未做或声明为否，须补做偏离自检"))
    L.append("")
    L.append(SECTION_HEADINGS[1])
    if facts:
        L.extend(facts)
    else:
        L.append("- （本步 acceptance 无 actual 实测记录——机械渲染不编造事实）")
    L.append("")
    L.append(SECTION_HEADINGS[2])
    if done:
        for d in done:
            L.append("- %s" % d)
    else:
        L.append("- （镜像 done[] 为空——三要素之一缺失，须补写已完成产出）")
    L.append("")
    L.append(SECTION_HEADINGS[3])
    if issues:
        L.extend(issues)
    else:
        L.append("- （本步无未闭环问题，known_gaps 为空）")
    L.append("")
    L.append(SECTION_HEADINGS[4])
    L.append("- 目标：%s" % (nxt or "（镜像 next 为空——待 agent 补写唯一下一步）"))
    L.append("")
    L.append(SECTION_HEADINGS[5])
    L.append("- 状态文件：%s（tier=%s，step=%s，node=%s）" % (state_path, tier, step_id, node_id))
    L.append("- 交接载体：%s（本文件由 acs_compress_handoff.py 机械渲染，语义压缩仍是 agent 原生职责）" % HO_PATH)
    L.append("")
    return "\n".join(L)


def default_out(state_path):
    """默认落点 = <state_dir>/<handoff 基名>；state 为 .acs/task-state.json 时即 .acs/handoff.md。"""
    base = os.path.basename(HO_PATH) or "handoff.md"
    return os.path.join(os.path.dirname(os.path.abspath(state_path)), base)


def do_render(args):
    state_path = args.get("state")
    if not state_path:
        usage_exit("--render 需要 --state <task-state.json>")
    state = load_json(state_path, "task-state")
    out_path = args.get("out") or default_out(state_path)

    text = render_markdown(state, state_path)
    parent = os.path.dirname(os.path.abspath(out_path))
    if parent and not os.path.isdir(parent):
        os.makedirs(parent)
    with io.open(out_path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)

    body_chars, _ = count_body_chars(text)
    rep = Report("acs_compress_handoff --render（机械渲染，非语义压缩）")
    rep.note("state=%s" % state_path)
    rep.note("out=%s（%d 字节，正文 %d 字；上限 %d+%d）" % (
        out_path, len(text.encode("utf-8")), body_chars, HO_MAX, HO_TOL))
    if body_chars > HO_MAX + HO_TOL:
        rep.warn("render", "渲染出的正文 %d 字已超上限 %d——机械搬运即超限，说明镜像本身过胖，须由 agent 语义压缩"
                 % (body_chars, HO_MAX + HO_TOL))
    rep.note("幂等：同状态重渲同字节；语义压缩仍是 agent 原生职责，本脚本不假装代劳")
    return rep.finish()


# ------------------------------------------------------------------ 校验


def count_body_chars(text):
    """正文散文字数：排除代码块内部、模板说明引用行（>）、纯分隔线与空行。

    这是「机械」计量：只数字符，不判语义。逐行 strip 后累加长度，返回 (字数, 计入的行列表)。
    """
    total = 0
    kept = []
    in_fence = False
    for raw in text.splitlines():
        s = raw.strip()
        if s.startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        if s == "":
            continue
        if s.startswith(">"):        # 模板自带的硬约束说明 / 引用行，不计入正文
            continue
        if s in ("---", "***", "___"):  # 分隔线
            continue
        total += len(s)
        kept.append(s)
    return total, kept


def parse_sections(text):
    """按 markdown 标题切段，返回 [(heading_text, [body_raw_lines]), ...]；代码块内部不切段。"""
    sections = []
    head = None
    body = []
    in_fence = False
    for raw in text.splitlines():
        s = raw.strip()
        if s.startswith("```"):
            in_fence = not in_fence
            if head is not None:
                body.append(raw)
            continue
        if in_fence:
            if head is not None:
                body.append(raw)
            continue
        if s.startswith("#"):
            if head is not None:
                sections.append((head, body))
            head = s.lstrip("#").strip()
            body = []
        elif head is not None:
            body.append(raw)
    if head is not None:
        sections.append((head, body))
    return sections


def _find_section(sections, keyword):
    cands = SECTION_ALIASES.get(keyword, [keyword])
    for head, body in sections:
        for c in cands:
            if c in head:
                return head, body
    return None


def _section_has_prose(body):
    for l in body:
        s = l.strip()
        if s and not s.startswith(">") and s not in ("---", "***", "___"):
            return True
    return False


def _extract_goal_anchor(body):
    """从「当前目标」段正文里取出锚点散文：剥掉项目符号、总目标/本步前缀、偏离自检行。"""
    pieces = []
    for l in body:
        s = l.strip()
        if not s or s.startswith(">"):
            continue
        s = s.lstrip("-*").strip()
        if s.startswith("偏离自检"):
            continue
        for prefix in ("总目标：", "本步唯一目标：", "目标："):
            if s.startswith(prefix):
                s = s[len(prefix):].strip()
                break
        if s:
            pieces.append(s)
    return " ".join(pieces).strip()


def do_validate(args):
    handoff_path = args.get("handoff")
    if not handoff_path:
        usage_exit("--validate 需要 --handoff <path>")
    if not os.path.isfile(handoff_path):
        usage_exit("待校验载体不存在：%s" % handoff_path)

    tier = norm_tier(args.get("tier"), default="") if args.get("tier") else ""
    text = _read(handoff_path)
    sections = parse_sections(text)
    body_chars, _ = count_body_chars(text)
    ceiling = HO_MAX + HO_TOL

    rep = Report("acs_compress_handoff --validate（载体形状机检）")
    rep.note("handoff=%s 正文 %d 字（上限 %d+%d=%d）" % (handoff_path, body_chars, HO_MAX, HO_TOL, ceiling))
    if tier:
        rep.note("tier=%s" % tier)
        if tier not in HO_APPLY_TIERS:
            rep.note("tier=%s 不在 handoff.apply_tiers=%s 内：本交接门对该级不适用，仅做形状提示"
                     % (tier, "/".join(HO_APPLY_TIERS)))

    # 1) 字数硬上限（含容差）
    if body_chars > ceiling:
        rep.error("body_chars", "正文 %d 字 > 硬上限 %d（+%d 容差）：总结膨胀成第二份历史，回灌禁令失效"
                  % (body_chars, HO_MAX, HO_TOL))
    elif body_chars > HO_TARGET:
        rep.warn("body_chars", "正文 %d 字 > 目标 %d 字（未破硬上限，建议由 agent 语义压缩）"
                 % (body_chars, HO_TARGET))

    # 2) 三要素锚点在位且非空
    for kw in HO_REQUIRED_SECTIONS:
        found = _find_section(sections, kw)
        if found is None:
            rep.error("required_sections", "缺段落锚点「%s」：三要素缺一即有损压缩，下一步无从接续" % kw)
        elif not _section_has_prose(found[1]):
            rep.error("required_sections", "段落「%s」为空：锚点在但无内容，等于没交接" % kw)

    # 3) 目标锚点：在位 + 长度达标 + 非空话
    if HO_REQUIRE_GOAL:
        goal_sec = _find_section(sections, "当前目标")
        if goal_sec is None:
            rep.error("goal_anchor", "找不到「当前目标」段：无目标锚点，压缩会跑偏")
        else:
            anchor = _extract_goal_anchor(goal_sec[1])
            if len(anchor) < HO_MIN_GOAL:
                rep.error("goal_anchor", "目标锚点过短（%d 字 < %d）：%r" % (len(anchor), HO_MIN_GOAL, anchor))
            elif _is_vague(anchor):
                rep.error("goal_anchor", "目标锚点是空话 %r，须写唯一可辨识目标" % anchor)

    # 4) 偏离自检声明
    if HO_REQUIRE_DRIFT:
        if "drift_checked=true" not in text:
            rep.error("drift_checked", "载体未见 drift_checked=true 声明：未做方向偏离自检（压缩不跑偏的机检底座）")

    # 5) 可选：与状态镜像交叉核对
    if args.get("state"):
        state = load_json(args["state"], "task-state")
        last = _last_step(state)
        ho = last.get("handoff")
        if not isinstance(ho, dict):
            rep.error("state.steps[-1].handoff", "状态最后一步缺 handoff 镜像：载体与状态不同源，无法交叉核对")
        else:
            chars = ho.get("chars")
            if not isinstance(chars, int) or isinstance(chars, bool):
                rep.error("state.steps[-1].handoff.chars", "镜像缺整数字段 chars：无法核对压缩上限，等于没计量")
            elif chars > ceiling:
                rep.error("state.steps[-1].handoff.chars", "镜像 chars=%d > 上限 %d（+%d）" % (chars, HO_MAX, HO_TOL))
            if HO_REQUIRE_DRIFT and ho.get("drift_checked") is not True:
                rep.error("state.steps[-1].handoff.drift_checked", "镜像 drift_checked 非 true：未做偏离自检")
            rep.note("已交叉核对 state=%s 最后一步镜像" % args["state"])

    return rep.finish()


def _read(path):
    with io.open(path, "r", encoding="utf-8", errors="replace") as fh:
        return fh.read()


def main(argv):
    args = _parse(argv)
    if args.get("help"):
        sys.stdout.write(USAGE)
        sys.stdout.flush()
        return EXIT_PASS
    render = args.get("render")
    validate = args.get("validate")
    if render and validate:
        usage_exit("--render 与 --validate 不能同时给（一次只做一件事）")
    if render:
        return do_render(args)
    if validate:
        return do_validate(args)
    # 无模式：打印用法后按 USAGE_ERROR 退出（未指定要做的事 = 未验证 = 未完成）
    sys.stdout.write(USAGE)
    sys.stdout.flush()
    usage_exit("未指定模式：需 --render 或 --validate")


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
