'use strict';

const crypto = require('node:crypto');

const RUN_EVENT_SCHEMA_VERSION = '2026-04-01';

const ACTOR_TYPES = {
  system: 'system',
  skill: 'skill',
  agent: 'agent',
  tool: 'tool',
  playwright: 'playwright',
  user: 'user',
  guardrail: 'guardrail',
  parser: 'parser',
};

const EVENT_KINDS = {
  run: 'run',
  skill: 'skill',
  phase: 'phase',
  action: 'action',
  toolCall: 'tool_call',
  userGate: 'user_gate',
  testCase: 'test_case',
  artifact: 'artifact',
  guardrail: 'guardrail',
  debugSnapshot: 'debug_snapshot',
};

const EVENT_TYPES = {
  runStarted: 'run.started',
  runCompleted: 'run.completed',
  runFailed: 'run.failed',
  skillStarted: 'skill.started',
  skillCompleted: 'skill.completed',
  phaseStarted: 'phase.started',
  phaseCompleted: 'phase.completed',
  phaseBlocked: 'phase.blocked',
  toolCallStarted: 'tool_call.started',
  toolCallCompleted: 'tool_call.completed',
  toolCallFailed: 'tool_call.failed',
  userGateWaiting: 'user_gate.waiting',
  userGateApproved: 'user_gate.approved',
  userGateRejected: 'user_gate.rejected',
  testCaseStarted: 'test_case.started',
  testCaseCompleted: 'test_case.completed',
  actionStarted: 'action.started',
  actionCompleted: 'action.completed',
  guardrailWarn: 'guardrail.warn',
  guardrailBlocked: 'guardrail.blocked',
};

const KIND_ENUM = new Set(Object.values(EVENT_KINDS));
const ACTOR_ENUM = new Set(Object.values(ACTOR_TYPES));
const STATUS_ENUM = new Set([
  'started',
  'completed',
  'failed',
  'blocked',
  'warning',
  'waiting',
  'skipped',
  'cancelled',
  'observed',
  'unobserved',
]);

const EVENT_TYPE_TO_ALLOWED_STATUS = {
  [EVENT_TYPES.runStarted]: new Set(['started']),
  [EVENT_TYPES.runCompleted]: new Set(['completed', 'warning', 'failed', 'blocked']),
  [EVENT_TYPES.runFailed]: new Set(['failed', 'warning']),
  [EVENT_TYPES.skillStarted]: new Set(['started']),
  [EVENT_TYPES.skillCompleted]: new Set(['completed', 'warning', 'failed', 'blocked']),
  [EVENT_TYPES.phaseStarted]: new Set(['started']),
  [EVENT_TYPES.phaseCompleted]: new Set(['completed', 'warning', 'failed']),
  [EVENT_TYPES.phaseBlocked]: new Set(['blocked', 'warning']),
  [EVENT_TYPES.toolCallStarted]: new Set(['started']),
  [EVENT_TYPES.toolCallCompleted]: new Set(['completed', 'warning', 'failed']),
  [EVENT_TYPES.toolCallFailed]: new Set(['failed', 'warning']),
  [EVENT_TYPES.userGateWaiting]: new Set(['waiting']),
  [EVENT_TYPES.userGateApproved]: new Set(['completed', 'warning']),
  [EVENT_TYPES.userGateRejected]: new Set(['blocked', 'warning']),
  [EVENT_TYPES.testCaseStarted]: new Set(['started']),
  [EVENT_TYPES.testCaseCompleted]: new Set(['completed', 'failed', 'warning', 'skipped']),
  [EVENT_TYPES.actionStarted]: new Set(['started']),
  [EVENT_TYPES.actionCompleted]: new Set(['completed', 'failed', 'warning']),
  [EVENT_TYPES.guardrailWarn]: new Set(['warning']),
  [EVENT_TYPES.guardrailBlocked]: new Set(['blocked']),
};

