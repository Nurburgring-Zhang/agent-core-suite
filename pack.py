# -*- coding: utf-8 -*-
"""打包分发件：把套件按 manifest.json 的 files 清单打进 zip，并回读校验。

用法：
    python -X utf8 pack.py --root <套件目录> [--out <zip 路径>]

行为：
  1. 先跑 install_check 的同等校验（清单齐全 + 非空 + VERSION 一致）。
  2. 只打包 manifest.files 里列出的文件（天然排除 __pycache__ / .pytest_cache / 临时物）。
  3. 打包后重新打开 zip，逐项比对条目名、未压缩字节数与 **sha256**，不一致即失败。

退出码：0=成功，1=失败，2=USAGE_ERROR。
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import sys
import zipfile

EXIT_PASS, EXIT_FAIL, EXIT_USAGE = 0, 1, 2


def usage_exit(msg):
    sys.stdout.write("USAGE_ERROR %s\n" % msg)
    sys.stdout.write(__doc__ or "")
    raise SystemExit(EXIT_USAGE)


def parse_args(argv):
    args = {}
    i = 0
    while i < len(argv):
        token = argv[i]
        if not token.startswith("--"):
            usage_exit("无法识别的参数：%s" % token)
        key = token[2:]
        if i + 1 >= len(argv) or argv[i + 1].startswith("--"):
            usage_exit("参数 --%s 缺少取值" % key)
        args[key] = argv[i + 1]
        i += 2
    return args


def sha256_bytes(data):
    digest = hashlib.sha256()
    digest.update(data)
    return digest.hexdigest()


def main(argv):
    args = parse_args(argv)
    root = os.path.abspath(args.get("root") or ".")
    if not os.path.isdir(root):
        usage_exit("root 不是目录：%s" % root)
    manifest_path = os.path.join(root, "manifest.json")
    if not os.path.isfile(manifest_path):
        usage_exit("找不到 manifest.json：%s" % manifest_path)
    with io.open(manifest_path, "r", encoding="utf-8") as fh:
        manifest = json.load(fh)

    name = manifest.get("name") or "agent-core-suite"
    version = manifest.get("version") or "0.0.0"
    files = manifest.get("files") or []
    out = args.get("out") or os.path.join(os.path.dirname(root), "%s-v%s.zip" % (name, version))
    out = os.path.abspath(out)

    sys.stdout.write("=== pack (%s v%s) ===\n" % (name, version))
    sys.stdout.write("  note  root=%s\n  note  out =%s\n" % (root, out))

    version_file = os.path.join(root, "VERSION")
    if os.path.isfile(version_file):
        with io.open(version_file, "r", encoding="utf-8") as fh:
            declared = fh.read().strip()
        if declared != version:
            sys.stdout.write("FAIL  VERSION=%s 与 manifest.version=%s 不一致\n" % (declared, version))
            return EXIT_FAIL

    expected = {}
    missing = []
    for rel in files:
        path = os.path.join(root, rel.replace("/", os.sep))
        if not os.path.isfile(path) or os.path.getsize(path) <= 0:
            missing.append(rel)
        else:
            with io.open(path, "rb") as fh:
                raw = fh.read()
            expected[rel] = (len(raw), sha256_bytes(raw))
    if missing:
        for rel in missing:
            sys.stdout.write("FAIL  缺文件或为空：%s\n" % rel)
        return EXIT_FAIL

    prefix = "%s-v%s" % (name, version)
    out_dir = os.path.dirname(out)
    if out_dir and not os.path.isdir(out_dir):
        os.makedirs(out_dir)
    if os.path.exists(out):
        os.remove(out)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for rel in sorted(expected):
            zf.write(os.path.join(root, rel.replace("/", os.sep)), "%s/%s" % (prefix, rel))

    # 回读校验：条目齐全 + 未压缩大小一致 + sha256 逐项一致（A7）
    with zipfile.ZipFile(out, "r") as zf:
        bad = zf.testzip()
        if bad is not None:
            sys.stdout.write("FAIL  zip 内损坏条目：%s\n" % bad)
            return EXIT_FAIL
        infos = {}
        for info in zf.infolist():
            infos[info.filename] = info.file_size
        problems = 0
        for rel, (size, sha) in sorted(expected.items()):
            key = "%s/%s" % (prefix, rel)
            if key not in infos:
                sys.stdout.write("FAIL  zip 缺条目：%s\n" % key)
                problems += 1
                continue
            if infos[key] != size:
                sys.stdout.write("FAIL  大小不一致：%s（源 %d / zip %d）\n" % (key, size, infos[key]))
                problems += 1
                continue
            got = sha256_bytes(zf.read(key))
            if got != sha:
                sys.stdout.write("FAIL  sha256 不一致：%s\n        源 %s\n        zip %s\n" % (key, sha, got))
                problems += 1
        extra = set(infos) - set("%s/%s" % (prefix, r) for r in expected)
        for key in sorted(extra):
            sys.stdout.write("FAIL  zip 含清单外条目：%s\n" % key)
            problems += 1
    if problems:
        sys.stdout.write("结果：FAIL（%d 项不一致）\n" % problems)
        return EXIT_FAIL

    sys.stdout.write("结果：PASS（%d 项已打包，字节数与 sha256 逐项回读一致，zip %d 字节）\n"
                     % (len(expected), os.path.getsize(out)))
    sys.stdout.write("解包后安装：Windows `install.ps1 -Target <workspace>`；Linux/macOS `./install.sh --target <workspace>`\n")
    return EXIT_PASS


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
