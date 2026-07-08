const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { spawnSync } = require('node:child_process');

const { loadLatestRuntimeRun } = require('../runtime/emitter.cjs');

const emitEventCli = path.resolve(__dirname, '..', 'runtime', 'emit-event.cjs');

function runEmit(repoRoot, args) {
  const result = spawnSync(process.execPath, [emitEventCli, '--repo-root', repoRoot, ...args], {
    encoding: 'utf8',
  });
  assert.equal(result.status, 0, result.stderr || result.stdout);
  return result;
}

test('emit-event CLI auto-creates and reuses latest runtime run', () => {
  const repoRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'obs-emit-cli-'));

  runEmit(repoRoot, [
    '--product', 'demo-product',
    '--type', 'run.started',
    '--title', 'pipeline started',
  ]);
  runEmit(repoRoot, [
    '--product', 'demo-product',
    '--type', 'skill.started',
    '--skill-name', 'mpt-ins-prd-ana',
    '--title', 'PRD 解析',
  ]);
  runEmit(repoRoot, [
    '--product', 'demo-product',
    '--type', 'phase.started',
    '--skill-name', 'mpt-ins-prd-ana',
    '--phase-name', 'phase1',
    '--title', 'PRD图片预处理',
  ]);
  runEmit(repoRoot, [
    '--product', 'demo-product',
    '--type', 'tool_call.started',
    '--skill-name', 'mpt-ins-prd-ana',
    '--phase-name', 'phase1',
    '--tool-name', 'ReadFile',
    '--title', '读取 parsing-log',
  ]);
  runEmit(repoRoot, [
    '--product', 'demo-product',
    '--type', 'tool_call.completed',
    '--skill-name', 'mpt-ins-prd-ana',
    '--phase-name', 'phase1',
    '--tool-name', 'ReadFile',
    '--title', '读取 parsing-log',
    '--started-at', '2026-04-03T08:00:00.000Z',
    '--ended-at', '2026-04-03T08:00:01.000Z',
  ]);
  runEmit(repoRoot, [
    '--product', 'demo-product',
    '--type', 'user_gate.waiting',
    '--skill-name', 'mpt-ins-prd-ana',
    '--phase-name', 'phase4',
    '--gate-id', 'gate1',
    '--title', '门禁 1',
    '--occurred-at', '2026-04-03T08:10:00.000Z',
  ]);
  runEmit(repoRoot, [
    '--product', 'demo-product',
    '--type', 'user_gate.approved',
    '--skill-name', 'mpt-ins-prd-ana',
    '--phase-name', 'phase4',
    '--gate-id', 'gate1',
    '--title', '门禁 1',
    '--occurred-at', '2026-04-03T08:11:00.000Z',
  ]);

  const latest = loadLatestRuntimeRun(repoRoot, 'demo-product');
  assert.match(latest.runId, /^run_demo-product_runtime_/);
  assert.equal(latest.events.some((event) => event.event_type === 'skill.started'), true);
  assert.equal(latest.events.some((event) => event.event_type === 'phase.started'), true);
  assert.equal(latest.events.some((event) => event.event_type === 'tool_call.completed'), true);
  assert.equal(latest.events.some((event) => event.event_type === 'user_gate.approved'), true);
  assert.equal(new Set(latest.events.map((event) => event.run_id)).size, 1);
});

test('emit-event CLI does not append fresh skill events into an already completed latest run', () => {
  const repoRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'obs-emit-cli-closed-'));

  runEmit(repoRoot, [
    '--product', 'demo-product',
    '--type', 'run.started',
    '--title', 'pipeline started',
  ]);
  const firstRun = loadLatestRuntimeRun(repoRoot, 'demo-product').runId;
  runEmit(repoRoot, [
    '--product', 'demo-product',
    '--type', 'run.completed',
    '--title', 'pipeline completed',
    '--status', 'warning',
    '--started-at', '2026-04-03T08:00:00.000Z',
    '--ended-at', '2026-04-03T08:10:00.000Z',
  ]);
  runEmit(repoRoot, [
    '--product', 'demo-product',
    '--type', 'skill.started',
    '--skill-name', 'mpt-ins-prd-ana',
    '--title', 'PRD 解析',
  ]);

  const latest = loadLatestRuntimeRun(repoRoot, 'demo-product');
  assert.notEqual(latest.runId, firstRun);
  assert.equal(latest.events.at(-1).event_type, 'skill.started');
});

