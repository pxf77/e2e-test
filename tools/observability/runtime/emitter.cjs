'use strict';

const fs = require('node:fs');
const path = require('node:path');

const {
  ACTOR_TYPES,
  EVENT_KINDS,
  EVENT_TYPES,
  createRunEvent,
  validateRunEvent,
} = require('../lib/run-event.cjs');
const { resolveRepoPath, validateProductId } = require('../lib/path-safety.cjs');

function getRuntimeRoot(repoRoot, productId) {
  return resolveRepoPath(repoRoot, `products/${validateProductId(productId)}/observability/runtime`);
}

function validateRunId(runId) {
  if (typeof runId !== 'string' || !/^run_[A-Za-z0-9_-]+$/.test(runId)) {
    throw new Error(`非法 runId: ${runId}`);
  }
  return runId;
}

function getRunDirectory(repoRoot, productId, runId) {
  return resolveRepoPath(repoRoot, `products/${validateProductId(productId)}/observability/runtime/runs/${validateRunId(runId)}`);
}

function ensureDirectory(dirPath) {
  fs.mkdirSync(dirPath, { recursive: true });
}

function appendJsonLine(filePath, payload) {
  fs.appendFileSync(filePath, `${JSON.stringify(payload)}\n`, 'utf8');
}

function safeJsonParse(text) {
  try {
    return JSON.parse(text);
  } catch {
    return null;
  }
}

function readJsonLines(filePath) {
  if (!fs.existsSync(filePath)) {
    return [];
  }

  try {
    return fs.readFileSync(filePath, 'utf8')
      .split(/\r?\n/)
      .filter(Boolean)
      .map((line) => safeJsonParse(line))
      .filter(Boolean);
  } catch {
    return [];
  }
}

function resolveSafeRuntimeEventsPath(runtimeRoot, candidatePath) {
  const runtimeRootResolved = path.resolve(runtimeRoot);
  let runtimeRootRealPath;
  try {
    runtimeRootRealPath = fs.realpathSync.native ? fs.realpathSync.native(runtimeRootResolved) : fs.realpathSync(runtimeRootResolved);
  } catch {
    return null;
  }
  const resolvedEventsPath = path.resolve(candidatePath);
  let realEventsPath;
  try {
    realEventsPath = fs.realpathSync.native ? fs.realpathSync.native(resolvedEventsPath) : fs.realpathSync(resolvedEventsPath);
    if (!fs.statSync(realEventsPath).isFile()) {
      return null;
    }
  } catch {
    return null;
  }
  if (!(realEventsPath === runtimeRootRealPath || realEventsPath.startsWith(`${runtimeRootRealPath}${path.sep}`))) {
    return null;
  }
  return realEventsPath;
}

function filterValidRuntimeEvents(events, expectedRunId, expectedProductId) {
  return (events || []).filter((event) => {
    const validation = validateRunEvent(event);
    return validation.valid && event.run_id === expectedRunId && event.product_id === expectedProductId;
  });
}

function writeLatestRunPointer(runtimeRoot, pointer) {
  ensureDirectory(runtimeRoot);
  fs.writeFileSync(path.join(runtimeRoot, 'latest-run.json'), JSON.stringify(pointer, null, 2), 'utf8');
}

function buildRuntimeRunId(productId, label = 'runtime') {
  return `run_${validateProductId(productId)}_${label}_${Date.now()}`;
}

