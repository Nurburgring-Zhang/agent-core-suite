"""G2/Graph 门：task-state.json 契约与任务 DAG 校验。

用法：
    python -X utf8 gate_state_validate.py --state .acs/task-state.json [--schema <path>] [--tier T2]

退出码：0=PASS，1=BLOCK，2=USAGE_ERROR（视为未验证=未完成）

v1.1 增强（spec 同名节，全部「可选字段=出现即校验，分级必填由 tier 判定」）：
    edge_gates          边上验证门语义（kind=cmd 必须 expect_exit；T3 至少一条边挂门）
    barriers.policy     汇聚四语义（min_success 需 min_count 且 1 ≤ min_count ≤ 分支数）
    acceptance_binding  验收必须打到声明的产出物（交白卷盲区：测试全绿≠有产出）
    artifacts           工件记忆（produced_by 溯源到节点 + supersedes 链不成环）
    contract            契约版本化（语义化版本；高于已知只告警）
    model_tier          T3 调模型节点必须声明档位（图让成本看得见）
"""

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _acs_common import (  # noqa: E402
    Report,
    load_json,
    norm_tier,
    parse_args,
    spec_section,
    usage_exit,
    validate,
)

# inputs 里出现这些写法 = 上下文回灌入口，必须写具体路径或上游输出字段
BANNED_INPUT_PHRASES = (
    "上文", "前文", "之前的讨论", "相关背景", "历史对话", "如前所述",
    "参考上面", "参考前面", "见上", "context above", "previous discussion",
)

# ---------------------------------------------------------------- v1.1 增强（阈值全部来自 spec 同名节）
_EG = spec_section("edge_gates")
EDGE_REQUIRED_TIERS = tuple(_EG["required_tiers"])
EDGE_MIN_GATES = _EG["min_edge_gates"]

_AB = spec_section("acceptance_binding")
ACCEPT_TIERS = tuple(_AB["apply_tiers"])
ACCEPT_MIN_BASENAME = _AB["min_basename_chars"]

_ART = spec_section("artifacts")
ARTIFACT_REQUIRED_TIERS = tuple(_ART["required_tiers"])
ARTIFACT_MIN = _ART["min_artifacts"]

_CT = spec_section("contract")
CONTRACT_KNOWN = _CT["known_version"]
CONTRACT_PATTERN = _CT["pattern"]

_MT = spec_section("model_tier")
MODEL_TIER_REQUIRED_TIERS = tuple(_MT["required_tiers"])

DEFAULT_SCHEMA = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "templates", "task-state.schema.json",
)


def expand_refs(schema):
    """把 $ref_inline 展开为 $defs_inline 中的定义（本子集校验器不支持标准 $ref）。"""
    defs = schema.get("$defs_inline", {})

    def walk(node):
        if isinstance(node, dict):
            if "$ref_inline" in node:
                key = node["$ref_inline"]
                if key not in defs:
                    usage_exit("schema 中 $ref_inline 引用了不存在的定义：%s" % key)
                return walk(defs[key])
            return dict((k, walk(v)) for k, v in node.items() if k != "$defs_inline")
        if isinstance(node, list):
            return [walk(x) for x in node]
        return node

    return walk(schema)


