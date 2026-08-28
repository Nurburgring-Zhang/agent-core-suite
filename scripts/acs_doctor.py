"""acs_doctor —— 运行时能力探针与多终端识别（纯只读，零第三方依赖）。

为什么需要它：
1. 「装了」不等于「强制住了」。硬门禁能不能真跑，取决于环境里有没有 Python / Node；
   不先探明就宣称套件生效，等于把强制力押在假设上。
2. 多终端适配的落点（技能目录、规则文件）各终端不同，散落在两个安装脚本和文档里必然漂移。
   故落点只存 spec/terminals.json，本脚本与安装脚本都只当解释器。
3. 探测结果必须是显式结论：既没有 Python 也没有 Node 时，结论是「只剩软约束、强制力归零」，
   退出码 1 —— 显式失败，不静默降级。

用法：
    python -X utf8 scripts/acs_doctor.py [--target .]            # 人读报告
    python -X utf8 scripts/acs_doctor.py --json                  # 机读（给 CI / 外部编排）
    python -X utf8 scripts/acs_doctor.py --mode-only             # 只打印识别到的终端 id
    python -X utf8 scripts/acs_doctor.py --paths [--mode qoder]  # 打印安装落点 KEY=VALUE
    python -X utf8 scripts/acs_doctor.py --paths --posix         # 同上，但输出正斜杠（给 bash 用）

退出码：0 = 硬门禁可用（full / partial）；1 = 只剩软约束（soft_only）；2 = 用法错误或 spec 缺失。
注意：本脚本不写盘、不建目录、不改环境，只读取文件系统标记与运行时版本。
"""

import io
import json
import os
import subprocess
import sys

EXIT_PASS = 0
EXIT_BLOCK = 1
EXIT_USAGE = 2

_HERE = os.path.dirname(os.path.abspath(__file__))
_SUITE = os.path.dirname(_HERE)
TERMINALS_PATH = os.environ.get("ACS_TERMINALS_PATH") or os.path.join(
    _SUITE, "spec", "terminals.json")
THRESHOLDS_PATH = os.environ.get("ACS_SPEC_PATH") or os.path.join(
    _SUITE, "spec", "thresholds.json")

PY_CANDIDATES = ("python3", "python", "py")
NODE_CANDIDATES = ("node",)


def usage_exit(msg):
    sys.stderr.write("[USAGE_ERROR] %s\n" % msg)
    sys.stderr.write("视为未验证 = 未完成。\n")
    raise SystemExit(EXIT_USAGE)


