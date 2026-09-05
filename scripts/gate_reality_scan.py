"""G3 真实性扫描：查虚假实现、空壳函数、替身残留与降级痕迹。

用法：
    python -X utf8 gate_reality_scan.py --root . [--ext .py,.ts] [--whitelist path] [--max 200]

免检方式（二者之一）：
    1. 在该行尾加标记 ACS-ALLOW（仅豁免当行，必须是有正当理由的模式常量/示例）
    2. 在 --whitelist 文件中登记 glob（每行一个，# 开头为注释）

退出码：0=PASS，1=BLOCK，2=USAGE_ERROR（视为未验证=未完成）
"""

import ast
import fnmatch
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _acs_common import Report, parse_args, read_text, spec_section, usage_exit  # noqa: E402

_RS = spec_section("reality_scan")
ALLOW_MARKER = _RS["allow_marker"]
DEFAULT_MAX_FINDINGS = _RS["default_max_findings"]

# (模式, 类别)；类别 fake 在测试文件中豁免（测试用替身是正当的）
PATTERNS = (
    ("TODO", "stub"),                    # ACS-ALLOW
    ("FIXME", "stub"),                   # ACS-ALLOW
    ("HACK", "stub"),                    # ACS-ALLOW
    ("NotImplementedError", "stub"),     # ACS-ALLOW
    ("not implemented", "stub"),         # ACS-ALLOW
    ("待实现", "stub"),                   # ACS-ALLOW
    ("暂未实现", "stub"),                 # ACS-ALLOW
    ("尚未实现", "stub"),                 # ACS-ALLOW
    ("后续补充", "stub"),                 # ACS-ALLOW
    ("简化实现", "degrade"),              # ACS-ALLOW
    ("临时方案", "degrade"),              # ACS-ALLOW
    ("降级处理", "degrade"),              # ACS-ALLOW
    ("硬编码", "degrade"),                # ACS-ALLOW
    ("写死", "degrade"),                  # ACS-ALLOW
    ("占位", "fake"),                     # ACS-ALLOW
    ("模拟实现", "fake"),                 # ACS-ALLOW
    ("模拟数据", "fake"),                 # ACS-ALLOW
    ("假数据", "fake"),                   # ACS-ALLOW
    ("示例数据", "fake"),                 # ACS-ALLOW
    ("placeholder", "fake"),             # ACS-ALLOW
    ("dummy", "fake"),                   # ACS-ALLOW
    ("mock", "fake"),                    # ACS-ALLOW
)

DEFAULT_EXTS = (
    ".py", ".js", ".jsx", ".ts", ".tsx", ".java", ".go", ".rs", ".rb", ".php",
    ".cs", ".c", ".cc", ".cpp", ".h", ".hpp", ".sh", ".ps1", ".sql", ".vue",
    ".kt", ".swift", ".scala", ".m", ".mm",
)

SKIP_DIRS = (
    ".git", ".hg", ".svn", "node_modules", "__pycache__", ".venv", "venv",
    "env", "dist", "build", ".mypy_cache", ".pytest_cache", ".ruff_cache",
    ".idea", ".vscode", "site-packages", ".tox", "coverage",
)

TEST_DIR_NAMES = {"test", "tests", "__tests__", "fixture", "fixtures"}  # 不含 spec/specs：本套件 spec/ 存真相源 JSON 而非测试，且下游 spec/ 子目录的 .py 不应被当测试替身豁免；spec 风格测试文件靠文件名 _spec/_test 后缀识别


def load_whitelist(path):
    if not path:
        return []
    if not os.path.isfile(path):
        usage_exit("whitelist 文件不存在：%s" % path)
    globs = []
    for line in read_text(path).splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            globs.append(line.replace("\\", "/"))
    return globs


def is_whitelisted(rel, globs):
    for pat in globs:
        if fnmatch.fnmatch(rel, pat):
            return True
    return False


def is_test_path(rel):
    parts = rel.replace("\\", "/").lower().split("/")
    filename = parts[-1]
    stem = os.path.splitext(filename)[0]
    in_test_dir = any(part in TEST_DIR_NAMES for part in parts[:-1])
    test_filename = stem == "test" or stem.startswith("test_") or stem.endswith("_test") or stem.endswith("_spec")
    return in_test_dir or test_filename