function createRuntimeRunWriter(options) {
  const repoRoot = path.resolve(options.repoRoot || process.cwd());
  const productId = validateProductId(options.productId);
  const runId = validateRunId(options.runId || buildRuntimeRunId(productId));
  const runtimeRoot = getRuntimeRoot(repoRoot, productId);
  const runDir = getRunDirectory(repoRoot, productId, runId);
  const eventsPath = path.join(runDir, 'events.jsonl');
  const metaPath = path.join(runDir, 'meta.json');

  // Detect whether this is a brand-new run or a reuse of an existing one.
  // Only a new run (no pre-existing events file) should claim the latest-run.json pointer
  // at construction time. Reusing an existing run ID means we are appending to a run that
  // is already the latest — overwriting the pointer is both redundant and dangerous when
  // another process (e.g. the playwright reporter) reuses the same run: it would reset the
  // pointer mid-pipeline and cause subsequent emit-event calls to see a "fresh" run.
  const isNewRun = !fs.existsSync(eventsPath);

  ensureDirectory(runDir);
  if (isNewRun) {
    fs.writeFileSync(metaPath, JSON.stringify({
      runId,
      productId,
      repoRoot,
      createdAt: new Date().toISOString(),
      eventsPath,
    }, null, 2), 'utf8');
    writeLatestRunPointer(runtimeRoot, {
      runId,
      productId,
      runDir,
      eventsPath,
      updatedAt: new Date().toISOString(),
    });
  }

  function emit(input) {
    const event = createRunEvent({
      run_id: runId,
      product_id: productId,
      ...input,
    });
    const validation = validateRunEvent(event);
    if (!validation.valid) {
      throw new Error(`非法 runtime event: ${validation.errors.join('; ')}`);
    }

    appendJsonLine(eventsPath, event);
    // NOTE: updatedAt in latest-run.json is metadata only and may interleave when two
    // processes (e.g. the agent CLI and playwright reporter) emit concurrently to the same run.
    // This is an accepted trade-off: events.jsonl is append-only and authoritative;
    // updatedAt is used only for display and does not affect business logic.
    writeLatestRunPointer(runtimeRoot, {
      runId,
      productId,
      runDir,
      eventsPath,
      updatedAt: event.occurred_at,
    });
    return event;
  }

  return {
    repoRoot,
    productId,
    runId,
    runDir,
    eventsPath,
    emit,
    startRun(input = {}) {
      return emit({
        kind: EVENT_KINDS.run,
        event_type: EVENT_TYPES.runStarted,
        actor_type: ACTOR_TYPES.system,
        status: 'started',
        title: input.title || 'runtime run started',
        occurred_at: input.occurredAt,
        data: input.data || null,
        tags: ['runtime', 'run'],
      });
    },
    completeRun(input = {}) {
      return emit({
        kind: EVENT_KINDS.run,
        event_type: input.status === 'failed' ? EVENT_TYPES.runFailed : EVENT_TYPES.runCompleted,
        actor_type: ACTOR_TYPES.system,
        status: input.status || 'completed',
        title: input.title || 'runtime run completed',
        started_at: input.startedAt,
        ended_at: input.endedAt,
        occurred_at: input.occurredAt || input.endedAt,
        data: input.data || null,
        tags: ['runtime', 'run'],
      });
    },
    startSkill(input) {
      return emit({
        kind: EVENT_KINDS.skill,
        event_type: EVENT_TYPES.skillStarted,
        actor_type: ACTOR_TYPES.agent,
        skill_name: input.skillName,
        status: 'started',
        title: input.title || input.skillName,
        parent_event_id: input.parentEventId || null,
        occurred_at: input.startedAt,
        data: input.data || null,
        tags: ['runtime', 'skill'],
      });
    },
    completeSkill(input) {
      return emit({
        kind: EVENT_KINDS.skill,
        event_type: EVENT_TYPES.skillCompleted,
        actor_type: ACTOR_TYPES.agent,
        skill_name: input.skillName,
        status: input.status || 'completed',
        title: input.title || input.skillName,
        parent_event_id: input.parentEventId || null,
        started_at: input.startedAt,
        ended_at: input.endedAt,
        occurred_at: input.occurredAt || input.endedAt,
        data: input.data || null,
        tags: ['runtime', 'skill'],
      });
    },
    startPhase(input) {
      return emit({
        kind: EVENT_KINDS.phase,
        event_type: EVENT_TYPES.phaseStarted,
        actor_type: ACTOR_TYPES.skill,
        skill_name: input.skillName,
        phase_name: input.phaseName,
        status: 'started',
        title: input.title || input.phaseName,
        parent_event_id: input.parentEventId || null,
        occurred_at: input.startedAt,
        data: input.data || null,
        tags: ['runtime', 'phase'],
      });
    },
    completePhase(input) {
      return emit({
        kind: EVENT_KINDS.phase,
        event_type: input.status === 'blocked' ? EVENT_TYPES.phaseBlocked : EVENT_TYPES.phaseCompleted,
        actor_type: ACTOR_TYPES.skill,
        skill_name: input.skillName,
        phase_name: input.phaseName,
        status: input.status || 'completed',
        title: input.title || input.phaseName,
        parent_event_id: input.parentEventId || null,
        started_at: input.startedAt,
        ended_at: input.endedAt,
        occurred_at: input.occurredAt || input.endedAt,
        data: input.data || null,
        tags: ['runtime', 'phase'],
      });
    },
    startToolCall(input) {
      return emit({
        kind: EVENT_KINDS.toolCall,
        event_type: EVENT_TYPES.toolCallStarted,
        actor_type: ACTOR_TYPES.tool,
        skill_name: input.skillName || null,
        phase_name: input.phaseName || null,
        status: 'started',
        title: input.title || input.toolName,
        parent_event_id: input.parentEventId || null,
        occurred_at: input.startedAt,
        data: {
          ...(input.data || {}),
          toolName: input.toolName,
        },
        tags: ['runtime', 'tool-call'],
      });
    },
    completeToolCall(input) {
      return emit({
        kind: EVENT_KINDS.toolCall,
        event_type: input.status === 'failed' ? EVENT_TYPES.toolCallFailed : EVENT_TYPES.toolCallCompleted,
        actor_type: ACTOR_TYPES.tool,
        skill_name: input.skillName || null,
        phase_name: input.phaseName || null,
        status: input.status || 'completed',
        title: input.title || input.toolName,
        parent_event_id: input.parentEventId || null,
        started_at: input.startedAt,
        ended_at: input.endedAt,
        occurred_at: input.occurredAt || input.endedAt,
        data: {
          ...(input.data || {}),
          toolName: input.toolName,
        },
        error_message: input.errorMessage || null,
        tags: ['runtime', 'tool-call'],
      });
    },
    waitForGate(input) {
      return emit({
        kind: EVENT_KINDS.userGate,
        event_type: EVENT_TYPES.userGateWaiting,
        actor_type: ACTOR_TYPES.user,
        skill_name: input.skillName || null,
        phase_name: input.phaseName || null,
        status: 'waiting',
        title: input.title || input.gateId,
        parent_event_id: input.parentEventId || null,
        occurred_at: input.occurredAt,
        data: {
          ...(input.data || {}),
          gateId: input.gateId,
        },
        tags: ['runtime', 'gate'],
      });
    },
    resolveGate(input) {
      return emit({
        kind: EVENT_KINDS.userGate,
        event_type: input.approved ? EVENT_TYPES.userGateApproved : EVENT_TYPES.userGateRejected,
        actor_type: ACTOR_TYPES.user,
        skill_name: input.skillName || null,
        phase_name: input.phaseName || null,
        status: input.approved ? 'completed' : 'blocked',
        title: input.title || input.gateId,
        parent_event_id: input.parentEventId || null,
        occurred_at: input.occurredAt,
        data: {
          ...(input.data || {}),
          gateId: input.gateId,
          approved: Boolean(input.approved),
        },
        tags: ['runtime', 'gate'],
      });
    },
    startTestCase(input) {
      return emit({
        kind: EVENT_KINDS.testCase,
        event_type: EVENT_TYPES.testCaseStarted,
        actor_type: ACTOR_TYPES.playwright,
        skill_name: input.skillName || 'mpt-ins-tc-exec',
        phase_name: input.phaseName || 'phase1',
        status: 'started',
        title: input.title,
        parent_event_id: input.parentEventId || null,
        occurred_at: input.startedAt,
        data: input.data || null,
        tags: ['runtime', 'test-case'],
      });
    },
    completeTestCase(input) {
      return emit({
        kind: EVENT_KINDS.testCase,
        event_type: EVENT_TYPES.testCaseCompleted,
        actor_type: ACTOR_TYPES.playwright,
        skill_name: input.skillName || 'mpt-ins-tc-exec',
        phase_name: input.phaseName || 'phase1',
        status: input.status || 'completed',
        title: input.title,
        parent_event_id: input.parentEventId || null,
        started_at: input.startedAt,
        ended_at: input.endedAt,
        occurred_at: input.endedAt,
        duration_ms: input.durationMs || null,
        data: input.data || null,
        error_message: input.errorMessage || null,
        tags: ['runtime', 'test-case'],
      });
    },
    startAction(input) {
      return emit({
        kind: EVENT_KINDS.action,
        event_type: EVENT_TYPES.actionStarted,
        actor_type: ACTOR_TYPES.playwright,
        skill_name: input.skillName || 'mpt-ins-tc-exec',
        phase_name: input.phaseName || 'phase1',
        status: 'started',
        title: input.title,
        parent_event_id: input.parentEventId || null,
        occurred_at: input.startedAt,
        data: input.data || null,
        tags: ['runtime', 'action'],
      });
    },
    completeAction(input) {
      return emit({
        kind: EVENT_KINDS.action,
        event_type: EVENT_TYPES.actionCompleted,
        actor_type: ACTOR_TYPES.playwright,
        skill_name: input.skillName || 'mpt-ins-tc-exec',
        phase_name: input.phaseName || 'phase1',
        status: input.status || 'completed',
        title: input.title,
        parent_event_id: input.parentEventId || null,
        started_at: input.startedAt,
        ended_at: input.endedAt,
        occurred_at: input.endedAt,
        data: input.data || null,
        error_message: input.errorMessage || null,
        tags: ['runtime', 'action'],
      });
    },
  };
}