test('emit-event CLI rejects semantic missing fields', () => {
  const repoRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'obs-emit-cli-invalid-'));
  const result = spawnSync(process.execPath, [
    emitEventCli,
    '--repo-root', repoRoot,
    '--product', 'demo-product',
    '--type', 'phase.started',
    '--title', '缺少 phase 参数',
    '--skill-name', 'mpt-ins-prd-ana',
  ], {
    encoding: 'utf8',
  });

  assert.notEqual(result.status, 0);
  assert.match(result.stderr, /缺少必要参数/);
});

test('emit-event CLI does not create a new latest run for unsupported event types', () => {
  const repoRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'obs-emit-cli-bogus-'));
  const result = spawnSync(process.execPath, [
    emitEventCli,
    '--repo-root', repoRoot,
    '--product', 'demo-product',
    '--type', 'bogus.event',
    '--title', 'bogus',
  ], {
    encoding: 'utf8',
  });

  assert.notEqual(result.status, 0);
  assert.equal(loadLatestRuntimeRun(repoRoot, 'demo-product'), null);
});

test('emit-event CLI does not create a latest run when --data is invalid json', () => {
  const repoRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'obs-emit-cli-bad-data-'));
  const result = spawnSync(process.execPath, [
    emitEventCli,
    '--repo-root', repoRoot,
    '--product', 'demo-product',
    '--type', 'skill.started',
    '--skill-name', 'mpt-ins-prd-ana',
    '--title', 'PRD 解析',
    '--data', '{bad-json',
  ], {
    encoding: 'utf8',
  });

  assert.notEqual(result.status, 0);
  assert.equal(loadLatestRuntimeRun(repoRoot, 'demo-product'), null);
});

test('emit-event CLI rejects non-object --data payloads before creating runtime events', () => {
  const repoRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'obs-emit-cli-bad-data-shape-'));
  const result = spawnSync(process.execPath, [
    emitEventCli,
    '--repo-root', repoRoot,
    '--product', 'demo-product',
    '--type', 'skill.started',
    '--skill-name', 'mpt-ins-prd-ana',
    '--title', 'PRD 解析',
    '--data', '123',
  ], {
    encoding: 'utf8',
  });

  assert.notEqual(result.status, 0);
  assert.match(result.stderr, /JSON object 或 null/);
  assert.equal(loadLatestRuntimeRun(repoRoot, 'demo-product'), null);
});

test('emit-event CLI rejects unknown flags before creating runtime events', () => {
  const repoRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'obs-emit-cli-unknown-'));
  const result = spawnSync(process.execPath, [
    emitEventCli,
    '--repo-root', repoRoot,
    '--product', 'demo-product',
    '--type', 'skill.started',
    '--skill-name', 'mpt-ins-prd-ana',
    '--title', 'PRD 解析',
    '--bogus', 'value',
  ], {
    encoding: 'utf8',
  });

  assert.notEqual(result.status, 0);
  assert.match(result.stderr, /未知参数/);
  assert.equal(loadLatestRuntimeRun(repoRoot, 'demo-product'), null);
});

test('emit-event CLI rejects explicit run-id without run_ prefix', () => {
  const repoRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'obs-emit-cli-bad-runid-'));
  const result = spawnSync(process.execPath, [
    emitEventCli,
    '--repo-root', repoRoot,
    '--product', 'demo-product',
    '--run-id', 'custom_1',
    '--type', 'skill.started',
    '--skill-name', 'mpt-ins-prd-ana',
    '--title', 'PRD 解析',
  ], {
    encoding: 'utf8',
  });

  assert.notEqual(result.status, 0);
  assert.match(result.stderr, /runId/);
  assert.equal(loadLatestRuntimeRun(repoRoot, 'demo-product'), null);
});

