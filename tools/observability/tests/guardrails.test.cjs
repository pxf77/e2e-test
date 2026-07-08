const test = require('node:test');
const assert = require('node:assert/strict');

const { evaluateRunGuardrails } = require('../guardrails/evaluate-run.cjs');
const { buildArtifactRun } = require('../lib/aggregate-run.cjs');

test('evaluateRunGuardrails flags fixme, long phases and unresolved gates', () => {
  const snapshot = {
    run: {
      runId: 'run_demo_product_artifact_20260403005346',
      productId: 'demo-product',
      status: 'warning',
      startedAt: '2026-04-03T00:00:00.000Z',
      endedAt: '2026-04-03T01:00:00.000Z',
      durationMs: 3600000,
    },
    skills: [
      {
        skillName: 'mpt-ins-tc-exec',
        phases: [
          {
            phaseId: 'phase1',
            title: '执行测试并生成报告',
            status: 'warning',
            durationMs: 3_700_000,
          },
        ],
      },
    ],
    execution: {
      summary: {
        total: 11,
        passed: 10,
        failed: 0,
        fixme: 1,
      },
      fixmeTests: [
        {
          tcId: 'TC-INFO-001',
          reason: '证件有效期复合组件不稳定',
        },
      ],
    },
    timeline: [
      {
        event_id: 'evt_gate_wait',
        run_id: 'run_demo_product_artifact_20260403005346',
        trace_id: 'trace_gate',
        kind: 'user_gate',
        event_type: 'user_gate.waiting',
        actor_type: 'user',
        status: 'waiting',
        product_id: 'demo-product',
        skill_name: 'mpt-ins-tc-gen',
        phase_name: 'phase3',
        title: '门禁 2',
        occurred_at: '2026-04-03T00:10:00.000Z',
        started_at: null,
        ended_at: null,
        duration_ms: null,
        tags: ['runtime'],
      },
    ],
  };

  const result = evaluateRunGuardrails(snapshot);
  assert.ok(result.issues.some((issue) => issue.code === 'fixme_remaining'));
  assert.ok(result.issues.some((issue) => issue.code === 'phase_duration_exceeded'));
  assert.ok(result.issues.some((issue) => issue.code === 'gate_unresolved'));
  assert.ok(result.events.every((event) => event.kind === 'guardrail'));
});

test('evaluateRunGuardrails keeps earlier same-title gate waits unresolved when only latest instance is approved', () => {
  const snapshot = {
    run: {
      runId: 'run_demo_product_artifact_20260403005346',
      productId: 'demo-product',
      status: 'warning',
      startedAt: '2026-04-03T00:00:00.000Z',
      endedAt: '2026-04-03T01:00:00.000Z',
      durationMs: 3600000,
    },
    skills: [],
    execution: {
      summary: {
        total: 0,
        passed: 0,
        failed: 0,
        fixme: 0,
      },
      fixmeTests: [],
    },
    timeline: [
      {
        event_id: 'evt_gate_wait_old',
        run_id: 'run_demo_product_artifact_20260403005346',
        trace_id: 'trace_gate',
        kind: 'user_gate',
        event_type: 'user_gate.waiting',
        actor_type: 'user',
        status: 'waiting',
        product_id: 'demo-product',
        skill_name: 'mpt-ins-tc-gen',
        phase_name: 'phase3',
        title: '门禁 2',
        occurred_at: '2026-04-03T00:10:00.000Z',
        data: { gateId: 'gate2' },
      },
      {
        event_id: 'evt_gate_wait_new',
        run_id: 'run_demo_product_artifact_20260403005346',
        trace_id: 'trace_gate',
        kind: 'user_gate',
        event_type: 'user_gate.waiting',
        actor_type: 'user',
        status: 'waiting',
        product_id: 'demo-product',
        skill_name: 'mpt-ins-tc-gen',
        phase_name: 'phase3',
        title: '门禁 2',
        occurred_at: '2026-04-03T00:20:00.000Z',
        data: { gateId: 'gate2' },
      },
      {
        event_id: 'evt_gate_approved',
        run_id: 'run_demo_product_artifact_20260403005346',
        trace_id: 'trace_gate',
        parent_event_id: 'evt_gate_wait_new',
        kind: 'user_gate',
        event_type: 'user_gate.approved',
        actor_type: 'user',
        status: 'completed',
        product_id: 'demo-product',
        skill_name: 'mpt-ins-tc-gen',
        phase_name: 'phase3',
        title: '门禁 2',
        occurred_at: '2026-04-03T00:21:00.000Z',
        data: { gateId: 'gate2', approved: true },
      },
    ],
  };

  const result = evaluateRunGuardrails(snapshot);
  const unresolvedGateIssue = result.issues.find((issue) => issue.code === 'gate_unresolved');
  assert.equal(Boolean(unresolvedGateIssue), true);
  assert.equal(unresolvedGateIssue.data.gateEventId, 'evt_gate_wait_old');
});

