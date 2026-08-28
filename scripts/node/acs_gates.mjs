#!/usr/bin/env node
/**
 * Agent Core Suite —— Node 版硬门禁（零第三方依赖，单文件）。
 *
 * 存在理由：原设计里硬门禁只有 Python 一种实现，环境无 Python 时整套件退化成纪律文档、
 * 强制力归零。本文件让「有 Node 无 Python」的环境同样拿到可机检的退出码。
 *
 * 铁律：阈值一律从 spec/thresholds.json 读取，本文件不得自带任何数字阈值。
 *       跨实现一致性由 tests/test_gates.py 的退出码对照测试守住。
 *
 * 用法：
 *   node acs_gates.mjs state     --state <task-state.json> [--schema <p>] [--tier T2]
 *   node acs_gates.mjs loop      --state <task-state.json> [--tier T2]
 *   node acs_gates.mjs checklist --state <task-state.json> [--tier T2]
 *   node acs_gates.mjs verify    --record <verify-record.json> [--schema <p>] [--tier T2]
 *   node acs_gates.mjs reality   ...   （不支持：需 Python AST，显式 USAGE_ERROR）
 *   node acs_gates.mjs run       --state <p> [--record <p>] [--tier T2]
 *   node acs_gates.mjs capability                （打印本实现的覆盖矩阵）
 *   node acs_gates.mjs doctor    [--target .] [--mode auto] [--json|--mode-only|--paths]
 *   node acs_gates.mjs check     --root <套件目录>   （清单齐全性，等价 install_check.py）
 *
 * 退出码：0=PASS，1=BLOCK，2=USAGE_ERROR（视为未验证=未完成）
 */

import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import process from "node:process";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

const EXIT_PASS = 0;
const EXIT_BLOCK = 1;
const EXIT_USAGE = 2;
const TIERS = ["T0", "T1", "T2", "T3"];

const HERE = path.dirname(fileURLToPath(import.meta.url));
// scripts/node/ -> 上两级即套件根（安装后为 .acs/），与 Python 侧解析规则一致
const SUITE_ROOT = path.dirname(path.dirname(HERE));
const SPEC_PATH = process.env.ACS_SPEC_PATH || path.join(SUITE_ROOT, "spec", "thresholds.json");

function usageExit(msg) {
  process.stderr.write(`[USAGE_ERROR] ${msg}\n`);
  process.stderr.write("视为未验证 = 未完成。\n");
  process.exit(EXIT_USAGE);
}

function loadSpec() {
  if (!fs.existsSync(SPEC_PATH) || !fs.statSync(SPEC_PATH).isFile()) {
    process.stderr.write(`[USAGE_ERROR] 找不到阈值真相源：${SPEC_PATH}\n`);
    process.stderr.write("拿不到阈值 = 无法计量 = 等于绕过闸门；本套件不使用内置默认值静默继续。\n");
    process.exit(EXIT_USAGE);
  }
  let data;
  try {
    data = JSON.parse(fs.readFileSync(SPEC_PATH, "utf-8"));
  } catch (e) {
    process.stderr.write(`[USAGE_ERROR] 阈值真相源不是合法 JSON：${SPEC_PATH}（${e.message}）\n`);
    process.exit(EXIT_USAGE);
  }
  for (const key of ["spec_version", "exit_codes", "tiers", "loop", "checklist", "vague_phrases"]) {
    if (!(key in data)) {
      process.stderr.write(`[USAGE_ERROR] 阈值真相源缺顶层字段 ${key}：${SPEC_PATH}\n`);
      process.exit(EXIT_USAGE);
    }
  }
  const c = data.exit_codes;
  if (c.pass !== EXIT_PASS || c.block !== EXIT_BLOCK || c.usage_error !== EXIT_USAGE) {
    process.stderr.write(`[USAGE_ERROR] 阈值真相源的退出码与实现不一致：${JSON.stringify(c)}\n`);
    process.exit(EXIT_USAGE);
  }
  const missing = TIERS.filter((t) => !(t in data.tiers));
  if (missing.length) {
    process.stderr.write(`[USAGE_ERROR] 阈值真相源缺分级 ${missing.join("/")}\n`);
    process.exit(EXIT_USAGE);
  }
  return data;
}

const SPEC = loadSpec();
const TIER_SPEC = SPEC.tiers;
const LOOP = SPEC.loop;
const CL = SPEC.checklist;
const VR = SPEC.verify_rank || {};
const VAGUE_ACCEPTANCE = SPEC.vague_phrases.acceptance;
const VAGUE_EVIDENCE = SPEC.vague_phrases.evidence;

// v1.1 增强节（与 Python 侧各门脚本的同名节逐项对齐；缺节时 Python 侧 usage_exit，跨实现测试会拦）
const EG = SPEC.edge_gates || {};
const AB = SPEC.acceptance_binding || {};
const ART = SPEC.artifacts || {};
const CT = SPEC.contract || {};
const MT = SPEC.model_tier || {};
const RETRY = SPEC.retry || {};
const DB = SPEC.double_blind || {};

const BANNED_INPUT_PHRASES = [
  "上文", "前文", "之前的讨论", "相关背景", "历史对话", "如前所述",
  "参考上面", "参考前面", "见上", "context above", "previous discussion",
];

const GATE_NAMES = {
  G0: "钢人门（双向论证 + 关键提问）",
  G1: "认知门（背景/过程/目标/外部/风险 + 缺口声明）",
  G2: "规划门（规划/设计标准/阶段明细/边界 + 工程手段登记）",
  G3: "执行门（真实实现 + 互审 + 成本闸门）",
  G4: "验证门（逐条验收 + 真实运行 + 方向校准）",
  G5: "交付门（对抗审核 ≥2 + 上线测试 ≥2 + 报告四要素）",
  G6: "进化门（经验沉淀 + RSI 闭环）",
};

// --------------------------------------------------------------- 基础设施

function loadJson(p, label) {
  if (!p) usageExit(`${label} 路径未提供`);
  if (!fs.existsSync(p) || !fs.statSync(p).isFile()) usageExit(`${label} 文件不存在：${p}`);
  try {
    return JSON.parse(fs.readFileSync(p, "utf-8"));
  } catch (e) {
    usageExit(`${label} 不是合法 JSON：${p}（${e.message}）`);
  }
}

function parseArgs(argv, spec, required) {
  const out = {};
  let i = 0;
  while (i < argv.length) {
    const tok = argv[i];
    if (tok in spec) {
      if (i + 1 >= argv.length) usageExit(`参数 ${tok} 缺少取值`);
      out[spec[tok]] = argv[i + 1];
      i += 2;
    } else if (tok === "-h" || tok === "--help") {
      out.help = "1";
      i += 1;
    } else {
      usageExit(`未知参数：${tok}（可用：${Object.keys(spec).sort().join(" ")}）`);
    }
  }
  for (const key of required) {
    if (!(key in out)) usageExit(`缺少必需参数 --${key}`);
  }
  return out;
}

function normTier(value, dflt = "T2") {
  const tier = String(value || dflt || "").toUpperCase();
  if (tier === "") return "";
  if (!TIERS.includes(tier)) usageExit(`tier 非法：${value}（可选 ${TIERS.join("/")}）`);
  return tier;
}

function median(values) {
  const ordered = values.slice().sort((a, b) => a - b);
  const n = ordered.length;
  if (n === 0) return null;
  const mid = Math.floor(n / 2);
  if (n % 2 === 1) return Number(ordered[mid]);
  return (Number(ordered[mid - 1]) + Number(ordered[mid])) / 2;
}

function isNum(v) {
  return typeof v === "number" && Number.isFinite(v);
}

function isInt(v) {
  return typeof v === "number" && Number.isInteger(v);
}

class Report {
  constructor(gate) {
    this.gate = gate;
    this.errors = [];
    this.warnings = [];
    this.notes = [];
  }
  error(where, msg) { this.errors.push([where, msg]); }
  warn(where, msg) { this.warnings.push([where, msg]); }
  note(msg) { this.notes.push(msg); }
  finish() {
    const out = [];
    out.push(`=== ${this.gate} ===`);
    for (const m of this.notes) out.push(`  note  ${m}`);
    for (const [w, m] of this.warnings) out.push(`  WARN  [${w}] ${m}`);
    for (const [w, m] of this.errors) out.push(`  BLOCK [${w}] ${m}`);
    if (this.errors.length) {
      out.push(`结果：BLOCK（${this.errors.length} 项违规，${this.warnings.length} 项告警）`);
      process.stdout.write(out.join("\n") + "\n");
      return EXIT_BLOCK;
    }
    out.push(`结果：PASS（0 项违规，${this.warnings.length} 项告警）`);
    process.stdout.write(out.join("\n") + "\n");
    return EXIT_PASS;
  }
}

// ------------------------------------------- JSON Schema 子集校验（对齐 Python 侧）

const KNOWN_TYPES = ["object", "array", "string", "boolean", "null", "number", "integer"];

function typeOk(value, expected) {
  switch (expected) {
    case "number": return isNum(value);
    case "integer": return isInt(value);
    case "boolean": return typeof value === "boolean";
    case "string": return typeof value === "string";
    case "null": return value === null;
    case "array": return Array.isArray(value);
    case "object": return value !== null && typeof value === "object" && !Array.isArray(value);
    default: return false;
  }
}

function validate(value, schema, where = "$") {
  const errs = [];
  if (schema === null || typeof schema !== "object" || Array.isArray(schema)) return errs;

  const expected = schema.type;
  if (expected) {
    const types = Array.isArray(expected) ? expected : [expected];
    const unknown = types.filter((t) => !KNOWN_TYPES.includes(t));
    if (unknown.length) {
      errs.push(`${where} 的 schema 声明了未知类型 ${unknown.join("/")}（疑为拼写错误，不予静默通过）`);
      return errs;
    }
    if (!types.some((t) => typeOk(value, t))) {
      const actual = value === null ? "NoneType" : Array.isArray(value) ? "list" : typeof value;
      errs.push(`${where} 类型应为 ${types.join("/")}，实际为 ${actual}`);
      return errs;
    }
  }

  if ("enum" in schema) {
    const hit = schema.enum.some((x) => JSON.stringify(x) === JSON.stringify(value));
    if (!hit) errs.push(`${where} 取值 ${JSON.stringify(value)} 越界，允许 ${JSON.stringify(schema.enum)}`);
  }

  if (typeof value === "string") {
    if ("minLength" in schema && value.length < schema.minLength) {
      errs.push(`${where} 长度 ${value.length} < 最小 ${schema.minLength}`);
    }
    if ("maxLength" in schema && value.length > schema.maxLength) {
      errs.push(`${where} 长度 ${value.length} > 最大 ${schema.maxLength}`);
    }
  }

  if (isNum(value)) {
    if ("minimum" in schema && value < schema.minimum) errs.push(`${where} 值 ${value} < 最小 ${schema.minimum}`);
    if ("maximum" in schema && value > schema.maximum) errs.push(`${where} 值 ${value} > 最大 ${schema.maximum}`);
  }

  if (Array.isArray(value)) {
    if ("minItems" in schema && value.length < schema.minItems) {
      errs.push(`${where} 元素数 ${value.length} < 最小 ${schema.minItems}`);
    }
    if ("maxItems" in schema && value.length > schema.maxItems) {
      errs.push(`${where} 元素数 ${value.length} > 最大 ${schema.maxItems}`);
    }
    if (schema.uniqueItems) {
      const seen = [];
      for (const item of value) {
        const key = JSON.stringify(item);
        if (seen.includes(key)) errs.push(`${where} 存在重复元素 ${key}`);
        seen.push(key);
      }
    }
    if (schema.items) {
      value.forEach((item, idx) => errs.push(...validate(item, schema.items, `${where}[${idx}]`)));
    }
  }

  if (value !== null && typeof value === "object" && !Array.isArray(value)) {
    for (const key of schema.required || []) {
      if (!(key in value)) errs.push(`${where} 缺少必需字段 ${key}`);
    }
    const props = schema.properties || {};
    for (const [key, sub] of Object.entries(props)) {
      if (key in value) errs.push(...validate(value[key], sub, `${where}.${key}`));
    }
    if (schema.additionalProperties === false) {
      for (const key of Object.keys(value)) {
        if (!(key in props)) errs.push(`${where} 出现未声明字段 ${key}`);
      }
    }
  }
  return errs;
}

