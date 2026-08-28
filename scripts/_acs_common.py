"""Agent Core Suite 门禁脚本共享工具（零第三方依赖）。

退出码约定（全套统一）：
    0 = PASS（准出）
    1 = BLOCK（发现违规）
    2 = USAGE_ERROR（参数/文件缺失，视为未验证 = 未完成）
"""

import io
import json
import os
import sys

EXIT_PASS = 0
EXIT_BLOCK = 1
EXIT_USAGE = 2

TIERS = ("T0", "T1", "T2", "T3")

# --------------------------------------------------------------------------
# 阈值单一真相源：spec/thresholds.json
#
# 为何不在这里写常量：一套阈值一旦在多个实现（Python / Node / 文档 / CI）各存一份，
# 就一定会漂移，而且漂移后两边都“自活”、没人报错。故只留一份 JSON，各实现只当解释器。
# 为何缺文件要硬失败：拿不到阈值 = 无法计量 = 等于绕过闸门，回退内置默认值就是静默降级。
# --------------------------------------------------------------------------

_HERE = os.path.dirname(os.path.abspath(__file__))
SPEC_PATH = os.environ.get("ACS_SPEC_PATH") or os.path.join(
    os.path.dirname(_HERE), "spec", "thresholds.json")


def _load_spec():
    if not os.path.isfile(SPEC_PATH):
        sys.stderr.write("[USAGE_ERROR] 找不到阈值真相源：%s\n" % SPEC_PATH)
        sys.stderr.write("拿不到阈值 = 无法计量 = 等于绕过闸门；本套件不使用内置默认值静默继续。\n")
        sys.stderr.write("请确认 spec/thresholds.json 已随套件安装，或用环境变量 ACS_SPEC_PATH 指定。\n")
        raise SystemExit(EXIT_USAGE)
    with io.open(SPEC_PATH, "r", encoding="utf-8") as fh:
        try:
            data = json.load(fh)
        except ValueError as exc:
            sys.stderr.write("[USAGE_ERROR] 阈值真相源不是合法 JSON：%s（%s）\n" % (SPEC_PATH, exc))
            raise SystemExit(EXIT_USAGE)
    for key in ("spec_version", "exit_codes", "tiers", "loop", "checklist", "vague_phrases"):
        if key not in data:
            sys.stderr.write("[USAGE_ERROR] 阈值真相源缺顶层字段 %s：%s\n" % (key, SPEC_PATH))
            raise SystemExit(EXIT_USAGE)
    codes = data["exit_codes"]
    if (codes.get("pass"), codes.get("block"), codes.get("usage_error")) != (
            EXIT_PASS, EXIT_BLOCK, EXIT_USAGE):
        sys.stderr.write("[USAGE_ERROR] 阈值真相源的退出码与实现不一致：%r\n" % (codes,))
        raise SystemExit(EXIT_USAGE)
    missing = [t for t in TIERS if t not in data["tiers"]]
    if missing:
        sys.stderr.write("[USAGE_ERROR] 阈值真相源缺分级 %s\n" % "/".join(missing))
        raise SystemExit(EXIT_USAGE)
    return data


SPEC = _load_spec()

# 各级下限：候选数 N、重复评估 R、评分门槛、安全阀默认轮数/分钟（来自 spec，不得在此硬写）
TIER_SPEC = SPEC["tiers"]


def spec_section(name):
    """取 spec 的一个小节；缺就直接 USAGE_ERROR，不给默认值。"""
    if name not in SPEC:
        usage_exit("阈值真相源缺小节 %s：%s" % (name, SPEC_PATH))
    return SPEC[name]


def read_text(path):
    with io.open(path, "r", encoding="utf-8", errors="replace") as fh:
        return fh.read()


def load_json(path, label):
    """读取 JSON。失败直接以 USAGE_ERROR 退出（拿不到输入 = 未验证）。"""
    if not path:
        usage_exit("%s 路径未提供" % label)
    if not os.path.isfile(path):
        usage_exit("%s 文件不存在：%s" % (label, path))
    try:
        return json.loads(read_text(path))
    except ValueError as exc:
        usage_exit("%s 不是合法 JSON：%s（%s）" % (label, path, exc))


def usage_exit(msg):
    sys.stderr.write("[USAGE_ERROR] %s\n" % msg)
    sys.stderr.write("视为未验证 = 未完成。\n")
    raise SystemExit(EXIT_USAGE)


def parse_args(argv, spec, required):
    """极简参数解析：spec 为 {"--state": "state"} 形式。"""
    out = {}
    i = 0
    while i < len(argv):
        tok = argv[i]
        if tok in spec:
            if i + 1 >= len(argv):
                usage_exit("参数 %s 缺少取值" % tok)
            out[spec[tok]] = argv[i + 1]
            i += 2
        elif tok in ("-h", "--help"):
            out["help"] = "1"
            i += 1
        else:
            usage_exit("未知参数：%s（可用：%s）" % (tok, " ".join(sorted(spec))))
    for key in required:
        if key not in out:
            usage_exit("缺少必需参数 --%s" % key)
    return out


