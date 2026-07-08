const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

const { buildArtifactRun } = require('../lib/aggregate-run.cjs');
const {
  createRuntimeRunWriter,
  loadLatestRuntimeRun,
  loadRuntimeRunById,
} = require('../runtime/emitter.cjs');

test('runtime writer stores skill, phase, tool call and gate events', () => {
  const repoRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'obs-runtime-'));
  const writer = createRuntimeRunWriter({
    repoRoot,
    productId: 'demo-product',
    runId: 'run_demo_product_runtime_001',
  });

  const skillStart = writer.startSkill({
    skillName: 'mpt-ins-tc-gen',
    title: '测试用例生成',
  });
  const phaseStart = writer.startPhase({
    skillName: 'mpt-ins-tc-gen',
    phaseName: 'phase3',
    title: '测试计划设计',
    parentEventId: skillStart.event_id,
  });
  const toolStart = writer.startToolCall({
    skillName: 'mpt-ins-tc-gen',
    phaseName: 'phase3',
    toolName: 'Shell',
    title: '运行 playwright test_list',
    parentEventId: phaseStart.event_id,
  });
  writer.completeToolCall({
    skillName: 'mpt-ins-tc-gen',
    phaseName: 'phase3',
    toolName: 'Shell',
    title: '运行 playwright test_list',
    parentEventId: toolStart.event_id,
    startedAt: '2026-04-03T08:00:00.000Z',
    endedAt: '2026-04-03T08:00:02.000Z',
    status: 'completed',
  });
  const gateWait = writer.waitForGate({
    skillName: 'mpt-ins-tc-gen',
    phaseName: 'phase3',
    gateId: 'gate2',
    title: '门禁 2',
    parentEventId: phaseStart.event_id,
  });
  writer.resolveGate({
    skillName: 'mpt-ins-tc-gen',
    phaseName: 'phase3',
    gateId: 'gate2',
    title: '门禁 2',
    parentEventId: gateWait.event_id,
    approved: true,
  });
  writer.completePhase({
    skillName: 'mpt-ins-tc-gen',
    phaseName: 'phase3',
    title: '测试计划设计',
    parentEventId: phaseStart.event_id,
    startedAt: '2026-04-03T08:00:00.000Z',
    endedAt: '2026-04-03T08:10:00.000Z',
    status: 'completed',
  });
  writer.completeSkill({
    skillName: 'mpt-ins-tc-gen',
    title: '测试用例生成',
    parentEventId: skillStart.event_id,
    startedAt: '2026-04-03T08:00:00.000Z',
    endedAt: '2026-04-03T08:10:00.000Z',
    status: 'completed',
  });

  const latest = loadLatestRuntimeRun(repoRoot, 'demo-product');
  assert.equal(latest.runId, 'run_demo_product_runtime_001');
  assert.ok(latest.events.some((event) => event.event_type === 'skill.started'));
  assert.ok(latest.events.some((event) => event.event_type === 'phase.completed'));
  assert.ok(latest.events.some((event) => event.event_type === 'tool_call.completed'));
  assert.ok(latest.events.some((event) => event.event_type === 'user_gate.approved'));

  const snapshot = buildArtifactRun({
    repoRoot,
    productId: 'demo-product',
    artifacts: {},
    runtime: latest,
  });
  assert.ok(snapshot.timeline.some((event) => event.event_type === 'tool_call.completed'));
});

test('runtime writer rejects unsafe run ids', () => {
  const repoRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'obs-runtime-unsafe-'));
  assert.throws(() => createRuntimeRunWriter({
    repoRoot,
    productId: 'demo-product',
    runId: '../../tc-exec',
  }), /runId/);
  assert.throws(() => createRuntimeRunWriter({
    repoRoot,
    productId: 'demo-product',
    runId: 'run.custom.1',
  }), /runId/);
  assert.throws(() => createRuntimeRunWriter({
    repoRoot,
    productId: 'demo-product',
    runId: 'custom_1',
  }), /runId/);
});