test('emit-event CLI rejects invalid status values before creating runtime events', () => {
  const repoRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'obs-emit-cli-bad-status-'));
  const result = spawnSync(process.execPath, [
    emitEventCli,
    '--repo-root', repoRoot,
    '--product', 'demo-product',
    '--type', 'skill.completed',
    '--skill-name', 'mpt-ins-prd-ana',
    '--status', 'bogus',
  ], {
    encoding: 'utf8',
  });

  assert.notEqual(result.status, 0);
  assert.match(result.stderr, /合法状态/);
  assert.equal(loadLatestRuntimeRun(repoRoot, 'demo-product'), null);
});

test('emit-event CLI rejects invalid timestamp arguments before creating runtime events', () => {
  const repoRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'obs-emit-cli-bad-time-'));
  const result = spawnSync(process.execPath, [
    emitEventCli,
    '--repo-root', repoRoot,
    '--product', 'demo-product',
    '--type', 'user_gate.approved',
    '--skill-name', 'mpt-ins-tc-gen',
    '--phase-name', 'phase3',
    '--gate-id', 'gate2',
    '--occurred-at', 'not-a-time',
  ], {
    encoding: 'utf8',
  });

  assert.notEqual(result.status, 0);
  assert.match(result.stderr, /合法时间戳/);
  assert.equal(loadLatestRuntimeRun(repoRoot, 'demo-product'), null);
});

test('emit-event CLI uses started-at for run.started occurred_at when occurred-at is absent', () => {
  const repoRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'obs-emit-cli-run-started-at-'));

  runEmit(repoRoot, [
    '--product', 'demo-product',
    '--type', 'run.started',
    '--title', 'pipeline started',
    '--started-at', '2026-04-03T08:00:00.000Z',
  ]);

  const latest = loadLatestRuntimeRun(repoRoot, 'demo-product');
  const runStarted = latest.events.find((event) => event.event_type === 'run.started');
  assert.equal(runStarted.occurred_at, '2026-04-03T08:00:00.000Z');
});

test('emit-event CLI auto-starts completed first events using ended-at to keep timeline ordered', () => {
  const repoRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'obs-emit-cli-ended-at-'));

  runEmit(repoRoot, [
    '--product', 'demo-product',
    '--type', 'run.completed',
    '--title', 'pipeline completed',
    '--ended-at', '2026-04-03T08:10:00.000Z',
  ]);

  const latest = loadLatestRuntimeRun(repoRoot, 'demo-product');
  assert.equal(latest.events[0].event_type, 'run.started');
  assert.equal(latest.events[0].occurred_at, '2026-04-03T08:10:00.000Z');
  assert.equal(latest.events[1].event_type, 'run.completed');
  assert.equal(latest.events[1].occurred_at, '2026-04-03T08:10:00.000Z');
});

test('emit-event CLI uses occurred-at for completed events when ended-at is absent', () => {
  const repoRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'obs-emit-cli-completed-occurred-at-'));

  runEmit(repoRoot, [
    '--product', 'demo-product',
    '--type', 'run.started',
    '--title', 'pipeline started',
    '--occurred-at', '2026-04-03T08:00:00.000Z',
  ]);
  runEmit(repoRoot, [
    '--product', 'demo-product',
    '--type', 'skill.completed',
    '--skill-name', 'mpt-ins-prd-ana',
    '--title', 'PRD 解析',
    '--occurred-at', '2026-04-03T08:10:00.000Z',
  ]);

  const latest = loadLatestRuntimeRun(repoRoot, 'demo-product');
  const skillCompleted = latest.events.find((event) => event.event_type === 'skill.completed');
  assert.equal(skillCompleted.occurred_at, '2026-04-03T08:10:00.000Z');
});

test('emit-event CLI preserves canonical toolName over conflicting data payload', () => {
  const repoRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'obs-emit-cli-tool-name-override-'));

  runEmit(repoRoot, [
    '--product', 'demo-product',
    '--type', 'tool_call.started',
    '--skill-name', 'mpt-ins-prd-ana',
    '--phase-name', 'phase1',
    '--tool-name', 'ReadFile',
    '--title', '读取文件',
    '--data', '{"toolName":"Shell"}',
  ]);

  const latest = loadLatestRuntimeRun(repoRoot, 'demo-product');
  const started = latest.events.find((event) => event.event_type === 'tool_call.started');
  assert.equal(started.data.toolName, 'ReadFile');
});