test('evaluateRunGuardrails matches same-title gate resolutions by gateId when parent_event_id is absent', () => {
  const snapshot = {
    run: {
      runId: 'run_demo_product_artifact_20260403005346',
      productId: 'demo-product',
      status: 'warning',
      startedAt: '2026-04-03T00:00:00.000Z',
      endedAt: '2026-04-03T01:00:00.000Z',
      durationMs: 3600000,
    },
    skills: [],
    execution: {
      summary: {
        total: 0,
        passed: 0,
        failed: 0,
        fixme: 0,
      },
      fixmeTests: [],
    },
    timeline: [
      {
        event_id: 'evt_gate_wait_a',
        run_id: 'run_demo_product_artifact_20260403005346',
        trace_id: 'trace_gate',
        kind: 'user_gate',
        event_type: 'user_gate.waiting',
        actor_type: 'user',
        status: 'waiting',
        product_id: 'demo-product',
        skill_name: 'mpt-ins-tc-gen',
        phase_name: 'phase3',
        title: '人工确认',
        occurred_at: '2026-04-03T00:10:00.000Z',
        data: { gateId: 'gate-a' },
      },
      {
        event_id: 'evt_gate_wait_b',
        run_id: 'run_demo_product_artifact_20260403005346',
        trace_id: 'trace_gate',
        kind: 'user_gate',
        event_type: 'user_gate.waiting',
        actor_type: 'user',
        status: 'waiting',
        product_id: 'demo-product',
        skill_name: 'mpt-ins-tc-gen',
        phase_name: 'phase3',
        title: '人工确认',
        occurred_at: '2026-04-03T00:20:00.000Z',
        data: { gateId: 'gate-b' },
      },
      {
        event_id: 'evt_gate_approved',
        run_id: 'run_demo_product_artifact_20260403005346',
        trace_id: 'trace_gate',
        kind: 'user_gate',
        event_type: 'user_gate.approved',
        actor_type: 'user',
        status: 'completed',
        product_id: 'demo-product',
        skill_name: 'mpt-ins-tc-gen',
        phase_name: 'phase3',
        title: '人工确认',
        occurred_at: '2026-04-03T00:21:00.000Z',
        data: { gateId: 'gate-a', approved: true },
      },
    ],
  };

  const result = evaluateRunGuardrails(snapshot);
  const unresolvedGateIssue = result.issues.find((issue) => issue.code === 'gate_unresolved');
  assert.equal(Boolean(unresolvedGateIssue), true);
  assert.equal(unresolvedGateIssue.data.gateEventId, 'evt_gate_wait_b');
});

test('evaluateRunGuardrails resolves out-of-order same-title gates by occurred_at when both sides lack gateId', () => {
  const snapshot = {
    run: {
      runId: 'run_demo_product_artifact_20260403005346',
      productId: 'demo-product',
      status: 'warning',
      startedAt: '2026-04-03T00:00:00.000Z',
      endedAt: '2026-04-03T01:00:00.000Z',
      durationMs: 3600000,
    },
    skills: [],
    execution: {
      summary: {
        total: 0,
        passed: 0,
        failed: 0,
        fixme: 0,
      },
      fixmeTests: [],
    },
    timeline: [
      {
        event_id: 'evt_gate_wait_new',
        run_id: 'run_demo_product_artifact_20260403005346',
        trace_id: 'trace_gate',
        kind: 'user_gate',
        event_type: 'user_gate.waiting',
        actor_type: 'user',
        status: 'waiting',
        product_id: 'demo-product',
        skill_name: 'mpt-ins-tc-gen',
        phase_name: 'phase3',
        title: '人工确认',
        occurred_at: '2026-04-03T00:20:00.000Z',
        data: null,
      },
      {
        event_id: 'evt_gate_approved',
        run_id: 'run_demo_product_artifact_20260403005346',
        trace_id: 'trace_gate',
        kind: 'user_gate',
        event_type: 'user_gate.approved',
        actor_type: 'user',
        status: 'completed',
        product_id: 'demo-product',
        skill_name: 'mpt-ins-tc-gen',
        phase_name: 'phase3',
        title: '人工确认',
        occurred_at: '2026-04-03T00:21:00.000Z',
        data: null,
      },
      {
        event_id: 'evt_gate_wait_old',
        run_id: 'run_demo_product_artifact_20260403005346',
        trace_id: 'trace_gate',
        kind: 'user_gate',
        event_type: 'user_gate.waiting',
        actor_type: 'user',
        status: 'waiting',
        product_id: 'demo-product',
        skill_name: 'mpt-ins-tc-gen',
        phase_name: 'phase3',
        title: '人工确认',
        occurred_at: '2026-04-03T00:10:00.000Z',
        data: null,
      },
    ],
  };

  const result = evaluateRunGuardrails(snapshot);
  const unresolvedGateIssue = result.issues.find((issue) => issue.code === 'gate_unresolved');
  assert.equal(Boolean(unresolvedGateIssue), true);
  assert.equal(unresolvedGateIssue.data.gateEventId, 'evt_gate_wait_old');
});