test('runtime writer rejects schema-invalid events before persisting', () => {
  const repoRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'obs-runtime-invalid-write-'));
  const writer = createRuntimeRunWriter({
    repoRoot,
    productId: 'demo-product',
    runId: 'run_demo_product_runtime_invalid_write',
  });

  assert.throws(() => writer.completeSkill({
    skillName: 'mpt-ins-prd-ana',
    status: 'bogus',
  }), /非法 runtime event/);
  assert.throws(() => writer.startToolCall({
    skillName: 'mpt-ins-prd-ana',
    phaseName: 'phase1',
  }), /非法 runtime event/);
  assert.throws(() => writer.waitForGate({
    skillName: 'mpt-ins-tc-gen',
    phaseName: 'phase3',
  }), /非法 runtime event/);
});

test('loadLatestRuntimeRun ignores pointers whose eventsPath escapes runtime root', () => {
  const repoRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'obs-runtime-pointer-'));
  const runtimeRoot = path.join(repoRoot, 'products', 'demo-product', 'observability', 'runtime');
  const outsideEvents = path.join(repoRoot, 'outside-events.jsonl');

  fs.mkdirSync(runtimeRoot, { recursive: true });
  fs.writeFileSync(outsideEvents, '{"event_id":"evt_outside"}\n', 'utf8');
  fs.writeFileSync(
    path.join(runtimeRoot, 'latest-run.json'),
    JSON.stringify({
      runId: 'run_demo_product_latest',
      productId: 'demo-product',
      eventsPath: outsideEvents,
      updatedAt: '2026-04-03T03:08:11.171Z',
    }, null, 2),
    'utf8'
  );

  assert.equal(loadLatestRuntimeRun(repoRoot, 'demo-product'), null);
});

test('loadLatestRuntimeRun ignores pointers with invalid runId semantics', () => {
  const repoRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'obs-runtime-bad-runid-'));
  const runtimeRoot = path.join(repoRoot, 'products', 'demo-product', 'observability', 'runtime');
  const runDir = path.join(runtimeRoot, 'runs', 'run_demo_product_ok');
  const eventsPath = path.join(runDir, 'events.jsonl');

  fs.mkdirSync(runDir, { recursive: true });
  fs.writeFileSync(eventsPath, '{"event_id":"evt_ok"}\n', 'utf8');
  fs.writeFileSync(
    path.join(runtimeRoot, 'latest-run.json'),
    JSON.stringify({
      runId: '../../outside',
      productId: 'demo-product',
      eventsPath,
      updatedAt: '2026-04-03T03:08:11.171Z',
    }, null, 2),
    'utf8'
  );

  assert.equal(loadLatestRuntimeRun(repoRoot, 'demo-product'), null);
});

test('loadLatestRuntimeRun ignores runtime-internal links that resolve outside runtime root', () => {
  const repoRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'obs-runtime-link-'));
  const runtimeRoot = path.join(repoRoot, 'products', 'demo-product', 'observability', 'runtime');
  const outsideDir = fs.mkdtempSync(path.join(os.tmpdir(), 'obs-runtime-outside-'));
  const linkDir = path.join(runtimeRoot, 'runs', 'run_demo_product_link');
  const linkedEventsPath = path.join(linkDir, 'events.jsonl');

  fs.mkdirSync(path.join(runtimeRoot, 'runs'), { recursive: true });
  fs.writeFileSync(path.join(outsideDir, 'events.jsonl'), '{"event_id":"evt_out"}\n', 'utf8');
  fs.symlinkSync(outsideDir, linkDir, 'junction');
  fs.writeFileSync(
    path.join(runtimeRoot, 'latest-run.json'),
    JSON.stringify({
      runId: 'run_demo_product_link',
      productId: 'demo-product',
      eventsPath: linkedEventsPath,
      updatedAt: '2026-04-03T03:08:11.171Z',
    }, null, 2),
    'utf8'
  );

  assert.equal(loadLatestRuntimeRun(repoRoot, 'demo-product'), null);
});