function expandRefs(schema) {
  const defs = schema.$defs_inline || {};
  function walk(node) {
    if (node !== null && typeof node === "object" && !Array.isArray(node)) {
      if ("$ref_inline" in node) {
        const key = node.$ref_inline;
        if (!(key in defs)) usageExit(`schema 中 $ref_inline 引用了不存在的定义：${key}`);
        return walk(defs[key]);
      }
      const out = {};
      for (const [k, v] of Object.entries(node)) {
        if (k !== "$defs_inline") out[k] = walk(v);
      }
      return out;
    }
    if (Array.isArray(node)) return node.map(walk);
    return node;
  }
  return walk(schema);
}

// --------------------------------------------------- Python 语义对齐层
// 为什么需要这一层：跨实现一致性靠的不是「看起来差不多」，而是逐个取值/判类型语义对齐。
// Python 的 dict.get 缺键返回 None、isinstance(True, int) 为真、`x or []` 把 [] 也当假，
// 这些细节任何一处不对齐，同一份样本就会得出不同退出码。

function G(o, k, d = null) {
  if (o === null || typeof o !== "object" || Array.isArray(o)) return d;
  return Object.prototype.hasOwnProperty.call(o, k) ? o[k] : d;
}

function has(o, k) {
  return o !== null && typeof o === "object" && Object.prototype.hasOwnProperty.call(o, k);
}

function isDict(v) {
  return v !== null && typeof v === "object" && !Array.isArray(v);
}

/** 对齐 Python `x or []`：null/undefined/非数组一律给空数组。 */
function arr(v) {
  return Array.isArray(v) ? v : [];
}

/** 对齐 Python isinstance(v, int)：bool 也算 int。 */
function pyIsInt(v) {
  if (typeof v === "boolean") return true;
  return typeof v === "number" && Number.isInteger(v);
}

/** 对齐 Python `isinstance(v, int) and not isinstance(v, bool)`。 */
function pyIsIntStrict(v) {
  return typeof v === "number" && Number.isInteger(v);
}

/** 对齐 Python isinstance(v, (int, float))：bool 也算。 */
function pyIsNum(v) {
  if (typeof v === "boolean") return true;
  return typeof v === "number" && Number.isFinite(v);
}

function pyNum(v) {
  return typeof v === "boolean" ? (v ? 1 : 0) : v;
}

/** 对齐 Python float(v)：无法转换时 Python 抛异常（退出码 1），此处给 NaN 并由调用方记 BLOCK（同为 1）。 */
function pyFloat(v) {
  if (typeof v === "boolean") return v ? 1 : 0;
  if (typeof v === "number") return v;
  if (typeof v === "string" && v.trim() !== "" && Number.isFinite(Number(v))) return Number(v);
  return NaN;
}

function pyr(v) {
  if (v === null || v === undefined) return "None";
  if (typeof v === "boolean") return v ? "True" : "False";
  if (typeof v === "string") return `'${v}'`;
  if (Array.isArray(v)) return `[${v.map(pyr).join(", ")}]`;
  return String(v);
}

function pys(v) {
  if (v === null || v === undefined) return "None";
  if (typeof v === "boolean") return v ? "True" : "False";
  if (Array.isArray(v)) return `[${v.map(pyr).join(", ")}]`;
  return String(v);
}

function f1(v) { return Number(pyNum(v)).toFixed(1); }
function f2(v) { return Number(pyNum(v)).toFixed(2); }
function f4(v) { return Number(pyNum(v)).toFixed(4); }

/** 对齐 Python `(x or "").strip()`：非字符串按 Python 会抛异常，此处退化成空串并由 schema 门拦。 */
function str0(v) {
  return typeof v === "string" ? v : "";
}

const DEFAULT_STATE_SCHEMA = path.join(SUITE_ROOT, "templates", "task-state.schema.json");
const DEFAULT_RECORD_SCHEMA = path.join(SUITE_ROOT, "templates", "verify-record.schema.json");

// ------------------------------------------------ G2/Graph 门：契约 + 任务 DAG

function checkGraph(graph, report) {
  const nodes = arr(G(graph, "nodes"));
  const ids = nodes.map((n) => G(n, "id"));
  const known = new Set(ids);

  for (const nid of known) {
    if (ids.filter((x) => x === nid).length > 1) {
      report.error("graph.nodes", `节点 id 重复：${pys(nid)}`);
    }
  }

  const edges = arr(G(graph, "edges"));
  edges.forEach((edge, idx) => {
    for (const side of ["from", "to"]) {
      if (!known.has(G(edge, side))) {
        report.error(`graph.edges[${idx}]`, `${side} 指向不存在的节点 ${pyr(G(edge, side))}`);
      }
    }
    if (G(edge, "from") === G(edge, "to")) {
      report.error(`graph.edges[${idx}]`, `自环边（from == to == ${pys(G(edge, "from"))}）`);
    }
    // 边上验证门：与节点/步骤验收同规矩，无法判成败的命令验证等于没有门
    const gate = G(edge, "gate");
    if (isDict(gate) && G(gate, "kind") === "cmd" && !has(gate, "expect_exit")) {
      report.error(`graph.edges[${idx}].gate`,
        "边上验证门 kind=cmd 必须给出 expect_exit（无法判成败的命令验证等于没有门）");
    }
  });

  // 环检测（Kahn 拓扑排序）
  const indeg = new Map();
  const adj = new Map();
  for (const nid of known) { indeg.set(nid, 0); adj.set(nid, []); }
  for (const edge of edges) {
    const a = G(edge, "from"), b = G(edge, "to");
    if (known.has(a) && known.has(b) && a !== b) {
      adj.get(a).push(b);
      indeg.set(b, indeg.get(b) + 1);
    }
  }
  const queue = [...known].filter((nid) => indeg.get(nid) === 0);
  let seenCount = 0;
  while (queue.length) {
    const cur = queue.pop();
    seenCount += 1;
    for (const nxt of adj.get(cur)) {
      indeg.set(nxt, indeg.get(nxt) - 1);
      if (indeg.get(nxt) === 0) queue.push(nxt);
    }
  }
  if (seenCount !== known.size) {
    report.error("graph", `DAG 中存在环，无法拓扑排序（涉及 ${known.size - seenCount} 个节点）`);
  }

  // 孤立节点：有边存在时，某节点既无入边也无出边
  if (edges.length && known.size > 1) {
    const touched = new Set();
    for (const edge of edges) { touched.add(G(edge, "from")); touched.add(G(edge, "to")); }
    for (const nid of ids) {
      if (!touched.has(nid)) {
        report.error("graph.nodes", `节点 ${pys(nid)} 既无入边也无出边（孤立节点，依赖未声明）`);
      }
    }
  }

  const upstream = new Map();
  for (const nid of known) upstream.set(nid, new Set());
  for (const edge of edges) {
    const a = G(edge, "from"), b = G(edge, "to");
    if (upstream.has(b) && known.has(a)) upstream.get(b).add(a);
  }

  const outOwner = new Map();
  nodes.forEach((node, idx) => {
    const where = `graph.nodes[${idx}](${pys(G(node, "id"))})`;
    if (G(node, "owner") && G(node, "owner") === G(node, "verifier")) {
      report.error(where, `owner == verifier == ${pyr(G(node, "owner"))}，禁止自评签字`);
    }
    if (G(node, "deterministic") === true && G(node, "calls_model") === true) {
      report.error(where, "deterministic=true 的节点声明了 calls_model=true（确定性工作禁止调模型）");
    }
    for (const phrase of BANNED_INPUT_PHRASES) {
      for (const item of arr(G(node, "inputs"))) {
        if (typeof item === "string" && item.includes(phrase)) {
          report.error(where, `inputs 含模糊回灌写法 ${pyr(item)}（须写具体路径或上游输出字段）`);
          break;
        }
      }
    }
    for (const item of arr(G(node, "inputs"))) {
      if (typeof item === "string" && item.includes(".") && known.has(item.split(".")[0])) {
        const src = item.split(".")[0];
        const up = upstream.get(G(node, "id")) || new Set();
        if (src !== G(node, "id") && !up.has(src)) {
          report.error(where, `inputs 引用了节点 ${pys(src)} 的输出，但缺少 ${pys(src)} -> ${pys(G(node, "id"))} 的边`);
        }
      }
    }
    for (const word of ["并且", "同时", "以及"]) {
      if (str0(G(node, "goal")).includes(word)) {
        report.warn(where, `goal 含 ${pyr(word)}，疑似一步多产出，建议再拆`);
      }
    }
    for (const out of arr(G(node, "outputs"))) {
      if (outOwner.has(out)) {
        report.warn(where, `输出 ${pys(out)} 与节点 ${pys(outOwner.get(out))} 重复，并行时会写冲突`);
      } else {
        outOwner.set(out, G(node, "id"));
      }
    }
  });

  const nodeById = new Map(nodes.map((n) => [G(n, "id"), n]));
  const edgePairs = new Set(edges.map((e) => JSON.stringify([G(e, "from"), G(e, "to")])));
  const pairHit = (a, b) => edgePairs.has(JSON.stringify([a, b]));

  arr(G(graph, "parallel_groups")).forEach((group, gidx) => {
    const g = arr(group);
    for (const nid of g) {
      if (!known.has(nid)) {
        report.error(`graph.parallel_groups[${gidx}]`, `引用不存在的节点 ${pyr(nid)}`);
      }
    }
    for (let i = 0; i < g.length; i += 1) {
      for (let j = 0; j < g.length; j += 1) {
        if (i === j) continue;
        if (pairHit(g[i], g[j])) {
          report.error(`graph.parallel_groups[${gidx}]`,
            `${pys(g[i])} -> ${pys(g[j])} 存在数据依赖，不能放入同一并行组`);
        }
      }
    }
    const outs = new Map();
    for (const nid of g) {
      for (const out of arr(G(nodeById.get(nid) || {}, "outputs"))) {
        if (outs.has(out)) {
          report.error(`graph.parallel_groups[${gidx}]`,
            `节点 ${pys(nid)} 与 ${pys(outs.get(out))} 并行却写同一产出 ${pys(out)}`);
        }
        outs.set(out, nid);
      }
    }
  });

  arr(G(graph, "barriers")).forEach((barrier, bidx) => {
    if (!known.has(G(barrier, "at"))) {
      report.error(`graph.barriers[${bidx}]`, `at 指向不存在的节点 ${pyr(G(barrier, "at"))}`);
    }
    for (const nid of arr(G(barrier, "waits_for"))) {
      if (!known.has(nid)) {
        report.error(`graph.barriers[${bidx}]`, `waits_for 含不存在的节点 ${pyr(nid)}`);
      } else if (!pairHit(nid, G(barrier, "at"))) {
        report.warn(`graph.barriers[${bidx}]`,
          `汇聚点 ${pys(G(barrier, "at"))} 等待 ${pys(nid)}，但两者之间没有边（依赖未显式声明）`);
      }
    }
    // 汇聚语义：min_success 必须给出最小成功数，且落在等待分支数范围内
    if (G(barrier, "policy") === "min_success") {
      const mc = G(barrier, "min_count");
      if (!pyIsIntStrict(mc)) {
        report.error(`graph.barriers[${bidx}]`,
          "policy=min_success 必须给出 min_count（最小成功数，不写等于没有汇聚规则）");
      } else if (mc < 1 || mc > arr(G(barrier, "waits_for")).length) {
        report.error(`graph.barriers[${bidx}]`,
          `min_count=${pys(mc)} 超出等待分支数 ${arr(G(barrier, "waits_for")).length}（汇聚规则必须可满足）`);
      }
    }
  });

  return known;
}

// --------------------------------------------- v1.1 增强（与 Python 侧逐项对齐）

function checkContractVersion(state, report) {
  // 契约版本化：格式非法才拦，高于已知只告警（旧版本文件照常通过，向后兼容）
  const cv = G(state, "contract_version");
  if (cv === undefined || cv === null) return;
  const pat = str0(CT.pattern);
  if (typeof cv !== "string" || !new RegExp(pat).test(cv)) {
    report.error("contract_version",
      `契约版本 ${pyr(cv)} 不符合语义化版本 ${pat}（改了规矩系统才能知道新旧关系）`);
    return;
  }
  const cur = cv.split(".").map(Number);
  const kn = str0(CT.known_version).split(".").map(Number);
  if (cur.some(Number.isNaN) || kn.some(Number.isNaN)) return;  // 防御分支（pattern 已保证纯数字）
  let higher = false;
  for (let i = 0; i < Math.max(cur.length, kn.length); i += 1) {
    const x = cur[i] || 0;
    const y = kn[i] || 0;
    if (x !== y) { higher = x > y; break; }
  }
  if (higher) {
    report.warn("contract_version",
      `文件版本 ${pys(cv)} 高于套件已知版本 ${pys(CT.known_version)}（来自更新套件？建议核对门禁行为）`);
  }
}

