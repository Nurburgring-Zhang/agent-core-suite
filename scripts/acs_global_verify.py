#!/usr/bin/env python3
"""Statically verify a product's global Agent Core Suite integration (multi-product).

v2.0 generalizes the former qwenworkcn-only verifier into a product-agnostic engine.
Everything product-specific (home marker, awareness dir, soul/agents filenames, skills
dir, suite dir, required anchors, native routing, boundaries) is read from the product's
global fusion contract at ``spec/<product>-global.json``; this script hardcodes no paths.

The verifier is strictly read-only. Structured evidence is emitted to stdout with --json
so the caller controls any persistence under its own file policy.

Usage:
    python -X utf8 scripts/acs_global_verify.py --product qoderwork --json
    python -X utf8 scripts/acs_global_verify.py --product qwenworkcn --home ~/.qwenworkcn
    python -X utf8 scripts/acs_global_verify.py --spec spec/qoderwork-global.json

Exit codes: 0 = STATIC_PASS, 1 = STATIC_BLOCK, 2 = USAGE_ERROR (未验证 = 未完成).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

PASS = 0
BLOCK = 1
USAGE_ERROR = 2
SUITE = Path(__file__).resolve().parent.parent

# Single registry of products that have a global fusion contract. Adding a product means
# adding one spec/<id>-global.json file plus one entry here; nothing else is product-specific.
PRODUCTS = {
    "qoderwork": "qoderwork-global.json",
    "qwenworkcn": "qwenwork-global.json",
}
DEFAULT_PRODUCT = "qoderwork"
# terminals.json global_verify pointer for every globally-integrated product.
GLOBAL_VERIFY_ENTRY = "scripts/acs_global_verify.py"
CONTRACT_VERSION = "2.0.0"

CACHE_DIRS = {".git", "__pycache__", ".pytest_cache", ".ruff_cache", ".mypy_cache", "node_modules"}
REQUIRED_SPEC_KEYS = {
    "spec_version", "product", "positioning", "paths", "required_skills",
    "required_suite_files", "required_anchors", "native_routing",
    "enforcement", "integrity_model", "boundaries",
}
REQUIRED_PATH_KEYS = {
    "home_marker", "awareness_dir", "soul_file", "agents_file", "skills_dir", "suite_dir",
}


def default_spec_path(product: str) -> Path:
    filename = PRODUCTS.get(product)
    if not filename:
        raise ValueError(
            "未知产品 %s（可用：%s）" % (product, ", ".join(sorted(PRODUCTS))))
    return SUITE / "spec" / filename


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"无法读取 JSON {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"JSON 根节点必须是对象: {path}")
    return data


def require_string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"契约字段 {field} 必须是非空字符串")
    return value


def require_string_list(value: Any, field: str) -> list[str]:
    if not isinstance(value, list) or not value:
        raise ValueError(f"契约字段 {field} 必须是非空字符串数组")
    result = [require_string(item, f"{field}[]") for item in value]
    if len(result) != len(set(result)):
        raise ValueError(f"契约字段 {field} 不允许重复值")
    return result


def safe_relative(value: Any, field: str) -> str:
    text = require_string(value, field)
    path = Path(text)
    if path.is_absolute() or any(part in ("", ".", "..") for part in path.parts):
        raise ValueError(f"契约字段 {field} 必须是无 . 或 .. 的相对路径: {text}")
    return text


def safe_name(value: Any, field: str) -> str:
    text = require_string(value, field)
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", text):
        raise ValueError(f"契约字段 {field} 不是安全名称: {text}")
    return text


def safe_home_marker(value: Any, field: str) -> str:
    text = require_string(value, field)
    if not re.fullmatch(r"\.[A-Za-z0-9][A-Za-z0-9._-]*", text):
        raise ValueError(f"契约字段 {field} 不是安全的隐藏目录名称: {text}")
    return text


def validate_spec(spec: dict[str, Any], product: str) -> dict[str, Any]:
    missing = sorted(REQUIRED_SPEC_KEYS - set(spec))
    unknown = sorted(set(spec) - REQUIRED_SPEC_KEYS)
    if missing:
        raise ValueError("全局融合契约缺少字段: " + ", ".join(missing))
    if unknown:
        raise ValueError("全局融合契约含未知字段: " + ", ".join(unknown))
    if spec.get("spec_version") != CONTRACT_VERSION:
        raise ValueError(f"全局融合契约 spec_version 必须是 {CONTRACT_VERSION}")
    if spec.get("product") != product:
        raise ValueError(f"全局融合契约 product 必须是 {product}")
    require_string(spec.get("positioning"), "positioning")

    paths = spec.get("paths")
    if not isinstance(paths, dict) or set(paths) != REQUIRED_PATH_KEYS:
        raise ValueError("paths 必须且只能包含: " + ", ".join(sorted(REQUIRED_PATH_KEYS)))
    safe_home_marker(paths["home_marker"], "paths.home_marker")
    for key in ("awareness_dir", "soul_file", "agents_file", "skills_dir", "suite_dir"):
        safe_relative(paths[key], f"paths.{key}")

    for idx, name in enumerate(require_string_list(spec.get("required_skills"), "required_skills")):
        safe_name(name, f"required_skills[{idx}]")
    for idx, rel in enumerate(require_string_list(spec.get("required_suite_files"), "required_suite_files")):
        safe_relative(rel, f"required_suite_files[{idx}]")

    anchors = spec.get("required_anchors")
    if not isinstance(anchors, dict) or set(anchors) != {paths["soul_file"], paths["agents_file"]}:
        raise ValueError("required_anchors 必须精确覆盖 SOUL.md 与 AGENTS.md")
    for filename, values in anchors.items():
        safe_relative(filename, f"required_anchors.{filename}")
        require_string_list(values, f"required_anchors.{filename}")

    for field in ("native_routing", "enforcement", "integrity_model"):
        value = spec.get(field)
        if not isinstance(value, dict) or not value:
            raise ValueError(f"契约字段 {field} 必须是非空对象")
        for key, item in value.items():
            require_string(key, f"{field}.key")
            require_string(item, f"{field}.{key}")
    require_string_list(spec.get("boundaries"), "boundaries")
    return spec


def resolve_home(product: str, paths: dict[str, Any], raw: str | None) -> Path:
    if raw:
        return Path(raw).expanduser().resolve()
    env_home = os.environ.get(f"{product.upper()}_HOME") or os.environ.get("ACS_HOME")
    if env_home:
        return Path(env_home).expanduser().resolve()
    return Path.home().joinpath(paths["home_marker"]).resolve()


def files_under(root: Path) -> list[Path]:
    return sorted(
        path for path in root.rglob("*")
        if path.is_file() and not (CACHE_DIRS & set(path.parts))
    )


def expected_terminals_wiring(product: str, paths: dict[str, Any]) -> dict[str, str]:
    """Derive the terminals.json wiring this product's contract implies — no hardcoding."""
    hm = paths["home_marker"]
    awareness = paths["awareness_dir"]
    return {
        "skills_dir": f"~/{hm}/{paths['skills_dir']}",
        "global_awareness_dir": f"~/{hm}/{awareness}",
        "global_soul_target": f"~/{hm}/{awareness}/{paths['soul_file']}",
        "global_agents_target": f"~/{hm}/{awareness}/{paths['agents_file']}",
        "global_verify": GLOBAL_VERIFY_ENTRY,
    }