def check_graph(graph, report):
    nodes = graph.get("nodes", [])
    ids = [n.get("id") for n in nodes]
    known = set(ids)

    for nid in known:
        if ids.count(nid) > 1:
            report.error("graph.nodes", "节点 id 重复：%s" % nid)

    edges = graph.get("edges", []) or []
    for idx, edge in enumerate(edges):
        for side in ("from", "to"):
            if edge.get(side) not in known:
                report.error("graph.edges[%d]" % idx, "%s 指向不存在的节点 %r" % (side, edge.get(side)))
        if edge.get("from") == edge.get("to"):
            report.error("graph.edges[%d]" % idx, "自环边（from == to == %s）" % edge.get("from"))
        # 边上验证门：与节点/步骤验收同规矩，无法判成败的命令验证等于没有门
        gate = edge.get("gate")
        if isinstance(gate, dict) and gate.get("kind") == "cmd" and "expect_exit" not in gate:
            report.error("graph.edges[%d].gate" % idx,
                         "边上验证门 kind=cmd 必须给出 expect_exit（无法判成败的命令验证等于没有门）")

    # 环检测（Kahn 拓扑排序）
    indeg = dict((nid, 0) for nid in known)
    adj = dict((nid, []) for nid in known)
    for edge in edges:
        a, b = edge.get("from"), edge.get("to")
        if a in known and b in known and a != b:
            adj[a].append(b)
            indeg[b] += 1
    queue = [nid for nid in known if indeg[nid] == 0]
    seen = 0
    while queue:
        cur = queue.pop()
        seen += 1
        for nxt in adj[cur]:
            indeg[nxt] -= 1
            if indeg[nxt] == 0:
                queue.append(nxt)
    if seen != len(known):
        report.error("graph", "DAG 中存在环，无法拓扑排序（涉及 %d 个节点）" % (len(known) - seen))

    # 孤立节点：有边存在时，某节点既无入边也无出边
    if edges and len(known) > 1:
        touched = set()
        for edge in edges:
            touched.add(edge.get("from"))
            touched.add(edge.get("to"))
        for nid in ids:
            if nid not in touched:
                report.error("graph.nodes", "节点 %s 既无入边也无出边（孤立节点，依赖未声明）" % nid)

    upstream = dict((nid, set()) for nid in known)
    for edge in edges:
        if edge.get("to") in upstream and edge.get("from") in known:
            upstream[edge["to"]].add(edge["from"])

    out_owner = {}
    for idx, node in enumerate(nodes):
        where = "graph.nodes[%d](%s)" % (idx, node.get("id"))
        if node.get("owner") and node.get("owner") == node.get("verifier"):
            report.error(where, "owner == verifier == %r，禁止自评签字" % node.get("owner"))
        if node.get("deterministic") is True and node.get("calls_model") is True:
            report.error(where, "deterministic=true 的节点声明了 calls_model=true（确定性工作禁止调模型）")
        for phrase in BANNED_INPUT_PHRASES:
            for item in node.get("inputs", []):
                if phrase in item:
                    report.error(where, "inputs 含模糊回灌写法 %r（须写具体路径或上游输出字段）" % item)
                    break
        for item in node.get("inputs", []):
            if "." in item and item.split(".")[0] in known:
                src = item.split(".")[0]
                if src != node.get("id") and src not in upstream.get(node.get("id"), set()):
                    report.error(where, "inputs 引用了节点 %s 的输出，但缺少 %s -> %s 的边" % (src, src, node.get("id")))
        for goal_word in ("并且", "同时", "以及"):
            if goal_word in (node.get("goal") or ""):
                report.warn(where, "goal 含 %r，疑似一步多产出，建议再拆" % goal_word)
        for out in node.get("outputs", []):
            if out in out_owner:
                report.warn(where, "输出 %s 与节点 %s 重复，并行时会写冲突" % (out, out_owner[out]))
            else:
                out_owner[out] = node.get("id")

    node_by_id = dict((n.get("id"), n) for n in nodes)
    edge_pairs = set((e.get("from"), e.get("to")) for e in edges)
    for gidx, group in enumerate(graph.get("parallel_groups", []) or []):
        for nid in group:
            if nid not in known:
                report.error("graph.parallel_groups[%d]" % gidx, "引用不存在的节点 %r" % nid)
        for i in range(len(group)):
            for j in range(len(group)):
                if i == j:
                    continue
                if (group[i], group[j]) in edge_pairs:
                    report.error("graph.parallel_groups[%d]" % gidx,
                                 "%s -> %s 存在数据依赖，不能放入同一并行组" % (group[i], group[j]))
        outs = {}
        for nid in group:
            for out in (node_by_id.get(nid) or {}).get("outputs", []):
                if out in outs:
                    report.error("graph.parallel_groups[%d]" % gidx,
                                 "节点 %s 与 %s 并行却写同一产出 %s" % (nid, outs[out], out))
                outs[out] = nid

    for bidx, barrier in enumerate(graph.get("barriers", []) or []):
        if barrier.get("at") not in known:
            report.error("graph.barriers[%d]" % bidx, "at 指向不存在的节点 %r" % barrier.get("at"))
        waits = barrier.get("waits_for", []) or []
        for nid in waits:
            if nid not in known:
                report.error("graph.barriers[%d]" % bidx, "waits_for 含不存在的节点 %r" % nid)
            elif (nid, barrier.get("at")) not in edge_pairs:
                report.warn("graph.barriers[%d]" % bidx,
                            "汇聚点 %s 等待 %s，但两者之间没有边（依赖未显式声明）" % (barrier.get("at"), nid))
        # 汇聚语义：min_success 必须给出最小成功数，且落在等待分支数范围内
        if barrier.get("policy") == "min_success":
            mc = barrier.get("min_count")
            if not isinstance(mc, int) or isinstance(mc, bool):
                report.error("graph.barriers[%d]" % bidx,
                             "policy=min_success 必须给出 min_count（最小成功数，不写等于没有汇聚规则）")
            elif not (1 <= mc <= len(waits)):
                report.error("graph.barriers[%d]" % bidx,
                             "min_count=%s 超出等待分支数 %d（汇聚规则必须可满足）" % (mc, len(waits)))
    return known