def norm_tier(value, default="T2"):
    tier = (value or default or "").upper()
    if tier == "":
        return ""
    if tier not in TIERS:
        usage_exit("tier 非法：%s（可选 %s）" % (value, "/".join(TIERS)))
    return tier


def median(values):
    ordered = sorted(values)
    n = len(ordered)
    if n == 0:
        return None
    mid = n // 2
    if n % 2 == 1:
        return float(ordered[mid])
    return (float(ordered[mid - 1]) + float(ordered[mid])) / 2.0


class Report(object):
    """收集违规与告警，统一输出与退出码。"""

    def __init__(self, gate_name):
        self.gate = gate_name
        self.errors = []
        self.warnings = []
        self.notes = []

    def error(self, where, msg):
        self.errors.append((where, msg))

    def warn(self, where, msg):
        self.warnings.append((where, msg))

    def note(self, msg):
        self.notes.append(msg)

    def finish(self):
        out = sys.stdout
        out.write("=== %s ===\n" % self.gate)
        for msg in self.notes:
            out.write("  note  %s\n" % msg)
        for where, msg in self.warnings:
            out.write("  WARN  [%s] %s\n" % (where, msg))
        for where, msg in self.errors:
            out.write("  BLOCK [%s] %s\n" % (where, msg))
        if self.errors:
            out.write("结果：BLOCK（%d 项违规，%d 项告警）\n" % (len(self.errors), len(self.warnings)))
            out.flush()
            return EXIT_BLOCK
        out.write("结果：PASS（0 项违规，%d 项告警）\n" % len(self.warnings))
        out.flush()
        return EXIT_PASS


# --------------------------------------------------------------------------
# JSON Schema 子集校验器：支持 type/required/properties/items/enum/additionalProperties
# /minimum/maximum/minItems/maxItems/minLength/maxLength/uniqueItems
# --------------------------------------------------------------------------

_TYPE_MAP = {
    "object": dict,
    "array": list,
    "string": str,
    "boolean": bool,
    "null": type(None),
}

# 允许的 type 名；schema 里出现其它名字一律当作 schema 本身写错，不得静默通过
_KNOWN_TYPES = ("object", "array", "string", "boolean", "null", "number", "integer")


def _type_ok(value, expected):
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "boolean":
        return isinstance(value, bool)
    py = _TYPE_MAP.get(expected)
    if py is None:
        return False
    return isinstance(value, py)


def validate(value, schema, where="$"):
    """返回错误字符串列表；空列表表示通过。"""
    errs = []
    if not isinstance(schema, dict):
        return errs

    expected = schema.get("type")
    if expected:
        types = expected if isinstance(expected, list) else [expected]
        unknown = [t for t in types if t not in _KNOWN_TYPES]
        if unknown:
            errs.append("%s 的 schema 声明了未知类型 %s（疑为拼写错误，不予静默通过）" % (where, "/".join(unknown)))
            return errs
        if not any(_type_ok(value, t) for t in types):
            errs.append("%s 类型应为 %s，实际为 %s" % (where, "/".join(types), type(value).__name__))
            return errs

    if "enum" in schema and value not in schema["enum"]:
        errs.append("%s 取值 %r 越界，允许 %s" % (where, value, schema["enum"]))

    if isinstance(value, str):
        if "minLength" in schema and len(value) < schema["minLength"]:
            errs.append("%s 长度 %d < 最小 %d" % (where, len(value), schema["minLength"]))
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            errs.append("%s 长度 %d > 最大 %d" % (where, len(value), schema["maxLength"]))

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            errs.append("%s 值 %s < 最小 %s" % (where, value, schema["minimum"]))
        if "maximum" in schema and value > schema["maximum"]:
            errs.append("%s 值 %s > 最大 %s" % (where, value, schema["maximum"]))

    if isinstance(value, list):
        if "minItems" in schema and len(value) < schema["minItems"]:
            errs.append("%s 元素数 %d < 最小 %d" % (where, len(value), schema["minItems"]))
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            errs.append("%s 元素数 %d > 最大 %d" % (where, len(value), schema["maxItems"]))
        if schema.get("uniqueItems"):
            seen = []
            for item in value:
                key = json.dumps(item, sort_keys=True, ensure_ascii=False)
                if key in seen:
                    errs.append("%s 存在重复元素 %s" % (where, key))
                seen.append(key)
        item_schema = schema.get("items")
        if item_schema:
            for idx, item in enumerate(value):
                errs.extend(validate(item, item_schema, "%s[%d]" % (where, idx)))

    if isinstance(value, dict):
        for key in schema.get("required", []):
            if key not in value:
                errs.append("%s 缺少必需字段 %s" % (where, key))
        props = schema.get("properties", {})
        for key, sub in props.items():
            if key in value:
                errs.extend(validate(value[key], sub, "%s.%s" % (where, key)))
        if schema.get("additionalProperties") is False:
            for key in value:
                if key not in props:
                    errs.append("%s 出现未声明字段 %s" % (where, key))
    return errs