const EVENT_TYPE_TO_KIND = {
  [EVENT_TYPES.runStarted]: EVENT_KINDS.run,
  [EVENT_TYPES.runCompleted]: EVENT_KINDS.run,
  [EVENT_TYPES.runFailed]: EVENT_KINDS.run,
  [EVENT_TYPES.skillStarted]: EVENT_KINDS.skill,
  [EVENT_TYPES.skillCompleted]: EVENT_KINDS.skill,
  [EVENT_TYPES.phaseStarted]: EVENT_KINDS.phase,
  [EVENT_TYPES.phaseCompleted]: EVENT_KINDS.phase,
  [EVENT_TYPES.phaseBlocked]: EVENT_KINDS.phase,
  [EVENT_TYPES.toolCallStarted]: EVENT_KINDS.toolCall,
  [EVENT_TYPES.toolCallCompleted]: EVENT_KINDS.toolCall,
  [EVENT_TYPES.toolCallFailed]: EVENT_KINDS.toolCall,
  [EVENT_TYPES.userGateWaiting]: EVENT_KINDS.userGate,
  [EVENT_TYPES.userGateApproved]: EVENT_KINDS.userGate,
  [EVENT_TYPES.userGateRejected]: EVENT_KINDS.userGate,
  [EVENT_TYPES.testCaseStarted]: EVENT_KINDS.testCase,
  [EVENT_TYPES.testCaseCompleted]: EVENT_KINDS.testCase,
  [EVENT_TYPES.actionStarted]: EVENT_KINDS.action,
  [EVENT_TYPES.actionCompleted]: EVENT_KINDS.action,
  [EVENT_TYPES.guardrailWarn]: EVENT_KINDS.guardrail,
  [EVENT_TYPES.guardrailBlocked]: EVENT_KINDS.guardrail,
};

function deriveTraceId(runId, skillName) {
  const h = crypto.createHash('sha256');
  h.update(`${runId}\0${skillName || ''}`);
  return `trace_${h.digest('hex').slice(0, 32)}`;
}

function randomEventId() {
  return `evt_${crypto.randomBytes(8).toString('hex')}`;
}

function parseIso(ms) {
  if (typeof ms !== 'string') {
    return null;
  }
  const t = Date.parse(ms);
  return Number.isNaN(t) ? null : t;
}

function createRunEvent(input) {
  const now = new Date().toISOString();
  const skillName = input.skill_name ?? null;
  const started = input.started_at;
  const ended = input.ended_at;
  const startMs = parseIso(started);
  const endMs = parseIso(ended);
  let duration_ms = input.duration_ms;
  if (duration_ms == null && startMs != null && endMs != null) {
    duration_ms = Math.max(0, endMs - startMs);
  }

  const event = {
    schema_version: RUN_EVENT_SCHEMA_VERSION,
    event_id: input.event_id || randomEventId(),
    run_id: input.run_id,
    trace_id: input.trace_id || deriveTraceId(input.run_id, skillName || ''),
    parent_event_id: input.parent_event_id ?? null,
    kind: input.kind,
    event_type: input.event_type,
    actor_type: input.actor_type,
    status: input.status,
    product_id: input.product_id,
    skill_name: skillName,
    phase_name: input.phase_name ?? null,
    title: input.title ?? null,
    summary: input.summary ?? null,
    occurred_at: input.occurred_at || ended || started || now,
    started_at: started ?? null,
    ended_at: ended ?? null,
    duration_ms: duration_ms ?? null,
    artifact_path: input.artifact_path ?? null,
    input_ref: input.input_ref ?? null,
    output_ref: input.output_ref ?? null,
    error_code: input.error_code ?? null,
    error_message: input.error_message ?? null,
    tags: Array.isArray(input.tags) ? input.tags : [],
    metrics: input.metrics ?? null,
    data: input.data === undefined ? null : input.data,
  };
  return event;
}