def check_contract_version(state, report):
    """契约版本化：格式非法才拦，高于已知只告警（旧版本文件照常通过，向后兼容）。"""
    cv = state.get("contract_version")
    if cv is None:
        return
    if not isinstance(cv, str) or not re.match(CONTRACT_PATTERN, cv or ""):
        report.error("contract_version",
                     "契约版本 %r 不符合语义化版本 %s（改了规矩系统才能知道新旧关系）" % (cv, CONTRACT_PATTERN))
        return
    try:
        cur = tuple(int(x) for x in cv.split("."))
        known = tuple(int(x) for x in CONTRACT_KNOWN.split("."))
    except ValueError:
        return  # pattern 已保证纯数字，防御分支
    if cur > known:
        report.warn("contract_version",
                    "文件版本 %s 高于套件已知版本 %s（来自更新套件？建议核对门禁行为）" % (cv, CONTRACT_KNOWN))


def check_acceptance_binding(state, tier, report):
    """验收必须打到声明的产出物（SWE Refactor Bench 教训：交白卷也满分）。

    行为测试全绿证明的是『没改坏』，不是『真的交付了』；验收命令里
    必须能找到产出物本身（子串可核验），否则尺子量错了对象。
    只对文件型产出物强制（含路径/扩展名特征）；纯逻辑名产出不误伤。
    """
    if tier not in ACCEPT_TIERS:
        return
    units = []
    for idx, step in enumerate(state.get("steps", []) or []):
        if isinstance(step, dict):
            units.append(("steps[%d](%s)" % (idx, step.get("id")),
                          step.get("outputs") or [], step.get("acceptance") or []))
    graph = state.get("graph")
    if isinstance(graph, dict):
        for node in graph.get("nodes", []) or []:
            if isinstance(node, dict):
                units.append(("graph.nodes(%s)" % node.get("id"),
                              node.get("outputs") or [], node.get("acceptance") or []))
    for where, outs, acc in units:
        file_outputs = [o for o in outs if isinstance(o, str) and ("/" in o or "\\" in o or "." in o)]
        if not file_outputs or not acc:
            continue
        values = [(a.get("value") or "") if isinstance(a, dict) else "" for a in acc]
        for out in file_outputs:
            base = out.replace("\\", "/").split("/")[-1]
            if len(base) < ACCEPT_MIN_BASENAME:
                continue
            if not any(base in v for v in values):
                report.error("%s.acceptance" % where,
                             "产出物 %s 未出现在任何验收命令里（交白卷盲区：验收打的是输入而非产出，"
                             "证明的是『没改坏』不是『真的交付了』）" % out)


def check_artifacts(state, tier, report):
    """工件记忆层：transcript 不是数据库，产出必须能溯源到生产节点。

    produced_by 指向 graph 节点；supersedes 引用必须存在且链不成环
    （版本谱系必须是 DAG，否则新旧关系自相矛盾）。
    """
    arts = state.get("artifacts")
    if arts is None:
        if tier in ARTIFACT_REQUIRED_TIERS:
            report.error("artifacts",
                         "%s 级必须登记工件（Artifact Memory：transcript 不是数据库，"
                         "产出必须能溯源到生产节点）" % tier)
        return
    if not isinstance(arts, list):
        return  # 形状交给 schema
    graph = state.get("graph")
    node_ids = set()
    if isinstance(graph, dict):
        node_ids = set(n.get("id") for n in graph.get("nodes", []) or [] if isinstance(n, dict))
    ids = set(a.get("id") for a in arts if isinstance(a, dict))
    for idx, a in enumerate(arts):
        if not isinstance(a, dict):
            continue
        at = "artifacts[%d]" % idx
        pb = a.get("produced_by")
        if node_ids and pb not in node_ids:
            report.error(at, "produced_by=%r 不指向任何 graph 节点（工件必须能溯源到生产节点）" % pb)
        sup = a.get("supersedes")
        if sup is not None and sup not in ids:
            report.error(at, "supersedes=%r 引用不存在的工件 id（版本谱系断链）" % sup)
    # supersedes 链环检测：谱系必须是 DAG
    nxt = dict((a.get("id"), a.get("supersedes")) for a in arts if isinstance(a, dict))
    for aid in nxt:
        seen = set()
        cur = nxt[aid]
        while cur is not None and cur in nxt:
            if cur == aid:
                report.error("artifacts", "工件 %s 的 supersedes 链成环（版本谱系必须是 DAG）" % aid)
                break
            if cur in seen:
                break  # 别的环已报或将于其自身起点报
            seen.add(cur)
            cur = nxt[cur]
    if tier in ARTIFACT_REQUIRED_TIERS and len(arts) < ARTIFACT_MIN:
        report.error("artifacts", "工件 %d 个 < %d 个（%s 级下限）" % (len(arts), ARTIFACT_MIN, tier))


