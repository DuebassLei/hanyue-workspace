#!/usr/bin/env python
"""派生脚本：`writing_rules_factory.json` → `app/resources/skills/builtin_skills.json`。

为什么需要它
-----------
YAML 退役后（R2），`builtin_skills.json` 不再是手写源，而是出厂 JSON 的**派生物**：
把出厂规则行经 `writing_rules.compose_manifest` 合成回 manifest 形态，再走
`skill_registry._build_builtin_manifest` 补齐 promptTemplate（style 类）与
input/output/riskLevel 元数据。`sync_builtin_skills` / `get_builtin_specs` 的机制不变，
只是换数据源——任何人都不能再手改这个文件生效，改规则只能走出厂 JSON 或「创作规则」页。

`manifest` 是 `_build_builtin_manifest` 的不动点：本脚本存进去的就是它的输出，
加载后再跑一遍结果不变（测试 `test_derived_manifest_roundtrips_through_registry_builder`）。

用法（在 apps/api 目录下）：

    python scripts/export_factory_to_builtin.py            # 生成/覆盖派生文件
    python scripts/export_factory_to_builtin.py --check    # 校验派生文件是否与出厂一致（CI 用）
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from types import SimpleNamespace

API_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(API_DIR))

from app.services import skill_registry, writing_rules  # noqa: E402

FACTORY_RESOURCE = API_DIR / "app" / "resources" / "writing_rules_factory.json"
# 路径单一来源：skill_registry 自己声明读哪个文件
BUILTIN_RESOURCE = skill_registry.BUILTIN_RESOURCE


def _fake_rows(rules: list[dict]) -> list[SimpleNamespace]:
    """把出厂规则行伪装成 ORM 行，喂给 compose_manifest（只用到排序与换算所需字段）。"""
    return [
        SimpleNamespace(
            id=f"{rule['ruleType']}:{rule['ruleKey']}",
            rule_type=rule["ruleType"],
            payload=rule.get("payload") or {},
            sort_order=rule.get("sortOrder", 0),
            enabled=rule.get("enabled", True),
            created_at=0,
        )
        for rule in rules
    ]


def _derive_skill(skill: dict) -> dict:
    fake_entry = SimpleNamespace(manifest={})
    manifest = writing_rules.compose_manifest(fake_entry, _fake_rows(skill["rules"]))
    spec = skill_registry.BuiltinSkillSpec(
        key=skill["key"],
        version=skill.get("version", "1.0.0"),
        name=skill["name"],
        description=skill.get("description", ""),
        category=skill.get("category", "flow"),
        tools=list(skill.get("tools") or []),
        manifest=manifest,
    )
    return {
        "key": spec.key,
        "version": spec.version,
        "name": spec.name,
        "description": spec.description,
        "category": spec.category,
        "tools": spec.tools,
        "manifest": skill_registry._build_builtin_manifest(spec),
    }


def derive(factory: dict) -> dict:
    return {"version": 1, "skills": [_derive_skill(skill) for skill in factory["skills"]]}


def check() -> list[str]:
    """校验派生文件与出厂 JSON 一致；返回错误列表（空 = 一致）。"""
    errors: list[str] = []
    if not FACTORY_RESOURCE.exists():
        return [f"缺少出厂数据文件：{FACTORY_RESOURCE}"]
    if not BUILTIN_RESOURCE.exists():
        return [f"缺少派生文件：{BUILTIN_RESOURCE}（运行本脚本不带 --check 生成）"]
    factory = json.loads(FACTORY_RESOURCE.read_text(encoding="utf-8"))
    current = json.loads(BUILTIN_RESOURCE.read_text(encoding="utf-8"))
    derived = derive(factory)
    if current == derived:
        return errors
    errors.append(f"{BUILTIN_RESOURCE.name} 与出厂 JSON 派生结果不一致；重跑本脚本（不带 --check）")
    current_keys = [s["key"] for s in current.get("skills") or []]
    derived_keys = [s["key"] for s in derived["skills"]]
    if current_keys != derived_keys:
        errors.append(f"条目清单不一致：现文件={current_keys}，应=")
    else:
        for expected, actual in zip(derived["skills"], current["skills"]):
            if expected["manifest"] != actual.get("manifest"):
                errors.append(f"manifest 不一致：{expected['key']}")
    return errors


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="只校验不写文件，不一致时退出码 1")
    args = parser.parse_args()
    if args.check:
        errors = check()
        for message in errors:
            print(message, file=sys.stderr)
        sys.exit(1 if errors else 0)
    factory = json.loads(FACTORY_RESOURCE.read_text(encoding="utf-8"))
    derived = derive(factory)
    BUILTIN_RESOURCE.write_text(
        json.dumps(derived, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(f"已写入 {BUILTIN_RESOURCE}（skills: {len(derived['skills'])}）")


if __name__ == "__main__":
    main()
