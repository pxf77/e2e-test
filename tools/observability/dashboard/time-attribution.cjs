'use strict';

const PHASE_HOTSPOT_LIMIT = 3;
const RUNTIME_HOTSPOT_LIMIT = 2;
const GLOBAL_HOTSPOT_LIMIT = 5;
const SKILL_RUNTIME_HOTSPOT_LIMIT = 5;

const RUNTIME_ATTRIBUTION_KINDS = new Set([
  'tool_call',
  'test_case',
  'action',
  'user_gate',
]);

function hasDurationMs(value) {
  return Number.isFinite(value) && value > 0;
}

function normalizeMatchKey(value) {
  return String(value || '').trim().toLowerCase();
}

function sumDurationMs(items, key) {
  return (items || []).reduce((sum, item) => sum + (item?.[key] || 0), 0);
}

function buildPhaseBreakdown(skill) {
  const phases = Array.isArray(skill?.phases)
    ? skill.phases.filter((phase) => hasDurationMs(phase.durationMs))
    : [];
  const totalDurationMs = hasDurationMs(skill?.durationMs)
    ? skill.durationMs
    : sumDurationMs(phases, 'durationMs');

  return phases
    .map((phase) => ({
      phaseId: phase.phaseId,
      label: phase.title || phase.phaseId,
      title: phase.title || phase.phaseId,
      stageLabel: phase.stageLabel || null,
      status: phase.status || 'observed',
      durationMs: phase.durationMs,
      percent: totalDurationMs ? Math.round((phase.durationMs / totalDurationMs) * 100) : 0,
    }))
    .sort((left, right) => right.durationMs - left.durationMs);
}

function buildRuntimeHotspotFromEvent(skill, event) {
  const matchKeys = [];
  if (event.kind === 'test_case') {
    matchKeys.push(event.data?.tcId, event.title, event.data?.title, event.data?.fullTitle);
  }

  return {
    id: event.event_id || `${skill.skillName}:${event.kind}:${event.title || event.event_type}`,
    kind: event.kind,
    skillName: skill.skillName,
    skillDisplayName: skill.displayName || skill.skillName,
    label: event.kind === 'test_case'
      ? (event.data?.tcId || event.title || event.event_type)
      : (event.title || event.event_type || event.kind),
    subtitle: [
      event.phase_name || null,
      event.event_type || null,
      event.status || null,
    ].filter(Boolean).join(' · '),
    durationMs: event.duration_ms,
    status: event.status || 'observed',
    source: 'runtime',
    matchKeys: matchKeys.map((value) => normalizeMatchKey(value)).filter(Boolean),
  };
}

function buildRuntimeHotspotFromExecution(skill, testCase, status) {
  const matchKeys = [testCase.tcId, testCase.title, testCase.fullTitle]
    .map((value) => normalizeMatchKey(value))
    .filter(Boolean);

  return {
    id: `${skill.skillName}:execution:${testCase.tcId}:${status}`,
    kind: 'test_case',
    skillName: skill.skillName,
    skillDisplayName: skill.displayName || skill.skillName,
    label: testCase.tcId,
    subtitle: [
      'phase1',
      status,
      testCase.title || testCase.reason || 'tc-exec report',
    ].filter(Boolean).join(' · '),
    durationMs: testCase.durationMs,
    status,
    source: 'execution',
    matchKeys,
  };
}

function uniqueHotspots(items) {
  const seen = new Set();
  return (items || []).filter((item) => {
    const key = `${item.kind}:${item.skillName}:${item.label}:${item.subtitle}:${item.durationMs}`;
    if (seen.has(key)) {
      return false;
    }
    seen.add(key);
    return true;
  });
}

