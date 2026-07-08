const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

const {
  buildArtifactInput,
  parseFlagArgs,
  resolveOutputPath,
} = require('../lib/local-data-loader.cjs');
const { ACTOR_TYPES, EVENT_KINDS, EVENT_TYPES, createRunEvent } = require('../lib/run-event.cjs');

test('parseFlagArgs rejects missing values and unknown flags', () => {
  assert.throws(
    () => parseFlagArgs(['node', 'script', '--product', 'demo-product', '--output'], {
      '--product': 'product',
      '--output': 'output',
    }),
    /缺少取值/
  );

  assert.throws(
    () => parseFlagArgs(['node', 'script', '--unknown'], {
      '--product': 'product',
    }),
    /未知参数/
  );
});

test('buildArtifactInput tolerates missing optional observability artifacts', () => {
  const tempRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'obs-loader-'));
  const productRoot = path.join(tempRoot, 'products', 'demo-product');

  fs.mkdirSync(path.join(productRoot, 'prd-ana'), { recursive: true });
  fs.mkdirSync(path.join(productRoot, 'tc-gen', '.artifacts'), { recursive: true });
  fs.mkdirSync(path.join(productRoot, 'ts-gen', '.artifacts'), { recursive: true });
  fs.writeFileSync(path.join(productRoot, 'prd-ana', 'parsing-log.md'), '# prd', 'utf8');
  fs.writeFileSync(path.join(productRoot, 'tc-gen', '.artifacts', 'tc-gen-log.md'), '# tc-gen', 'utf8');
  fs.writeFileSync(path.join(productRoot, 'ts-gen', '.artifacts', 'ts-gen-log.md'), '# ts-gen', 'utf8');

  const input = buildArtifactInput(tempRoot, 'demo-product');

  assert.equal(input.productId, 'demo-product');
  assert.equal(input.artifacts.healing, null);
  assert.equal(input.artifacts.tcExec, null);
  assert.equal(input.artifacts.htmlReportPath, null);
});

test('buildArtifactInput tolerates partial pipeline artifacts and loads exec-log supplement', () => {
  const tempRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'obs-loader-partial-'));
  const productRoot = path.join(tempRoot, 'products', 'demo-product');

  fs.mkdirSync(path.join(productRoot, 'prd-ana'), { recursive: true });
  fs.mkdirSync(path.join(productRoot, 'tc-exec', '.artifacts'), { recursive: true });
  fs.writeFileSync(path.join(productRoot, 'prd-ana', 'parsing-log.md'), '# prd', 'utf8');
  fs.writeFileSync(path.join(productRoot, 'tc-exec', '.artifacts', 'exec-log.md'), '# exec', 'utf8');

  const input = buildArtifactInput(tempRoot, 'demo-product');

  assert.ok(input.artifacts.prdAna);
  assert.equal(input.artifacts.tcGen, null);
  assert.equal(input.artifacts.tsGen, null);
  assert.ok(input.artifacts.execLog);
  assert.equal(input.artifacts.execLog.path.endsWith('tc-exec/.artifacts/exec-log.md'), true);
});

