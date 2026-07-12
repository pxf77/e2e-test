from __future__ import annotations

import asyncio
import json
from pathlib import Path

from e2e_agent.core.knowledge_base import KnowledgeLoader
from e2e_agent.legacy.skills.loader import SkillPackageLoader


def test_knowledge_loader_ignores_missing_knowledge(tmp_path: Path):
    loaded = KnowledgeLoader(root_dir=tmp_path).load("demo-product")

    assert loaded.available is False
    assert loaded.workflow_cases == []
    assert "knowledge-base.json not found" in loaded.warnings[0]


def test_agent3_hints_loads_ui_field_and_mcp_evidence(tmp_path: Path):
    from e2e_agent.core.knowledge_agent3_hints import load_knowledge_agent3_hints

    root = tmp_path / "knowledge" / "demo-product"
    mcp_root = root / "mcp"
    mcp_root.mkdir(parents=True)
    (root / "ui-ontology.json").write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "product_id": "demo-product",
                "pages": [
                    {
                        "page_id": "PAGE-001",
                        "name": "产品详情",
                        "actions": ["立即投保", "保费试算"],
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (root / "field-catalog.json").write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "product_id": "demo-product",
                "fields": [
                    {
                        "field_id": "FIELD-001",
                        "name": "投保 / 证件有效期",
                        "priority": "P0",
                        "rules": ["证件有效期开始日期必填"],
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (mcp_root / "page-snapshots.json").write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "product_id": "demo-product",
                "snapshots": [
                    {
                        "status": "completed",
                        "mode": "page-probe",
                        "url": "https://example.test/product/detail?prodId=1",
                        "title": "Demo 产品详情",
                        "dom_signature": "sha256:detail",
                        "body_text_excerpt": "产品详情 保障责任 立即投保",
                        "fields": [
                            {
                                "selector": "#premium",
                                "label": "保费",
                                "tag": "input",
                                "required": True,
                            }
                        ],
                        "actions": [
                            {
                                "selector": "#buy",
                                "text": "立即投保",
                                "tag": "button",
                                "visible": True,
                            }
                        ],
                        "primary_actions": [
                            {
                                "selector": "#buy",
                                "text": "立即投保",
                                "tag": "button",
                            }
                        ],
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (mcp_root / "exploration-evidence.json").write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "product_id": "demo-product",
                "mode": "page-probe",
                "status": "completed",
                "entry_url": "https://example.test/product/detail?prodId=1",
                "snapshot_count": 1,
                "field_count": 1,
                "action_count": 1,
                "screenshot_path": "screenshots/entry.png",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    hints = load_knowledge_agent3_hints(tmp_path, "demo-product")

    assert hints["available"] is True
    assert hints["summary"]["page_hint_count"] == 2
    assert hints["summary"]["observed_page_hint_count"] == 1
    assert hints["summary"]["field_hint_count"] == 2
    observed = [item for item in hints["pages"] if item["evidence_status"] == "observed"][0]
    assert observed["node_id"] == "NODE-product-detail"
    assert observed["actual_url"] == "https://example.test/product/detail?prodId=1"
    assert observed["screenshot_path"] == "screenshots/entry.png"
    assert observed["mcp_evidence"]["status"] == "completed"
    assert {"by": "selector", "value": "#buy"} in observed["observed_actions"][0]["locators"]
    assert hints["field_hints"][0]["evidence_status"] == "document-inferred"
    assert hints["mcp_exploration_evidence"]["snapshot_count"] == 1


def test_agent3_hints_prefers_page_identity_over_secondary_action_terms(tmp_path: Path):
    from e2e_agent.core.knowledge_agent3_hints import load_knowledge_agent3_hints

    root = tmp_path / "knowledge" / "demo-product"
    root.mkdir(parents=True)
    (root / "ui-ontology.json").write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "product_id": "demo-product",
                "pages": [
                    {
                        "page_id": "PAGE-PAYMENT",
                        "name": "支付",
                        "actions": ["首期保费"],
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    hints = load_knowledge_agent3_hints(tmp_path, "demo-product")

    assert hints["pages"][0]["node_id"] == "NODE-payment"


def test_agent3_hints_rejects_product_id_path_traversal(tmp_path: Path):
    from e2e_agent.core.knowledge_agent3_hints import load_knowledge_agent3_hints

    outside_root = tmp_path / "outside"
    outside_root.mkdir()
    (outside_root / "ui-ontology.json").write_text(
        json.dumps(
            {
                "pages": [
                    {
                        "page_id": "PAGE-OUTSIDE",
                        "name": "Outside product",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    hints = load_knowledge_agent3_hints(tmp_path, "../outside")

    assert hints["available"] is False
    assert hints["pages"] == []
    assert "invalid knowledge product id" in hints["warnings"][0]
    assert str(tmp_path / "outside") not in hints["knowledge_root"]


def test_run_entry_executes_repo_klg_gen_materials_only(tmp_path: Path):
    loader = SkillPackageLoader()
    result = loader.run_entry(
        "mpt-ins-klg-gen",
        {
            "product_id": "demo-product",
            "product_name": "Demo Product",
            "root_dir": str(tmp_path),
            "prd_analysis": {
                "features": [
                    {
                        "feature_id": "FEAT-001",
                        "name": "Insure / payment loop",
                        "acceptance_criteria": [
                            "Reach the payment page",
                            "Show policy issue information after successful payment",
                        ],
                        "priority": "P0",
                    }
                ],
                "application_flow": [
                    {"step": 1, "page": "Insure", "action": "Submit application", "branching": False},
                    {"step": 2, "page": "Payment", "action": "Complete payment", "branching": False},
                ],
            },
            "workflow_cases": {
                "cases": [
                    {
                        "case_id": "KLG-CASE-001",
                        "title": "Wechat payment success policy issue main path",
                        "priority": "P0",
                        "steps": [
                            "Submit application",
                            "Reach Wechat payment page",
                            "Simulate payment success",
                            "Read policy issue information",
                        ],
                        "assertions": [
                            "Order status is payment_success",
                            "Electronic policy entry is visible",
                        ],
                    }
                ]
            },
        },
    )

    root = tmp_path / "knowledge" / "demo-product"
    assert result["product_id"] == "demo-product"
    assert result["knowledge_root"] == str(root)
    assert result["workflow_cases"]["cases"][0]["case_id"] == "KLG-CASE-001"
    assert (root / "knowledge-base.json").exists()
    assert (root / "ui-ontology.json").exists()
    assert (root / "field-catalog.json").exists()
    assert (root / "workflow-cases.json").exists()
    assert (root / "knowledge.md").exists()
    assert not (root / "mcp").exists()


def test_klg_gen_merges_page_probe_into_machine_contracts(tmp_path: Path):
    result = SkillPackageLoader().run_entry(
        "mpt-ins-klg-gen",
        {
            "product_id": "demo-product",
            "product_name": "Demo Product",
            "root_dir": str(tmp_path),
            "materialise": True,
            "exploration_mode": "page-probe",
            "entry_url": "https://example.test/product/detail",
            "page_probe": {
                "status": "completed",
                "url": "https://example.test/product/detail",
                "title": "Demo Product Detail",
                "body_text_excerpt": "Demo Product buy now premium",
                "fields": [
                    {
                        "selector": "#premium",
                        "label": "Premium",
                        "tag": "input",
                        "type": "text",
                        "required": True,
                    }
                ],
                "actions": [
                    {
                        "selector": "#buy",
                        "text": "Buy now",
                        "visible": True,
                        "score": 100,
                    }
                ],
                "primary_actions": [
                    {
                        "selector": "#buy",
                        "text": "Buy now",
                        "visible": True,
                        "score": 100,
                    }
                ],
            },
        },
    )

    root = tmp_path / "knowledge" / "demo-product"
    assert result["exploration"]["status"] == "completed"
    assert result["exploration"]["mode"] == "page-probe"
    assert result["page_probe"]["title"] == "Demo Product Detail"
    assert result["ui_ontology"]["pages"][-1]["source"] == "page-probe"
    assert result["ui_ontology"]["pages"][-1]["primary_actions"][0]["selector"] == "#buy"
    assert result["field_catalog"]["fields"][-1]["source"] == "page-probe"
    assert result["field_catalog"]["fields"][-1]["selector"] == "#premium"
    assert (root / "mcp" / "page-snapshots.json").exists()
    assert (root / "mcp" / "exploration-evidence.json").exists()


def test_klg_gen_records_page_probe_failure_evidence(tmp_path: Path):
    result = SkillPackageLoader().run_entry(
        "mpt-ins-klg-gen",
        {
            "product_id": "demo-product",
            "product_name": "Demo Product",
            "root_dir": str(tmp_path),
            "materialise": True,
            "exploration_mode": "page-probe",
            "entry_url": "https://example.test/product/detail",
            "page_probe": {},
            "page_probe_error": "network unavailable",
        },
    )

    root = tmp_path / "knowledge" / "demo-product"
    evidence_path = root / "mcp" / "exploration-evidence.json"
    assert result["exploration"]["status"] == "failed"
    assert evidence_path.exists()
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    assert evidence["status"] == "failed"
    assert evidence["entry_url"] == "https://example.test/product/detail"
    assert evidence["snapshot_count"] == 0
    assert evidence["field_count"] == 0
    assert evidence["action_count"] == 0
    assert evidence["error"] == "network unavailable"
    assert not (root / "mcp" / "page-snapshots.json").exists()


def test_klg_gen_tolerates_malformed_page_probe_counts(tmp_path: Path):
    result = SkillPackageLoader().run_entry(
        "mpt-ins-klg-gen",
        {
            "product_id": "demo-product",
            "product_name": "Demo Product",
            "root_dir": str(tmp_path),
            "materialise": False,
            "exploration_mode": "page-probe",
            "entry_url": "https://example.test/product/detail",
            "page_probe": {
                "status": "completed",
                "url": "https://example.test/product/detail",
                "field_count": "not-a-number",
                "action_count": "bad",
                "primary_action_count": "bad",
                "fields": [{"selector": "#premium"}],
                "actions": [{"selector": "#buy"}],
                "primary_actions": [{"selector": "#buy"}],
            },
        },
    )

    assert result["page_probe"]["field_count"] == 1
    assert result["page_probe"]["action_count"] == 1
    assert result["page_probe"]["primary_action_count"] == 1
    assert result["knowledge_base"]["source_summary"]["page_probe_field_count"] == 1
    assert result["knowledge_base"]["source_summary"]["page_probe_action_count"] == 1


def test_probe_entry_page_collects_snapshot_with_fake_browser(tmp_path: Path):
    from e2e_agent.core.knowledge_page_probe import probe_entry_page

    class FakeLocator:
        def __init__(self, page: "FakePage", selector: str) -> None:
            self.page = page
            self.selector = selector

        async def evaluate_all(self, _script: str):
            if self.selector == "input, select, textarea":
                return [
                    {
                        "index": 0,
                        "tag": "input",
                        "type": "text",
                        "name": "premium",
                        "id": "premium",
                        "placeholder": "Premium",
                        "label": "",
                        "required": True,
                        "disabled": False,
                        "readonly": False,
                        "checked": False,
                        "value_present": False,
                        "options": [],
                        "selector": "#premium",
                    }
                ]
            return [
                {
                    "index": 0,
                    "tag": "button",
                    "text": "Buy now",
                    "href": "",
                    "id": "buy",
                    "className": "primary",
                    "role": "button",
                    "visible": True,
                    "selector": "#buy",
                    "text_selector": "button >> text=Buy now",
                }
            ]

        async def inner_text(self, timeout: int = 0) -> str:
            return self.page.body_text

    class FakePage:
        def __init__(self) -> None:
            self.url = "about:blank"
            self.body_text = "Demo Product buy now premium"
            self.gotos: list[str] = []
            self.screenshots: list[str] = []

        async def goto(self, url: str, **_kwargs):
            self.gotos.append(url)
            self.url = url

        async def wait_for_load_state(self, *_args, **_kwargs):
            return None

        async def wait_for_timeout(self, *_args, **_kwargs):
            return None

        async def title(self) -> str:
            return "Demo Product Detail"

        def locator(self, selector: str) -> FakeLocator:
            return FakeLocator(self, selector)

        async def screenshot(self, path: str, **_kwargs):
            self.screenshots.append(path)
            Path(path).write_bytes(b"fake-png")

    class FakeSession:
        created: list["FakeSession"] = []

        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs
            self.page = FakePage()
            FakeSession.created.append(self)

        async def __aenter__(self) -> "FakeSession":
            return self

        async def __aexit__(self, *_args) -> None:
            return None

    snapshot = asyncio.run(
        probe_entry_page(
            "https://example.test/product/detail",
            screenshot_dir=tmp_path / "screenshots",
            session_factory=FakeSession,
            headless=True,
        )
    )

    assert snapshot["status"] == "completed"
    assert snapshot["url"] == "https://example.test/product/detail"
    assert snapshot["title"] == "Demo Product Detail"
    assert snapshot["fields"][0]["selector"] == "#premium"
    assert snapshot["actions"][0]["selector"] == "#buy"
    assert Path(snapshot["screenshot_path"]).exists()
    assert FakeSession.created[0].page.gotos == ["https://example.test/product/detail"]


def test_tc_gen_uses_explicit_workflow_cases(tmp_path: Path):
    result = SkillPackageLoader().run_entry(
        "mpt-ins-tc-gen",
        {
            "product_id": "demo-product",
            "root_dir": str(tmp_path),
            "materialise": False,
            "prd_analysis": {"features": [], "application_flow": [], "dependencies": []},
            "workflow_cases": {
                "cases": [
                    {
                        "case_id": "KLG-CASE-001",
                        "title": "Alipay payment success policy issue main path",
                        "priority": "P0",
                        "steps": [
                            "Submit application",
                            "Reach Alipay cashier",
                            "Simulate payment success",
                            "Read policy issue information",
                        ],
                        "assertions": [
                            "Payment status is success",
                            "Policy issue information is readable",
                        ],
                    }
                ]
            },
        },
    )

    case = result["skeleton"][0]
    assert case["id"] == "KLG-CASE-001"
    assert case["title"] == "Alipay payment success policy issue main path"
    assert case["test_data_hints"]["source"] == "knowledge.workflow_cases"