function checkAcceptanceBinding(state, tier, report) {
  // 验收必须打到声明的产出物（SWE Refactor Bench 教训：交白卷也满分）
  if (!arr(AB.apply_tiers).includes(tier)) return;
  const units = [];
  arr(G(state, "steps")).forEach((step, idx) => {
    if (isDict(step)) {
      units.push([`steps[${idx}](${pys(G(step, "id"))})`, arr(G(step, "outputs")), arr(G(step, "acceptance"))]);
    }
  });
  const graph = G(state, "graph");
  if (isDict(graph)) {
    arr(G(graph, "nodes")).forEach((node) => {
      if (isDict(node)) {
        units.push([`graph.nodes(${pys(G(node, "id"))})`, arr(G(node, "outputs")), arr(G(node, "acceptance"))]);
      }
    });
  }
  for (const [where, outs, acc] of units) {
    const fileOutputs = outs.filter(
      (o) => typeof o === "string" && (o.includes("/") || o.includes("\\") || o.includes(".")));
    if (!fileOutputs.length || !acc.length) continue;
    const values = acc.map((a) => (isDict(a) ? str0(G(a, "value")) : ""));
    for (const out of fileOutputs) {
      const base = out.replace(/\\/g, "/").split("/").pop();
      if ([...base].length < AB.min_basename_chars) continue;
      if (!values.some((v) => v.includes(base))) {
        report.error(`${where}.acceptance`,
          `产出物 ${pys(out)} 未出现在任何验收命令里（交白卷盲区：验收打的是输入而非产出，`
          + `证明的是『没改坏』不是『真的交付了』）`);
      }
    }
  }
}

function checkArtifacts(state, tier, report) {
  // 工件记忆层：transcript 不是数据库，产出必须能溯源到生产节点；supersedes 链不成环
  const arts = G(state, "artifacts");
  if (arts === undefined || arts === null) {
    if (arr(ART.required_tiers).includes(tier)) {
      report.error("artifacts",
        `${tier} 级必须登记工件（Artifact Memory：transcript 不是数据库，`
        + `产出必须能溯源到生产节点）`);
    }
    return;
  }
  if (!Array.isArray(arts)) return;  // 形状交给 schema
  const graph = G(state, "graph");
  const nodeIds = new Set();
  if (isDict(graph)) {
    for (const n of arr(G(graph, "nodes"))) {
      if (isDict(n)) nodeIds.add(G(n, "id"));
    }
  }
  const ids = new Set(arts.filter((a) => isDict(a)).map((a) => G(a, "id")));
  arts.forEach((a, idx) => {
    if (!isDict(a)) return;
    const at = `artifacts[${idx}]`;
    const pb = G(a, "produced_by");
    if (nodeIds.size && !nodeIds.has(pb)) {
      report.error(at, `produced_by=${pyr(pb)} 不指向任何 graph 节点（工件必须能溯源到生产节点）`);
    }
    const sup = G(a, "supersedes");
    if (sup !== undefined && sup !== null && !ids.has(sup)) {
      report.error(at, `supersedes=${pyr(sup)} 引用不存在的工件 id（版本谱系断链）`);
    }
  });
  // supersedes 链环检测：谱系必须是 DAG
  const nxt = new Map();
  for (const a of arts) {
    if (isDict(a)) nxt.set(G(a, "id"), G(a, "supersedes"));
  }
  for (const aid of nxt.keys()) {
    const seen = new Set();
    let cur = nxt.get(aid);
    while (cur !== undefined && cur !== null && nxt.has(cur)) {
      if (cur === aid) {
        report.error("artifacts", `工件 ${pys(aid)} 的 supersedes 链成环（版本谱系必须是 DAG）`);
        break;
      }
      if (seen.has(cur)) break;  // 别的环已报或将于其自身起点报
      seen.add(cur);
      cur = nxt.get(cur);
    }
  }
  if (arr(ART.required_tiers).includes(tier) && arts.length < ART.min_artifacts) {
    report.error("artifacts", `工件 ${arts.length} 个 < ${ART.min_artifacts} 个（${tier} 级下限）`);
  }
}

function checkModelTier(graph, tier, report) {
  // T3 调模型的节点必须声明档位：模型分层让成本看得见
  if (!arr(MT.required_tiers).includes(tier) || !isDict(graph)) return;
  arr(G(graph, "nodes")).forEach((node, idx) => {
    if (!isDict(node)) return;
    if (G(node, "calls_model") === true && !str0(G(node, "model_tier")).trim()) {
      report.error(`graph.nodes[${idx}](${pys(G(node, "id"))})`,
        "calls_model=true 的节点未声明 model_tier（模型分层让成本看得见：贵模型干杂活必须暴露）");
    }
  });
}

function checkEdgeGateCoverage(state, tier, report) {
  // T3 图至少一条边挂验证门：无门边让上游错误直接流进所有下游节点
  if (!arr(EG.required_tiers).includes(tier)) return;
  const graph = G(state, "graph");
  if (!isDict(graph)) return;  // T2+ 已在 gateState 里拦缺 graph
  const edges = arr(G(graph, "edges"));
  if (!edges.length) return;  // 单节点图无边不罚
  const gated = edges.filter((e) => isDict(e) && isDict(G(e, "gate"))).length;
  if (gated < EG.min_edge_gates) {
    report.error("graph.edges",
      `${tier} 级图 ${edges.length} 条边中只有 ${gated} 条挂验证门（下限 ${EG.min_edge_gates}）：`
      + `边是数据契约，无门边让上游错误直接流进下游`);
  }
}

function gateState(argv) {
  const args = parseArgs(argv, { "--state": "state", "--schema": "schema", "--tier": "tier" }, ["state"]);
  const tier = normTier(args.tier, "");
  const schemaPath = args.schema || DEFAULT_STATE_SCHEMA;
  const schema = expandRefs(loadJson(schemaPath, "schema"));
  const state = loadJson(args.state, "task-state");

  const report = new Report("gate_state_validate (契约 + 任务 DAG)");
  report.note(`state=${args.state} tier=${tier}`);

  for (const err of validate(state, schema, "$")) report.error("schema", err);

  if (G(state, "tier") && tier && G(state, "tier") !== tier) {
    report.error("tier", `命令行 tier=${tier} 与 state.tier=${pys(G(state, "tier"))} 不一致`);
  }

  const effective = G(state, "tier") || tier;
  if (effective === "T2" || effective === "T3") {
    if (!G(state, "enabled_engineering")) {
      report.error("enabled_engineering", "T2+ 必须登记启用的工程手段（loop/graph/self-verify/token-thrift）");
    }
    if (!G(state, "safety_valve")) {
      report.error("safety_valve", "T2+ 必须声明安全阀（max_rounds/max_requests_proxy/max_minutes/on_break）");
    }
    if (!G(state, "graph")) {
      report.error("graph", "T2+ 必须给出任务 DAG（graph.nodes）");
    }
  }

  let known = new Set();
  if (isDict(G(state, "graph"))) known = checkGraph(G(state, "graph"), report);

  arr(G(state, "steps")).forEach((step, idx) => {
    const nid = G(step, "node_id");
    if (nid && known.size && !known.has(nid)) {
      report.error(`steps[${idx}]`, `node_id=${pyr(nid)} 不存在于 graph.nodes`);
    }
  });

  // v1.1 增强：契约版本 / 验收绑定 / 工件记忆 / 模型分层 / 边门覆盖
  checkContractVersion(state, report);
  checkAcceptanceBinding(state, effective, report);
  checkArtifacts(state, effective, report);
  checkModelTier(G(state, "graph"), effective, report);
  checkEdgeGateCoverage(state, effective, report);

  return report.finish();
}

// ------------------------------------------------------- Loop 门：四道成本闸门

// token 计量：与 Python 侧 gate_loop_guard.check_cost_metering 逐项对齐
const CM = SPEC.cost_metering || {};

function checkCostMetering(state, tier, report) {
  const cm = G(state, "cost_metering");
  if (!isDict(cm)) {
    if (arr(CM.declare_required_tiers).includes(tier)) {
      report.error("cost_metering",
        `${tier} 级必须声明 cost_metering.source：不交代计量来源，proxy 数字就无从审计`
        + `（真拿不到就显式写 source=unavailable，沉默不算声明）`);
    }
    return;
  }

  const src = G(cm, "source");
  if (!arr(CM.sources).includes(src)) {
    report.error("cost_metering", `source=${pyr(src)} 不在允许来源 ${pyr(arr(CM.sources))} 内`);
    return;
  }

  const actual = G(cm, "tokens_actual");
  const hasActual = pyIsIntStrict(actual);
  const ev = str0(G(cm, "evidence_ref")).trim();

  if (arr(CM.auditable_sources).includes(src)) {
    if (!hasActual) {
      report.error("cost_metering", `source=${src} 声称可审计却没有 tokens_actual：声称拿到真账就必须回填数字`);
    }
    if ([...ev].length < CM.min_evidence_chars) {
      report.error("cost_metering",
        `source=${src} 必须给出 evidence_ref（usage 落盘路径或取数命令），当前 ${pyr(G(cm, "evidence_ref"))} 不足 ${CM.min_evidence_chars} 字`);
    } else {
      const low = ev.toLowerCase();
      for (const bad of VAGUE_EVIDENCE) {
        if (ev.includes(bad) || low.includes(bad)) {
          report.error("cost_metering", `evidence_ref 是空话 ${pyr(bad)}：要的是可复查的路径或命令，不是结论`);
          break;
        }
      }
    }
  } else if (hasActual) {
    report.error("cost_metering",
      `source=${src} 不可审计却填了 tokens_actual=${pyNum(actual)}：请退回 *_proxy 字段，`
      + `字段名带后缀就是为了不冒充真实计量`);
  }

  if (src === "unavailable" && ev) {
    report.warn("cost_metering", "source=unavailable 却给了 evidence_ref，二者矛盾（按无计量处理）");
  }

  const ins = G(cm, "input_tokens_actual");
  const outs = G(cm, "output_tokens_actual");
  if (hasActual && pyIsIntStrict(ins) && pyIsIntStrict(outs) && ins + outs !== actual) {
    report.error("cost_metering",
      `input+output=${ins + outs} != tokens_actual=${pyNum(actual)}，分项与总量不自洽（计量必须能对上账）`);
  }

  const caps = CM.max_tokens_actual_by_tier || {};
  const cap = has(caps, tier) ? caps[tier] : null;
  if (hasActual && pyIsIntStrict(cap) && actual > cap) {
    report.error("cost_metering",
      `tokens_actual=${pyNum(actual)} > ${tier} 级上限 ${cap}，成本已失控`
      + `（反面基线：同框架对照模型 9 个任务共 956,630 token）`);
  }
}

function checkSafetyValve(state, tier, report) {
  const valve = G(state, "safety_valve");
  if (tier === "T0") return;
  if (!isDict(valve)) {
    report.error("safety_valve", "未声明安全阀（max_rounds/max_requests_proxy/max_minutes/on_break），循环无上限");
    return;
  }
  const spec = TIER_SPEC[tier];
  for (const field of ["max_rounds", "max_minutes"]) {
    if (!pyIsIntStrict(G(valve, field))) {
      report.error("safety_valve", `${field} 缺失或非整数（${pyr(G(valve, field))}）：无上限等于没有安全阀`);
    }
  }
  const rounds = has(valve, "max_rounds") ? valve.max_rounds : 0;
  const minutes = has(valve, "max_minutes") ? valve.max_minutes : 0;
  if (rounds > spec.max_rounds) {
    report.error("safety_valve", `max_rounds=${pys(G(valve, "max_rounds"))} 超过 ${tier} 级上限 ${spec.max_rounds}`);
  }
  if (minutes > spec.max_minutes) {
    report.error("safety_valve", `max_minutes=${pys(G(valve, "max_minutes"))} 超过 ${tier} 级上限 ${spec.max_minutes}`);
  }
  const steps = arr(G(state, "steps"));
  if (G(valve, "max_rounds") && steps.length > valve.max_rounds) {
    report.error("safety_valve",
      `已执行 ${steps.length} 步 > max_rounds=${pys(valve.max_rounds)}，安全阀已触发却未记录中断处置`);
  }
}