test('emit-event CLI preserves canonical gateId and gate resolution timestamp over conflicting payload', () => {
  const repoRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'obs-emit-cli-gate-override-'));

  runEmit(repoRoot, [
    '--product', 'demo-product',
    '--type', 'run.started',
    '--title', 'pipeline started',
  ]);
  runEmit(repoRoot, [
    '--product', 'demo-product',
    '--type', 'user_gate.waiting',
    '--skill-name', 'mpt-ins-tc-gen',
    '--phase-name', 'phase3',
    '--gate-id', 'gate1',
    '--title', '门禁 1',
    '--occurred-at', '2026-04-03T08:00:00.000Z',
    '--data', '{"gateId":"gate2"}',
  ]);
  runEmit(repoRoot, [
    '--product', 'demo-product',
    '--type', 'user_gate.approved',
    '--skill-name', 'mpt-ins-tc-gen',
    '--phase-name', 'phase3',
    '--gate-id', 'gate1',
    '--title', '门禁 1',
    '--ended-at', '2026-04-03T08:06:00.000Z',
    '--data', '{"gateId":"gate2","approved":false}',
  ]);

  const latest = loadLatestRuntimeRun(repoRoot, 'demo-product');
  const waiting = latest.events.find((event) => event.event_type === 'user_gate.waiting');
  const approved = latest.events.find((event) => event.event_type === 'user_gate.approved');

  assert.equal(waiting.data.gateId, 'gate1');
  assert.equal(approved.data.gateId, 'gate1');
  assert.equal(approved.data.approved, true);
  assert.equal(approved.occurred_at, '2026-04-03T08:06:00.000Z');
  assert.equal(approved.parent_event_id, waiting.event_id);
});

test('emit-event CLI rejects explicit parent-event-id when it conflicts with gate payload', () => {
  const repoRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'obs-emit-cli-bad-parent-gate-'));

  runEmit(repoRoot, [
    '--product', 'demo-product',
    '--type', 'run.started',
    '--title', 'pipeline started',
  ]);
  runEmit(repoRoot, [
    '--product', 'demo-product',
    '--type', 'user_gate.waiting',
    '--skill-name', 'mpt-ins-tc-gen',
    '--phase-name', 'phase3',
    '--gate-id', 'gate-a',
    '--title', '人工确认',
    '--occurred-at', '2026-04-03T08:00:00.000Z',
  ]);

  const latest = loadLatestRuntimeRun(repoRoot, 'demo-product');
  const waiting = latest.events.find((event) => event.event_type === 'user_gate.waiting');
  const result = spawnSync(process.execPath, [
    emitEventCli,
    '--repo-root', repoRoot,
    '--product', 'demo-product',
    '--type', 'user_gate.approved',
    '--skill-name', 'mpt-ins-tc-gen',
    '--phase-name', 'phase3',
    '--gate-id', 'gate-b',
    '--title', '人工确认',
    '--parent-event-id', waiting.event_id,
    '--occurred-at', '2026-04-03T08:05:00.000Z',
  ], {
    encoding: 'utf8',
  });

  assert.notEqual(result.status, 0);
  assert.match(result.stderr, /parent-event-id/);
});

test('emit-event CLI auto-start uses started-at before occurred-at and ended-at', () => {
  const repoRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'obs-emit-cli-started-at-priority-'));

  runEmit(repoRoot, [
    '--product', 'demo-product',
    '--type', 'tool_call.completed',
    '--skill-name', 'mpt-ins-prd-ana',
    '--phase-name', 'phase1',
    '--tool-name', 'ReadFile',
    '--title', '读取文件',
    '--started-at', '2026-04-03T08:00:00.000Z',
    '--occurred-at', '2026-04-03T08:05:00.000Z',
    '--ended-at', '2026-04-03T08:10:00.000Z',
  ]);

  const latest = loadLatestRuntimeRun(repoRoot, 'demo-product');
  assert.equal(latest.events[0].event_type, 'run.started');
  assert.equal(latest.events[0].occurred_at, '2026-04-03T08:00:00.000Z');
});

