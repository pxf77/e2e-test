const test = require('node:test');
const assert = require('node:assert/strict');

const {
  ACTOR_TYPES,
  EVENT_KINDS,
  EVENT_TYPES,
  RUN_EVENT_SCHEMA_VERSION,
  createRunEvent,
  deriveTraceId,
  validateRunEvent,
} = require('../lib/run-event.cjs');

test('createRunEvent fills ids, timestamps and duration', () => {
  const event = createRunEvent({
    run_id: 'run-demo-product-001',
    kind: EVENT_KINDS.phase,
    event_type: EVENT_TYPES.phaseCompleted,
    actor_type: ACTOR_TYPES.skill,
    product_id: 'demo-product',
    skill_name: 'mpt-ins-tc-gen',
    phase_name: 'phase1',
    title: '骨架生成',
    status: 'completed',
    started_at: '2026-04-03T12:00:00.000Z',
    ended_at: '2026-04-03T12:05:00.000Z',
    tags: ['artifact'],
  });

  assert.equal(event.schema_version, RUN_EVENT_SCHEMA_VERSION);
  assert.match(event.event_id, /^evt_/);
  assert.equal(event.trace_id, deriveTraceId('run-demo-product-001', 'mpt-ins-tc-gen'));
  assert.equal(event.duration_ms, 300000);
  assert.equal(event.occurred_at, '2026-04-03T12:05:00.000Z');
});

test('validateRunEvent rejects invalid enum values and missing ids', () => {
  const result = validateRunEvent({
    kind: 'unknown',
    event_type: 'phase.completed',
    actor_type: ACTOR_TYPES.skill,
    status: 'completed',
    product_id: 'demo-product',
  });

  assert.equal(result.valid, false);
  assert.match(result.errors.join('\n'), /run_id/);
  assert.match(result.errors.join('\n'), /kind/);
});

test('validateRunEvent rejects semantic mismatches for kind, required fields and data shape', () => {
  const result = validateRunEvent({
    schema_version: RUN_EVENT_SCHEMA_VERSION,
    event_id: 'evt_semantic',
    run_id: 'run_demo_product_semantic',
    trace_id: 'trace_semantic',
    kind: EVENT_KINDS.run,
    event_type: EVENT_TYPES.skillStarted,
    actor_type: ACTOR_TYPES.agent,
    status: 'started',
    product_id: 'demo-product',
    occurred_at: '2026-04-03T12:00:00.000Z',
    data: new Date('2026-04-03T12:00:00.000Z'),
  });

  assert.equal(result.valid, false);
  assert.match(result.errors.join('\n'), /kind .* invalid/);
  assert.match(result.errors.join('\n'), /skill_name is required/);
  assert.match(result.errors.join('\n'), /data must be an object or null/);
});

test('validateRunEvent rejects impossible time ordering and missing stable runtime identifiers', () => {
  const result = validateRunEvent({
    schema_version: RUN_EVENT_SCHEMA_VERSION,
    event_id: 'evt_time_order',
    run_id: 'run_demo_product_time_order',
    trace_id: 'trace_time_order',
    kind: EVENT_KINDS.toolCall,
    event_type: EVENT_TYPES.toolCallCompleted,
    actor_type: ACTOR_TYPES.tool,
    status: 'completed',
    product_id: 'demo-product',
    skill_name: 'mpt-ins-prd-ana',
    phase_name: 'phase1',
    occurred_at: '2026-04-03T12:00:00.000Z',
    started_at: '2026-04-03T12:10:00.000Z',
    ended_at: '2026-04-03T12:00:00.000Z',
    data: {},
  });

  assert.equal(result.valid, false);
  assert.match(result.errors.join('\n'), /ended_at must be greater than or equal to started_at/);
  assert.match(result.errors.join('\n'), /data\.toolName is required/);
});

test('deriveTraceId is deterministic per run and skill', () => {
  const first = deriveTraceId('run-demo-product-001', 'mpt-ins-ts-gen');
  const second = deriveTraceId('run-demo-product-001', 'mpt-ins-ts-gen');
  const third = deriveTraceId('run-demo-product-001', 'mpt-ins-tc-gen');

  assert.equal(first, second);
  assert.notEqual(first, third);
  assert.match(first, /^trace_[0-9a-f]{32}$/);
});

test('validateRunEvent rejects event_type and status mismatches', () => {
  const result = validateRunEvent({
    schema_version: RUN_EVENT_SCHEMA_VERSION,
    event_id: 'evt_phase_started_completed',
    run_id: 'run_demo_product_phase_started_completed',
    trace_id: 'trace_phase_started_completed',
    kind: EVENT_KINDS.phase,
    event_type: EVENT_TYPES.phaseStarted,
    actor_type: ACTOR_TYPES.skill,
    status: 'completed',
    product_id: 'demo-product',
    skill_name: 'mpt-ins-prd-ana',
    phase_name: 'phase1',
    occurred_at: '2026-04-03T12:00:00.000Z',
    data: {},
  });

  assert.equal(result.valid, false);
  assert.match(result.errors.join('\n'), /status/);
});

test('createRunEvent supports guardrail event types', () => {
  const event = createRunEvent({
    run_id: 'run_demo_product_guardrail_001',
    kind: EVENT_KINDS.guardrail,
    event_type: EVENT_TYPES.guardrailWarn,
    actor_type: ACTOR_TYPES.guardrail,
    product_id: 'demo-product',
    status: 'warning',
    title: 'guardrail warning',
    occurred_at: '2026-04-03T12:00:00.000Z',
    data: {
      code: 'runtime_not_merged',
    },
  });

  assert.equal(event.event_type, EVENT_TYPES.guardrailWarn);
  assert.equal(validateRunEvent(event).valid, true);
});

test('validateRunEvent rejects payloads that miss schema required fields', () => {
  const result = validateRunEvent({
    run_id: 'run_demo_product_missing_required',
    kind: EVENT_KINDS.phase,
    event_type: EVENT_TYPES.phaseCompleted,
    actor_type: ACTOR_TYPES.skill,
    status: 'completed',
    product_id: 'demo-product',
    skill_name: 'mpt-ins-prd-ana',
    phase_name: 'phase1',
    data: {},
  });

  assert.equal(result.valid, false);
  assert.match(result.errors.join('\n'), /schema_version/);
  assert.match(result.errors.join('\n'), /event_id/);
  assert.match(result.errors.join('\n'), /trace_id/);
  assert.match(result.errors.join('\n'), /occurred_at/);
});