function validateRunEvent(raw) {
  const errors = [];
  if (!raw || typeof raw !== 'object') {
    return { valid: false, errors: ['payload must be an object'] };
  }

  if (typeof raw.schema_version !== 'string' || !raw.schema_version.length) {
    errors.push(`schema_version 无效或缺失: ${raw.schema_version}`);
  }

  const runId = raw.run_id;
  if (typeof runId !== 'string' || !/^run_[A-Za-z0-9_-]+$/.test(runId)) {
    errors.push('run_id 格式无效或缺失');
  }

  const kind = raw.kind;
  if (!KIND_ENUM.has(kind)) {
    errors.push(`kind 无效: ${kind}`);
  }

  if (!ACTOR_ENUM.has(raw.actor_type)) {
    errors.push(`actor_type 无效: ${raw.actor_type}`);
  }

  if (!STATUS_ENUM.has(raw.status)) {
    errors.push(`status 无效: ${raw.status}`);
  }

  if (typeof raw.product_id !== 'string' || !raw.product_id.length) {
    errors.push('product_id 必填');
  }

  if (typeof raw.occurred_at !== 'string' || parseIso(raw.occurred_at) == null) {
    errors.push('occurred_at 必填且必须为合法时间戳');
  }

  const expectedKind = EVENT_TYPE_TO_KIND[raw.event_type];
  if (!expectedKind) {
    errors.push(`event_type 无效: ${raw.event_type}`);
  }
  if (expectedKind && kind && expectedKind !== kind) {
    errors.push(`kind is invalid for event_type: expected ${expectedKind}, got ${kind}`);
  }

  if (raw.event_type && raw.status && !isStatusAllowedForEventType(raw.event_type, raw.status)) {
    errors.push(
      `status is invalid for event_type: expected one of ${[...(EVENT_TYPE_TO_ALLOWED_STATUS[raw.event_type] || [])].join(', ')}, got ${raw.status}`
    );
  }

  if (
    raw.event_type === EVENT_TYPES.skillStarted ||
    raw.event_type === EVENT_TYPES.skillCompleted
  ) {
    if (typeof raw.skill_name !== 'string' || !raw.skill_name.length) {
      errors.push('skill_name is required');
    }
  }

  if (raw.data !== undefined && raw.data !== null) {
    const badData =
      typeof raw.data !== 'object' ||
      Array.isArray(raw.data) ||
      Object.prototype.toString.call(raw.data) !== '[object Object]';
    if (badData) {
      errors.push('data must be an object or null');
    }
  }

  if (kind === EVENT_KINDS.toolCall && raw.event_type) {
    if (!raw.data || typeof raw.data.toolName !== 'string' || !raw.data.toolName.length) {
      errors.push('data.toolName is required');
    }
  }

  if (kind === EVENT_KINDS.userGate && raw.event_type) {
    if (!raw.data || typeof raw.data.gateId !== 'string' || !raw.data.gateId.length) {
      errors.push('data.gateId is required');
    }
  }

  const sMs = parseIso(raw.started_at);
  const eMs = parseIso(raw.ended_at);
  if (sMs != null && eMs != null && eMs < sMs) {
    errors.push('ended_at must be greater than or equal to started_at');
  }

  const idFields = ['event_id', 'trace_id'];
  for (const field of idFields) {
    const v = raw[field];
    if (typeof v !== 'string' || !v.length) {
      errors.push(`${field} 格式无效`);
      continue;
    }
    if (v != null && typeof v === 'string') {
      const patterns = {
        event_id: /^evt_[A-Za-z0-9_-]+$/,
        trace_id: /^trace_[A-Za-z0-9_-]+$/,
      };
      if (patterns[field] && !patterns[field].test(v)) {
        errors.push(`${field} 格式无效`);
      }
    }
  }

  return { valid: errors.length === 0, errors };
}

function isStatusAllowedForEventType(eventType, status) {
  const allowed = EVENT_TYPE_TO_ALLOWED_STATUS[eventType];
  if (!allowed) {
    return false;
  }
  return allowed.has(status);
}

module.exports = {
  ACTOR_TYPES,
  EVENT_KINDS,
  EVENT_TYPES,
  VALID_STATUSES: STATUS_ENUM,
  RUN_EVENT_SCHEMA_VERSION,
  createRunEvent,
  deriveTraceId,
  isStatusAllowedForEventType,
  validateRunEvent,
};
