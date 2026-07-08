const test = require('node:test');
const assert = require('node:assert/strict');

const {
  buildResolvedWaitEventIds,
  findLatestUnresolvedWaitEvent,
  gateEventsMatch,
} = require('../lib/gate-resolution.cjs');

function buildGateEvent(overrides = {}) {
  return {
    event_id: overrides.event_id || 'evt_default',
    event_type: overrides.event_type || 'user_gate.waiting',
    skill_name: overrides.skill_name || 'mpt-ins-tc-gen',
    phase_name: overrides.phase_name || 'phase3',
    title: overrides.title || '人工确认',
    occurred_at: Object.prototype.hasOwnProperty.call(overrides, 'occurred_at') ? overrides.occurred_at : '2026-04-03T08:00:00.000Z',
    data: Object.prototype.hasOwnProperty.call(overrides, 'data') ? overrides.data : null,
    parent_event_id: overrides.parent_event_id || null,
  };
}

test('gateEventsMatch only falls back to title when both sides lack gateId', () => {
  const waitWithoutGateId = buildGateEvent({
    event_id: 'evt_wait',
    data: null,
  });
  const resolutionWithoutGateId = buildGateEvent({
    event_id: 'evt_resolve_a',
    event_type: 'user_gate.approved',
    data: null,
  });
  const resolutionWithGateId = buildGateEvent({
    event_id: 'evt_resolve_b',
    event_type: 'user_gate.approved',
    data: { gateId: 'gate-a' },
  });

  assert.equal(gateEventsMatch(waitWithoutGateId, resolutionWithoutGateId), true);
  assert.equal(gateEventsMatch(waitWithoutGateId, resolutionWithGateId), false);
});

test('findLatestUnresolvedWaitEvent uses occurred_at instead of append order', () => {
  const waitLater = buildGateEvent({
    event_id: 'evt_wait_later',
    data: { gateId: 'gate-a' },
    occurred_at: '2026-04-03T08:05:00.000Z',
  });
  const waitEarlier = buildGateEvent({
    event_id: 'evt_wait_earlier',
    data: { gateId: 'gate-a' },
    occurred_at: '2026-04-03T08:00:00.000Z',
  });
  const resolution = buildGateEvent({
    event_id: 'evt_approved',
    event_type: 'user_gate.approved',
    data: { gateId: 'gate-a' },
    occurred_at: '2026-04-03T08:06:00.000Z',
  });

  const matched = findLatestUnresolvedWaitEvent([waitLater, waitEarlier], resolution);
  assert.equal(matched.event_id, 'evt_wait_later');
});

test('findLatestUnresolvedWaitEvent prefers timed waits over untimed waits', () => {
  const waitUntimed = buildGateEvent({
    event_id: 'evt_wait_untimed',
    data: { gateId: 'gate-a' },
    occurred_at: null,
  });
  const waitTimed = buildGateEvent({
    event_id: 'evt_wait_timed',
    data: { gateId: 'gate-a' },
    occurred_at: '2026-04-03T08:05:00.000Z',
  });
  const resolution = buildGateEvent({
    event_id: 'evt_approved',
    event_type: 'user_gate.approved',
    data: { gateId: 'gate-a' },
    occurred_at: '2026-04-03T08:06:00.000Z',
  });

  const matched = findLatestUnresolvedWaitEvent([waitUntimed, waitTimed], resolution);
  assert.equal(matched.event_id, 'evt_wait_timed');
});

test('findLatestUnresolvedWaitEvent ignores waits that occur after the resolution event', () => {
  const waitBeforeResolution = buildGateEvent({
    event_id: 'evt_wait_before',
    data: { gateId: 'gate-a' },
    occurred_at: '2026-04-03T08:05:00.000Z',
  });
  const waitAfterResolution = buildGateEvent({
    event_id: 'evt_wait_after',
    data: { gateId: 'gate-a' },
    occurred_at: '2026-04-03T08:07:00.000Z',
  });
  const resolution = buildGateEvent({
    event_id: 'evt_approved',
    event_type: 'user_gate.approved',
    data: { gateId: 'gate-a' },
    occurred_at: '2026-04-03T08:06:00.000Z',
  });

  const matched = findLatestUnresolvedWaitEvent([waitBeforeResolution, waitAfterResolution], resolution);
  assert.equal(matched.event_id, 'evt_wait_before');
});

test('buildResolvedWaitEventIds resolves out-of-order same-title gates when both sides lack gateId', () => {
  const waitNew = buildGateEvent({
    event_id: 'evt_wait_new',
    occurred_at: '2026-04-03T08:20:00.000Z',
    data: null,
  });
  const resolution = buildGateEvent({
    event_id: 'evt_approved',
    event_type: 'user_gate.approved',
    occurred_at: '2026-04-03T08:21:00.000Z',
    data: null,
  });
  const waitOld = buildGateEvent({
    event_id: 'evt_wait_old',
    occurred_at: '2026-04-03T08:10:00.000Z',
    data: null,
  });

  const resolvedWaitEventIds = buildResolvedWaitEventIds([waitNew, resolution, waitOld]);
  assert.equal(resolvedWaitEventIds.has('evt_wait_new'), true);
  assert.equal(resolvedWaitEventIds.has('evt_wait_old'), false);
});

test('buildResolvedWaitEventIds respects explicit parent_event_id before latest-match fallback', () => {
  const waitOlder = buildGateEvent({
    event_id: 'evt_wait_older',
    occurred_at: '2026-04-03T08:00:00.000Z',
    data: { gateId: 'gate-a' },
  });
  const waitLatest = buildGateEvent({
    event_id: 'evt_wait_latest',
    occurred_at: '2026-04-03T08:05:00.000Z',
    data: { gateId: 'gate-a' },
  });
  const resolution = buildGateEvent({
    event_id: 'evt_approved',
    event_type: 'user_gate.approved',
    parent_event_id: 'evt_wait_older',
    occurred_at: '2026-04-03T08:06:00.000Z',
    data: { gateId: 'gate-a', approved: true },
  });

  const resolvedWaitEventIds = buildResolvedWaitEventIds([waitOlder, waitLatest, resolution]);
  assert.equal(resolvedWaitEventIds.has('evt_wait_older'), true);
  assert.equal(resolvedWaitEventIds.has('evt_wait_latest'), false);
});