function loadLatestRuntimeRun(repoRoot, productId) {
  const runtimeRoot = getRuntimeRoot(repoRoot, productId);
  const pointerPath = path.join(runtimeRoot, 'latest-run.json');
  if (!fs.existsSync(pointerPath)) {
    return null;
  }

  const pointer = safeJsonParse(fs.readFileSync(pointerPath, 'utf8'));
  if (!pointer?.eventsPath) {
    return null;
  }
  try {
    validateRunId(pointer.runId);
  } catch {
    return null;
  }
  let realEventsPath = resolveSafeRuntimeEventsPath(runtimeRoot, pointer.eventsPath);
  if (!realEventsPath) {
    try {
      const fallbackEventsPath = path.join(getRunDirectory(repoRoot, productId, pointer.runId), 'events.jsonl');
      realEventsPath = resolveSafeRuntimeEventsPath(runtimeRoot, fallbackEventsPath);
    } catch {
      return null;
    }
  }
  if (!realEventsPath) {
    return null;
  }
  return {
    ...pointer,
    runDir: path.dirname(realEventsPath),
    eventsPath: realEventsPath,
    events: filterValidRuntimeEvents(readJsonLines(realEventsPath), pointer.runId, productId),
  };
}

function loadRuntimeRunById(repoRoot, productId, runId) {
  const runtimeRoot = getRuntimeRoot(repoRoot, productId);
  let runDir;
  try {
    runDir = getRunDirectory(repoRoot, productId, validateRunId(runId));
  } catch {
    return null;
  }
  const eventsPath = path.join(runDir, 'events.jsonl');
  const realEventsPath = resolveSafeRuntimeEventsPath(runtimeRoot, eventsPath);
  if (!realEventsPath) {
    return null;
  }

  return {
    runId,
    productId,
    runDir,
    eventsPath: realEventsPath,
    events: filterValidRuntimeEvents(readJsonLines(realEventsPath), runId, productId),
  };
}

module.exports = {
  buildRuntimeRunId,
  createRuntimeRunWriter,
  loadLatestRuntimeRun,
  loadRuntimeRunById,
  readJsonLines,
  validateRunId,
};
