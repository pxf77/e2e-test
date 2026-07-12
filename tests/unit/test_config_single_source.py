from __future__ import annotations

from pathlib import Path

from e2e_agent.legacy.agents.agent2_path_extract import node as path_extract_node_module
from e2e_agent.core.assertion_templates import load_assertion_template_catalog
from e2e_agent.legacy.skills.loader import SkillPackageLoader

ROOT = Path(__file__).resolve().parents[2]


OBSOLETE_CONFIG_FILES = [
    "state-deps.yaml",
    "assertion-templates.yaml",
    "skill-manifest.yaml",
    "playwright-config.yaml",
]

CURRENT_RUNTIME_DIRS = [
    ROOT / "src" / "e2e_agent" / "assertions",
    ROOT / "src" / "e2e_agent" / "config",
    ROOT / "src" / "e2e_agent" / "contracts",
    ROOT / "src" / "e2e_agent" / "data",
    ROOT / "src" / "e2e_agent" / "domains",
    ROOT / "src" / "e2e_agent" / "plugins",
    ROOT / "src" / "e2e_agent" / "reporting",
    ROOT / "src" / "e2e_agent" / "runners",
    ROOT / "src" / "e2e_agent" / "workflow",
    ROOT / "tools",
    ROOT / "workflows",
]


def test_obsolete_global_configs_are_absent() -> None:
    assert [name for name in OBSOLETE_CONFIG_FILES if (ROOT / "config" / name).exists()] == []
    assert (ROOT / "config" / "model-routing.yaml").exists()
    assert (ROOT / "config" / "gate-operator.yaml").exists()


def test_legacy_agent2_uses_insurance_domain_state_deps() -> None:
    config = path_extract_node_module._load_state_deps_config()

    assert config["version"] == "1.1"
    assert "underwritingResult" in config["whitelist"]["/underwriting*"]
    assert "policyNo" in config["whitelist"]["/payment*"]


def test_legacy_assertion_catalog_uses_insurance_domain_pack() -> None:
    catalog = load_assertion_template_catalog(root_dir=ROOT)

    source = Path(str(catalog["source"])).resolve()
    assert source == (ROOT / "domains" / "insurance" / "assertion-pack.yaml").resolve()
    assert {"price_premium", "underwriting_result", "order_status"} <= set(catalog["templates"])


def test_skill_packages_are_discovered_without_global_index() -> None:
    skills = SkillPackageLoader().list_skills()

    assert {
        "mpt-ins-prd-ana",
        "mpt-ins-tc-gen",
        "mpt-reg-case-merge",
        "mpt-reg-exec",
        "mpt-reg-path-extract",
        "mpt-ins-ts-gen",
    } <= set(skills)


def test_current_runtime_does_not_reference_deleted_config_paths() -> None:
    obsolete_references = [f"config/{name}" for name in OBSOLETE_CONFIG_FILES]
    violations: list[str] = []
    for scan_root in CURRENT_RUNTIME_DIRS:
        if not scan_root.exists():
            continue
        for path in scan_root.rglob("*"):
            if not path.is_file() or path.suffix not in {".py", ".yaml", ".yml", ".json"}:
                continue
            text = path.read_text(encoding="utf-8", errors="replace").replace("\\", "/")
            for reference in obsolete_references:
                if reference in text:
                    violations.append(f"{path.relative_to(ROOT)} -> {reference}")

    assert violations == []
