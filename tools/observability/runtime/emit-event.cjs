#!/usr/bin/env node
'use strict';

const { VALID_STATUSES, isStatusAllowedForEventType } = require('../lib/run-event.cjs');
const { buildRuntimeRunId, createRuntimeRunWriter, loadLatestRuntimeRun, loadRuntimeRunById, validateRunId } = require('./emitter.cjs');
const { findLatestUnresolvedWaitEvent, gateEventsMatch } = require('../lib/gate-resolution.cjs');

const ALLOWED_FLAGS = new Set([
  '--repo-root',
  '--product',
  '--type',
  '--title',
  '--skill-name',
  '--phase-name',
  '--tool-name',
  '--gate-id',
  '--run-id',
  '--run-id-file',
  '--save-run-id-file',
  '--parent-event-id',
  '--occurred-at',
  '--started-at',
  '--ended-at',
  '--status',
  '--data',
  '--error-message',
]);

function parseArgs(argv) {
  const args = {};
  for (let index = 2; index < argv.length; index++) {
    const current = argv[index];
    const next = argv[index + 1];
    if (!current.startsWith('--')) {
      throw new Error(`未知参数: ${current}`);
    }
    if (!ALLOWED_FLAGS.has(current)) {
      throw new Error(`未知参数: ${current}`);
    }
    if (!next || next.startsWith('--')) {
      throw new Error(`参数 ${current} 缺少取值`);
    }
    args[current.slice(2)] = next;
    index++;
  }
  return args;
}

function maybeJson(value) {
  if (!value) {
    return null;
  }

  let parsed;
  try {
    parsed = JSON.parse(value);
  } catch {
    throw new Error(`--data 不是合法的 JSON：${value}`);
  }
  if (parsed === null) {
    return null;
  }
  if (Array.isArray(parsed) || typeof parsed !== 'object') {
    throw new Error('--data 仅支持 JSON object 或 null');
  }
  return parsed;
}

function isValidTimestamp(value) {
  return Boolean(value) && Number.isFinite(Date.parse(value));
}

function validateEventArgs(args) {
  const supportedTypes = new Set([
    'run.started',
    'run.completed',
    'run.failed',
    'skill.started',
    'skill.completed',
    'phase.started',
    'phase.completed',
    'phase.blocked',
    'tool_call.started',
    'tool_call.completed',
    'tool_call.failed',
    'user_gate.waiting',
    'user_gate.approved',
    'user_gate.rejected',
  ]);
  if (!supportedTypes.has(args.type)) {
    throw new Error(`不支持的事件类型: ${args.type}`);
  }

  const requirements = {
    'skill.started': ['skill-name'],
    'skill.completed': ['skill-name'],
    'phase.started': ['skill-name', 'phase-name'],
    'phase.completed': ['skill-name', 'phase-name'],
    'phase.blocked': ['skill-name', 'phase-name'],
    'tool_call.started': ['skill-name', 'phase-name', 'tool-name'],
    'tool_call.completed': ['skill-name', 'phase-name', 'tool-name'],
    'tool_call.failed': ['skill-name', 'phase-name', 'tool-name'],
    'user_gate.waiting': ['skill-name', 'phase-name', 'gate-id'],
    'user_gate.approved': ['skill-name', 'phase-name', 'gate-id'],
    'user_gate.rejected': ['skill-name', 'phase-name', 'gate-id'],
  };
  const missing = (requirements[args.type] || []).filter((key) => !args[key]);
  if (missing.length > 0) {
    throw new Error(`事件 ${args.type} 缺少必要参数: ${missing.map((key) => `--${key}`).join(', ')}`);
  }
}

function validateTimestampArgs(args) {
  for (const key of ['occurred-at', 'started-at', 'ended-at']) {
    if (args[key] && !isValidTimestamp(args[key])) {
      throw new Error(`参数 --${key} 不是合法时间戳: ${args[key]}`);
    }
  }
}

function validateStatusArg(args) {
  if (!args.status) {
    return;
  }
  if (!VALID_STATUSES.has(args.status)) {
    throw new Error(`参数 --status 不是合法状态: ${args.status}`);
  }
  if (!isStatusAllowedForEventType(args.type, args.status)) {
    throw new Error(`参数 --status=${args.status} 不适用于事件类型 ${args.type}`);
  }
}