test('buildArtifactInput prefers explicit tc-exec runtime pointer over latest runtime', () => {
  const tempRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'obs-loader-runtime-'));
  const productRoot = path.join(tempRoot, 'products', 'demo-product');

  fs.mkdirSync(path.join(productRoot, 'prd-ana'), { recursive: true });
  fs.mkdirSync(path.join(productRoot, 'tc-gen', '.artifacts'), { recursive: true });
  fs.mkdirSync(path.join(productRoot, 'ts-gen', '.artifacts'), { recursive: true });
  fs.mkdirSync(path.join(productRoot, 'tc-exec', 'reports'), { recursive: true });
  fs.mkdirSync(path.join(productRoot, 'observability', 'runtime', 'runs', 'run_demo_product_precise'), { recursive: true });
  fs.mkdirSync(path.join(productRoot, 'observability', 'runtime', 'runs', 'run_demo_product_latest'), { recursive: true });

  fs.writeFileSync(path.join(productRoot, 'prd-ana', 'parsing-log.md'), '# prd', 'utf8');
  fs.writeFileSync(path.join(productRoot, 'tc-gen', '.artifacts', 'tc-gen-log.md'), '# tc-gen', 'utf8');
  fs.writeFileSync(path.join(productRoot, 'ts-gen', '.artifacts', 'ts-gen-log.md'), '# ts-gen', 'utf8');
  fs.writeFileSync(path.join(productRoot, 'tc-exec', 'reports', 'test-summary.md'), '# tc-exec', 'utf8');
  fs.writeFileSync(
    path.join(productRoot, 'observability', 'runtime', 'latest-run.json'),
    JSON.stringify({
      runId: 'run_demo_product_latest',
      productId: 'demo-product',
      eventsPath: path.join(productRoot, 'observability', 'runtime', 'runs', 'run_demo_product_latest', 'events.jsonl'),
      updatedAt: '2026-04-03T03:08:11.171Z',
    }, null, 2),
    'utf8'
  );
  fs.writeFileSync(
    path.join(productRoot, 'observability', 'runtime', 'runs', 'run_demo_product_latest', 'events.jsonl'),
    '',
    'utf8'
  );
  fs.writeFileSync(
    path.join(productRoot, 'tc-exec', 'observability-run.json'),
    JSON.stringify({ runId: 'run_demo_product_precise' }, null, 2),
    'utf8'
  );
  const preciseEvent = createRunEvent({
    event_id: 'evt_precise',
    run_id: 'run_demo_product_precise',
    kind: EVENT_KINDS.testCase,
    event_type: EVENT_TYPES.testCaseCompleted,
    actor_type: ACTOR_TYPES.playwright,
    status: 'completed',
    product_id: 'demo-product',
    title: 'TC-PLAN-001',
    occurred_at: '2026-04-03T00:53:46.230Z',
  });
  fs.writeFileSync(
    path.join(productRoot, 'observability', 'runtime', 'runs', 'run_demo_product_precise', 'events.jsonl'),
    `${JSON.stringify(preciseEvent)}\n`,
    'utf8'
  );

  const input = buildArtifactInput(tempRoot, 'demo-product');
  assert.equal(input.runtime.runId, 'run_demo_product_precise');
  assert.equal(input.runtime.events[0].event_id, 'evt_precise');
});

test('buildArtifactInput ignores malformed runtime pointer files', () => {
  const tempRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'obs-loader-bad-pointer-'));
  const productRoot = path.join(tempRoot, 'products', 'demo-product');

  fs.mkdirSync(path.join(productRoot, 'prd-ana'), { recursive: true });
  fs.writeFileSync(path.join(productRoot, 'prd-ana', 'parsing-log.md'), '# prd', 'utf8');
  fs.mkdirSync(path.join(productRoot, 'tc-exec'), { recursive: true });
  fs.writeFileSync(path.join(productRoot, 'tc-exec', 'observability-run.json'), '{bad-json', 'utf8');

  const input = buildArtifactInput(tempRoot, 'demo-product');
  assert.equal(input.runtime, null);
});

test('buildArtifactInput falls back to latest runtime when tc-exec pointer is malformed but latest run exists', () => {
  const tempRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'obs-loader-pointer-fallback-'));
  const productRoot = path.join(tempRoot, 'products', 'demo-product');
  const runtimeRoot = path.join(productRoot, 'observability', 'runtime', 'runs', 'run_demo_product_latest');
  const validEvent = createRunEvent({
    event_id: 'evt_latest',
    run_id: 'run_demo_product_latest',
    kind: EVENT_KINDS.run,
    event_type: EVENT_TYPES.runStarted,
    actor_type: ACTOR_TYPES.system,
    status: 'started',
    product_id: 'demo-product',
    title: 'runtime run started',
    occurred_at: '2026-04-03T03:08:11.171Z',
    data: { source: 'latest-run' },
  });

  fs.mkdirSync(path.join(productRoot, 'prd-ana'), { recursive: true });
  fs.mkdirSync(path.join(productRoot, 'tc-exec', 'reports'), { recursive: true });
  fs.mkdirSync(runtimeRoot, { recursive: true });
  fs.writeFileSync(path.join(productRoot, 'prd-ana', 'parsing-log.md'), '# prd', 'utf8');
  fs.writeFileSync(path.join(productRoot, 'tc-exec', 'reports', 'test-summary.md'), '# tc-exec', 'utf8');
  fs.writeFileSync(path.join(productRoot, 'tc-exec', 'observability-run.json'), '{bad-json', 'utf8');
  fs.writeFileSync(
    path.join(productRoot, 'observability', 'runtime', 'latest-run.json'),
    JSON.stringify({
      runId: 'run_demo_product_latest',
      productId: 'demo-product',
      eventsPath: path.join(runtimeRoot, 'events.jsonl'),
      updatedAt: '2026-04-03T03:08:11.171Z',
    }, null, 2),
    'utf8'
  );
  fs.writeFileSync(path.join(runtimeRoot, 'events.jsonl'), `${JSON.stringify(validEvent)}\n`, 'utf8');

  const input = buildArtifactInput(tempRoot, 'demo-product');
  assert.equal(input.runtime.runId, 'run_demo_product_latest');
  assert.equal(input.runtime.events[0].event_id, 'evt_latest');
});

