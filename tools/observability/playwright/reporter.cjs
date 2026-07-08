'use strict';

const fs = require('node:fs');
const path = require('node:path');

const { buildRuntimeRunId, createRuntimeRunWriter, validateRunId } = require('../runtime/emitter.cjs');

function extractProductIdFromFile(filePath) {
  const match = String(filePath || '').match(/products[\\/]+([^\\/]+)[\\/]+/);
  return match ? match[1] : null;
}

function extractTcId(title) {
  const match = String(title || '').match(/(TC-[A-Z]+-\d+)/);
  return match ? match[1] : title;
}

function buildTestKey(test) {
  return `${test.location?.file || ''}::${test.title || ''}`;
}

function resolveReporterRunId(preferredRunId, productId) {
  if (!preferredRunId) {
    return buildRuntimeRunId(productId, 'playwright');
  }

  try {
    return validateRunId(preferredRunId);
  } catch {
    return buildRuntimeRunId(productId, 'playwright');
  }
}

function writeObservabilityPointers(pointerDirs, runId) {
  for (const pointerDir of new Set((pointerDirs || []).filter(Boolean).map((dir) => path.resolve(dir)))) {
    fs.mkdirSync(pointerDir, { recursive: true });
    fs.writeFileSync(path.join(pointerDir, 'observability-run.json'), JSON.stringify({ runId }, null, 2), 'utf8');
  }
}

class ObservabilityReporter {
  constructor(options = {}) {
    this.options = options;
    this.writer = null;
    this.skillStart = null;
    this.phaseStart = null;
    this.testStarts = new Map();
    this.stepStarts = new WeakMap();
    this.reportStartedAt = null;
    this.hasFixmeCase = false;
  }

  getFixmeAnnotation(test) {
    return (test?.annotations || []).find((item) => item?.type === 'fixme') || null;
  }

  ensureWriterFromSuite(suite) {
    if (this.writer) {
      return;
    }

    const allTests = suite?.allTests?.() || [];
    const firstProductTest = allTests.find((item) => extractProductIdFromFile(item?.location?.file));
    const productId = this.options.productId || extractProductIdFromFile(firstProductTest?.location?.file) || 'unknown-product';
    const runId = resolveReporterRunId(this.options.runId || process.env.OBSERVABILITY_RUN_ID || null, productId);

    this.writer = createRuntimeRunWriter({
      repoRoot: this.options.repoRoot || process.cwd(),
      productId,
      runId,
    });
    const canonicalReportDir = path.resolve(this.options.repoRoot || process.cwd(), 'products', productId, 'tc-exec');
    const reportDir = this.options.reportDir
      ? path.resolve(this.options.reportDir)
      : canonicalReportDir;
    fs.mkdirSync(reportDir, { recursive: true });
    writeObservabilityPointers([reportDir, canonicalReportDir], runId);
    this.reportStartedAt = new Date().toISOString();
    // NOTE: Do NOT call writer.startRun() here. The reporter participates in an existing
    // pipeline run managed by the agent. Calling startRun/completeRun would mark the run as
    // terminal, causing any subsequent emit-event calls (without --run-id) to auto-create a
    // new run and fragment the observability timeline.
    this.skillStart = this.writer.startSkill({
      skillName: 'mpt-ins-tc-exec',
      title: '测试执行',
      startedAt: this.reportStartedAt,
    });
    this.phaseStart = this.writer.startPhase({
      skillName: 'mpt-ins-tc-exec',
      phaseName: 'phase1',
      title: '执行测试并生成报告',
      parentEventId: this.skillStart.event_id,
      startedAt: this.reportStartedAt,
    });
  }

  onBegin(config, suite) {
    this.ensureWriterFromSuite(suite);
  }