test('emit-event CLI auto-start uses occurred-at before ended-at when started-at is absent', () => {
  const repoRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'obs-emit-cli-occurred-at-priority-'));

  runEmit(repoRoot, [
    '--product', 'demo-product',
    '--type', 'user_gate.waiting',
    '--skill-name', 'mpt-ins-prd-ana',
    '--phase-name', 'phase4',
    '--gate-id', 'gate1',
    '--title', '门禁 1',
    '--occurred-at', '2026-04-03T08:05:00.000Z',
    '--ended-at', '2026-04-03T08:10:00.000Z',
  ]);

  const latest = loadLatestRuntimeRun(repoRoot, 'demo-product');
  assert.equal(latest.events[0].event_type, 'run.started');
  assert.equal(latest.events[0].occurred_at, '2026-04-03T08:05:00.000Z');
});

test('emit-event CLI auto-links gate approvals by latest waiting occurred_at instead of append order', () => {
  const repoRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'obs-emit-cli-gate-link-'));

  runEmit(repoRoot, [
    '--product', 'demo-product',
    '--type', 'run.started',
    '--title', 'pipeline started',
  ]);
  runEmit(repoRoot, [
    '--product', 'demo-product',
    '--type', 'user_gate.waiting',
    '--skill-name', 'mpt-ins-tc-gen',
    '--phase-name', 'phase3',
    '--gate-id', 'gate2',
    '--title', '门禁 2',
    '--occurred-at', '2026-04-03T08:05:00.000Z',
  ]);
  runEmit(repoRoot, [
    '--product', 'demo-product',
    '--type', 'user_gate.waiting',
    '--skill-name', 'mpt-ins-tc-gen',
    '--phase-name', 'phase3',
    '--gate-id', 'gate2',
    '--title', '门禁 2',
    '--occurred-at', '2026-04-03T08:00:00.000Z',
  ]);
  runEmit(repoRoot, [
    '--product', 'demo-product',
    '--type', 'user_gate.approved',
    '--skill-name', 'mpt-ins-tc-gen',
    '--phase-name', 'phase3',
    '--gate-id', 'gate2',
    '--title', '门禁 2',
    '--occurred-at', '2026-04-03T08:06:00.000Z',
  ]);

  const latest = loadLatestRuntimeRun(repoRoot, 'demo-product');
  const waitEvents = latest.events.filter((event) => event.event_type === 'user_gate.waiting');
  const approved = latest.events.find((event) => event.event_type === 'user_gate.approved');
  assert.equal(waitEvents.length, 2);
  assert.equal(approved.parent_event_id, waitEvents[0].event_id);
});

test('emit-event CLI auto-links same-title gate approvals by gateId when multiple waits coexist', () => {
  const repoRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'obs-emit-cli-gate-id-link-'));

  runEmit(repoRoot, [
    '--product', 'demo-product',
    '--type', 'run.started',
    '--title', 'pipeline started',
  ]);
  runEmit(repoRoot, [
    '--product', 'demo-product',
    '--type', 'user_gate.waiting',
    '--skill-name', 'mpt-ins-tc-gen',
    '--phase-name', 'phase3',
    '--gate-id', 'gate-a',
    '--title', '人工确认',
    '--occurred-at', '2026-04-03T08:00:00.000Z',
  ]);
  runEmit(repoRoot, [
    '--product', 'demo-product',
    '--type', 'user_gate.waiting',
    '--skill-name', 'mpt-ins-tc-gen',
    '--phase-name', 'phase3',
    '--gate-id', 'gate-b',
    '--title', '人工确认',
    '--occurred-at', '2026-04-03T08:05:00.000Z',
  ]);
  runEmit(repoRoot, [
    '--product', 'demo-product',
    '--type', 'user_gate.approved',
    '--skill-name', 'mpt-ins-tc-gen',
    '--phase-name', 'phase3',
    '--gate-id', 'gate-a',
    '--title', '人工确认',
    '--occurred-at', '2026-04-03T08:06:00.000Z',
  ]);

  const latest = loadLatestRuntimeRun(repoRoot, 'demo-product');
  const waitEvents = latest.events.filter((event) => event.event_type === 'user_gate.waiting');
  const approved = latest.events.find((event) => event.event_type === 'user_gate.approved');
  const gateAWait = waitEvents.find((event) => event.data?.gateId === 'gate-a');

  assert.equal(waitEvents.length, 2);
  assert.equal(approved.parent_event_id, gateAWait.event_id);
});