def scan_lines(rel, text, report, counter, limit):
    testish = is_test_path(rel)
    for lineno, line in enumerate(text.splitlines(), start=1):
        if ALLOW_MARKER in line:
            continue
        low = line.lower()
        for pat, kind in PATTERNS:
            if kind == "fake" and testish:
                continue
            hit = pat.lower() in low if pat.isascii() else pat in line
            if hit:
                if counter[0] >= limit:
                    return
                counter[0] += 1
                snippet = line.strip()
                if len(snippet) > 90:
                    snippet = snippet[:90] + "..."
                report.error("%s:%d" % (rel, lineno), "[%s] %s ← %s" % (kind, pat, snippet))
                break


def scan_python_ast(rel, text, report, counter, limit):
    """空壳函数检测：函数体只有 pass / ... / return None / 抛「未实现」异常。"""
    try:
        tree = ast.parse(text)
    except SyntaxError as exc:
        report.error("%s:%s" % (rel, exc.lineno), "[syntax] 文件无法解析，不能声称可运行：%s" % exc.msg)
        return
    lines = text.splitlines()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        body = [b for b in node.body if not (isinstance(b, ast.Expr) and isinstance(b.value, ast.Constant)
                                             and isinstance(b.value.value, str))]
        if len(body) != 1:
            continue
        only = body[0]
        empty = False
        if isinstance(only, ast.Pass):
            empty = True
        elif isinstance(only, ast.Expr) and isinstance(only.value, ast.Constant) and only.value.value is Ellipsis:
            empty = True
        elif isinstance(only, ast.Return) and (only.value is None or (
                isinstance(only.value, ast.Constant) and only.value.value is None)):
            empty = True
        elif isinstance(only, ast.Raise):
            name = ""
            exc_node = only.exc
            if isinstance(exc_node, ast.Call) and isinstance(exc_node.func, ast.Name):
                name = exc_node.func.id
            elif isinstance(exc_node, ast.Name):
                name = exc_node.id
            if name == "NotImplementedError":  # ACS-ALLOW
                empty = True
        if not empty:
            continue
        decorators = []
        for dec in node.decorator_list:
            if isinstance(dec, ast.Name):
                decorators.append(dec.id)
            elif isinstance(dec, ast.Attribute):
                decorators.append(dec.attr)
        if "abstractmethod" in decorators or "overload" in decorators:
            continue
        idx = node.lineno - 1
        if 0 <= idx < len(lines) and ALLOW_MARKER in lines[idx]:
            continue
        if counter[0] >= limit:
            return
        counter[0] += 1
        report.error("%s:%d" % (rel, node.lineno), "[empty-impl] 函数 %s 只有空实现，属未真实实现" % node.name)


def main(argv):
    args = parse_args(argv, {"--root": "root", "--ext": "ext", "--whitelist": "whitelist", "--max": "max"}, ["root"])
    root = args["root"]
    if not os.path.isdir(root):
        usage_exit("root 不是目录：%s" % root)
    exts = tuple(x.strip().lower() for x in args["ext"].split(",")) if args.get("ext") else DEFAULT_EXTS
    globs = load_whitelist(args.get("whitelist"))
    try:
        raw_limit = args.get("max")
        limit = int(raw_limit) if raw_limit is not None else DEFAULT_MAX_FINDINGS
    except ValueError:
        usage_exit("--max 必须是整数")
    if limit < 1:
        usage_exit("--max 必须是大于等于 1 的整数")

    report = Report("gate_reality_scan (真实性扫描)")
    report.note("root=%s ext=%s whitelist=%d 条 限额=%d" % (root, ",".join(exts), len(globs), limit))

    counter = [0]
    scanned = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for name in sorted(filenames):
            ext = os.path.splitext(name)[1].lower()
            if ext not in exts:
                continue
            full = os.path.join(dirpath, name)
            rel = os.path.relpath(full, root).replace("\\", "/")
            if is_whitelisted(rel, globs):
                continue
            scanned += 1
            text = read_text(full)
            scan_lines(rel, text, report, counter, limit)
            if ext == ".py":
                scan_python_ast(rel, text, report, counter, limit)
    report.note("已扫描 %d 个文件，命中 %d 处" % (scanned, counter[0]))
    if counter[0] >= limit:
        report.note("已达输出限额 %d，可能还有更多问题未列出" % limit)
    return report.finish()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