  onTestBegin(test) {
    this.ensureWriterFromSuite({
      allTests() {
        return [test];
      },
    });
    const startedAt = new Date().toISOString();
    const event = this.writer.startTestCase({
      parentEventId: this.phaseStart.event_id,
      title: extractTcId(test.title),
      startedAt,
      data: {
        file: test.location?.file || null,
        fullTitle: test.title,
      },
    });
    this.testStarts.set(buildTestKey(test), { event, startedAt });
  }

  onStepBegin(test, result, step) {
    if (!this.writer) {
      return;
    }

    const testStart = this.testStarts.get(buildTestKey(test));
    const action = this.writer.startAction({
      parentEventId: testStart?.event?.event_id || this.phaseStart?.event_id || null,
      title: step.title,
      startedAt: new Date().toISOString(),
      data: {
        category: step.category || null,
        tcId: extractTcId(test.title),
      },
    });
    this.stepStarts.set(step, action);
  }

  onStepEnd(test, result, step) {
    if (!this.writer) {
      return;
    }

    const started = this.stepStarts.get(step);
    const status = step?.error ? 'failed' : 'completed';
    this.writer.completeAction({
      parentEventId: started?.event_id || this.phaseStart?.event_id || null,
      title: step.title,
      startedAt: started?.occurred_at || new Date().toISOString(),
      endedAt: new Date().toISOString(),
      status,
      data: {
        category: step.category || null,
        tcId: extractTcId(test.title),
      },
      errorMessage: step?.error?.message || null,
    });
  }

  onTestEnd(test, result) {
    if (!this.writer) {
      return;
    }

    const key = buildTestKey(test);
    const started = this.testStarts.get(key);
    const startedAt = started?.startedAt || result.startTime?.toISOString() || new Date().toISOString();
    const endedAt = new Date(new Date(startedAt).getTime() + (result.duration || 0)).toISOString();
    const fixmeAnnotation = this.getFixmeAnnotation(test);
    const isFixme = result.status === 'skipped' && Boolean(fixmeAnnotation);
    const fixmeReason = isFixme ? (fixmeAnnotation.description || 'static fixme') : null;
    const status = result.status === 'passed'
      ? 'completed'
      : isFixme
        ? 'warning'
        : result.status === 'skipped'
        ? 'skipped'
        : 'failed';
    if (isFixme) {
      this.hasFixmeCase = true;
    }

    this.writer.completeTestCase({
      parentEventId: started?.event?.event_id || this.phaseStart?.event_id || null,
      title: extractTcId(test.title),
      startedAt,
      endedAt,
      durationMs: result.duration || 0,
      status,
      data: {
        file: test.location?.file || null,
        fullTitle: test.title,
        playwrightStatus: result.status,
        isFixme,
        reason: fixmeReason,
        attachments: result.attachments || [],
      },
      errorMessage: result.errors?.[0]?.message || null,
    });
  }

  async onEnd(result) {
    if (!this.writer) {
      return;
    }

    const endedAt = new Date().toISOString();
    const status = result?.status === 'passed'
      ? (this.hasFixmeCase ? 'warning' : 'completed')
      : 'failed';
    this.writer.completePhase({
      skillName: 'mpt-ins-tc-exec',
      phaseName: 'phase1',
      title: '执行测试并生成报告',
      parentEventId: this.phaseStart?.event_id || null,
      startedAt: this.phaseStart?.occurred_at || this.reportStartedAt,
      endedAt,
      status,
      data: {
        playwrightStatus: result?.status || null,
      },
    });
    this.writer.completeSkill({
      skillName: 'mpt-ins-tc-exec',
      title: '测试执行',
      parentEventId: this.skillStart?.event_id || null,
      startedAt: this.skillStart?.occurred_at || this.reportStartedAt,
      endedAt,
      status,
    });
    // NOTE: Do NOT call writer.completeRun() here. Completing the run from the reporter
    // would mark it as terminal, causing any emit-event calls that follow (e.g. pipeline
    // post-processing) to silently create a new run and lose the shared timeline.
  }
}

module.exports = ObservabilityReporter;