function checkStep(idx, step, tier, report) {
  const where = `steps[${idx}](${pys(G(step, "id"))})`;

  for (const field of LOOP.required_step_fields) {
    if (!has(step, field)) {
      report.error(where, `缺少计量字段 ${field}：无法计量即等于绕过闸门，不得省略`);
    }
  }

  const outs = arr(G(step, "outputs"));
  if (outs.length > LOOP.max_outputs_per_step) {
    report.error(where, `单步声明 ${outs.length} 个产出（上限 ${LOOP.max_outputs_per_step}），违反窄步闸，必须拆分`);
  }
  if (!outs.length) report.error(where, "单步没有任何产出，不是有效工作单元");

  const budget = G(step, "budget_min");
  if (pyIsInt(budget) && pyNum(budget) > LOOP.max_budget_min) {
    report.error(where, `budget_min=${pyNum(budget)} > ${LOOP.max_budget_min} 分钟，单步目标过重`);
  }

  const acc = arr(G(step, "acceptance"));
  if (!acc.length) report.error(where, "缺少 acceptance，完成标准不清晰");
  acc.forEach((item, aidx) => {
    const val = isDict(item) ? str0(G(item, "value")) : "";
    const kind = isDict(item) ? str0(G(item, "kind")) : "";
    if (kind === "cmd" && !has(item, "expect_exit")) {
      report.error(`${where}.acceptance[${aidx}]`, "kind=cmd 必须给出 expect_exit（退出码断言）");
    }
    const low = val.toLowerCase();
    for (const bad of VAGUE_ACCEPTANCE) {
      if (val.includes(bad) || low.includes(bad)) {
        report.error(`${where}.acceptance[${aidx}]`, `完成标准含空话 ${pyr(bad)}：${pyr(val)}`);
        break;
      }
    }
  });

  if (G(step, "refeed_full_history") === true) {
    report.error(where, "refeed_full_history=true，全量上文回灌（输入 token 是成本主项，禁止）");
  }
  const ctx = G(step, "context_bytes_proxy");
  if (pyIsInt(ctx) && pyNum(ctx) > LOOP.max_context_bytes) {
    report.error(where, `context_bytes_proxy=${pyNum(ctx)} > ${LOOP.max_context_bytes}，单步上下文过重`);
  }
  const filesRead = G(step, "files_read_proxy");
  if (pyIsInt(filesRead) && pyNum(filesRead) > LOOP.max_files_read) {
    report.error(where, `files_read_proxy=${pyNum(filesRead)} > ${LOOP.max_files_read}，违反渐进披露（先检索定位再定点读）`);
  }
  const summ = G(step, "summary_chars");
  if (pyIsInt(summ) && pyNum(summ) > LOOP.max_summary_chars) {
    report.error(where, `summary_chars=${pyNum(summ)} > ${LOOP.max_summary_chars}，压缩总结超长`);
  }
  if (pyIsInt(summ) && pyNum(summ) > 0 && typeof G(step, "summary") === "string") {
    const real = [...G(step, "summary")].length === G(step, "summary").length
      ? G(step, "summary").length : [...G(step, "summary")].length;
    if (Math.abs(real - pyNum(summ)) > LOOP.summary_chars_tolerance) {
      report.error(where, `summary_chars=${pyNum(summ)} 与 summary 实际长度 ${real} 不符（禁止漏记/虚报）`);
    }
  }

  const ratio = G(step, "think_ratio");
  if (pyIsNum(ratio) && pyNum(ratio) > LOOP.max_think_ratio) {
    report.error(where, `think_ratio=${f2(ratio)} > ${f2(LOOP.max_think_ratio)}，应改写最小可执行验证而非继续推理`);
  }

  // 有界重试（v1.1）：重试必须有上限、有留痕、有变化（同法重试即空转）
  const retries = G(step, "retries");
  if (retries !== undefined && retries !== null) {
    if (!pyIsIntStrict(retries) || retries < 0) {
      report.error(where, `retries=${pyr(retries)} 必须是非负整数（重试次数必须可计量）`);
    } else if (retries > RETRY.max_retries) {
      report.error(where,
        `retries=${pyNum(retries)} 超过上限 ${RETRY.max_retries}：`
        + `同法重试超过 ${RETRY.max_retries} 次就该换路，不是再来一次`);
    } else if (retries > 0) {
      const reason = str0(G(step, "retry_reason")).trim();
      const delta = str0(G(step, "delta_from_last")).trim();
      if ([...reason].length < RETRY.min_reason_chars) {
        report.error(where,
          `retries=${pyNum(retries)} 但 retry_reason 缺失或过短（≥${RETRY.min_reason_chars} 字）：重试不写原因等于掩盖失败`);
      }
      if ([...delta].length < RETRY.min_delta_chars) {
        report.error(where,
          `retries=${pyNum(retries)} 但 delta_from_last 缺失或过短（≥${RETRY.min_delta_chars} 字）：`
          + `说不清这次与上次差在哪，就是同法重试`);
      }
    }
  }

  if ((tier === "T2" || tier === "T3") && G(step, "builder") && G(step, "builder") === G(step, "verifier")) {
    report.error(where, `builder == verifier == ${pyr(G(step, "builder"))}，禁止自评签字`);
  }
}

function checkSpin(steps, report) {
  let streak = 0;
  steps.forEach((step, idx) => {
    const delta = G(step, "evidence_delta");
    if (pyIsInt(delta) && pyNum(delta) === 0) {
      streak += 1;
      if (streak >= LOOP.spin_strikes) {
        report.error(`steps[${idx}](${pys(G(step, "id"))})`,
          `连续 ${streak} 步 evidence_delta=0（two-strike）：必须停止并升级换路，禁止同法重试`);
      }
    } else {
      streak = 0;
    }
  });
}

function checkTrendState(state, report) {
  const trend = arr(G(state, "progress_trend"));
  let streak = 0;
  for (let i = 1; i < trend.length; i += 1) {
    if (trend[i] <= trend[i - 1]) {
      streak += 1;
      if (streak >= LOOP.spin_strikes) {
        report.error("progress_trend",
          `验证分数连续 ${streak} 次不升（${i >= 2 ? pys(trend[i - 2]) : "-"} → ${pys(trend[i - 1])} → ${pys(trend[i])}），方向已偏离，必须换路`);
      }
    } else {
      streak = 0;
    }
    if (trend[i] < trend[i - 1]) {
      report.warn("progress_trend", `第 ${i + 1} 次评分下降（${pys(trend[i - 1])} → ${pys(trend[i])}），应回滚到上一高分状态`);
    }
  }
}

function gateLoop(argv) {
  const args = parseArgs(argv, { "--state": "state", "--tier": "tier" }, ["state"]);
  const state = loadJson(args.state, "task-state");
  const tier = normTier(args.tier || G(state, "tier"), "T2");

  const report = new Report("gate_loop_guard (四道成本闸门)");
  report.note(`state=${args.state} tier=${tier}`);

  if (tier === "T0") {
    report.note("T0 仅适用诚实纪律，四道成本闸门不启用。");
    return report.finish();
  }

  const steps = arr(G(state, "steps"));
  if (!steps.length) {
    report.error("steps", "没有任何步骤记录，无法证明执行过程（状态外置是硬要求）");
  }
  steps.forEach((step, idx) => checkStep(idx, step, tier, report));
  checkSpin(steps, report);
  checkTrendState(state, report);
  checkSafetyValve(state, tier, report);
  checkCostMetering(state, tier, report);
  return report.finish();
}

// ------------------------------------------------------ G0-G6 门禁清单核对

// 软约束硬化层：与 Python 侧 gate_checklist.check_* 逐项对齐（v1.1 增 G5_blind_reviews）。
// 两份实现对同一 state 必须给出相同退出码，否则「通过」就变成抽奖。
const HD = CL.hardened;
const MIN_PRO = HD.min_steelman_pro;
const MIN_CON = HD.min_steelman_con;
const MAX_KEY_QUESTIONS = HD.max_key_questions;
const MIN_CLAIM_CHARS = HD.min_claim_chars;
const MIN_BASIS_CHARS = HD.min_basis_chars;
const MIN_REVIEWS = HD.min_reviews;
const COGNITION_FIELDS = HD.cognition_fields;
const PLAN_FIELDS = HD.plan_fields;
const REPORT_FIELDS = HD.report_fields;
const RSI_FIELDS = HD.rsi_fields;
const FIELD_OWNER_GATE = HD.field_owner_gate;
const HARDENED_BY_TIER = HD.required_by_tier;

const HARDENED_LABELS = {
  G0_steelman: `G0 双向钢人（正/反各 ≥${MIN_PRO} 条且条条有依据 + 分歧定位）`,
  G1_cognition: "G1 认知摘要六要素",
  G2_plan: "G2 规划四要素（规划/设计标准/阶段明细/边界）",
  G3_review_closure: "G3 双身份互审闭环（签字人不同 + 发现→修复→复验）",
  G4_acceptance_actual: "G4 验收逐条实证（acceptance.actual）",
  G5_report: "G5 交付报告四要素",
  G5_blind_reviews: `G5 双盲审查（独立审查者 ≥${DB.min_reviewers} 人 + 非建造者 + 分歧仲裁）`,
  G6_rsi: "G6 RSI 闭环（问题→根因→动作→验证→沉淀位置）",
};

const VAGUE_EVIDENCE_LOWER = VAGUE_EVIDENCE.map((p) => p.toLowerCase());

function isVague(text) {
  const low = str0(text).trim().toLowerCase();
  return VAGUE_EVIDENCE_LOWER.indexOf(low) >= 0;
}

/** 对齐 Python _nonempty_strings：非列表返回 null（视为缺字段），否则过滤出真有内容的项。 */
function nonemptyStrings(value) {
  if (!Array.isArray(value)) return null;
  return value.filter((x) => typeof x === "string" && x.trim());
}

function checkFieldPlacement(gates, report) {
  for (const key of Object.keys(gates).sort()) {
    const entry = gates[key];
    if (!isDict(entry)) continue;
    for (const field of Object.keys(FIELD_OWNER_GATE).sort()) {
      const owner = FIELD_OWNER_GATE[field];
      if (has(entry, field) && key !== owner) {
        report.error(`gates.${key}.${field}`,
          `字段 ${field} 属于 ${owner}，写在 ${key} 下会让机检落空`);
      }
    }
  }
}

function checkSteelman(entry, report) {
  const where = "gates.G0.steelman";
  const sm = G(entry, "steelman");
  if (!isDict(sm)) {
    report.error(where, "缺双向钢人记录：正/反论证没有结构化留痕就无法核查是否真做过");
    return;
  }
  for (const [side, floor, label] of [["pro", MIN_PRO, "正向"], ["con", MIN_CON, "反向"]]) {
    const items = G(sm, side);
    if (!Array.isArray(items) || items.length < floor) {
      report.error(`${where}.${side}`,
        `${label}钢人 ${Array.isArray(items) ? items.length : 0} 条 < ${floor} 条`);
      continue;
    }
    items.forEach((item, i) => {
      const at = `${where}.${side}[${i}]`;
      if (!isDict(item)) {
        report.error(at, "条目须为 {claim, basis} 对象");
        return;
      }
      const claim = str0(G(item, "claim")).trim();
      const basis = str0(G(item, "basis")).trim();
      if (claim.length < MIN_CLAIM_CHARS) {
        report.error(at, `claim 过短（${claim.length} 字 < ${MIN_CLAIM_CHARS}）`);
      }
      if (basis.length < MIN_BASIS_CHARS) {
        report.error(at, `basis 过短（${basis.length} 字 < ${MIN_BASIS_CHARS}）：只有主张没有依据等于没论证`);
      } else if (isVague(basis)) {
        report.error(at, `basis 是空话 ${pyr(basis)}，须写文件/命令/实测值`);
      }
    });
  }
  if (!str0(G(sm, "divergence")).trim()) {
    report.error(`${where}.divergence`, "缺分歧定位：不写分歧就等于没做双向论证");
  }
  if (!str0(G(sm, "key_variable")).trim()) {
    report.error(`${where}.key_variable`, "缺最可能改变结论的关键变量");
  }

  const questions = G(entry, "key_questions");
  if (questions === null || questions === undefined) {
    if (!str0(G(entry, "questions_compressed_reason")).trim()) {
      report.error("gates.G0.key_questions",
        "既没有关键问题记录，也没写压缩理由（可压缩，但必须记录理由）");
    }
    return;
  }
  if (!Array.isArray(questions)) {
    report.error("gates.G0.key_questions", "key_questions 须为数组");
    return;
  }
  if (questions.length > MAX_KEY_QUESTIONS) {
    report.error("gates.G0.key_questions",
      `关键问题 ${questions.length} 个 > ${MAX_KEY_QUESTIONS} 个上限（问太多是把决策推回主人）`);
  }
  questions.forEach((item, i) => {
    const at = `gates.G0.key_questions[${i}]`;
    if (!isDict(item)) {
      report.error(at, "条目须为 {q, answer} 对象");
      return;
    }
    if (!str0(G(item, "answer")).trim()) {
      report.error(at, `问题 ${pyr(str0(G(item, "q")).slice(0, 20))} 没有主人答复，不得当作已澄清`);
    }
  });
}

