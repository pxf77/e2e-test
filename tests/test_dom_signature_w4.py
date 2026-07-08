from __future__ import annotations


def test_compare_dom_signature_bundles_reports_layered_changes() -> None:
    from e2e_agent.core.dom_signature import (
        build_dom_signature_bundle,
        compare_dom_signature_bundles,
    )

    baseline = build_dom_signature_bundle(
        {
            "page_content_record_id": "PCR-001",
            "actual_url": "https://example.com/product/detail?_t=1700000000",
            "actual_page_key": "PK-product-detail",
            "body_text_excerpt": "Premium 123 at 2026-05-11 10:00:00",
            "field_map": [
                {"field_key": "name", "tag": "input", "type": "text", "selector": "#name"}
            ],
            "selector_map": {
                "actions": [
                    {"selector": "#submit", "text": "Next", "tag": "button"},
                ]
            },
        },
        product_id="demo-product",
    )
    candidate = build_dom_signature_bundle(
        {
            "page_content_record_id": "PCR-002",
            "actual_url": "https://example.com/product/detail?_t=1700009999",
            "actual_page_key": "PK-product-detail",
            "body_text_excerpt": "Premium 456 at 2026-05-12 11:30:00 Confirm",
            "field_map": [
                {"field_key": "name", "tag": "input", "type": "text", "selector": "#name"},
                {"field_key": "mobile", "tag": "input", "type": "tel", "selector": "#mobile"},
            ],
            "selector_map": {
                "actions": [
                    {"selector": "#submit", "text": "Next", "tag": "button"},
                ]
            },
        },
        product_id="demo-product",
    )

    diff = compare_dom_signature_bundles(baseline, candidate)

    assert diff["structure_changed"] is True
    assert diff["component_changed_count"] == 1
    assert diff["text_changed"] is True
    assert diff["component_changes"]["added"][0]["component_type"] == "field"
    assert diff["review_required"] is True


def test_build_dom_sample_validation_report_tracks_noise_filtered_samples() -> None:
    from e2e_agent.core.dom_signature import build_dom_sample_validation_report

    report = build_dom_sample_validation_report(
        [
            {
                "page_content_record_id": "PCR-001",
                "actual_page_key": "PK-product-detail",
                "actual_url": "https://example.com/product/detail?_t=1700000000",
                "body_text_excerpt": "Premium 123 at 2026-05-11 10:00:00",
                "field_map": [
                    {"field_key": "name", "tag": "input", "type": "text", "selector": "#name"}
                ],
            },
            {
                "page_content_record_id": "PCR-002",
                "actual_page_key": "PK-product-detail",
                "actual_url": "https://example.com/product/detail?_t=1700009999",
                "body_text_excerpt": "Premium 456 at 2026-05-12 11:30:00",
                "field_map": [
                    {"field_key": "name", "tag": "input", "type": "text", "selector": "#name"}
                ],
            },
        ],
        product_id="demo-product",
    )

    assert report["summary"]["comparison_count"] == 1
    assert report["summary"]["noise_filtered_count"] == 1
    assert report["summary"]["false_positive_rate"] == 0
    assert report["comparisons"][0]["noise_filtered"] is True
    assert "replace_numbers" in report["comparisons"][0]["noise_rules"]

