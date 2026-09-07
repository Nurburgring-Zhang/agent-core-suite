#!/usr/bin/env python3
"""G-Capability 能力治理门禁：机检 capability-registry 的五条治理规则。

用法：
    python -X utf8 gate_capability_registry.py --root .

治理规则（见 spec/capability-registry.json governance_rules）：
    1. portable 域必须 in_pack=true 且 carrier 指向真实存在的文件
    2. runtime 域必须 in_pack=false 且 carrier=null（禁止谎称打包运行时本体）
    3. 每个域必须有非空 governed_by 与 discipline（否则治理盲区）
    4. runtime 域必须有非空 fallback（否则宿主缺位无降级路径）
    5. 每个 registry runtime 域经 coverage_domain_map 映射到 capability-coverage 中
       一个 in_pack=false 的域（证明两清单都承认该运行时侧面不可打包）

退出码：0=PASS，1=BLOCK，2=USAGE_ERROR（视为未验证=未完成）
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _acs_common import Report, load_json, parse_args, usage_exit  # noqa: E402

REGISTRY_NAME = os.path.join("spec", "capability-registry.json")
COVERAGE_NAME = os.path.join("spec", "capability-coverage.json")


def resolve(root, name):
    env_key = "ACS_" + os.path.basename(name).replace(".json", "").replace("-", "_").upper() + "_PATH"
    return os.environ.get(env_key) or os.path.join(root, name)


def main(argv):
    args = parse_args(argv, {"--root": "root"}, ["root"])
    root = args["root"]
    if not os.path.isdir(root):
        usage_exit("root 不是目录：%s" % root)

    registry = load_json(resolve(root, REGISTRY_NAME), "能力治理清单 capability-registry.json")
    for key in ("spec_version", "layers", "registry", "governance_rules"):
        if key not in registry:
            usage_exit("能力治理清单缺顶层字段 %s" % key)

    report = Report("gate_capability_registry (能力治理)")
    report.note("root=%s 域数=%d" % (root, len(registry["registry"])))

    portable_count = 0
    runtime_count = 0
    runtime_domains = []
    for idx, item in enumerate(registry["registry"]):
        where = "registry[%d](%s)" % (idx, item.get("domain", "?"))
        domain = item.get("domain")
        layer = item.get("layer")
        if not domain or not isinstance(domain, str):
            report.error(where, "缺 domain")
            continue
        if layer not in ("portable", "runtime"):
            report.error(where, "layer=%r 越界，允许 portable/runtime" % layer)
            continue

        governed = item.get("governed_by")
        if not isinstance(governed, list) or not governed:
            report.error(where, "缺 governed_by：该能力处于治理盲区")
        discipline = item.get("discipline")
        if not isinstance(discipline, str) or len(discipline.strip()) < 8:
            report.error(where, "缺 discipline 或过短：治理能力未定义")

        if layer == "portable":
            portable_count += 1
            if item.get("in_pack") is not True:
                report.error(where, "portable 域 in_pack 非 true：可移植能力未登记入包")
            carrier = item.get("carrier")
            if not isinstance(carrier, str) or not carrier:
                report.error(where, "portable 域缺 carrier")
            else:
                carrier_path = os.path.join(root, carrier.replace("/", os.sep))
                if not os.path.isfile(carrier_path):
                    report.error(where, "portable 域 carrier 不存在：%s" % carrier)
            if item.get("fallback") is not None:
                report.error(where, "portable 域不应有 fallback（它在包内，无需降级）")
        else:
            runtime_count += 1
            runtime_domains.append(domain)
            if item.get("in_pack") is not False:
                report.error(where, "runtime 域 in_pack 非 false：禁止谎称已打包运行时本体")
            if item.get("carrier") is not None:
                report.error(where, "runtime 域 carrier 必须为 null（运行时本体不可打包）")
            fallback = item.get("fallback")
            if not isinstance(fallback, str) or len(fallback.strip()) < 8:
                report.error(where, "runtime 域缺 fallback：宿主缺位时无降级路径")

    # 规则 5：与 capability-coverage 语义一致（registry runtime 域映射到 coverage in_pack=false 域）
    coverage_path = resolve(root, COVERAGE_NAME)
    domain_map = registry.get("coverage_domain_map") or {}
    if os.path.isfile(coverage_path):
        coverage = load_json(coverage_path, "capability-coverage.json")
        cov_by_domain = {c["domain"]: c for c in coverage.get("capabilities", [])}
        for rd in sorted(runtime_domains):
            target = domain_map.get(rd)
            if not target:
                report.error("coverage_consistency",
                             "registry runtime 域 %r 在 coverage_domain_map 无映射" % rd)
                continue
            cov_item = cov_by_domain.get(target)
            if cov_item is None:
                report.error("coverage_consistency",
                             "registry runtime 域 %r 映射到 coverage 域 %r，但该域不存在" % (rd, target))
                continue
            if cov_item.get("in_pack") is not False:
                report.error("coverage_consistency",
                             "registry runtime 域 %r 映射到 coverage 域 %r，但其 in_pack 非 false"
                             "（coverage 未承认该运行时侧面不可打包）" % (rd, target))
        report.note("runtime 域（registry）：%s" % ", ".join(sorted(runtime_domains)))
    else:
        report.note("capability-coverage.json 不存在，跳过口径一致性核对（非通过）")

    report.note("portable=%d runtime=%d" % (portable_count, runtime_count))
    if portable_count == 0:
        report.error("registry", "无任何 portable 域：清单为空即无法证明可移植能力已登记")
    return report.finish()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