function resolveOccurredAtArg(args) {
  return args['occurred-at'] || args['ended-at'] || args['started-at'] || null;
}

function isTerminalRun(latestRun) {
  const lastRunEvent = [...(latestRun?.events || [])]
    .reverse()
    .find((event) => event.kind === 'run');
  return Boolean(lastRunEvent && (
    ['run.completed', 'run.failed'].includes(lastRunEvent.event_type) ||
    ['completed', 'failed', 'blocked', 'warning'].includes(lastRunEvent.status)
  ));
}

function readRunIdFile(filePath) {
  let content;
  try {
    content = require('node:fs').readFileSync(filePath, 'utf8').trim();
  } catch (error) {
    throw new Error(`读取 --run-id-file 失败: ${error.message}`);
  }
  if (!content) {
    throw new Error(`--run-id-file 文件为空: ${filePath}`);
  }
  try {
    validateRunId(content);
  } catch {
    throw new Error(`--run-id-file 内容格式非法: ${content}`);
  }
  return content;
}

function saveRunIdFile(filePath, runId) {
  try {
    const fs = require('node:fs');
    const path = require('node:path');
    fs.mkdirSync(path.dirname(filePath), { recursive: true });
    fs.writeFileSync(filePath, runId, 'utf8');
  } catch (error) {
    // Non-fatal: log warning but don't abort
    console.error(`警告: 保存 --save-run-id-file 失败: ${error.message}`);
  }
}

function resolveRunContext(args) {
  const repoRoot = args['repo-root'] || process.cwd();

  if (args['run-id'] && args['run-id-file']) {
    throw new Error('--run-id 和 --run-id-file 不能同时使用，请选择其一');
  }

  // Explicit run ID takes highest priority (direct value or file)
  if (args['run-id']) {
    return {
      runId: args['run-id'],
      autoStartRun: false,
    };
  }

  if (args['run-id-file']) {
    const runId = readRunIdFile(args['run-id-file']);
    return {
      runId,
      autoStartRun: false,
    };
  }

  if (args.type === 'run.started') {
    return {
      runId: buildRuntimeRunId(args.product),
      autoStartRun: false,
    };
  }

  const latest = loadLatestRuntimeRun(repoRoot, args.product);
  if (!latest?.runId || isTerminalRun(latest)) {
    return {
      runId: buildRuntimeRunId(args.product),
      autoStartRun: true,
    };
  }

  return {
    runId: latest.runId,
    autoStartRun: false,
  };
}

function inferGateParentEventId(args, repoRoot, runId) {
  if (!['user_gate.approved', 'user_gate.rejected'].includes(args.type)) {
    return args['parent-event-id'] || null;
  }

  const resolutionEvent = {
    skill_name: args['skill-name'],
    phase_name: args['phase-name'],
    title: args.title || null,
    occurred_at: resolveOccurredAtArg(args) || new Date().toISOString(),
    data: {
      gateId: args['gate-id'],
    },
  };

  if (args['parent-event-id']) {
    const runtime = loadRuntimeRunById(repoRoot, args.product, runId);
    const parentWaitEvent = runtime?.events?.find((event) => event.event_id === args['parent-event-id']) || null;
    if (!parentWaitEvent || parentWaitEvent.event_type !== 'user_gate.waiting' || !gateEventsMatch(parentWaitEvent, resolutionEvent)) {
      throw new Error(`参数 --parent-event-id 与当前门禁事件不一致: ${args['parent-event-id']}`);
    }
    return args['parent-event-id'];
  }

  const runtime = loadRuntimeRunById(repoRoot, args.product, runId);
  if (!runtime?.events?.length) {
    return null;
  }

  const waitingEvent = findLatestUnresolvedWaitEvent(runtime.events, resolutionEvent);

  return waitingEvent?.event_id || null;
}