function buildSkillRuntimeHotspots(snapshot, skill) {
  const runtimeEvents = (snapshot?.timeline || [])
    .filter((event) =>
      event &&
      event.skill_name === skill.skillName &&
      RUNTIME_ATTRIBUTION_KINDS.has(event.kind) &&
      event.actor_type !== 'parser' &&
      hasDurationMs(event.duration_ms)
    )
    .map((event) => buildRuntimeHotspotFromEvent(skill, event));

  const runtimeTestCaseLabels = new Set(
    runtimeEvents
      .filter((item) => item.kind === 'test_case')
      .flatMap((item) => item.matchKeys || [])
  );

  const executionHotspots = skill.skillName !== 'mpt-ins-tc-exec'
    ? []
    : [
        ...((snapshot?.execution?.passedTests || [])
          .filter((testCase) =>
            hasDurationMs(testCase.durationMs) &&
            ![testCase.tcId, testCase.title, testCase.fullTitle]
              .map((value) => normalizeMatchKey(value))
              .filter(Boolean)
              .some((value) => runtimeTestCaseLabels.has(value))
          )
          .map((testCase) => buildRuntimeHotspotFromExecution(skill, testCase, 'completed'))),
        ...((snapshot?.execution?.failedTests || [])
          .filter((testCase) =>
            hasDurationMs(testCase.durationMs) &&
            ![testCase.tcId, testCase.title, testCase.fullTitle]
              .map((value) => normalizeMatchKey(value))
              .filter(Boolean)
              .some((value) => runtimeTestCaseLabels.has(value))
          )
          .map((testCase) => buildRuntimeHotspotFromExecution(skill, testCase, 'failed'))),
        ...((snapshot?.execution?.fixmeTests || [])
          .filter((testCase) =>
            hasDurationMs(testCase.durationMs) &&
            ![testCase.tcId, testCase.title, testCase.fullTitle]
              .map((value) => normalizeMatchKey(value))
              .filter(Boolean)
              .some((value) => runtimeTestCaseLabels.has(value))
          )
          .map((testCase) => buildRuntimeHotspotFromExecution(skill, testCase, 'warning'))),
      ];

  const hotspots = uniqueHotspots([...runtimeEvents, ...executionHotspots])
    .sort((left, right) => right.durationMs - left.durationMs)
    .slice(0, SKILL_RUNTIME_HOTSPOT_LIMIT);

  let emptyState = '当前 skill 尚未采集到带耗时的 runtime 子事件。';
  if (snapshot?.run?.source === 'artifact' || snapshot?.runtime?.merged === false) {
    emptyState = '当前 skill 尚未采集到带耗时的 runtime 子事件，暂仅展示 artifact 侧 phase 耗时。';
  }

  return {
    hotspots,
    emptyState,
    runtimeHotspotCount: runtimeEvents.length,
    fallbackHotspotCount: executionHotspots.length,
  };
}

function buildGlobalHotspots(skillCards) {
  const phaseHotspots = skillCards
    .flatMap((skill) => skill.phaseBreakdown.map((phase) => ({
      id: `${skill.skillName}:${phase.phaseId}`,
      kind: 'phase',
      label: `${skill.displayName} / ${phase.title}`,
      subtitle: `${phase.phaseId} · ${phase.status} · 占 ${skill.displayName} ${phase.percent}%`,
      durationMs: phase.durationMs,
      status: phase.status,
      skillName: skill.skillName,
      skillDisplayName: skill.displayName,
    })))
    .sort((left, right) => right.durationMs - left.durationMs);

  const runtimeHotspots = skillCards
    .flatMap((skill) => skill.runtimeHotspots)
    .sort((left, right) => right.durationMs - left.durationMs);

  const picked = [];
  const pickedIds = new Set();

  // limit 控制每次调用最多从当前来源取几条（PHASE_HOTSPOT_LIMIT/RUNTIME_HOTSPOT_LIMIT）
  // GLOBAL_HOTSPOT_LIMIT 是全局上限，两个条件共同出口
  function pickFrom(items, limit) {
    for (const item of items) {
      if (picked.length >= GLOBAL_HOTSPOT_LIMIT || limit <= 0) {
        break;
      }
      if (pickedIds.has(item.id)) {
        continue;
      }
      picked.push(item);
      pickedIds.add(item.id);
      limit--;
    }
  }

  pickFrom(phaseHotspots, PHASE_HOTSPOT_LIMIT);
  pickFrom(runtimeHotspots, RUNTIME_HOTSPOT_LIMIT);
  pickFrom(phaseHotspots, GLOBAL_HOTSPOT_LIMIT - picked.length);
  pickFrom(runtimeHotspots, GLOBAL_HOTSPOT_LIMIT - picked.length);

  return picked
    .sort((left, right) => right.durationMs - left.durationMs)
    .map((item, index) => ({
      ...item,
      rank: index + 1,
    }));
}