function checkCognition(entry, report) {
  const where = "gates.G1.cognition";
  const cg = G(entry, "cognition");
  if (!isDict(cg)) {
    report.error(where, `缺《任务认知摘要》结构化记录（${COGNITION_FIELDS.join("/")}）`);
    return;
  }
  for (const field of COGNITION_FIELDS) {
    const at = `${where}.${field}`;
    const value = G(cg, field);
    if (Array.isArray(value)) {
      const items = nonemptyStrings(value);
      if (!items.length) report.error(at, `为空：认知摘要缺 ${field} 一类`);
      for (const item of items) {
        if (isVague(item)) report.error(at, `含空话 ${pyr(item)}`);
      }
    } else if (typeof value === "string") {
      if (!value.trim()) {
        report.error(at, `为空：认知摘要缺 ${field} 一类`);
      } else if (isVague(value)) {
        report.error(at, `是空话 ${pyr(value)}`);
      }
    } else {
      report.error(at, `缺字段 ${field}（认知摘要必须覆盖六类）`);
    }
  }
  arr(G(cg, "assumptions")).forEach((item, i) => {
    if (isDict(item) && !str0(G(item, "risk")).trim()) {
      report.error(`${where}.assumptions[${i}]`, "未确认假设必须标注风险");
    }
  });
}

function checkPlan(entry, report) {
  const where = "gates.G2.plan";
  const plan = G(entry, "plan");
  if (!isDict(plan)) {
    report.error(where, `缺规划四要素结构化记录（${PLAN_FIELDS.join("/")}）`);
    return;
  }
  for (const field of ["phases", "stage_goals"]) {
    const items = nonemptyStrings(G(plan, field));
    if (!items || !items.length) report.error(`${where}.${field}`, `为空：缺 ${field}`);
  }
  const standards = str0(G(plan, "design_standards")).trim();
  if (!standards) {
    report.error(`${where}.design_standards`, "缺设计与标准定义（架构/数据/API 契约位置）");
  } else if (isVague(standards)) {
    report.error(`${where}.design_standards`, `是空话 ${pyr(standards)}`);
  }
  const boundary = G(plan, "boundary");
  if (!isDict(boundary)) {
    report.error(`${where}.boundary`, "缺边界约束（in_scope/out_scope/out_of_scope_policy）");
    return;
  }
  const inScope = nonemptyStrings(G(boundary, "in_scope"));
  if (!inScope || !inScope.length) {
    report.error(`${where}.boundary.in_scope`, "为空：没写做什么");
  }
  if (G(boundary, "out_scope") === null || G(boundary, "out_scope") === undefined) {
    report.error(`${where}.boundary.out_scope`, "缺 out_scope：不写不做什么就守不住边界（无排除项写空数组）");
  }
  if (!str0(G(boundary, "out_of_scope_policy")).trim()) {
    report.error(`${where}.boundary.out_of_scope_policy`, "缺边界外需求的处置方式");
  }
}

function checkReviewClosure(entry, report) {
  const where = "gates.G3.reviews";
  const reviews = G(entry, "reviews");
  if (!Array.isArray(reviews) || reviews.length < MIN_REVIEWS) {
    report.error(where,
      `互审记录 ${Array.isArray(reviews) ? reviews.length : 0} 条 < ${MIN_REVIEWS} 条`
      + "（Builder/Verifier 双身份分离交叉签字）");
    return;
  }
  reviews.forEach((item, i) => {
    const at = `${where}[${i}]`;
    if (!isDict(item)) {
      report.error(at, "条目须为对象");
      return;
    }
    const builder = str0(G(item, "builder")).trim();
    const verifier = str0(G(item, "verifier")).trim();
    if (!builder || !verifier) {
      report.error(at, "builder/verifier 必须都签字");
    } else if (builder === verifier) {
      report.error(at, `builder == verifier == ${pyr(builder)}，自评签字不算互审`);
    }
    const findings = G(item, "findings");
    if (!Array.isArray(findings)) {
      report.error(at, "缺 findings（无问题写空数组，字段不得缺）");
      return;
    }
    const real = findings.filter((x) => typeof x === "string" && x.trim());
    if (!real.length) return;
    const fixed = nonemptyStrings(G(item, "fixed")) || [];
    const recheck = str0(G(item, "recheck")).trim();
    if (fixed.length < real.length) {
      report.error(at, `发现 ${real.length} 项问题但只记录 ${fixed.length} 项修复（发现→修复→复验必须闭环）`);
    }
    if (!recheck) {
      report.error(at, "有问题被修复却没有复验记录（未复验 = 未闭环）");
    } else if (isVague(recheck)) {
      report.error(at, `复验记录是空话 ${pyr(recheck)}，须写命令与真实输出`);
    }
  });
}

function checkAcceptanceActual(state, report) {
  const steps = arr(G(state, "steps"));
  if (!steps.length) {
    report.error("steps", "没有任何步骤记录，无法核验逐条验收");
    return;
  }
  steps.forEach((step, idx) => {
    if (!isDict(step)) return;
    arr(G(step, "acceptance")).forEach((item, aidx) => {
      const at = `steps[${idx}].acceptance[${aidx}]`;
      if (!isDict(item)) return;
      const actual = str0(G(item, "actual")).trim();
      if (!actual) {
        report.error(at, `验收项 ${pyr(str0(G(item, "value")).slice(0, 40))} 缺 actual 实测值（未验证 = 未完成）`);
      } else if (isVague(actual)) {
        report.error(at, `actual 是空话 ${pyr(actual)}，须写退出码/输出/实际数值`);
      }
    });
  });
}

function checkReport(entry, report) {
  const where = "gates.G5.report";
  const rp = G(entry, "report");
  if (!isDict(rp)) {
    report.error(where, `缺交付报告四要素结构化记录（${REPORT_FIELDS.join("/")}）`);
    return;
  }
  for (const field of REPORT_FIELDS) {
    const at = `${where}.${field}`;
    const items = nonemptyStrings(G(rp, field));
    if (items === null) {
      report.error(at, `缺字段 ${field}（交付报告四要素）`);
      continue;
    }
    if ((field === "deliverables" || field === "evidence") && !items.length) {
      report.error(at, `为空：${field} 不能空`);
    }
    for (const item of items) {
      if (isVague(item)) report.error(at, `含空话 ${pyr(item)}`);
    }
  }
}

function checkRsi(entry, report) {
  const where = "gates.G6.rsi";
  const rsi = G(entry, "rsi");
  if (!isDict(rsi)) {
    report.error(where, `缺 RSI 闭环记录（${RSI_FIELDS.join("/")}）`);
    return;
  }
  for (const field of RSI_FIELDS) {
    const at = `${where}.${field}`;
    const value = G(rsi, field);
    if (typeof value !== "string" || !value.trim()) {
      report.error(at, `缺 ${field}：闭环断在这里，经验就沉淀不下来`);
    } else if (isVague(value)) {
      report.error(at, `是空话 ${pyr(value)}`);
    }
  }
}

function checkBlindReviews(state, entry, report) {
  // 双盲审查（Anthropic 图工程第 6 步）：两个互相看不见对方推理的独立审查者。
  // 三条硬线：人数下限（同人审两轮不是双盲）；审查者不得是建造者（角色不分离，
  // 双盲就退化成自评）；verdict 冲突必须仲裁留痕（分歧是验收标准有歧义的信号）。
  const where = "gates.G5.blind_reviews";
  const reviews = G(entry, "blind_reviews");
  if (!Array.isArray(reviews) || reviews.length < DB.min_reviewers) {
    report.error(where,
      `双盲审查 ${Array.isArray(reviews) ? reviews.length : 0} 条 < ${DB.min_reviewers} 条`
      + `（独立审查者各留一份记录；同人审两轮不是双盲）`);
    return;
  }
  const builders = new Set();
  for (const step of arr(G(state, "steps"))) {
    if (isDict(step) && str0(G(step, "builder")).trim()) {
      builders.add(str0(G(step, "builder")).trim());
    }
  }
  const seen = new Set();
  const verdicts = [];
  reviews.forEach((item, i) => {
    const at = `${where}[${i}]`;
    if (!isDict(item)) {
      report.error(at, "条目须为 {reviewer, verdict, findings} 对象");
      return;
    }
    const reviewer = str0(G(item, "reviewer")).trim();
    if (!reviewer) {
      report.error(at, "缺 reviewer（匿名也要有代号，无署名等于无责任人）");
    } else if (builders.has(reviewer)) {
      report.error(at, `reviewer=${pyr(reviewer)} 是建造者：审批者与建造者不得兼任，否则双盲退化成自评`);
    } else if (seen.has(reviewer)) {
      report.error(at, `reviewer=${pyr(reviewer)} 重复出现：同一个人审两轮不是双盲`);
    }
    seen.add(reviewer);
    const verdict = str0(G(item, "verdict")).trim();
    if (verdict !== "approve" && verdict !== "reject") {
      report.error(at, `verdict=${pyr(verdict)} 非法（approve/reject）`);
    } else {
      verdicts.push(verdict);
    }
    if (!has(item, "findings")) {
      report.error(at, "缺 findings（无发现写空数组，字段不得缺）");
    }
  });
  if (verdicts.includes("approve") && verdicts.includes("reject")) {
    const arb = G(entry, "arbitration");
    if (!isDict(arb) || !str0(G(arb, "resolution")).trim()) {
      report.error("gates.G5.arbitration",
        "双盲 verdict 冲突（approve/reject 并存）却没有仲裁结论："
        + "分歧是验收标准有歧义的信号，必须仲裁留痕");
    } else if (!str0(G(arb, "reason")).trim()) {
      report.error("gates.G5.arbitration", "仲裁缺 reason（不写理由的裁决无法复盘）");
    }
  }
}

const HARDENED_CHECKS = {
  G0_steelman: ["G0", checkSteelman],
  G1_cognition: ["G1", checkCognition],
  G2_plan: ["G2", checkPlan],
  G3_review_closure: ["G3", checkReviewClosure],
  G5_report: ["G5", checkReport],
  G6_rsi: ["G6", checkRsi],
};

function checkHardened(state, gates, tier, report) {
  for (const item of arr(G(HARDENED_BY_TIER, tier))) {
    const label = HARDENED_LABELS[item] || item;
    if (item === "G4_acceptance_actual") {
      checkAcceptanceActual(state, report);
      continue;
    }
    if (item === "G5_blind_reviews") {
      const entry5 = has(gates, "G5") ? gates.G5 : null;
      if (!isDict(entry5)) {
        report.error("gates.G5", `${label} 无法核查：该门未登记`);
      } else if (G(entry5, "status") === "skipped") {
        report.warn("gates.G5", `${label} 因跳门未核查`);
      } else {
        checkBlindReviews(state, entry5, report);
      }
      continue;
    }
    const pair = HARDENED_CHECKS[item];
    if (!pair) usageExit(`spec 里 checklist.hardened.required_by_tier 含未知硬化项 ${item}`);
    const [gateKey, fn] = pair;
    const entry = has(gates, gateKey) ? gates[gateKey] : null;
    if (!isDict(entry)) {
      report.error(`gates.${gateKey}`, `${label} 无法核查：该门未登记`);
      continue;
    }
    if (G(entry, "status") === "skipped") {
      report.warn(`gates.${gateKey}`, `${label} 因跳门未核查`);
      continue;
    }
    fn(entry, report);
  }
}

