#!/usr/bin/env python3
"""Statically verify QwenWorkCN global Agent Core Suite integration.

The verifier is strictly read-only. Structured evidence is emitted to stdout
with --json so the caller controls any persistence under its own file policy.
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
DEFAULT_SPEC = SUITE / "spec" / "qwenwork-global.json"
CACHE_DIRS = {".git", "__pycache__", ".pytest_cache", ".ruff_cache", ".mypy_cache", "node_modules"}
REQUIRED_SPEC_KEYS = {
    "spec_version", "product", "positioning", "paths", "required_skills",
    "required_suite_files", "required_anchors", "native_routing",
    "enforcement", "integrity_model", "boundaries",
}
REQUIRED_PATH_KEYS = {
    "home_marker", "awareness_dir", "soul_file", "agents_file", "skills_dir", "suite_dir",
}


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


def validate_spec(spec: dict[str, Any]) -> dict[str, Any]:
    missing = sorted(REQUIRED_SPEC_KEYS - set(spec))
    unknown = sorted(set(spec) - REQUIRED_SPEC_KEYS)
    if missing:
        raise ValueError("全局融合契约缺少字段: " + ", ".join(missing))
    if unknown:
        raise ValueError("全局融合契约含未知字段: " + ", ".join(unknown))
    if spec.get("spec_version") != "1.0.0":
        raise ValueError("全局融合契约 spec_version 必须是 1.0.0")
    if spec.get("product") != "qwenworkcn":
        raise ValueError("全局融合契约 product 必须是 qwenworkcn")
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


def resolve_home(raw: str | None) -> Path:
    if raw:
        return Path(raw).expanduser().resolve()
    env_home = os.environ.get("QWENWORK_HOME")
    if env_home:
        return Path(env_home).expanduser().resolve()
    return Path.home().joinpath(".qwenworkcn").resolve()


def files_under(root: Path) -> list[Path]:
    return sorted(
        path for path in root.rglob("*")
        if path.is_file() and not (CACHE_DIRS & set(path.parts))
    )


def verify(home: Path, spec: dict[str, Any]) -> tuple[list[str], list[str], dict[str, Any]]:
    errors: list[str] = []
    notes: list[str] = []
    evidence: dict[str, Any] = {
        "verification_kind": "static_configuration_integrity",
        "home": str(home),
        "files": {},
        "skills": {},
    }
    paths = spec["paths"]
    awareness = home / paths["awareness_dir"]
    suite = home / paths["suite_dir"]
    skills = home / paths["skills_dir"]

    if not home.is_dir():
        errors.append(f"千问办公资源目录不存在: {home}")
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
            errors.append(f"千问办公用户 Skill 缺失或为空: {target}")
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
        qwenwork = (terminals.get("terminals") or {}).get("qwenworkcn") or {}
        expected = {
            "skills_dir": "~/.qwenworkcn/skills",
            "global_awareness_dir": "~/.qwenworkcn/awareness/main",
            "global_soul_target": "~/.qwenworkcn/awareness/main/SOUL.md",
            "global_agents_target": "~/.qwenworkcn/awareness/main/AGENTS.md",
            "global_verify": "scripts/qwenwork_global_verify.py",
        }
        if (terminals.get("detect_order") or [None])[0] != "qwenworkcn":
            errors.append("terminals.json detect_order 首项不是 qwenworkcn")
        for key, wanted in expected.items():
            if qwenwork.get(key) != wanted:
                errors.append(f"qwenworkcn.{key} 与全局契约不符")
        notes.append(f"qwenworkcn terminal label={qwenwork.get('label', '')}")
    except ValueError as exc:
        errors.append(str(exc))

    evidence["integrity_model"] = spec["integrity_model"]
    evidence["boundaries"] = spec["boundaries"]
    return errors, notes, evidence


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--home", help="千问办公资源目录，默认 ~/.qwenworkcn")
    parser.add_argument("--spec", default=str(DEFAULT_SPEC), help="全局融合契约 JSON")
    parser.add_argument("--json", action="store_true", help="把结构化静态证据输出到标准输出")
    args = parser.parse_args(argv)

    try:
        spec = validate_spec(load_json(Path(args.spec).resolve()))
        home = resolve_home(args.home)
        errors, notes, evidence = verify(home, spec)
        evidence["status"] = "STATIC_BLOCK" if errors else "STATIC_PASS"
        evidence["error_count"] = len(errors)
    except ValueError as exc:
        print(f"USAGE_ERROR: {exc}")
        return USAGE_ERROR

    if args.json:
        print(json.dumps(evidence, ensure_ascii=False, indent=2))
        return BLOCK if errors else PASS

    print("=== qwenwork_global_verify (静态配置完整性) ===")
    for note in notes:
        print(f"  note  {note}")
    for error in errors:
        print(f"  BLOCK {error}")
    if errors:
        print(f"结果：BLOCK（{len(errors)} 项静态配置违规）")
        return BLOCK
    print("结果：PASS（静态规则锚点、核心 Skills、套件文件与 QwenWorkCN 映射一致）")
    print("边界：本结果不证明系统提示词、产品私有内核或未公开全局 Hook 已被修改或加载。")
    return PASS


if __name__ == "__main__":
    raise SystemExit(main())