def load_terminals():
    """读终端映射真相源；缺文件或缺字段一律 USAGE_ERROR，不回退内置默认表。"""
    if not os.path.isfile(TERMINALS_PATH):
        usage_exit("找不到终端映射真相源：%s（可用 ACS_TERMINALS_PATH 指定）" % TERMINALS_PATH)
    try:
        with io.open(TERMINALS_PATH, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except ValueError as exc:
        usage_exit("终端映射真相源不是合法 JSON：%s（%s）" % (TERMINALS_PATH, exc))
    for key in ("spec_version", "detect_order", "terminals", "enforcement_levels"):
        if key not in data:
            usage_exit("终端映射真相源缺顶层字段 %s：%s" % (key, TERMINALS_PATH))
    missing = [t for t in data["detect_order"] if t not in data["terminals"]]
    if missing:
        usage_exit("detect_order 里的 %s 在 terminals 中无定义" % "/".join(missing))
    if data["detect_order"][-1] != "generic":
        usage_exit("detect_order 的最后一项必须是 generic（兜底项），实际为 %s"
                   % data["detect_order"][-1])
    return data


def _run(cmd):
    """跑一条只读探测命令，返回 (returncode, 首行输出)；跑不起来返回 (None, "")。"""
    try:
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    except (OSError, ValueError):
        return None, ""
    text = proc.stdout.decode("utf-8", "replace").strip().splitlines()
    return proc.returncode, (text[0] if text else "")


def _which(name):
    from shutil import which

    return which(name)


def probe_python():
    """找一个真的 Python 3。当前解释器本身就算一个候选（脚本正在被它执行）。"""
    found = []
    seen = set()
    for exe in (sys.executable,) + PY_CANDIDATES:
        if not exe:
            continue
        path = exe if os.path.isabs(exe) else _which(exe)
        if not path or path in seen:
            continue
        seen.add(path)
        code, out = _run([path, "-c", "import sys;print('.'.join(map(str,sys.version_info[:3])))"])
        if code == 0 and out:
            found.append({"exe": exe, "path": path, "version": out})
    return found


def probe_node(min_major):
    found = []
    for exe in NODE_CANDIDATES:
        path = _which(exe)
        if not path:
            continue
        code, out = _run([path, "--version"])
        if code != 0 or not out:
            continue
        major = 0
        digits = out.lstrip("v").split(".")[0]
        if digits.isdigit():
            major = int(digits)
        found.append({
            "exe": exe, "path": path, "version": out,
            "major": major, "ok": major >= min_major,
        })
    return found


def _bundled(*rel):
    """PATH 里没有 ≠ 机器上没有：IDE 自带的 Git 常不入 PATH。

    只查 PATH 会得出「install.sh 跑不了 / 挂不了 pre-commit」的错误结论。
    """
    home = os.path.expanduser("~")
    bases = (
        os.path.join(home, ".qoder", "bin", "git"),
        os.path.join(home, ".qoderwork", "bin", "git"),
        "C:/Program Files/Git",
        "C:/Program Files (x86)/Git",
    )
    for base in bases:
        cand = os.path.join(base, *rel)
        if os.path.isfile(cand):
            return cand
    return None


def probe_bash():
    for exe in ("bash", "sh"):
        path = _which(exe)
        if path:
            return {"exe": exe, "path": path}
    cand = _bundled("bin", "bash.exe")
    return {"exe": "bash", "path": cand} if cand else None


def probe_git(workspace):
    path = _which("git") or _bundled("bin", "git.exe") or _bundled("cmd", "git.exe")
    version = ""
    if path:
        code, out = _run([path, "--version"])
        version = out if code == 0 else ""
    git_dir = os.path.join(workspace, ".git")
    return {
        "path": path, "version": version,
        "is_repo": os.path.isdir(git_dir),
        "hooks_dir": os.path.join(git_dir, "hooks") if os.path.isdir(git_dir) else "",
    }


def detect_terminal(spec, workspace, home):
    """按 detect_order 逐个看文件系统标记；命中即止，全不中落到 generic。"""
    for tid in spec["detect_order"]:
        term = spec["terminals"][tid]
        evidence = []
        for marker in term.get("markers_home", []):
            path = os.path.join(home, marker)
            if os.path.exists(path):
                evidence.append(path)
        for marker in term.get("markers_workspace", []):
            path = os.path.join(workspace, marker)
            if os.path.exists(path):
                evidence.append(path)
        if evidence:
            return tid, evidence
    return "generic", []


def resolve_paths(spec, tid, workspace, home, posix=False):
    """把 spec 里的落点模板展开成真实路径。

    posix=True 时统一输出正斜杠：Windows 上的 Git Bash 拿到反斜杠路径会当转义符处理，
    install.sh 因此必须取 posix 形式，否则 mkdir/cp 会静默拼出错误目录。
    """
    if tid not in spec["terminals"]:
        usage_exit("未知终端 id：%s（可用：%s）" % (tid, " ".join(sorted(spec["terminals"]))))
    term = spec["terminals"][tid]
    ws = workspace.replace("\\", "/") if posix else workspace
    hm = home.replace("\\", "/") if posix else home

    def expand(value):
        text = value or ""
        if text.startswith("~"):
            text = hm + text[1:]
        text = text.replace("<workspace>", ws)
        return text if posix else os.path.normpath(text)

    return {
        "terminal": tid,
        "label": term.get("label", tid),
        "skills_dir": expand(term.get("skills_dir", "")),
        "rules_source": term.get("rules_source", ""),
        "rules_target": expand(term.get("rules_target", "")),
        "note": term.get("post_install_note", ""),
    }


def enforcement(spec, pythons, nodes):
    levels = spec["enforcement_levels"]
    if pythons:
        return "full", levels["full"]
    if [n for n in nodes if n.get("ok")]:
        return "partial", levels["partial"]
    return "soft_only", levels["soft_only"]


def build_report(spec, workspace, home):
    min_major = spec["enforcement_levels"]["partial"].get("min_node_major")
    if not isinstance(min_major, int):
        usage_exit("terminals.json 的 enforcement_levels.partial.min_node_major 必须是整数")
    pythons = probe_python()
    nodes = probe_node(min_major)
    level, level_spec = enforcement(spec, pythons, nodes)
    tid, evidence = detect_terminal(spec, workspace, home)
    return {
        "workspace": workspace,
        "home": home,
        "terminals_spec": TERMINALS_PATH,
        "thresholds_spec": THRESHOLDS_PATH,
        "thresholds_present": os.path.isfile(THRESHOLDS_PATH),
        "python": pythons,
        "node": nodes,
        "node_min_major": min_major,
        "bash": probe_bash(),
        "git": probe_git(workspace),
        "detected": tid,
        "detect_evidence": evidence,
        "paths": resolve_paths(spec, tid, workspace, home),
        "enforcement": {"level": level, "gates": level_spec.get("gates"),
                        "note": level_spec.get("note", "")},
    }


def print_human(rep):
    out = sys.stdout
    out.write("=== acs_doctor（运行时能力探针）===\n")
    out.write("  spec      : %s\n" % rep["terminals_spec"])
    out.write("  workspace : %s\n" % rep["workspace"])
    out.write("\n[运行时]\n")
    if rep["python"]:
        first = rep["python"][0]
        out.write("  python3   : OK   %s（%s）\n" % (first["version"], first["path"]))
    else:
        out.write("  python3   : MISSING  五道门的权威实现跑不了\n")
    ok_nodes = [n for n in rep["node"] if n["ok"]]
    if ok_nodes:
        out.write("  node      : OK   %s（>=%d，可跑 4/5 门）\n"
                  % (ok_nodes[0]["version"], rep["node_min_major"]))
    elif rep["node"]:
        out.write("  node      : TOO OLD  %s < v%d\n"
                  % (rep["node"][0]["version"], rep["node_min_major"]))
    else:
        out.write("  node      : MISSING\n")
    out.write("  bash      : %s\n" % (rep["bash"]["path"] if rep["bash"] else "MISSING（install.sh 跑不了，用 install.ps1）"))
    git = rep["git"]
    if git["path"] and git["is_repo"]:
        out.write("  git       : OK   %s；本工作区是仓库，可挂 pre-commit 外部强制点\n" % git["version"])
    elif git["path"]:
        out.write("  git       : OK   %s；但工作区不是 git 仓库，挂不了 pre-commit\n" % git["version"])
    else:
        out.write("  git       : MISSING  外部强制点（hook / CI）无法启用\n")
    out.write("  thresholds: %s\n" % ("OK" if rep["thresholds_present"] else "MISSING  门禁上岗即 USAGE_ERROR"))

    paths = rep["paths"]
    out.write("\n[终端识别]\n")
    out.write("  detected  : %s（%s）\n" % (rep["detected"], paths["label"]))
    if rep["detect_evidence"]:
        out.write("  evidence  : %s\n" % "、".join(rep["detect_evidence"]))
    else:
        out.write("  evidence  : 无标记命中，按 generic 兜底（显式落到最通用路径，不假装识别）\n")
    out.write("  skills   -> %s\n" % paths["skills_dir"])
    out.write("  rules    -> %s（源：%s）\n" % (paths["rules_target"], paths["rules_source"]))
    if paths["note"]:
        out.write("  note      : %s\n" % paths["note"])

    enf = rep["enforcement"]
    out.write("\n[执行力等级]\n")
    out.write("  级别      : %s（%s/5 门可机检）\n" % (enf["level"], enf["gates"]))
    out.write("  说明      : %s\n" % enf["note"])
    if enf["level"] == "soft_only":
        out.write("结果：BLOCK（无 Python 也无可用 Node，硬门禁无法执行；只剩软约束）\n")
        return EXIT_BLOCK
    out.write("  安装建议  : install.sh --target %s --mode %s\n" % (rep["workspace"], rep["detected"]))
    out.write("结果：PASS（硬门禁可用，等级 %s）\n" % enf["level"])
    return EXIT_PASS


def parse_args(argv):
    args = {"target": ".", "mode": "auto", "json": False, "mode_only": False,
            "paths": False, "posix": False}
    i = 0
    while i < len(argv):
        tok = argv[i]
        if tok in ("--target", "--mode"):
            if i + 1 >= len(argv):
                usage_exit("参数 %s 缺少取值" % tok)
            args[tok[2:]] = argv[i + 1]
            i += 2
        elif tok == "--json":
            args["json"] = True
            i += 1
        elif tok == "--mode-only":
            args["mode_only"] = True
            i += 1
        elif tok == "--paths":
            args["paths"] = True
            i += 1
        elif tok == "--posix":
            args["posix"] = True
            i += 1
        elif tok in ("-h", "--help"):
            sys.stdout.write(__doc__)
            raise SystemExit(EXIT_PASS)
        else:
            usage_exit("未知参数：%s（可用 --target/--mode/--json/--mode-only/--paths/--posix）" % tok)
    return args


def main(argv):
    args = parse_args(argv)
    spec = load_terminals()
    workspace = os.path.abspath(args["target"])
    home = os.path.expanduser("~")

    mode = args["mode"]
    if mode != "auto" and mode not in spec["terminals"]:
        usage_exit("--mode %s 不在 terminals 里（可用：auto %s）"
                   % (mode, " ".join(spec["detect_order"])))

    # 纯查询模式：给安装脚本取值用，只回结论不做判决，故恒 exit 0。
    if args["mode_only"] or args["paths"]:
        tid = mode if mode != "auto" else detect_terminal(spec, workspace, home)[0]
        if args["mode_only"]:
            sys.stdout.write("%s\n" % tid)
            return EXIT_PASS
        paths = resolve_paths(spec, tid, workspace, home, args["posix"])
        for key in ("terminal", "label", "skills_dir", "rules_source", "rules_target", "note"):
            sys.stdout.write("%s=%s\n" % (key.upper(), paths[key]))
        return EXIT_PASS

    rep = build_report(spec, workspace, home)
    if mode != "auto":
        rep["detected"] = mode
        rep["detect_evidence"] = []
        rep["paths"] = resolve_paths(spec, mode, workspace, home)
    if args["json"]:
        sys.stdout.write(json.dumps(rep, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
        return EXIT_BLOCK if rep["enforcement"]["level"] == "soft_only" else EXIT_PASS
    return print_human(rep)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