function checkGate(key, entry, tier, report) {
  const where = `gates.${key}`;
  const label = GATE_NAMES[key] || key;
  if (!isDict(entry)) {
    report.error(where, `${label} 未登记（缺该门的状态与证据）`);
    return;
  }
  const status = G(entry, "status");
  const evidence = str0(G(entry, "evidence")).trim();

  if (status === "fail") {
    report.error(where, `${label} 状态为 fail，未准出`);
  } else if (status === "skipped") {
    if (!str0(G(entry, "approved_by")).trim()) {
      report.error(where, `${label} 被跳过但没有 approved_by（跳门必须主人明确批准）`);
    } else {
      report.warn(where, `${label} 被跳过，批准人=${pys(G(entry, "approved_by"))}（须写入交付报告）`);
    }
  } else if (status !== "pass") {
    report.error(where, `${label} status=${pyr(status)} 非法（pass/fail/skipped）`);
  }

  if (status === "pass") {
    if (!evidence) {
      report.error(where, `${label} 声明 pass 但没有证据`);
    } else {
      const low = evidence.toLowerCase();
      for (const bad of VAGUE_EVIDENCE) {
        if (evidence.trim() === bad || low.trim() === bad) {
          report.error(where, `${label} 证据是空话 ${pyr(evidence)}，必须写命令/文件/实际值`);
          break;
        }
      }
      if (evidence.length < 10) {
        report.warn(where, `${label} 证据过短（${evidence.length} 字），建议给出命令与真实输出`);
      }
    }
  }

  if (key === "G5" && (tier === "T2" || tier === "T3") && status === "pass") {
    const reviews = G(entry, "adversarial_reviews");
    const tests = G(entry, "release_tests");
    if (!pyIsIntStrict(reviews) || reviews < CL.min_adversarial_reviews) {
      report.error(where, `对抗审核轮次 ${pyr(reviews)} < ${CL.min_adversarial_reviews}`);
    }
    if (!pyIsIntStrict(tests) || tests < CL.min_release_tests) {
      report.error(where, `上线测试轮次 ${pyr(tests)} < ${CL.min_release_tests}`);
    }
  }
}

function gateChecklist(argv) {
  const args = parseArgs(argv, { "--state": "state", "--tier": "tier" }, ["state"]);
  const state = loadJson(args.state, "task-state");
  const tier = normTier(args.tier || G(state, "tier"), "T2");

  const report = new Report("gate_checklist (G0-G6 清单核对)");
  report.note(`state=${args.state} tier=${tier}`);

  if (!G(state, "tier")) report.error("tier", "未声明分级 tier（先定级再动手）");
  if (!str0(G(state, "tier_reason")).trim()) {
    report.error("tier_reason", "未写定级理由，无法核查是否该升级");
  }
  if (!has(state, "known_gaps")) {
    report.error("known_gaps", "缺 known_gaps 字段：抓不到的信息必须如实声明为缺口（写空数组亦可）");
  }

  if (tier === "T0") {
    report.note("T0 无门禁清单要求，仅适用诚实纪律。");
    return report.finish();
  }

  if ((tier === "T2" || tier === "T3") && !G(state, "enabled_engineering")) {
    report.error("enabled_engineering", "T2+ 必须登记启用的工程手段（G2 硬要求）");
  }

  const gates = isDict(G(state, "gates")) ? G(state, "gates") : {};
  for (const key of CL.required_gates_by_tier[tier]) {
    checkGate(key, has(gates, key) ? gates[key] : null, tier, report);
  }

  const extra = Object.keys(gates).filter((k) => !(k in GATE_NAMES)).sort();
  for (const key of extra) {
    report.error(`gates.${key}`, "未知门编号（合法为 G0-G6）");
  }

  checkFieldPlacement(gates, report);
  checkHardened(state, gates, tier, report);

  const trend = arr(G(state, "progress_trend"));
  if (tier === "T3" && trend.length < 2) {
    report.error("progress_trend", "T3 必须记录 ≥2 次验证评分以支持方向偏离检测");
  } else if (tier === "T2" && trend.length < 2) {
    report.warn("progress_trend", "建议记录 ≥2 次验证评分用于方向偏离检测");
  }

  return report.finish();
}

// ------------------------------------------------ 自验证门：评分 / 排序 / 门槛

function checkCriteria(record, tier, report) {
  const criteria = arr(G(record, "criteria"));
  const weights = arr(G(record, "weights"));
  if (criteria.length !== weights.length) {
    report.error("weights", `criteria 数 ${criteria.length} 与 weights 数 ${weights.length} 不一致`);
  }
  let total = 0;
  for (const w of weights) if (pyIsNum(w)) total += pyNum(w);
  if (weights.length && Math.abs(total - 1.0) > VR.weight_tol) {
    report.error("weights", `权重和 ${f4(total)} != 1.0（权重必须在评分前锁定且合计为 1）`);
  }
  if (criteria.length < VR.min_criteria) {
    if (tier === "T2" || tier === "T3") {
      report.error("criteria", `只有 ${criteria.length} 项评价标准 < ${VR.min_criteria}，评价标准拆分维度缺失`);
    } else {
      report.warn("criteria", `只有 ${criteria.length} 项评价标准，建议拆到 ${VR.min_criteria} 项`);
    }
  }
  return [criteria, weights];
}

function checkCandidate(idx, cand, criteria, weights, repeats, report, expectLen = null) {
  const where = `candidates[${idx}](${pys(G(cand, "id"))})`;
  const scores = arr(G(cand, "scores"));
  const seen = new Map();
  scores.forEach((sc, sidx) => {
    const cwhere = `${where}.scores[${sidx}](${pys(G(sc, "criterion"))})`;
    const name = G(sc, "criterion");
    if (seen.has(name)) report.error(cwhere, `标准 ${pys(name)} 重复打分`);
    seen.set(name, sc);
    if (criteria.length && !criteria.includes(name)) {
      report.error(cwhere, `标准 ${pyr(name)} 未在 criteria 中声明`);
    }
    const values = arr(G(sc, "values"));
    let want = repeats;
    if (expectLen) {
      const key = JSON.stringify([G(cand, "id"), name]);
      if (expectLen.has(key)) want = expectLen.get(key);
    }
    if (values.length !== want) {
      if (want !== repeats) {
        report.error(cwhere, `重复评估次数 ${values.length} != repeats+extra_samples=${want}（该标准已登记重采样）`);
      } else {
        report.error(cwhere, `重复评估次数 ${values.length} != repeats=${want}`);
      }
    }
    if (values.length) {
      const nums = values.map(pyNum);
      const spread = Math.max(...nums) - Math.min(...nums);
      if (spread > VR.spread_limit) {
        report.error(cwhere, `重复评分极差 ${pys(spread)} > ${VR.spread_limit}，说明标准描述不清，须先修 rubric 再重评`);
      }
      const real = median(nums);
      const declared = pyFloat(has(sc, "median") ? sc.median : -1);
      if (!Number.isFinite(declared) || Math.abs(declared - real) > 1e-6) {
        report.error(cwhere, `median=${pys(G(sc, "median"))} 与 values ${pys(values)} 的真实中位数 ${pys(real)} 不符`);
      }
    }
    if (!str0(G(sc, "evidence")).trim()) {
      report.error(cwhere, "分数缺少证据，视为无效分");
    }
  });

  for (const name of criteria) {
    if (!seen.has(name)) report.error(where, `缺少标准 ${pys(name)} 的评分`);
  }

  const medians = criteria
    .filter((n) => seen.has(n) && pyIsNum(G(seen.get(n), "median")))
    .map((n) => pyNum(G(seen.get(n), "median")));
  if (medians.length > 1 && new Set(medians).size === 1) {
    report.warn(where, `所有标准得分相同（${pys(medians[0])}），疑未真正分项评估`);
  }

  if (criteria.length === weights.length && medians.length === criteria.length) {
    let expect = 0;
    for (let i = 0; i < criteria.length; i += 1) {
      expect += pyNum(weights[i]) * pyFloat(G(seen.get(criteria[i]), "median"));
    }
    const got = G(cand, "weighted");
    if (pyIsNum(got) && Math.abs(pyNum(got) - expect) > VR.weighted_tol) {
      report.error(where, `weighted=${pys(got)} 与按权重重算值 ${f2(expect)} 不符（禁止手改总分）`);
    }
  }
  return seen;
}

function checkRanking(record, report) {
  const cands = arr(G(record, "candidates"));
  const ids = cands.map((c) => G(c, "id"));
  const ranking = arr(G(record, "ranking"));
  const key = (xs) => JSON.stringify(xs.slice().map((x) => pys(x)).sort());
  if (key(ranking) !== key(ids)) {
    report.error("ranking", `ranking ${pys(ranking)} 与候选集合 ${pys(ids)} 不是一一对应`);
    return;
  }
  const weighted = new Map(cands.map((c) => [G(c, "id"), G(c, "weighted")]));
  for (let i = 1; i < ranking.length; i += 1) {
    const prev = weighted.get(ranking[i - 1]);
    const cur = weighted.get(ranking[i]);
    if (pyIsNum(prev) && pyIsNum(cur)) {
      if (pyNum(cur) > pyNum(prev)) {
        report.error("ranking", `排序与分数矛盾：${pys(ranking[i])}(${f2(cur)}) 排在 ${pys(ranking[i - 1])}(${f2(prev)}) 之后`);
      } else if (pyNum(cur) === pyNum(prev)) {
        report.error("ranking", `${pys(ranking[i - 1])} 与 ${pys(ranking[i])} 加权总分平局（${f2(cur)}）未处置：必须追加区分性标准，禁止任选`);
      }
    }
  }
  if (G(record, "winner") !== ranking[0]) {
    report.error("winner", `winner=${pyr(G(record, "winner"))} 与 ranking[0]=${pyr(ranking[0])} 不一致`);
  }
}

function checkThreshold(record, tier, report) {
  const spec = TIER_SPEC[tier];
  const cands = new Map(arr(G(record, "candidates")).map((c) => [G(c, "id"), c]));
  const win = cands.get(G(record, "winner"));
  if (!win) {
    report.error("winner", `winner=${pyr(G(record, "winner"))} 不在候选列表中`);
    return;
  }
  const got = G(win, "weighted");
  if (pyIsNum(got) && pyNum(got) < spec.threshold) {
    report.error("threshold",
      `冠军加权总分 ${f2(got)} < ${tier} 级门槛 ${f1(spec.threshold)}：必须返工/换方案/上报，禁止放宽门槛`);
  }
  if (tier === "T3") {
    const byName = new Map(arr(G(win, "scores")).map((s) => [G(s, "criterion"), G(s, "median")]));
    for (const name of Object.keys(VR.t3_hard_floor).sort()) {
      const floor = VR.t3_hard_floor[name];
      const val = byName.has(name) ? byName.get(name) : null;
      if (val === null || val === undefined) {
        report.error("threshold", `T3 缺少硬底线标准 ${name} 的评分`);
      } else if (!Number.isFinite(pyFloat(val)) || pyFloat(val) < floor) {
        report.error("threshold", `T3 硬底线未达：${name}=${f1(pyFloat(val))} < ${f1(floor)}`);
      }
    }
  }
}

function checkScale(record, tier, report) {
  const spec = TIER_SPEC[tier];
  const cands = arr(G(record, "candidates"));
  if (cands.length < spec.n) {
    report.error("candidates", `${tier} 级要求至少 ${spec.n} 个候选，实际 ${cands.length} 个`);
  }
  const repeats = has(record, "repeats") ? record.repeats : 0;
  if (repeats < spec.r) {
    report.error("repeats", `${tier} 级要求重复评估 ≥${spec.r} 次，实际 ${pys(G(record, "repeats"))}`);
  }
  if (tier === "T3") {
    const pivots = arr(G(record, "pivots"));
    if (pivots.length !== VR.pivot_k) {
      report.error("pivots", `T3 必须用 k=${VR.pivot_k} 个 pivot 做近似排序（O(Nk)），实际 ${pivots.length} 个`);
    }
    const ids = new Set(cands.map((c) => G(c, "id")));
    for (const pid of pivots) {
      if (!ids.has(pid)) report.error("pivots", `pivot ${pyr(pid)} 不在候选列表中`);
    }
  }
  if (G(record, "builder") && G(record, "builder") === G(record, "verifier")) {
    report.error("verifier", `builder == verifier == ${pyr(G(record, "builder"))}，验证者必须独立于执行者`);
  }
  if (!str0(G(record, "winner_reason")).trim()) {
    report.error("winner_reason", "未给出选优理由");
  }
}

function checkTrendRecord(record, report) {
  const trend = arr(G(record, "progress_trend"));
  let streak = 0;
  for (let i = 1; i < trend.length; i += 1) {
    if (trend[i] <= trend[i - 1]) {
      streak += 1;
      if (streak >= LOOP.spin_strikes) {
        report.error("progress_trend", `验证分数连续 ${streak} 次不升（... ${pys(trend[i - 1])} → ${pys(trend[i])}），方向已偏离`);
      }
    } else {
      streak = 0;
    }
  }
}

// 自一致性重采样：与 Python 侧 gate_verify_rank.check_self_consistency 逐项对齐
const SC = SPEC.self_consistency || {};

function scObj(record) {
  const sc = G(record, "self_consistency");
  return isDict(sc) ? sc : null;
}

