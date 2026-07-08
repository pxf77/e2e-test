'use strict';

const crypto = require('node:crypto');

function toUnixNanoString(value) {
  const millis = new Date(value).getTime();
  if (!Number.isFinite(millis)) {
    return null;
  }
  return `${BigInt(millis) * 1000000n}`;
}

function stripPrefix(value, prefix) {
  return String(value || '').startsWith(prefix) ? String(value).slice(prefix.length) : String(value || '');
}

function normalizeHexId(value, length, fallbackSeed) {
  const raw = String(value || '').toLowerCase().replace(/[^0-9a-f]/g, '');
  if (raw.length === length) {
    return raw;
  }
  if (raw.length > length) {
    return raw.slice(0, length);
  }
  return crypto.createHash('sha256')
    .update(raw || String(fallbackSeed || 'observability'))
    .digest('hex')
    .slice(0, length);
}

function attribute(key, value) {
  if (typeof value === 'number') {
    return {
      key,
      value: {
        doubleValue: value,
      },
    };
  }

  if (typeof value === 'boolean') {
    return {
      key,
      value: {
        boolValue: value,
      },
    };
  }

  return {
    key,
    value: {
      stringValue: String(value ?? ''),
    },
  };
}

function getEventTimeRange(event) {
  const startTimeUnixNano = toUnixNanoString(event.started_at || event.occurred_at || event.ended_at);
  const endTimeUnixNano = toUnixNanoString(event.ended_at || event.occurred_at || event.started_at);

  if (!startTimeUnixNano || !endTimeUnixNano) {
    return null;
  }

  return {
    startTimeUnixNano,
    endTimeUnixNano,
  };
}

function eventToSpan(event, snapshot) {
  const timeRange = getEventTimeRange(event);
  if (!timeRange) {
    return null;
  }

  return {
    traceId: normalizeHexId(stripPrefix(event.trace_id, 'trace_'), 32, `${event.run_id}:${event.skill_name}:${event.kind}`),
    spanId: normalizeHexId(stripPrefix(event.event_id, 'evt_'), 16, event.event_id),
    parentSpanId: event.parent_event_id
      ? normalizeHexId(stripPrefix(event.parent_event_id, 'evt_'), 16, event.parent_event_id)
      : undefined,
    name: event.title || event.event_type,
    kind: 1,
    startTimeUnixNano: timeRange.startTimeUnixNano,
    endTimeUnixNano: timeRange.endTimeUnixNano,
    attributes: [
      attribute('run.id', event.run_id || snapshot.run.runId),
      attribute('product.id', event.product_id || snapshot.run.productId),
      attribute('event.kind', event.kind),
      attribute('event.type', event.event_type),
      attribute('event.status', event.status),
      attribute('skill.name', event.skill_name || ''),
      attribute('phase.name', event.phase_name || ''),
    ],
  };
}

function exportSnapshotToOtel(snapshot) {
  return {
    resourceSpans: [
      {
        resource: {
          attributes: [
            attribute('service.name', 'skills-observability'),
            attribute('product.id', snapshot.run.productId),
            attribute('run.source', snapshot.run.source || 'artifact'),
          ],
        },
        scopeSpans: [
          {
            scope: {
              name: 'skills-observability',
            },
            spans: (snapshot.timeline || [])
              .map((event) => eventToSpan(event, snapshot))
              .filter(Boolean),
          },
        ],
      },
    ],
  };
}

module.exports = {
  exportSnapshotToOtel,
};