function buildCoverageNote(snapshot, summary) {
  if (summary.totalDisplayedHotspots === 0) {
    return '当前快照暂无可展示的耗时热点。';
  }
  if (summary.runtimeHotspotCount === 0 && summary.fallbackHotspotCount === 0) {
    return '当前快照尚未采集到可归因的 runtime 子事件，热点榜主要基于 phase 耗时。';
  }
  if (summary.runtimeHotspotCount === 0 && summary.fallbackHotspotCount > 0) {
    return '当前快照尚未采集到可归因的 runtime 子事件，热点榜已回退到 phase 与 execution fallback 耗时。';
  }
  if (summary.runtimeHotspotCount > 0 && summary.fallbackHotspotCount > 0) {
    return '热点榜当前混合展示 phase、runtime 与 execution fallback 热点；当 runtime 热点不足时，会自动回填 phase 热点。';
  }
  if (snapshot?.runtime?.merged === false) {
    return '当前快照检测到 runtime telemetry 但尚未完全合并，热点榜已混合展示 phase 与已采集的 runtime 子事件。';
  }
  return '热点榜采用 phase Top3 + runtime Top2 的混合策略；当 runtime 热点不足时，会自动回填 phase 热点。';
}

function buildSkillPanelNote(snapshot, summary) {
  const suffix = '由于 schema 尚未提供页面停留事件，这里暂不展示页面节点耗时。';
  if (summary.totalDisplayedSkillHotspots === 0 && summary.totalSkillCards === 0) {
    return `当前真实看板暂无可展示的 skill 归因数据；${suffix}`;
  }
  if (summary.runtimeHotspotCount === 0 && summary.fallbackHotspotCount === 0) {
    return `当前真实看板暂以 phase 与 artifact 回放结果做归因；${suffix}`;
  }
  if (summary.runtimeHotspotCount === 0 && summary.fallbackHotspotCount > 0) {
    return `当前真实看板暂以 phase 与 execution fallback 做归因；${suffix}`;
  }
  if (summary.runtimeHotspotCount > 0 && summary.fallbackHotspotCount > 0) {
    return `当前真实看板基于 phase、runtime 与 execution fallback 的混合覆盖做归因；${suffix}`;
  }
  if (snapshot?.runtime?.merged === false) {
    return `当前真实看板基于 phase 与部分 runtime 子事件做归因；${suffix}`;
  }
  return `当前真实看板基于 phase 与 runtime 子事件做归因；${suffix}`;
}

function buildTimeAttributionModel(snapshot) {
  const skills = Array.isArray(snapshot?.skills) ? snapshot.skills : [];
  const skillCards = skills.map((skill) => {
    const phaseBreakdown = buildPhaseBreakdown(skill);
    const totalDurationMs = hasDurationMs(skill.durationMs)
      ? skill.durationMs
      : sumDurationMs(phaseBreakdown, 'durationMs');
    const runtime = buildSkillRuntimeHotspots(snapshot, skill);
    const runtimeEmptyState = runtime.hotspots.length === 0 && phaseBreakdown.length === 0
      ? '当前 skill 暂无可展示的 phase 或 runtime 归因数据。'
      : runtime.emptyState;

    return {
      skillName: skill.skillName,
      displayName: skill.displayName || skill.skillName,
      status: skill.status || 'observed',
      totalDurationMs: totalDurationMs || null,
      phaseBreakdown,
      runtimeHotspots: runtime.hotspots,
      runtimeEmptyState,
      runtimeHotspotCount: runtime.runtimeHotspotCount,
      fallbackHotspotCount: runtime.fallbackHotspotCount,
    };
  });
  const hotspots = buildGlobalHotspots(skillCards);
  const summary = {
    runtimeHotspotCount: hotspots.filter((item) => item.source === 'runtime').length,
    fallbackHotspotCount: hotspots.filter((item) => item.source === 'execution').length,
    totalDisplayedHotspots: hotspots.length,
  };
  const skillSummary = {
    runtimeHotspotCount: skillCards.reduce(
      (sum, skill) => sum + skill.runtimeHotspots.filter((item) => item.source === 'runtime').length,
      0
    ),
    fallbackHotspotCount: skillCards.reduce(
      (sum, skill) => sum + skill.runtimeHotspots.filter((item) => item.source === 'execution').length,
      0
    ),
    totalDisplayedSkillHotspots: skillCards.reduce((sum, skill) => sum + skill.runtimeHotspots.length, 0),
    totalSkillCards: skillCards.length,
  };

  return {
    hotspots,
    skills: skillCards,
    coverageNote: buildCoverageNote(snapshot, summary),
    skillPanelNote: buildSkillPanelNote(snapshot, skillSummary),
  };
}

function prepareDashboardSnapshot(snapshot) {
  return {
    ...snapshot,
    dashboard: {
      ...(snapshot?.dashboard || {}),
      timeAttribution: buildTimeAttributionModel(snapshot),
    },
  };
}

module.exports = {
  buildTimeAttributionModel,
  prepareDashboardSnapshot,
};