def verify(home: Path, spec: dict[str, Any]) -> tuple[list[str], list[str], dict[str, Any]]:
    errors: list[str] = []
    notes: list[str] = []
    product = spec["product"]
    evidence: dict[str, Any] = {
        "verification_kind": "static_configuration_integrity",
        "product": product,
        "home": str(home),
        "files": {},
        "skills": {},
    }
    paths = spec["paths"]
    awareness = home / paths["awareness_dir"]
    suite = home / paths["suite_dir"]
    skills = home / paths["skills_dir"]

    if not home.is_dir():
        errors.append(f"{product} 资源目录不存在: {home}")
        return errors, notes, evidence

    for filename, required in spec["required_anchors"].items():
        path = awareness / filename
        if not path.is_file() or path.stat().st_size == 0:
            errors.append(f"全局规则文件缺失或为空: {path}")
            continue
        text = path.read_text(encoding="utf-8")
        missing = [anchor for anchor in required if anchor not in text]
        if missing:
            errors.append(f"{filename} 缺少静态锚点: {', '.join(missing)}")
        evidence["files"][filename] = {
            "path": str(path), "bytes": path.stat().st_size, "sha256": sha256(path),
        }

    suite_skills = suite / "skills"
    for name in spec["required_skills"]:
        source = suite_skills / name
        target = skills / name
        source_files = files_under(source) if source.is_dir() else []
        target_files = files_under(target) if target.is_dir() else []
        if not source_files:
            errors.append(f"套件 Skill 缺失或为空: {source}")
            continue
        if not target_files:
            errors.append(f"{product} 用户 Skill 缺失或为空: {target}")
            continue
        source_map = {path.relative_to(source).as_posix(): sha256(path) for path in source_files}
        target_map = {path.relative_to(target).as_posix(): sha256(path) for path in target_files}
        if source_map != target_map:
            errors.append(f"Skill 静态漂移: {name}（套件源与用户目录字节不一致）")
        evidence["skills"][name] = {
            "source_files": len(source_map), "target_files": len(target_map),
            "identical": source_map == target_map, "source_hashes": source_map,
        }

    for rel in spec["required_suite_files"]:
        path = suite / rel
        if not path.is_file() or path.stat().st_size == 0:
            errors.append(f"套件必需文件缺失或为空: {rel}")
        else:
            evidence["files"][f"suite:{rel}"] = {
                "bytes": path.stat().st_size, "sha256": sha256(path),
            }

    terminals_path = suite / "spec" / "terminals.json"
    try:
        terminals = load_json(terminals_path)
        product_term = (terminals.get("terminals") or {}).get(product) or {}
        detect_order = terminals.get("detect_order") or []
        if product not in detect_order:
            errors.append(f"terminals.json detect_order 未包含 {product}")
        if not detect_order or detect_order[-1] != "generic":
            errors.append("terminals.json detect_order 末项必须是 generic 兜底")
        for key, wanted in expected_terminals_wiring(product, paths).items():
            if product_term.get(key) != wanted:
                errors.append(
                    f"{product}.{key} 与全局契约不符（期望 {wanted}，实际 {product_term.get(key)!r}）")
        notes.append(f"{product} terminal label={product_term.get('label', '')}")
    except ValueError as exc:
        errors.append(str(exc))

    evidence["integrity_model"] = spec["integrity_model"]
    evidence["boundaries"] = spec["boundaries"]
    return errors, notes, evidence


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--product", default=DEFAULT_PRODUCT,
                        help="目标产品 id（%s），默认 %s" % ("/".join(sorted(PRODUCTS)), DEFAULT_PRODUCT))
    parser.add_argument("--home", help="产品资源目录，默认 ~/.<home_marker>（可由 <PRODUCT>_HOME 或 ACS_HOME 覆盖）")
    parser.add_argument("--spec", help="全局融合契约 JSON，默认 spec/<product>-global.json")
    parser.add_argument("--json", action="store_true", help="把结构化静态证据输出到标准输出")
    args = parser.parse_args(argv)

    try:
        if args.product not in PRODUCTS:
            raise ValueError("未知产品 %s（可用：%s）" % (args.product, ", ".join(sorted(PRODUCTS))))
        spec_path = Path(args.spec).resolve() if args.spec else default_spec_path(args.product)
        spec = validate_spec(load_json(spec_path), args.product)
        home = resolve_home(args.product, spec["paths"], args.home)
        errors, notes, evidence = verify(home, spec)
        evidence["status"] = "STATIC_BLOCK" if errors else "STATIC_PASS"
        evidence["error_count"] = len(errors)
    except ValueError as exc:
        print(f"USAGE_ERROR: {exc}")
        return USAGE_ERROR

    label = args.product
    if args.json:
        print(json.dumps(evidence, ensure_ascii=False, indent=2))
        return BLOCK if errors else PASS

    print(f"=== acs_global_verify（静态配置完整性 · {label}）===")
    for note in notes:
        print(f"  note  {note}")
    for error in errors:
        print(f"  BLOCK {error}")
    if errors:
        print(f"结果：BLOCK（{len(errors)} 项静态配置违规）")
        return BLOCK
    print(f"结果：PASS（静态规则锚点、核心 Skills、套件文件与 {label} 映射一致）")
    print("边界：本结果不证明系统提示词、产品私有内核或未公开全局 Hook 已被修改或加载。")
    return PASS


if __name__ == "__main__":
    raise SystemExit(main())