function main() {
  try {
    const args = parseArgs(process.argv);
    validateEventArgs(args);
    validateTimestampArgs(args);
    validateStatusArg(args);
    const parsedData = maybeJson(args.data);
    const repoRoot = args['repo-root'] || process.cwd();
    const { runId, autoStartRun } = resolveRunContext(args);
    const resolvedGateParentEventId = ['user_gate.approved', 'user_gate.rejected'].includes(args.type)
      ? inferGateParentEventId(args, repoRoot, runId)
      : null;
    const writer = createRuntimeRunWriter({
      repoRoot,
      productId: args.product,
      runId,
    });

    if (autoStartRun) {
      writer.startRun({
        title: 'runtime run auto-started',
        occurredAt: args['started-at'] || args['occurred-at'] || args['ended-at'] || new Date().toISOString(),
        data: { source: 'emit-event-cli' },
      });
    }

    switch (args.type) {
      case 'run.started':
        writer.startRun({ title: args.title, occurredAt: args['started-at'] || args['occurred-at'], data: parsedData });
        break;
      case 'run.completed':
      case 'run.failed':
        writer.completeRun({
          title: args.title,
          startedAt: args['started-at'],
          endedAt: args['ended-at'],
          occurredAt: args['occurred-at'] || args['ended-at'],
          status: args.type === 'run.failed' ? 'failed' : (args.status || 'completed'),
          data: parsedData,
        });
        break;
      case 'skill.started':
        writer.startSkill({
          skillName: args['skill-name'],
          title: args.title,
          startedAt: args['started-at'] || args['occurred-at'],
          parentEventId: args['parent-event-id'],
          data: parsedData,
        });
        break;
      case 'skill.completed':
        writer.completeSkill({
          skillName: args['skill-name'],
          title: args.title,
          parentEventId: args['parent-event-id'],
          startedAt: args['started-at'],
          endedAt: args['ended-at'],
          occurredAt: args['occurred-at'] || args['ended-at'],
          status: args.status || 'completed',
          data: parsedData,
        });
        break;
      case 'phase.started':
        writer.startPhase({
          skillName: args['skill-name'],
          phaseName: args['phase-name'],
          title: args.title,
          parentEventId: args['parent-event-id'],
          startedAt: args['started-at'] || args['occurred-at'],
          data: parsedData,
        });
        break;
      case 'phase.completed':
      case 'phase.blocked':
        writer.completePhase({
          skillName: args['skill-name'],
          phaseName: args['phase-name'],
          title: args.title,
          parentEventId: args['parent-event-id'],
          startedAt: args['started-at'],
          endedAt: args['ended-at'],
          occurredAt: args['occurred-at'] || args['ended-at'],
          status: args.type === 'phase.blocked' ? 'blocked' : (args.status || 'completed'),
          data: parsedData,
        });
        break;
      case 'tool_call.started':
        writer.startToolCall({
          skillName: args['skill-name'],
          phaseName: args['phase-name'],
          toolName: args['tool-name'],
          title: args.title,
          parentEventId: args['parent-event-id'],
          startedAt: args['started-at'] || args['occurred-at'],
          data: parsedData,
        });
        break;
      case 'tool_call.completed':
      case 'tool_call.failed':
        writer.completeToolCall({
          skillName: args['skill-name'],
          phaseName: args['phase-name'],
          toolName: args['tool-name'],
          title: args.title,
          parentEventId: args['parent-event-id'],
          startedAt: args['started-at'],
          endedAt: args['ended-at'],
          occurredAt: args['occurred-at'] || args['ended-at'],
          status: args.type === 'tool_call.failed' ? 'failed' : (args.status || 'completed'),
          data: parsedData,
          errorMessage: args['error-message'] || null,
        });
        break;
      case 'user_gate.waiting':
        writer.waitForGate({
          skillName: args['skill-name'],
          phaseName: args['phase-name'],
          gateId: args['gate-id'],
          title: args.title,
          parentEventId: args['parent-event-id'],
          occurredAt: resolveOccurredAtArg(args),
          data: parsedData,
        });
        break;
      case 'user_gate.approved':
      case 'user_gate.rejected':
        writer.resolveGate({
          skillName: args['skill-name'],
          phaseName: args['phase-name'],
          gateId: args['gate-id'],
          title: args.title,
          parentEventId: resolvedGateParentEventId,
          occurredAt: resolveOccurredAtArg(args),
          approved: args.type === 'user_gate.approved',
          data: parsedData,
        });
        break;
      default:
        throw new Error(`不支持的事件类型: ${args.type}`);
    }

    if (args['save-run-id-file']) {
      saveRunIdFile(args['save-run-id-file'], runId);
    }
    if (!args['run-id'] && !args['run-id-file']) {
      console.log(`runtime run id: ${runId}`);
    }
  } catch (error) {
    console.error(`写入 runtime event 失败: ${error.message}`);
    process.exit(1);
  }
}

main();