def check_model_tier(graph, tier, report):
    """T3 调模型的节点必须声明档位：不上图不登记，『贵模型干杂活』的浪费永远无人可见。"""
    if tier not in MODEL_TIER_REQUIRED_TIERS or not isinstance(graph, dict):
        return
    for idx, node in enumerate(graph.get("nodes", []) or []):
        if not isinstance(node, dict):
            continue
        if node.get("calls_model") is True and not (node.get("model_tier") or "").strip():
            report.error("graph.nodes[%d](%s)" % (idx, node.get("id")),
                         "calls_model=true 的节点未声明 model_tier（模型分层让成本看得见：贵模型干杂活必须暴露）")


def check_edge_gate_coverage(state, tier, report):
    """T3 图至少一条边挂验证门：无门边让上游错误直接流进所有下游节点。"""
    if tier not in EDGE_REQUIRED_TIERS:
        return
    graph = state.get("graph")
    if not isinstance(graph, dict):
        return  # T2+ 已在 main 里拦缺 graph
    edges = graph.get("edges", []) or []
    if not edges:
        return  # 单节点图无边不罚
    gated = sum(1 for e in edges if isinstance(e, dict) and isinstance(e.get("gate"), dict))
    if gated < EDGE_MIN_GATES:
        report.error("graph.edges",
                     "%s 级图 %d 条边中只有 %d 条挂验证门（下限 %d）：边是数据契约，无门边让上游错误直接流进下游"
                     % (tier, len(edges), gated, EDGE_MIN_GATES))


def main(argv):
    args = parse_args(argv, {"--state": "state", "--schema": "schema", "--tier": "tier"}, ["state"])
    tier = norm_tier(args.get("tier"), default="")
    schema_path = args.get("schema") or DEFAULT_SCHEMA
    schema = expand_refs(load_json(schema_path, "schema"))
    state = load_json(args["state"], "task-state")

    report = Report("gate_state_validate (契约 + 任务 DAG)")
    report.note("state=%s tier=%s" % (args["state"], tier))

    for err in validate(state, schema, "$"):
        report.error("schema", err)

    if state.get("tier") and tier and state["tier"] != tier:
        report.error("tier", "命令行 tier=%s 与 state.tier=%s 不一致" % (tier, state["tier"]))

    effective = state.get("tier") or tier
    if effective in ("T2", "T3"):
        if not state.get("enabled_engineering"):
            report.error("enabled_engineering", "T2+ 必须登记启用的工程手段（loop/graph/self-verify/token-thrift）")
        if not state.get("safety_valve"):
            report.error("safety_valve", "T2+ 必须声明安全阀（max_rounds/max_requests_proxy/max_minutes/on_break）")
        if not state.get("graph"):
            report.error("graph", "T2+ 必须给出任务 DAG（graph.nodes）")

    known = set()
    if isinstance(state.get("graph"), dict):
        known = check_graph(state["graph"], report)

    for idx, step in enumerate(state.get("steps", []) or []):
        nid = step.get("node_id")
        if nid and known and nid not in known:
            report.error("steps[%d]" % idx, "node_id=%r 不存在于 graph.nodes" % nid)

    # v1.1 增强：契约版本 / 验收绑定 / 工件记忆 / 模型分层 / 边门覆盖
    check_contract_version(state, report)
    check_acceptance_binding(state, effective, report)
    check_artifacts(state, effective, report)
    check_model_tier(state.get("graph"), effective, report)
    check_edge_gate_coverage(state, effective, report)

    return report.finish()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
