# -*- coding: utf-8 -*-
"""安装前后自检：核对 manifest.json 的文件清单是否齐全且非空。

用法：
    python -X utf8 scripts/install_check.py --root <套件目录> [--manifest <path>]

退出码：0=PASS，1=BLOCK（缺文件/空文件/manifest 不合法），2=USAGE_ERROR。
说明：只做存在性与非空校验，**不做哈希校验**（未实现，故不作完整性强度声明）。
"""
from __future__ import annotations

import io
import json
import os
import sys

EXIT_PASS, EXIT_BLOCK, EXIT_USAGE = 0, 1, 2


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


def main(argv):
    args = parse_args(argv)
    root = args.get("root") or "."
    if not os.path.isdir(root):
        usage_exit("root 不是目录：%s" % root)
    manifest_path = args.get("manifest") or os.path.join(root, "manifest.json")
    if not os.path.isfile(manifest_path):
        usage_exit("找不到 manifest：%s" % manifest_path)

    try:
        with io.open(manifest_path, "r", encoding="utf-8") as fh:
            manifest = json.load(fh)
    except ValueError as exc:
        sys.stdout.write("BLOCK [manifest] JSON 解析失败：%s\n" % exc)
        return EXIT_BLOCK

    files = manifest.get("files")
    if not isinstance(files, list) or not files:
        sys.stdout.write("BLOCK [manifest] files 字段缺失或为空\n")
        return EXIT_BLOCK

    version = manifest.get("version") or "?"
    sys.stdout.write("=== install_check (%s v%s) ===\n" % (manifest.get("name") or "?", version))
    sys.stdout.write("  note  root=%s，清单 %d 项\n" % (root, len(files)))

    missing, empty = [], []
    for rel in files:
        if not isinstance(rel, str) or not rel:
            sys.stdout.write("BLOCK [manifest] files 含非法条目：%r\n" % (rel,))
            return EXIT_BLOCK
        path = os.path.join(root, rel.replace("/", os.sep))
        if not os.path.isfile(path):
            missing.append(rel)
        elif os.path.getsize(path) <= 0:
            empty.append(rel)

    for rel in missing:
        sys.stdout.write("BLOCK [missing] 缺文件：%s\n" % rel)
    for rel in empty:
        sys.stdout.write("BLOCK [empty] 文件为空：%s\n" % rel)

    version_file = os.path.join(root, "VERSION")
    if os.path.isfile(version_file):
        with io.open(version_file, "r", encoding="utf-8") as fh:
            declared = fh.read().strip()
        if declared and declared != version:
            sys.stdout.write("BLOCK [version] VERSION=%s 与 manifest.version=%s 不一致\n" % (declared, version))
            return EXIT_BLOCK

    total = len(missing) + len(empty)
    if total:
        sys.stdout.write("结果：BLOCK（%d 项不合格，安装不完整）\n" % total)
        return EXIT_BLOCK
    sys.stdout.write("结果：PASS（%d 项齐全且非空；未做哈希校验）\n" % len(files))
    return EXIT_PASS


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