/** 返回 [冠亚加权分差或 null, 前两名 id]。 */
function topGap(record) {
  const ranking = arr(G(record, "ranking"));
  if (ranking.length < 2) return [null, []];
  const weighted = new Map(arr(G(record, "candidates")).map((c) => [G(c, "id"), G(c, "weighted")]));
  const a = weighted.get(ranking[0]);
  const b = weighted.get(ranking[1]);
  if (!(pyIsNum(a) && pyIsNum(b))) return [null, ranking.slice(0, 2)];
  return [pyFloat(a) - pyFloat(b), ranking.slice(0, 2)];
}

function resampleExpectation(record) {
  const sc = scObj(record);
  const plan = new Map();
  if (!sc || G(sc, "triggered") !== true) return plan;
  const extra = G(sc, "extra_samples");
  if (!pyIsIntStrict(extra) || extra <= 0) return plan;
  const repeats = G(record, "repeats") || 0;
  const [, top2] = topGap(record);
  for (const cid of top2) {
    for (const name of arr(G(sc, "resampled"))) {
      plan.set(JSON.stringify([cid, name]), repeats + extra);
    }
  }
  return plan;
}

function checkSelfConsistency(record, tier, criteria, report) {
  const sc = scObj(record);

  if (sc && G(sc, "triggered") !== true) {
    if (G(sc, "extra_samples") || (arr(G(sc, "resampled")).length)) {
      report.error("self_consistency",
        "triggered 非 true 却填了 extra_samples/resampled，两个字段至少有一个是假的");
    }
  }

  const triggered = Boolean(sc && G(sc, "triggered") === true);

  if (!arr(SC.enabled_tiers).includes(tier)) {
    if (triggered) {
      report.note(`self_consistency：${tier} 级不强制重采样，主动加采样属多花 token 但不违规`);
    }
    return;
  }

  const [gap, top2] = topGap(record);
  if (gap === null) {
    if (top2.length < 2) report.note("self_consistency：候选不足 2 个，无冠亚分差可判");
    return;
  }

  if (gap >= SC.trigger_score_gap) {
    if (triggered) {
      report.warn("self_consistency",
        `冠亚分差 ${f2(gap)} ≥ ${f1(SC.trigger_score_gap)} 本不需重采样，仍触发属主动多花 token`);
    }
    return;
  }

  if (!triggered) {
    report.error("self_consistency",
      `冠亚加权分差 ${f2(gap)} < ${f1(SC.trigger_score_gap)}，落在可能改判区间：拿不到 logits，`
      + `重复评分的分歧度是唯一可得的置信度代理，必须先重采样再定案`);
    return;
  }

  const extra = G(sc, "extra_samples");
  if (!pyIsIntStrict(extra) || extra < SC.extra_samples || extra > SC.max_extra_samples) {
    report.error("self_consistency",
      `extra_samples=${pyr(extra)} 必须是 ${SC.extra_samples}~${SC.max_extra_samples} 之间的整数（少了不够去噪，多了只是烧 token）`);
  }

  const resampled = arr(G(sc, "resampled"));
  if (!resampled.length) {
    report.error("self_consistency", "triggered=true 却没有 resampled：重采样必须落到具体标准上");
  }
  for (const name of resampled) {
    if (criteria.length && !criteria.includes(name)) {
      report.error("self_consistency", `resampled 含未在 criteria 声明的标准 ${pyr(name)}`);
    }
  }

  if (!str0(G(sc, "reason")).trim()) {
    report.error("self_consistency", "缺 reason：哪些标准最可能改判、为何重采，必须写清");
  }

  const declared = G(sc, "gap");
  if (pyIsNum(declared) && Math.abs(pyFloat(declared) - gap) > 0.01) {
    report.error("self_consistency",
      `gap=${pys(declared)} 与按 weighted 重算的冠亚分差 ${f2(gap)} 不符（禁止手改）`);
  }
}

function gateVerify(argv) {
  const args = parseArgs(argv, { "--record": "record", "--tier": "tier", "--schema": "schema" }, ["record"]);
  const record = loadJson(args.record, "verify-record");
  const tier = normTier(args.tier || G(record, "tier"), "T2");
  const schema = loadJson(args.schema || DEFAULT_RECORD_SCHEMA, "schema");

  const report = new Report("gate_verify_rank (自验证评分与排序)");
  report.note(`record=${args.record} tier=${tier}`);

  for (const err of validate(record, schema, "$")) report.error("schema", err);

  if (tier === "T0") {
    report.note("T0 不启用自验证扩展。");
    return report.finish();
  }

  const [criteria, weights] = checkCriteria(record, tier, report);
  const repeats = G(record, "repeats") || 0;
  const expectLen = resampleExpectation(record);
  arr(G(record, "candidates")).forEach((cand, idx) => {
    checkCandidate(idx, cand, criteria, weights, repeats, report, expectLen);
  });
  checkRanking(record, report);
  checkScale(record, tier, report);
  checkThreshold(record, tier, report);
  checkSelfConsistency(record, tier, criteria, report);
  checkTrendRecord(record, report);
  return report.finish();
}

// -------------------------------------------------- 显式能力缺口：reality_scan

const REALITY_GAP = [
  "reality_scan 门需要 Python AST（ast.parse）做副作用与占位物静态扫描，",
  "Node 实现不覆盖此门 —— 这是显式声明的能力缺口，不是通过。",
  "请在有 Python 3 的环境用 python -X utf8 scripts/gate_reality_scan.py --root . 补跑。",
].join("");

function gateReality() {
  usageExit(REALITY_GAP);
}

// ------------------------------------------------------------------ 编排 / CLI

const LABELS = { 0: "PASS", 1: "BLOCK", 2: "USAGE_ERROR" };
const COVERED = ["state", "loop", "checklist", "verify"];
const NOT_COVERED = ["reality"];

function gateRun(argv) {
  const args = parseArgs(argv, {
    "--state": "state", "--root": "root", "--tier": "tier",
    "--record": "record", "--whitelist": "whitelist", "--skip": "skip",
  }, ["state"]);
  if (!fs.existsSync(args.state) || !fs.statSync(args.state).isFile()) {
    usageExit(`task-state 文件不存在：${args.state}`);
  }
  const root = args.root || ".";
  if (!fs.existsSync(root) || !fs.statSync(root).isDirectory()) {
    usageExit(`root 不是目录：${root}`);
  }
  const tier = normTier(args.tier, "");
  const skip = new Set(String(args.skip || "").split(",").map((x) => x.trim()).filter(Boolean));
  const tierArgv = tier ? ["--tier", tier] : [];
  const results = [];

  const step = (name, fn, subArgv) => {
    if (skip.has(name)) return;
    process.stdout.write(`\n$ ${name} ${subArgv.join(" ")}\n`);
    results.push([name, fn(subArgv)]);
  };

  step("state", gateState, ["--state", args.state, ...tierArgv]);
  step("loop", gateLoop, ["--state", args.state, ...tierArgv]);
  step("checklist", gateChecklist, ["--state", args.state, ...tierArgv]);

  if (!skip.has("reality")) {
    process.stdout.write("\n=== gate_reality_scan ===\n");
    process.stdout.write(`  note  未覆盖：${REALITY_GAP}\n`);
  }

  const record = args.record;
  if (!skip.has("verify")) {
    if (record && fs.existsSync(record) && fs.statSync(record).isFile()) {
      step("verify", gateVerify, ["--record", record, ...tierArgv]);
    } else if (tier === "T3") {
      process.stdout.write("\n=== gate_verify_rank ===\n");
      process.stdout.write("  BLOCK [record] T3 必须提供 verify-record.json（多候选竞标证据缺失）\n");
      results.push(["verify", EXIT_BLOCK]);
    } else {
      process.stdout.write("\n=== gate_verify_rank ===\n  note  未提供 --record，本级不强制，跳过。\n");
    }
  }

  process.stdout.write("\n================ 门禁汇总 ================\n");
  const width = results.length ? Math.max(...results.map(([n]) => n.length)) : 8;
  for (const [name, code] of results) {
    process.stdout.write(`  ${name.padEnd(width)}  ${LABELS[code] || "UNKNOWN"} (exit=${code})\n`);
  }

  const codes = results.map(([, c]) => c);
  let final;
  let verdict;
  if (codes.includes(EXIT_USAGE)) {
    final = EXIT_USAGE;
    verdict = "USAGE_ERROR：有门禁未能真实执行，按未验证=未完成处理";
  } else if (codes.includes(EXIT_BLOCK)) {
    final = EXIT_BLOCK;
    verdict = "BLOCK：存在违规，禁止进入下一门";
  } else {
    final = EXIT_PASS;
    verdict = "PASS：本实现覆盖的门全部通过";
  }
  process.stdout.write(`总判定：${verdict}\n`);
  process.stdout.write(
    `PARTIAL_COVERAGE ${COVERED.length}/${COVERED.length + NOT_COVERED.length} 门`
    + `（未覆盖：${NOT_COVERED.join(",")} —— 需 Python AST）\n`);
  process.stdout.write("注意：Node 实现是无 Python 环境的兜底，PASS 不等于全门通过；有 Python 时以 Python 实现为准。\n");
  return final;
}

function gateCapability() {
  const rows = [
    ["state", "covered", "契约 + DAG（与 Python 等价）"],
    ["loop", "covered", "四道成本闸门（与 Python 等价）"],
    ["checklist", "covered", "G0-G6 清单（与 Python 等价）"],
    ["verify", "covered", "评分/排序/门槛（与 Python 等价）"],
    ["reality", "NOT COVERED", "需 Python AST 静态扫描，Node 无等价实现"],
  ];
  process.stdout.write("=== acs_gates.mjs 能力矩阵（Node 实现）===\n");
  process.stdout.write(`  spec      : ${SPEC_PATH}（v${SPEC.spec_version}）\n`);
  process.stdout.write(`  runtime   : node ${process.version}\n`);
  for (const [name, status, note] of rows) {
    process.stdout.write(`  ${name.padEnd(10)}${status.padEnd(13)}${note}\n`);
  }
  process.stdout.write(`覆盖 ${COVERED.length}/${rows.length} 门。缺口是显式声明的，不是静默降级。\n`);
  return EXIT_PASS;
}

// ------------------------------------------------- 能力探针（与 acs_doctor.py 对齐）
//
// 为什么 Node 侧也要有：探针的用途正是「环境里有没有 Python」。若探针本身只能用 Python 跑，
// 那么最需要它的场景（无 Python）恰好用不上 —— 那是自相矛盾的设计。

const TERMINALS_PATH = process.env.ACS_TERMINALS_PATH
  || path.join(SUITE_ROOT, "spec", "terminals.json");

function loadTerminals() {
  if (!fs.existsSync(TERMINALS_PATH) || !fs.statSync(TERMINALS_PATH).isFile()) {
    usageExit(`找不到终端映射真相源：${TERMINALS_PATH}（可用 ACS_TERMINALS_PATH 指定）`);
  }
  let data;
  try {
    data = JSON.parse(fs.readFileSync(TERMINALS_PATH, "utf-8"));
  } catch (e) {
    usageExit(`终端映射真相源不是合法 JSON：${TERMINALS_PATH}（${e.message}）`);
  }
  for (const key of ["spec_version", "detect_order", "terminals", "enforcement_levels"]) {
    if (!(key in data)) usageExit(`终端映射真相源缺顶层字段 ${key}：${TERMINALS_PATH}`);
  }
  const missing = data.detect_order.filter((t) => !(t in data.terminals));
  if (missing.length) usageExit(`detect_order 里的 ${missing.join("/")} 在 terminals 中无定义`);
  const last = data.detect_order[data.detect_order.length - 1];
  if (last !== "generic") usageExit(`detect_order 的最后一项必须是 generic（兜底项），实际为 ${last}`);
  return data;
}

function probeCmd(exe, args) {
  let res;
  try {
    res = spawnSync(exe, args, { encoding: "utf-8" });
  } catch {
    return null;
  }
  if (!res || res.error || res.status !== 0) return null;
  const line = String(res.stdout || res.stderr || "").trim().split(/\r?\n/)[0] || "";
  return line;
}

/** PATH 里没有 ≠ 机器上没有：IDE 自带的 Git 常不入 PATH（与 Python 侧 _bundled 对齐）。 */
function bundled(...rel) {
  const home = os.homedir();
  const bases = [
    path.join(home, ".qoder", "bin", "git"),
    path.join(home, ".qoderwork", "bin", "git"),
    "C:/Program Files/Git",
    "C:/Program Files (x86)/Git",
  ];
  for (const base of bases) {
    const cand = path.join(base, ...rel);
    if (fs.existsSync(cand) && fs.statSync(cand).isFile()) return cand;
  }
  return null;
}