test('buildArtifactInput ignores runtime pointers with invalid runId semantics', () => {
  const tempRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'obs-loader-bad-runid-'));
  const productRoot = path.join(tempRoot, 'products', 'demo-product');

  fs.mkdirSync(path.join(productRoot, 'prd-ana'), { recursive: true });
  fs.mkdirSync(path.join(productRoot, 'tc-exec'), { recursive: true });
  fs.writeFileSync(path.join(productRoot, 'prd-ana', 'parsing-log.md'), '# prd', 'utf8');
  fs.writeFileSync(
    path.join(productRoot, 'tc-exec', 'observability-run.json'),
    JSON.stringify({ runId: '../../outside' }, null, 2),
    'utf8'
  );

  const input = buildArtifactInput(tempRoot, 'demo-product');
  assert.equal(input.runtime, null);
});

test('buildArtifactInput skips malformed runtime jsonl lines instead of throwing', () => {
  const tempRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'obs-loader-bad-jsonl-'));
  const productRoot = path.join(tempRoot, 'products', 'demo-product');
  const runtimeRoot = path.join(productRoot, 'observability', 'runtime', 'runs', 'run_demo_product_latest');

  fs.mkdirSync(path.join(productRoot, 'prd-ana'), { recursive: true });
  fs.mkdirSync(runtimeRoot, { recursive: true });
  fs.writeFileSync(path.join(productRoot, 'prd-ana', 'parsing-log.md'), '# prd', 'utf8');
  fs.writeFileSync(
    path.join(productRoot, 'observability', 'runtime', 'latest-run.json'),
    JSON.stringify({
      runId: 'run_demo_product_latest',
      productId: 'demo-product',
      eventsPath: path.join(runtimeRoot, 'events.jsonl'),
      updatedAt: '2026-04-03T03:08:11.171Z',
    }, null, 2),
    'utf8'
  );
  const validEvent = createRunEvent({
    event_id: 'evt_ok',
    run_id: 'run_demo_product_latest',
    kind: EVENT_KINDS.skill,
    event_type: EVENT_TYPES.skillStarted,
    actor_type: ACTOR_TYPES.agent,
    status: 'started',
    product_id: 'demo-product',
    skill_name: 'mpt-ins-prd-ana',
    title: 'PRD 解析',
    occurred_at: '2026-04-03T03:08:11.171Z',
  });
  fs.writeFileSync(
    path.join(runtimeRoot, 'events.jsonl'),
    `${JSON.stringify(validEvent)}\n{bad-json\n`,
    'utf8'
  );

  const input = buildArtifactInput(tempRoot, 'demo-product');
  assert.equal(input.runtime.events.length, 1);
  assert.equal(input.runtime.events[0].event_id, 'evt_ok');
});

test('resolveOutputPath keeps generated files inside repo root', () => {
  const repoRoot = 'D:\\huizecode\\probation\\e2e-test';
  const safe = resolveOutputPath(repoRoot, 'products/demo-product/observability/latest/run-data.json');
  assert.match(safe, /observability[\\/]latest[\\/]run-data\.json$/);

  assert.throws(() => resolveOutputPath(repoRoot, '..\\outside.json'), /越界/);
});