test('evaluateRunGuardrails ignores mismatched parent_event_id on gate resolution', () => {
  const snapshot = {
    run: {
      runId: 'run_demo_product_artifact_20260403005346',
      productId: 'demo-product',
      status: 'warning',
      startedAt: '2026-04-03T00:00:00.000Z',
      endedAt: '2026-04-03T01:00:00.000Z',
      durationMs: 3600000,
    },
    skills: [],
    execution: {
      summary: {
        total: 0,
        passed: 0,
        failed: 0,
        fixme: 0,
      },
      fixmeTests: [],
    },
    timeline: [
      {
        event_id: 'evt_gate_wait_a',
        run_id: 'run_demo_product_artifact_20260403005346',
        trace_id: 'trace_gate',
        kind: 'user_gate',
        event_type: 'user_gate.waiting',
        actor_type: 'user',
        status: 'waiting',
        product_id: 'demo-product',
        skill_name: 'mpt-ins-tc-gen',
        phase_name: 'phase3',
        title: '人工确认',
        occurred_at: '2026-04-03T00:10:00.000Z',
        data: { gateId: 'gate-a' },
      },
      {
        event_id: 'evt_gate_approved',
        run_id: 'run_demo_product_artifact_20260403005346',
        trace_id: 'trace_gate',
        parent_event_id: 'evt_gate_wait_a',
        kind: 'user_gate',
        event_type: 'user_gate.approved',
        actor_type: 'user',
        status: 'completed',
        product_id: 'demo-product',
        skill_name: 'mpt-ins-tc-gen',
        phase_name: 'phase3',
        title: '人工确认',
        occurred_at: '2026-04-03T00:11:00.000Z',
        data: { gateId: 'gate-b', approved: true },
      },
    ],
  };

  const result = evaluateRunGuardrails(snapshot);
  const unresolvedGateIssue = result.issues.find((issue) => issue.code === 'gate_unresolved');
  assert.equal(Boolean(unresolvedGateIssue), true);
  assert.equal(unresolvedGateIssue.data.gateEventId, 'evt_gate_wait_a');
});

test('evaluateRunGuardrails flags runtime_not_merged for aggregate snapshots that still carry runtime telemetry', () => {
  const snapshot = buildArtifactRun({
    repoRoot: 'D:\\huizecode\\probation\\e2e-test',
    productId: 'demo-product',
    artifacts: {
      prdAna: {
        path: 'products/demo-product/prd-ana/parsing-log.md',
        text: `
# mpt-ins-prd-ana 解析日志

## [mpt-ins-prd-ana / 阶段一] PRD图片预处理
- 扫描时间: 2026-03-31 11:44:00
- 状态: ✅ COMPLETE
        `.trim(),
      },
    },
    runtime: {
      runId: 'run_demo_product_runtime_unmerged',
      updatedAt: '2026-04-03T03:08:11.171Z',
      events: [
        {
          event_id: 'evt_runtime_only',
          run_id: 'run_demo_product_runtime_unmerged',
          trace_id: 'trace_runtime_only',
          kind: 'run',
          event_type: 'run.completed',
          actor_type: 'system',
          status: 'warning',
          product_id: 'demo-product',
          occurred_at: '2026-04-03T03:08:11.171Z',
        },
      ],
    },
  });

  const result = evaluateRunGuardrails(snapshot);
  const runtimeIssue = result.issues.find((issue) => issue.code === 'runtime_not_merged');
  assert.equal(Boolean(runtimeIssue), true);
  assert.equal(runtimeIssue.data.runtimeRunId, 'run_demo_product_runtime_unmerged');
});
