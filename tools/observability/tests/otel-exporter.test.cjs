const test = require('node:test');
const assert = require('node:assert/strict');

const { exportSnapshotToOtel } = require('../exporters/otel.cjs');

test('exportSnapshotToOtel maps timeline events into OTLP-like spans', () => {
  const snapshot = {
    run: {
      runId: 'run_demo_product_artifact_20260403005346',
      productId: 'demo-product',
      status: 'warning',
      source: 'artifact',
    },
    timeline: [
      {
        event_id: 'evt_1234567890abcdef1234567890abcdef',
        run_id: 'run_demo_product_artifact_20260403005346',
        trace_id: 'trace_abcdef1234567890',
        parent_event_id: null,
        kind: 'phase',
        event_type: 'phase.completed',
        actor_type: 'parser',
        status: 'completed',
        product_id: 'demo-product',
        skill_name: 'mpt-ins-prd-ana',
        phase_name: 'phase2',
        title: '领域知识加载与险种识别',
        occurred_at: '2026-04-03T00:10:00.000Z',
        started_at: '2026-04-03T00:05:00.000Z',
        ended_at: '2026-04-03T00:10:00.000Z',
        duration_ms: 300000,
        tags: ['artifact', 'phase'],
        data: {
          source: 'products/demo-product/prd-ana/parsing-log.md',
        },
      },
    ],
  };

  const payload = exportSnapshotToOtel(snapshot);
  const span = payload.resourceSpans[0].scopeSpans[0].spans[0];

  assert.equal(span.name, '领域知识加载与险种识别');
  assert.match(span.traceId, /^[0-9a-f]{32}$/);
  assert.equal(span.attributes.find((item) => item.key === 'skill.name').value.stringValue, 'mpt-ins-prd-ana');
  assert.equal(span.attributes.find((item) => item.key === 'event.type').value.stringValue, 'phase.completed');
});

test('exportSnapshotToOtel skips events without valid timestamps', () => {
  const snapshot = {
    run: {
      runId: 'run_demo_product_runtime_20260403005346',
      productId: 'demo-product',
      status: 'completed',
      source: 'hybrid',
    },
    timeline: [
      {
        event_id: 'evt_valid_span',
        run_id: 'run_demo_product_runtime_20260403005346',
        trace_id: 'trace_runtime_span',
        kind: 'run',
        event_type: 'run.completed',
        actor_type: 'system',
        status: 'completed',
        product_id: 'demo-product',
        occurred_at: '2026-04-03T00:10:00.000Z',
      },
      {
        event_id: 'evt_invalid_time',
        run_id: 'run_demo_product_runtime_20260403005346',
        trace_id: 'trace_runtime_span',
        kind: 'phase',
        event_type: 'phase.completed',
        actor_type: 'skill',
        status: 'completed',
        product_id: 'demo-product',
        occurred_at: null,
      },
    ],
  };

  const payload = exportSnapshotToOtel(snapshot);
  assert.equal(payload.resourceSpans[0].scopeSpans[0].spans.length, 1);
});
