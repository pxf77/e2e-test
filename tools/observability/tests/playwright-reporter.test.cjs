const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

const ObservabilityReporter = require('../playwright/reporter.cjs');
const { loadLatestRuntimeRun } = require('../runtime/emitter.cjs');

test('playwright reporter emits tc-exec runtime events', async () => {
  const repoRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'obs-reporter-'));
  const reportDir = path.join(repoRoot, 'products', 'demo-product', 'tc-exec');
  fs.mkdirSync(reportDir, { recursive: true });
  const reporter = new ObservabilityReporter({
    repoRoot,
    runId: 'run_demo-product_runtime_reporter',
    reportDir,
  });

  const fakeSuite = {
    allTests() {
      return [
        {
          title: 'seed',
          location: {
            file: path.join(repoRoot, 'products', 'seed.spec.ts'),
          },
        },
        {
          title: 'TC-CALC-001 demo calculation succeeds',
          location: {
            file: path.join(repoRoot, 'products', 'demo-product', 'ts-gen', 'scripts', 'calc.spec.ts'),
          },
        },
      ];
    },
  };

  const fakeTest = {
    title: 'TC-CALC-001 demo calculation succeeds',
    location: {
      file: path.join(repoRoot, 'products', 'demo-product', 'ts-gen', 'scripts', 'calc.spec.ts'),
    },
  };

  reporter.onBegin({}, fakeSuite);
  reporter.onTestBegin(fakeTest);
  reporter.onStepBegin(fakeTest, {}, { title: 'fill form', category: 'test.step' });
  reporter.onStepEnd(fakeTest, {}, { title: 'fill form', category: 'test.step' });
  reporter.onTestEnd(fakeTest, {
    status: 'passed',
    duration: 6300,
    errors: [],
    attachments: [],
    startTime: new Date('2026-04-03T08:00:00.000Z'),
  });
  await reporter.onEnd({ status: 'passed' });

  const latest = loadLatestRuntimeRun(repoRoot, 'demo-product');
  assert.ok(latest.events.some((event) => event.event_type === 'phase.started' && event.skill_name === 'mpt-ins-tc-exec'));
  assert.ok(latest.events.some((event) => event.event_type === 'test_case.started'));
  assert.ok(latest.events.some((event) => event.event_type === 'test_case.completed' && event.status === 'completed'));
  const testStart = latest.events.find((event) => event.event_type === 'test_case.started');
  const actionStart = latest.events.find((event) => event.event_type === 'action.started');
  assert.equal(actionStart.parent_event_id, testStart.event_id);
  assert.ok(fs.existsSync(path.join(reportDir, 'observability-run.json')));
});

test('playwright reporter marks fixme skips as warning telemetry', async () => {
  const repoRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'obs-reporter-fixme-'));
  const reportDir = path.join(repoRoot, 'products', 'demo-product', 'tc-exec');
  fs.mkdirSync(reportDir, { recursive: true });
  const reporter = new ObservabilityReporter({
    repoRoot,
    runId: 'run_demo-product_runtime_fixme',
    reportDir,
  });

  const fakeSuite = {
    allTests() {
      return [
        {
          title: 'seed',
          location: {
            file: path.join(repoRoot, 'products', 'seed.spec.ts'),
          },
        },
        {
          title: 'TC-INFO-001 demo fixme case',
          annotations: [{ type: 'fixme' }],
          location: {
            file: path.join(repoRoot, 'products', 'demo-product', 'ts-gen', 'scripts', 'info.spec.ts'),
          },
        },
      ];
    },
  };

  const fakeTest = {
    title: 'TC-INFO-001 demo fixme case',
    annotations: [{ type: 'fixme' }],
    location: {
      file: path.join(repoRoot, 'products', 'demo-product', 'ts-gen', 'scripts', 'info.spec.ts'),
    },
  };

  reporter.onBegin({}, fakeSuite);
  reporter.onTestBegin(fakeTest);
  reporter.onTestEnd(fakeTest, {
    status: 'skipped',
    duration: 0,
    errors: [],
    attachments: [],
    startTime: new Date('2026-04-03T08:00:00.000Z'),
  });
  await reporter.onEnd({ status: 'passed' });

  const latest = loadLatestRuntimeRun(repoRoot, 'demo-product');
  const completedCase = latest.events.find((event) => event.event_type === 'test_case.completed');
  const completedPhase = latest.events.find((event) => event.event_type === 'phase.completed');
  const completedSkill = latest.events.find((event) => event.event_type === 'skill.completed');
  assert.equal(completedCase.status, 'warning');
  assert.equal(completedCase.data.isFixme, true);
  assert.equal(completedCase.data.reason, 'static fixme');
  assert.equal(completedPhase.status, 'warning');
  assert.equal(completedSkill.status, 'warning');
});

