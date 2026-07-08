'use strict';

function normalizeText(value) {
  return String(value || '').trim();
}

function parseTime(value) {
  if (!value) {
    return null;
  }
  const ts = Date.parse(value);
  return Number.isFinite(ts) ? ts : null;
}

function getGateId(event) {
  const gateId = event?.data?.gateId;
  return gateId == null ? null : normalizeText(gateId);
}

function gateEventsMatch(waitEvent, resolutionEvent) {
  if (!waitEvent || !resolutionEvent) {
    return false;
  }
  if (normalizeText(waitEvent.skill_name) !== normalizeText(resolutionEvent.skill_name)) {
    return false;
  }
  if (normalizeText(waitEvent.phase_name) !== normalizeText(resolutionEvent.phase_name)) {
    return false;
  }

  const waitGateId = getGateId(waitEvent);
  const resolutionGateId = getGateId(resolutionEvent);
  if (waitGateId || resolutionGateId) {
    return Boolean(waitGateId) && waitGateId === resolutionGateId;
  }

  return normalizeText(waitEvent.title) === normalizeText(resolutionEvent.title);
}

function sortWaitsForResolution(waitEvents, resolutionEvent) {
  const resolutionTime = parseTime(resolutionEvent?.occurred_at);

  return [...(waitEvents || [])]
    .filter((event) => event?.event_type === 'user_gate.waiting')
    .filter((event) => gateEventsMatch(event, resolutionEvent))
    .filter((event) => {
      const waitTime = parseTime(event?.occurred_at);
      if (resolutionTime == null || waitTime == null) {
        return true;
      }
      return waitTime <= resolutionTime;
    })
    .sort((left, right) => {
      const leftTime = parseTime(left?.occurred_at);
      const rightTime = parseTime(right?.occurred_at);
      if (leftTime == null && rightTime == null) {
        return 0;
      }
      if (leftTime == null) {
        return 1;
      }
      if (rightTime == null) {
        return -1;
      }
      return rightTime - leftTime;
    });
}

function findLatestUnresolvedWaitEvent(waitEvents, resolutionEvent, resolvedWaitEventIds = new Set()) {
  return sortWaitsForResolution(waitEvents, resolutionEvent)
    .find((event) => !resolvedWaitEventIds.has(event.event_id)) || null;
}

function buildResolvedWaitEventIds(events) {
  const resolvedWaitEventIds = new Set();
  const waitingEventsById = new Map(
    (events || [])
      .filter((event) => event?.event_type === 'user_gate.waiting' && event?.event_id)
      .map((event) => [event.event_id, event])
  );
  const resolutions = (events || []).filter((event) =>
    event?.event_type === 'user_gate.approved' || event?.event_type === 'user_gate.rejected'
  );

  for (const resolution of resolutions) {
    let matchedWait = null;
    if (resolution?.parent_event_id) {
      const explicitParentWait = waitingEventsById.get(resolution.parent_event_id) || null;
      if (
        explicitParentWait &&
        !resolvedWaitEventIds.has(explicitParentWait.event_id) &&
        gateEventsMatch(explicitParentWait, resolution)
      ) {
        matchedWait = explicitParentWait;
      }
    }
    if (!matchedWait) {
      matchedWait = findLatestUnresolvedWaitEvent(events, resolution, resolvedWaitEventIds);
    }
    if (matchedWait?.event_id) {
      resolvedWaitEventIds.add(matchedWait.event_id);
    }
  }

  return resolvedWaitEventIds;
}

module.exports = {
  buildResolvedWaitEventIds,
  findLatestUnresolvedWaitEvent,
  gateEventsMatch,
};