test('loadLatestRuntimeRun rebuilds events path from runId when pointer absolute path is stale', () => {
  const repoRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'obs-runtime-stale-pointer-'));
  const runtimeRoot = path.join(repoRoot, 'products', 'demo-product', 'observability', 'runtime');
  const runDir = path.join(runtimeRoot, 'runs', 'run_demo_product_latest');
  const eventsPath = path.join(runDir, 'events.jsonl');

  fs.mkdirSync(runDir, { recursive: true });
  fs.writeFileSync(eventsPath, JSON.stringify({
    schema_version: '1.0.0',
    event_id: 'evt_ok',
    run_id: 'run_demo_product_latest',
    trace_id: 'trace_ok',
    parent_event_id: null,
    kind: 'run',
    event_type: 'run.started',
    actor_type: 'system',
    status: 'started',
    product_id: 'demo-product',
    skill_name: null,
    phase_name: null,
    title: 'runtime run started',
    summary: null,
    occurred_at: '2026-04-03T03:08:11.171Z',
    started_at: null,
    ended_at: null,
    duration_ms: null,
    artifact_path: null,
    input_ref: null,
    output_ref: null,
    error_code: null,
    error_message: null,
    tags: ['runtime', 'run'],
    metrics: null,
    data: { source: 'test' },
  }) + '\n', 'utf8');
  fs.writeFileSync(
    path.join(runtimeRoot, 'latest-run.json'),
    JSON.stringify({
      runId: 'run_demo_product_latest',
      productId: 'demo-product',
      eventsPath: path.join(repoRoot, 'moved', 'away', 'events.jsonl'),
      updatedAt: '2026-04-03T03:08:11.171Z',
    }, null, 2),
    'utf8'
  );

  const latest = loadLatestRuntimeRun(repoRoot, 'demo-product');
  assert.equal(latest.events.length, 1);
  assert.equal(latest.events[0].event_id, 'evt_ok');
});

test('loadRuntimeRunById ignores run directories whose events file resolves outside runtime root', () => {
  const repoRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'obs-runtime-by-id-link-'));
  const runtimeRunDir = path.join(repoRoot, 'products', 'demo-product', 'observability', 'runtime', 'runs', 'run_demo_product_link');
  const outsideDir = fs.mkdtempSync(path.join(os.tmpdir(), 'obs-runtime-by-id-outside-'));
  const linkedEventsPath = path.join(runtimeRunDir, 'events.jsonl');

  fs.mkdirSync(runtimeRunDir, { recursive: true });
  fs.writeFileSync(path.join(outsideDir, 'events.jsonl'), '{"event_id":"evt_out"}\n', 'utf8');
  fs.rmSync(runtimeRunDir, { recursive: true, force: true });
  fs.symlinkSync(outsideDir, runtimeRunDir, 'junction');

  assert.equal(loadRuntimeRunById(repoRoot, 'demo-product', 'run_demo_product_link'), null);
});

test('loadRuntimeRunById returns null when runtime root does not exist yet', () => {
  const repoRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'obs-runtime-missing-root-'));
  assert.equal(loadRuntimeRunById(repoRoot, 'demo-product', 'run_demo_product_missing'), null);
});

test('loadLatestRuntimeRun filters out schema-invalid runtime events', () => {
  const repoRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'obs-runtime-invalid-event-'));
  const runtimeRoot = path.join(repoRoot, 'products', 'demo-product', 'observability', 'runtime');
  const runDir = path.join(runtimeRoot, 'runs', 'run_demo_product_latest');
  const eventsPath = path.join(runDir, 'events.jsonl');

  fs.mkdirSync(runDir, { recursive: true });
  fs.writeFileSync(eventsPath, '{"kind":"run","status":"failed","occurred_at":"2026-04-03T03:08:11.171Z"}\n', 'utf8');
  fs.writeFileSync(
    path.join(runtimeRoot, 'latest-run.json'),
    JSON.stringify({
      runId: 'run_demo_product_latest',
      productId: 'demo-product',
      eventsPath,
      updatedAt: '2026-04-03T03:08:11.171Z',
    }, null, 2),
    'utf8'
  );

  const latest = loadLatestRuntimeRun(repoRoot, 'demo-product');
  assert.equal(latest.events.length, 0);
});