test('playwright reporter records failed steps as failed actions', async () => {
  const repoRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'obs-reporter-step-fail-'));
  const reportDir = path.join(repoRoot, 'products', 'demo-product', 'tc-exec');
  fs.mkdirSync(reportDir, { recursive: true });
  const reporter = new ObservabilityReporter({
    repoRoot,
    runId: 'run_demo-product_runtime_step_fail',
    reportDir,
  });

  const fakeSuite = {
    allTests() {
      return [
        {
          title: 'seed',
          location: {
            file: path.join(repoRoot, 'products', 'seed.spec.ts'),
          },
        },
        {
          title: 'TC-CALC-001 demo calculation succeeds',
          location: {
            file: path.join(repoRoot, 'products', 'demo-product', 'ts-gen', 'scripts', 'calc.spec.ts'),
          },
        },
      ];
    },
  };

  const fakeTest = {
    title: 'TC-CALC-001 demo calculation succeeds',
    location: {
      file: path.join(repoRoot, 'products', 'demo-product', 'ts-gen', 'scripts', 'calc.spec.ts'),
    },
  };
  const failedStep = {
    title: 'click submit',
    category: 'test.step',
    error: new Error('locator timeout'),
  };

  reporter.onBegin({}, fakeSuite);
  reporter.onTestBegin(fakeTest);
  reporter.onStepBegin(fakeTest, {}, failedStep);
  reporter.onStepEnd(fakeTest, {}, failedStep);
  reporter.onTestEnd(fakeTest, {
    status: 'failed',
    duration: 1000,
    errors: [{ message: 'locator timeout' }],
    attachments: [],
    startTime: new Date('2026-04-03T08:00:00.000Z'),
  });
  await reporter.onEnd({ status: 'failed' });

  const latest = loadLatestRuntimeRun(repoRoot, 'demo-product');
  const action = latest.events.find((event) => event.event_type === 'action.completed');
  assert.equal(action.status, 'failed');
  assert.equal(action.error_message, 'locator timeout');
});

test('playwright reporter falls back to generated runId when provided runId is invalid', async () => {
  const repoRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'obs-reporter-bad-runid-'));
  const reportDir = path.join(repoRoot, 'products', 'demo-product', 'tc-exec');
  fs.mkdirSync(reportDir, { recursive: true });
  const reporter = new ObservabilityReporter({
    repoRoot,
    runId: 'legacy.invalid.runid',
    reportDir,
  });

  const fakeSuite = {
    allTests() {
      return [
        {
          title: 'TC-CALC-001 合法年龄保额缴费组合试算成功',
          location: {
            file: path.join(repoRoot, 'products', 'demo-product', 'ts-gen', 'scripts', 'calc.spec.ts'),
          },
        },
      ];
    },
  };

  reporter.onBegin({}, fakeSuite);
  await reporter.onEnd({ status: 'passed' });

  const latest = loadLatestRuntimeRun(repoRoot, 'demo-product');
  const pointer = JSON.parse(fs.readFileSync(path.join(reportDir, 'observability-run.json'), 'utf8'));
  assert.match(latest.runId, /^run_demo-product_playwright_/);
  assert.equal(pointer.runId, latest.runId);
  assert.ok(latest.events.some((event) => event.event_type === 'skill.started'));
});

test('playwright reporter writes canonical observability pointer even with custom reportDir', async () => {
  const repoRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'obs-reporter-custom-report-dir-'));
  const customReportDir = path.join(repoRoot, 'custom-output', 'tc-exec');
  const canonicalReportDir = path.join(repoRoot, 'products', 'demo-product', 'tc-exec');
  fs.mkdirSync(customReportDir, { recursive: true });
  const reporter = new ObservabilityReporter({
    repoRoot,
    runId: 'run_demo-product_runtime_custom_dir',
    reportDir: customReportDir,
  });

  const fakeSuite = {
    allTests() {
      return [
        {
          title: 'TC-CALC-001 demo calculation succeeds',
          location: {
            file: path.join(repoRoot, 'products', 'demo-product', 'ts-gen', 'scripts', 'calc.spec.ts'),
          },
        },
      ];
    },
  };

  reporter.onBegin({}, fakeSuite);
  await reporter.onEnd({ status: 'passed' });

  const latest = loadLatestRuntimeRun(repoRoot, 'demo-product');
  const canonicalPointer = JSON.parse(fs.readFileSync(path.join(canonicalReportDir, 'observability-run.json'), 'utf8'));
  assert.equal(canonicalPointer.runId, latest.runId);
});
