'use strict';

const { ACTOR_TYPES, EVENT_KINDS, EVENT_TYPES, createRunEvent } = require('../lib/run-event.cjs');
const { buildResolvedWaitEventIds } = require('../lib/gate-resolution.cjs');

const DEFAULT_PHASE_WARN_MS = 30 * 60 * 1000;

function buildIssue(code, severity, message, data) {
  return {
    code,
    severity,
    message,
    data: data || null,
  };
}

function evaluateRunGuardrails(snapshot) {
  const issues = [];
  const events = [];
  const timelineOccurredAt = (snapshot.timeline || [])
    .map((event) => event.occurred_at || event.ended_at || event.started_at)
    .filter(Boolean)
    .sort()
    .at(-1);
  const occurredAt = timelineOccurredAt || snapshot.run?.endedAt || snapshot.run?.startedAt || new Date().toISOString();

  if ((snapshot.execution?.summary?.failed || 0) > 0) {
    issues.push(buildIssue(
      'exec_failed_cases',
      'blocked',
      `存在 ${snapshot.execution.summary.failed} 条失败用例`,
      { failed: snapshot.execution.summary.failed }
    ));
  }

  if ((snapshot.execution?.summary?.fixme || 0) > 0) {
    issues.push(buildIssue(
      'fixme_remaining',
      'warning',
      `存在 ${snapshot.execution.summary.fixme} 条 fixme 用例待人工处理`,
      { fixmeTests: snapshot.execution.fixmeTests || [] }
    ));
  }

  for (const skill of snapshot.skills || []) {
    for (const phase of skill.phases || []) {
      if (phase.durationMs && phase.durationMs > DEFAULT_PHASE_WARN_MS) {
        issues.push(buildIssue(
          'phase_duration_exceeded',
          'warning',
          `${skill.skillName}/${phase.phaseId} 耗时超过 ${DEFAULT_PHASE_WARN_MS}ms`,
          {
            skillName: skill.skillName,
            phaseId: phase.phaseId,
            durationMs: phase.durationMs,
          }
        ));
      }
    }
  }

  const gateWaitEvents = (snapshot.timeline || []).filter((event) => event.event_type === 'user_gate.waiting');
  const resolvedWaitEventIds = buildResolvedWaitEventIds(snapshot.timeline || []);
  for (const gateEvent of gateWaitEvents) {
    const resolved = resolvedWaitEventIds.has(gateEvent.event_id);

    if (!resolved) {
      issues.push(buildIssue(
        'gate_unresolved',
        'blocked',
        `${gateEvent.title || gateEvent.data?.gateId || '未知门禁'} 仍处于等待状态`,
        {
          gateEventId: gateEvent.event_id,
          gateId: gateEvent.data?.gateId || null,
          skillName: gateEvent.skill_name,
          phaseName: gateEvent.phase_name,
          gateTitle: gateEvent.title,
        }
      ));
    }
  }

  if (snapshot.runtime && snapshot.runtime.eventCount > 0 && snapshot.runtime.merged === false) {
    issues.push(buildIssue(
      'runtime_not_merged',
      'warning',
      '检测到 runtime telemetry，但未与当前 artifact run 关联',
      {
        runtimeRunId: snapshot.runtime.runId,
      }
    ));
  }

  for (const issue of issues) {
    events.push(createRunEvent({
      run_id: snapshot.run.runId,
      kind: EVENT_KINDS.guardrail,
      event_type: issue.severity === 'blocked' ? EVENT_TYPES.guardrailBlocked : EVENT_TYPES.guardrailWarn,
      actor_type: ACTOR_TYPES.guardrail,
      product_id: snapshot.run.productId,
      skill_name: issue.data?.skillName || null,
      phase_name: issue.data?.phaseId || issue.data?.phaseName || null,
      status: issue.severity === 'blocked' ? 'blocked' : 'warning',
      title: issue.message,
      occurred_at: occurredAt,
      data: issue,
      tags: ['guardrail'],
    }));
  }

  return {
    issues,
    events,
  };
}

module.exports = {
  evaluateRunGuardrails,
};
