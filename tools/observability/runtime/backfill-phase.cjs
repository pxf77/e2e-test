#!/usr/bin/env node
'use strict';

/**
 * backfill-phase.cjs
 *
 * Retroactively emits phase.started / phase.completed events with custom timestamps.
 * Use this when a phase was executed in a prior session and the start event was never emitted.
 *
 * Usage:
 *   node tools/observability/runtime/backfill-phase.cjs \
 *     --product demo-product \
 *     --skill-name mpt-ins-prd-ana \
 *     --phase-name phase1 \
 *     --title "PRD图片预处理" \
 *     --started-at 2026-04-07T08:00:00.000Z \
 *     --ended-at   2026-04-07T08:30:00.000Z \
 *     [--status completed|failed|blocked]
 *     [--run-id <runId>]
 *     [--repo-root <path>]
 *
 * When --ended-at is omitted, only a phase.started event is written.
 * When --started-at is omitted, only a phase.completed event is written.
 * When both are provided, both events are written in chronological order.
 */

const path = require('node:path');
const { createRuntimeRunWriter, loadLatestRuntimeRun, buildRuntimeRunId, validateRunId } = require('./emitter.cjs');
const { validateProductId } = require('../lib/path-safety.cjs');

const ALLOWED_FLAGS = new Set([
  '--repo-root',
  '--product',
  '--skill-name',
  '--phase-name',
  '--title',
  '--started-at',
  '--ended-at',
  '--status',
  '--run-id',
  '--parent-event-id',
]);

function parseArgs(argv) {
  const args = {};
  for (let i = 2; i < argv.length; i++) {
    const current = argv[i];
    const next = argv[i + 1];
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
    i++;
  }
  return args;
}

function isValidTimestamp(value) {
  return Boolean(value) && Number.isFinite(Date.parse(value));
}

const ALLOWED_STATUSES = new Set(['completed', 'failed', 'blocked', 'warning']);

function validateArgs(args) {
  if (!args.product) {
    throw new Error('必须传入 --product');
  }
  if (!args['skill-name']) {
    throw new Error('必须传入 --skill-name');
  }
  if (!args['phase-name']) {
    throw new Error('必须传入 --phase-name');
  }
  if (!args['started-at'] && !args['ended-at']) {
    throw new Error('至少需要传入 --started-at 或 --ended-at 之一');
  }
  for (const key of ['started-at', 'ended-at']) {
    if (args[key] && !isValidTimestamp(args[key])) {
      throw new Error(`参数 --${key} 不是合法 ISO 时间戳: ${args[key]}`);
    }
  }
  if (args['started-at'] && args['ended-at']) {
    const start = Date.parse(args['started-at']);
    const end = Date.parse(args['ended-at']);
    if (end < start) {
      throw new Error('--ended-at 不能早于 --started-at');
    }
  }
  if (args.status && !ALLOWED_STATUSES.has(args.status)) {
    throw new Error(`--status 取值非法: ${args.status}，允许值: ${[...ALLOWED_STATUSES].join(' / ')}`);
  }
}

function resolveRunId(args, repoRoot) {
  if (args['run-id']) {
    try {
      return validateRunId(args['run-id']);
    } catch {
      throw new Error(`--run-id 格式非法: ${args['run-id']}`);
    }
  }
  const latest = loadLatestRuntimeRun(repoRoot, args.product);
  if (!latest?.runId) {
    return buildRuntimeRunId(args.product, 'backfill');
  }
  // Intentionally reuse the latest run even if it is terminal: backfill is a retroactive
  // operation and should append to the existing run rather than create a new one.
  return latest.runId;
}

function main() {
  try {
    const args = parseArgs(process.argv);
    validateArgs(args);

    const repoRoot = path.resolve(args['repo-root'] || process.cwd());
    validateProductId(args.product);
    const runId = resolveRunId(args, repoRoot);

    const writer = createRuntimeRunWriter({
      repoRoot,
      productId: args.product,
      runId,
    });

    const skillName = args['skill-name'];
    const phaseName = args['phase-name'];
    const title = args.title || phaseName;
    const parentEventId = args['parent-event-id'] || null;
    const status = args.status || 'completed';

    let startEvent = null;

    if (args['started-at']) {
      startEvent = writer.startPhase({
        skillName,
        phaseName,
        title,
        parentEventId,
        startedAt: args['started-at'],
      });
      console.log(`✅ phase.started  [${skillName} / ${phaseName}]  ${args['started-at']}`);
      console.log(`   event_id: ${startEvent.event_id}`);
    }

    if (args['ended-at']) {
      const completeEvent = writer.completePhase({
        skillName,
        phaseName,
        title,
        parentEventId: startEvent?.event_id || parentEventId,
        startedAt: args['started-at'] || null,
        endedAt: args['ended-at'],
        status,
      });
      console.log(`✅ phase.completed [${skillName} / ${phaseName}]  ${args['ended-at']}  status=${status}`);
      console.log(`   event_id: ${completeEvent.event_id}`);
    }

    console.log(`\nrun_id: ${runId}`);
  } catch (error) {
    console.error(`backfill-phase 失败: ${error.message}`);
    process.exit(1);
  }
}

main();