function probeGit() {
  const direct = probeCmd("git", ["--version"]);
  if (direct) return direct;
  const cand = bundled("bin", "git.exe") || bundled("cmd", "git.exe");
  return cand ? probeCmd(cand, ["--version"]) : null;
}

function probePythons() {
  const found = [];
  for (const exe of ["python3", "python", "py"]) {
    const ver = probeCmd(exe, ["-c", "import sys;print('.'.join(map(str,sys.version_info[:3])))"]);
    if (ver) found.push({ exe, version: ver });
  }
  return found;
}

function detectTerminal(spec, workspace, home) {
  for (const tid of spec.detect_order) {
    const term = spec.terminals[tid];
    const evidence = [];
    for (const m of arr(term.markers_home)) {
      const p = path.join(home, m);
      if (fs.existsSync(p)) evidence.push(p);
    }
    for (const m of arr(term.markers_workspace)) {
      const p = path.join(workspace, m);
      if (fs.existsSync(p)) evidence.push(p);
    }
    if (evidence.length) return [tid, evidence];
  }
  return ["generic", []];
}

function resolvePaths(spec, tid, workspace, home, posix = false) {
  if (!(tid in spec.terminals)) {
    usageExit(`未知终端 id：${tid}（可用：${Object.keys(spec.terminals).sort().join(" ")}）`);
  }
  const term = spec.terminals[tid];
  const ws = posix ? workspace.split("\\").join("/") : workspace;
  const hm = posix ? home.split("\\").join("/") : home;
  const expand = (value) => {
    let v = str0(value);
    if (v.startsWith("~")) v = hm + v.slice(1);
    v = v.split("<workspace>").join(ws);
    return posix ? v : path.normalize(v);
  };
  return {
    terminal: tid,
    label: str0(term.label) || tid,
    skills_dir: expand(term.skills_dir),
    rules_source: str0(term.rules_source),
    rules_target: expand(term.rules_target),
    note: str0(term.post_install_note),
  };
}

function gateDoctor(argv) {
  const args = { target: ".", mode: "auto", json: false, modeOnly: false, paths: false, posix: false };
  let i = 0;
  while (i < argv.length) {
    const tok = argv[i];
    if (tok === "--target" || tok === "--mode") {
      if (i + 1 >= argv.length) usageExit(`参数 ${tok} 缺少取值`);
      args[tok === "--target" ? "target" : "mode"] = argv[i + 1];
      i += 2;
    } else if (tok === "--json") { args.json = true; i += 1; }
    else if (tok === "--mode-only") { args.modeOnly = true; i += 1; }
    else if (tok === "--paths") { args.paths = true; i += 1; }
    else if (tok === "--posix") { args.posix = true; i += 1; }
    else usageExit(`未知参数：${tok}（可用 --target/--mode/--json/--mode-only/--paths/--posix）`);
  }

  const spec = loadTerminals();
  const workspace = path.resolve(args.target);
  const home = os.homedir();
  if (args.mode !== "auto" && !(args.mode in spec.terminals)) {
    usageExit(`--mode ${args.mode} 不在 terminals 里（可用：auto ${spec.detect_order.join(" ")}）`);
  }

  // 纯查询模式：只回结论不做判决，故恒 exit 0（安装脚本靠它取值）。
  if (args.modeOnly || args.paths) {
    const tid = args.mode !== "auto" ? args.mode : detectTerminal(spec, workspace, home)[0];
    if (args.modeOnly) {
      process.stdout.write(`${tid}\n`);
      return EXIT_PASS;
    }
    const p = resolvePaths(spec, tid, workspace, home, args.posix);
    for (const key of ["terminal", "label", "skills_dir", "rules_source", "rules_target", "note"]) {
      process.stdout.write(`${key.toUpperCase()}=${p[key]}\n`);
    }
    return EXIT_PASS;
  }

  const levels = spec.enforcement_levels;
  const minMajor = levels.partial ? levels.partial.min_node_major : null;
  if (!Number.isInteger(minMajor)) {
    usageExit("terminals.json 的 enforcement_levels.partial.min_node_major 必须是整数");
  }
  const pythons = probePythons();
  const nodeMajor = Number(String(process.version).replace(/^v/, "").split(".")[0]) || 0;
  const nodeOk = nodeMajor >= minMajor;
  let level;
  if (pythons.length) level = "full";
  else if (nodeOk) level = "partial";
  else level = "soft_only";

  let tid; let evidence;
  if (args.mode !== "auto") { tid = args.mode; evidence = []; } else {
    [tid, evidence] = detectTerminal(spec, workspace, home);
  }
  const paths = resolvePaths(spec, tid, workspace, home);
  const gitVer = probeGit();
  const rep = {
    workspace,
    home,
    terminals_spec: TERMINALS_PATH,
    thresholds_spec: SPEC_PATH,
    thresholds_present: fs.existsSync(SPEC_PATH),
    python: pythons,
    node: [{ exe: "node", version: process.version, major: nodeMajor, ok: nodeOk }],
    node_min_major: minMajor,
    git: { version: gitVer || "", is_repo: fs.existsSync(path.join(workspace, ".git")) },
    detected: tid,
    detect_evidence: evidence,
    paths,
    enforcement: { level, gates: levels[level].gates, note: str0(levels[level].note) },
    impl: "node",
  };

  if (args.json) {
    process.stdout.write(`${JSON.stringify(rep, null, 2)}\n`);
    return level === "soft_only" ? EXIT_BLOCK : EXIT_PASS;
  }

  process.stdout.write("=== acs_doctor（运行时能力探针 · Node 实现）===\n");
  process.stdout.write(`  spec      : ${TERMINALS_PATH}\n`);
  process.stdout.write(`  workspace : ${workspace}\n`);
  process.stdout.write("\n[运行时]\n");
  process.stdout.write(pythons.length
    ? `  python3   : OK   ${pythons[0].version}（${pythons[0].exe}）—— 有 Python 时以 Python 实现为准\n`
    : "  python3   : MISSING  五道门的权威实现跑不了，本实现只覆盖 4/5\n");
  process.stdout.write(`  node      : ${nodeOk ? "OK  " : "TOO OLD"} ${process.version}（要求 >=${minMajor}）\n`);
  process.stdout.write(`  git       : ${gitVer || "MISSING  外部强制点（hook / CI）无法启用"}\n`);
  process.stdout.write(`  thresholds: ${rep.thresholds_present ? "OK" : "MISSING  门禁上岗即 USAGE_ERROR"}\n`);
  process.stdout.write("\n[终端识别]\n");
  process.stdout.write(`  detected  : ${tid}（${paths.label}）\n`);
  process.stdout.write(evidence.length
    ? `  evidence  : ${evidence.join("、")}\n`
    : "  evidence  : 无标记命中，按 generic 兜底（显式落到最通用路径，不假装识别）\n");
  process.stdout.write(`  skills   -> ${paths.skills_dir}\n`);
  process.stdout.write(`  rules    -> ${paths.rules_target}（源：${paths.rules_source}）\n`);
  if (paths.note) process.stdout.write(`  note      : ${paths.note}\n`);
  process.stdout.write("\n[执行力等级]\n");
  process.stdout.write(`  级别      : ${level}（${rep.enforcement.gates}/5 门可机检）\n`);
  process.stdout.write(`  说明      : ${rep.enforcement.note}\n`);
  if (level === "soft_only") {
    process.stdout.write("结果：BLOCK（无 Python 也无可用 Node，硬门禁无法执行；只剩软约束）\n");
    return EXIT_BLOCK;
  }
  process.stdout.write(`结果：PASS（硬门禁可用，等级 ${level}）\n`);
  return EXIT_PASS;
}

// ------------------------------------------------- 清单自检（与 install_check.py 对齐）
//
// 无 Python 环境要能装、能验：装完连清单齐全性都验不了，就只能靠「文件看起来在」自我安慰。

function gateCheck(argv) {
  const args = parseArgs(argv, { "--root": "root", "--manifest": "manifest" }, []);
  const root = args.root || ".";
  if (!fs.existsSync(root) || !fs.statSync(root).isDirectory()) usageExit(`root 不是目录：${root}`);
  const manifestPath = args.manifest || path.join(root, "manifest.json");
  if (!fs.existsSync(manifestPath) || !fs.statSync(manifestPath).isFile()) {
    usageExit(`找不到 manifest：${manifestPath}`);
  }
  let manifest;
  try {
    manifest = JSON.parse(fs.readFileSync(manifestPath, "utf-8"));
  } catch (e) {
    process.stdout.write(`BLOCK [manifest] JSON 解析失败：${e.message}\n`);
    return EXIT_BLOCK;
  }
  const files = manifest.files;
  if (!Array.isArray(files) || !files.length) {
    process.stdout.write("BLOCK [manifest] files 字段缺失或为空\n");
    return EXIT_BLOCK;
  }
  const version = str0(manifest.version) || "?";
  process.stdout.write(`=== install_check (${str0(manifest.name) || "?"} v${version}) · Node 实现 ===\n`);
  process.stdout.write(`  note  root=${root}，清单 ${files.length} 项\n`);

  const missing = []; const empty = [];
  for (const rel of files) {
    if (typeof rel !== "string" || !rel) {
      process.stdout.write(`BLOCK [manifest] files 含非法条目：${JSON.stringify(rel)}\n`);
      return EXIT_BLOCK;
    }
    const p = path.join(root, rel.split("/").join(path.sep));
    if (!fs.existsSync(p) || !fs.statSync(p).isFile()) missing.push(rel);
    else if (fs.statSync(p).size <= 0) empty.push(rel);
  }
  for (const rel of missing) process.stdout.write(`BLOCK [missing] 缺文件：${rel}\n`);
  for (const rel of empty) process.stdout.write(`BLOCK [empty] 文件为空：${rel}\n`);

  const versionFile = path.join(root, "VERSION");
  if (fs.existsSync(versionFile) && fs.statSync(versionFile).isFile()) {
    const declared = fs.readFileSync(versionFile, "utf-8").trim();
    if (declared && declared !== version) {
      process.stdout.write(`BLOCK [version] VERSION=${declared} 与 manifest.version=${version} 不一致\n`);
      return EXIT_BLOCK;
    }
  }
  const total = missing.length + empty.length;
  if (total) {
    process.stdout.write(`结果：BLOCK（${total} 项不合格，安装不完整）\n`);
    return EXIT_BLOCK;
  }
  process.stdout.write(`结果：PASS（${files.length} 项齐全且非空；未做哈希校验）\n`);
  return EXIT_PASS;
}

const USAGE = [
  "用法：node acs_gates.mjs <子命令> [参数]",
  "  state      --state <task-state.json> [--schema <p>] [--tier T2]",
  "  loop       --state <task-state.json> [--tier T2]",
  "  checklist  --state <task-state.json> [--tier T2]",
  "  verify     --record <verify-record.json> [--schema <p>] [--tier T2]",
  "  reality    （不支持：需 Python AST，显式 USAGE_ERROR）",
  "  run        --state <p> [--root .] [--record <p>] [--tier T2] [--skip a,b]",
  "  capability （打印本实现的覆盖矩阵）",
  "  doctor     [--target .] [--mode auto] [--json] [--mode-only] [--paths]",
  "  check      --root <套件目录> [--manifest <p>]（清单齐全性，等价 install_check.py）",
  "退出码：0=PASS，1=BLOCK，2=USAGE_ERROR（视为未验证=未完成）",
].join("\n");

const DISPATCH = {
  state: gateState,
  loop: gateLoop,
  checklist: gateChecklist,
  verify: gateVerify,
  reality: gateReality,
  run: gateRun,
  capability: gateCapability,
  doctor: gateDoctor,
  check: gateCheck,
};

function main(argv) {
  const sub = argv[0];
  if (!sub || sub === "-h" || sub === "--help") {
    process.stdout.write(USAGE + "\n");
    return sub ? EXIT_PASS : EXIT_USAGE;
  }
  const fn = DISPATCH[sub];
  if (!fn) usageExit(`未知子命令：${sub}（可用：${Object.keys(DISPATCH).sort().join(" ")}）`);
  return fn(argv.slice(1));
}

const INVOKED_DIRECTLY = process.argv[1]
  && path.resolve(process.argv[1]) === path.resolve(fileURLToPath(import.meta.url));
if (INVOKED_DIRECTLY) process.exit(main(process.argv.slice(2)));

export { validate, expandRefs, median, SPEC, main };
