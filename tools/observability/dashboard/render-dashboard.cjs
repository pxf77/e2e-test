'use strict';

const path = require('node:path');
const { pathToFileURL } = require('node:url');
const { prepareDashboardSnapshot } = require('./time-attribution.cjs');

function escapeHtml(value) {
  return String(value ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function formatDuration(durationMs) {
  if (durationMs === null || durationMs === undefined) {
    return 'N/A';
  }

  if (durationMs < 1000) {
    return `${durationMs}ms`;
  }

  if (durationMs < 60_000) {
    return `${(durationMs / 1000).toFixed(1)}s`;
  }

  const minutes = Math.floor(durationMs / 60_000);
  const seconds = Math.round((durationMs % 60_000) / 1000);
  return `${minutes}m ${seconds}s`;
}

function formatDateTime(value) {
  if (!value) {
    return 'N/A';
  }

  return new Date(value).toLocaleString('zh-CN', {
    hour12: false,
  });
}

function toFileHref(absolutePath) {
  if (!absolutePath) {
    return '#';
  }
  return pathToFileURL(absolutePath).href;
}

function buildPhaseRows(snapshot) {
  const phases = [];
  for (const skill of snapshot.skills || []) {
    for (const phase of skill.phases || []) {
      phases.push({
        skillName: skill.skillName,
        skillDisplayName: skill.displayName,
        ...phase,
      });
    }
  }
  return phases;
}

function renderPhaseWaterfall(phases) {
  const maxDuration = phases.reduce((value, phase) => Math.max(value, phase.durationMs || 0), 0) || 1;

  return phases.map((phase) => {
    const width = phase.durationMs ? Math.max(Math.round((phase.durationMs / maxDuration) * 100), 6) : 0;
    const muted = phase.status === 'unobserved';

    return `
      <div class="phase-row">
        <div class="phase-meta">
          <div class="phase-title">${escapeHtml(phase.skillDisplayName)} / ${escapeHtml(phase.title)}</div>
          <div class="phase-subtitle">${escapeHtml(phase.phaseId)} · ${escapeHtml(phase.status)}</div>
        </div>
        <div class="phase-bar-wrap">
          ${muted ? '<div class="phase-placeholder">未观测到该阶段的结构化日志</div>' : `<div class="phase-bar" style="width:${width}%"></div>`}
        </div>
        <div class="phase-duration">${escapeHtml(formatDuration(phase.durationMs))}</div>
      </div>
    `;
  }).join('');
}

function renderArtifacts(artifacts) {
  return artifacts.map((artifact) => `
    <li>
      <a href="${escapeHtml(toFileHref(artifact.absolutePath))}">${escapeHtml(path.basename(artifact.path))}</a>
      <span class="artifact-path">${escapeHtml(artifact.path)}</span>
    </li>
  `).join('');
}

function renderPassedTests(execution) {
  return (execution.passedTests || []).map((testCase) => `
    <tr>
      <td>${escapeHtml(testCase.tcId)}</td>
      <td>${escapeHtml(testCase.title)}</td>
      <td>${escapeHtml(formatDuration(testCase.durationMs))}</td>
    </tr>
  `).join('');
}

function renderFixmeTests(execution) {
  return (execution.fixmeTests || []).map((testCase) => `
    <tr>
      <td>${escapeHtml(testCase.tcId)}</td>
      <td>
        ${escapeHtml(testCase.reason)}
        ${testCase.reportHtmlAbsolutePath ? `<div><a href="${escapeHtml(toFileHref(testCase.reportHtmlAbsolutePath))}">查看报告</a></div>` : ''}
      </td>
    </tr>
  `).join('');
}

function renderFailedTests(execution) {
  return (execution.failedTests || []).map((testCase) => `
    <tr>
      <td>${escapeHtml(testCase.tcId)}</td>
      <td>
        ${escapeHtml(testCase.reason)}
        ${testCase.reportHtmlAbsolutePath ? `<div><a href="${escapeHtml(toFileHref(testCase.reportHtmlAbsolutePath))}">查看报告</a></div>` : ''}
      </td>
    </tr>
  `).join('');
}

function renderTimeline(events) {
  return events.map((event) => `
    <tr>
      <td>${escapeHtml(formatDateTime(event.occurred_at || event.ended_at || event.started_at))}</td>
      <td>${escapeHtml(event.kind)}</td>
      <td>${escapeHtml(event.title || event.event_type)}</td>
      <td>${escapeHtml(event.status)}</td>
    </tr>
  `).join('');
}

function renderToolCallSection(snapshot) {
  const toolCalls = (snapshot.timeline || []).filter((event) => event.kind === 'tool_call');
  if (toolCalls.length === 0) {
    return '<div class="empty-state">未采集到 runtime tool call，当前视图仅展示 artifact 重建结果。接入 runtime hook 后这里会展示工具调用时间轴。</div>';
  }

  return `
    <table>
      <thead>
        <tr><th>时间</th><th>标题</th><th>状态</th><th>耗时</th></tr>
      </thead>
      <tbody>
        ${toolCalls.map((event) => `
          <tr>
            <td>${escapeHtml(formatDateTime(event.occurred_at || event.ended_at || event.started_at))}</td>
            <td>${escapeHtml(event.title || event.event_type)}</td>
            <td>${escapeHtml(event.status)}</td>
            <td>${escapeHtml(formatDuration(event.duration_ms))}</td>
          </tr>
        `).join('')}
      </tbody>
    </table>
  `;
}

function renderGuardrails(issues) {
  if (!issues || issues.length === 0) {
    return '<div class="empty-state">当前没有命中的 guardrail 风险。</div>';
  }

  return `
    <table>
      <thead>
        <tr><th>级别</th><th>规则</th><th>说明</th></tr>
      </thead>
      <tbody>
        ${issues.map((issue) => `
          <tr>
            <td>${escapeHtml(issue.severity)}</td>
            <td>${escapeHtml(issue.code)}</td>
            <td>${escapeHtml(issue.message)}</td>
          </tr>
        `).join('')}
      </tbody>
    </table>
  `;
}

function formatHotspotKind(kind) {
  switch (kind) {
    case 'phase':
      return 'Phase';
    case 'tool_call':
      return 'Tool';
    case 'test_case':
      return 'TestCase';
    case 'action':
      return 'Action';
    case 'user_gate':
      return 'Gate';
    default:
      return kind || 'Event';
  }
}

function renderHotspots(hotspots) {
  if (!hotspots || hotspots.length === 0) {
    return '<div class="empty-state">当前没有可展示的耗时热点。</div>';
  }

  return `
    <div class="hotspot-grid">
      ${hotspots.map((item) => `
        <div class="hotspot-card">
          <div class="hotspot-rank">${escapeHtml(String(item.rank))}</div>
          <div class="hotspot-main">
            <div class="hotspot-head">
              <span class="kind-badge">${escapeHtml(formatHotspotKind(item.kind))}</span>
              <span class="phase-subtitle">${escapeHtml(item.skillDisplayName || item.skillName || '')}</span>
            </div>
            <div class="hotspot-title">${escapeHtml(item.label)}</div>
            <div class="phase-subtitle">${escapeHtml(item.subtitle || '')}</div>
            <div class="hotspot-duration">${escapeHtml(formatDuration(item.durationMs))}</div>
          </div>
        </div>
      `).join('')}
    </div>
  `;
}

function renderSkillPhaseBreakdown(phases) {
  if (!phases || phases.length === 0) {
    return '<div class="empty-state">当前 skill 没有可计算的 phase 耗时。</div>';
  }

  return phases.map((phase) => {
    const width = Math.max(phase.percent || 0, 6);
    return `
      <div class="breakdown-row">
        <div class="breakdown-meta">
          <div class="phase-title">${escapeHtml(phase.title)}</div>
          <div class="phase-subtitle">${escapeHtml(phase.phaseId)} · ${escapeHtml(phase.status)} · ${escapeHtml(String(phase.percent))}%</div>
        </div>
        <div class="breakdown-bar-wrap">
          <div class="breakdown-bar" style="width:${width}%"></div>
        </div>
        <div class="phase-duration">${escapeHtml(formatDuration(phase.durationMs))}</div>
      </div>
    `;
  }).join('');
}

function renderSkillRuntimeHotspots(hotspots, emptyState) {
  if (!hotspots || hotspots.length === 0) {
    return `<div class="empty-state">${escapeHtml(emptyState)}</div>`;
  }

  return hotspots.map((item) => `
    <div class="runtime-row">
      <div class="runtime-main">
        <div class="runtime-head">
          <span class="kind-badge">${escapeHtml(formatHotspotKind(item.kind))}</span>
          <span class="phase-subtitle">${escapeHtml(item.source === 'execution' ? 'execution fallback' : 'timeline')}</span>
        </div>
        <div class="runtime-title">${escapeHtml(item.label)}</div>
        <div class="phase-subtitle">${escapeHtml(item.subtitle || '')}</div>
      </div>
      <div class="phase-duration">${escapeHtml(formatDuration(item.durationMs))}</div>
    </div>
  `).join('');
}

function renderSkillTimeAttribution(skillCards) {
  if (!skillCards || skillCards.length === 0) {
    return '<div class="empty-state">当前没有可展示的 skill 耗时归因。</div>';
  }

  return `
    <div class="skill-grid">
      ${skillCards.map((skill) => `
        <div class="skill-card">
          <div class="skill-card-head">
            <div>
              <h3>${escapeHtml(skill.displayName)}</h3>
              <div class="phase-subtitle">${escapeHtml(skill.skillName)} · ${escapeHtml(skill.status)}</div>
            </div>
            <div class="skill-total">${escapeHtml(formatDuration(skill.totalDurationMs))}</div>
          </div>

          <div class="section-label">阶段耗时拆解</div>
          ${renderSkillPhaseBreakdown(skill.phaseBreakdown)}

          <div class="section-label">阶段内热点</div>
          ${renderSkillRuntimeHotspots(skill.runtimeHotspots, skill.runtimeEmptyState)}
        </div>
      `).join('')}
    </div>
  `;
}

function normalizeExecution(execution) {
  return {
    summary: {
      total: execution?.summary?.total || 0,
      passed: execution?.summary?.passed || 0,
      failed: execution?.summary?.failed || 0,
      fixme: execution?.summary?.fixme || 0,
      skipped: execution?.summary?.skipped || 0,
      passRate: execution?.summary?.passRate || '0%',
    },
    passedTests: Array.isArray(execution?.passedTests) ? execution.passedTests : [],
    failedTests: Array.isArray(execution?.failedTests) ? execution.failedTests : [],
    fixmeTests: Array.isArray(execution?.fixmeTests) ? execution.fixmeTests : [],
    reportMarkdownPath: execution?.reportMarkdownPath || null,
    reportHtmlPath: execution?.reportHtmlPath || null,
    executedAt: execution?.executedAt || null,
  };
}

function renderDashboardHtml(snapshot) {
  const preparedSnapshot = snapshot?.dashboard?.timeAttribution
    ? snapshot
    : prepareDashboardSnapshot(snapshot);
  const phases = buildPhaseRows(preparedSnapshot);
  const execution = normalizeExecution(preparedSnapshot.execution);
  const timeAttribution = preparedSnapshot.dashboard.timeAttribution;

  return `<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Skills 全链路观测看板</title>
  <style>
    body {
      font-family: Arial, sans-serif;
      background: #0f172a;
      color: #e2e8f0;
      margin: 0;
      padding: 24px;
      line-height: 1.5;
    }
    h1, h2, h3 {
      margin: 0 0 12px;
    }
    .subtitle {
      color: #94a3b8;
      margin-bottom: 24px;
    }
    .grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
      gap: 12px;
      margin-bottom: 24px;
    }
    .card, .panel {
      background: #111827;
      border: 1px solid #1f2937;
      border-radius: 12px;
      padding: 16px;
    }
    .metric {
      font-size: 28px;
      font-weight: 700;
      margin-top: 8px;
    }
    .metric-label, .muted {
      color: #94a3b8;
    }
    .panel {
      margin-bottom: 18px;
    }
    .phase-row {
      display: grid;
      grid-template-columns: minmax(260px, 2fr) minmax(240px, 5fr) 90px;
      gap: 12px;
      align-items: center;
      margin: 10px 0;
    }
    .phase-title {
      font-weight: 600;
    }
    .phase-subtitle, .artifact-path {
      font-size: 12px;
      color: #94a3b8;
    }
    .phase-bar-wrap {
      background: #0b1220;
      border-radius: 999px;
      height: 14px;
      overflow: hidden;
      position: relative;
    }
    .phase-bar {
      background: linear-gradient(90deg, #38bdf8, #22c55e);
      height: 100%;
      border-radius: 999px;
    }
    .phase-placeholder {
      color: #94a3b8;
      font-size: 12px;
      padding: 0 8px;
      line-height: 14px;
    }
    table {
      width: 100%;
      border-collapse: collapse;
      margin-top: 12px;
    }
    th, td {
      text-align: left;
      padding: 10px 8px;
      border-bottom: 1px solid #1f2937;
      vertical-align: top;
    }
    th {
      color: #93c5fd;
      font-weight: 600;
    }
    a {
      color: #38bdf8;
      text-decoration: none;
    }
    ul {
      padding-left: 18px;
      margin: 0;
    }
    .empty-state {
      color: #94a3b8;
      padding: 12px 0 4px;
    }
    .two-column {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(340px, 1fr));
      gap: 18px;
    }
    .hotspot-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
      gap: 12px;
      margin-top: 12px;
    }
    .hotspot-card, .skill-card {
      background: #0b1220;
      border: 1px solid #1f2937;
      border-radius: 12px;
      padding: 14px;
    }
    .hotspot-card {
      display: grid;
      grid-template-columns: 36px minmax(0, 1fr);
      gap: 12px;
      align-items: start;
    }
    .hotspot-rank {
      width: 36px;
      height: 36px;
      border-radius: 999px;
      background: rgba(56, 189, 248, 0.14);
      color: #dbeafe;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      font-weight: 700;
    }
    .hotspot-main {
      min-width: 0;
    }
    .hotspot-head, .runtime-head {
      display: flex;
      align-items: center;
      gap: 8px;
      flex-wrap: wrap;
    }
    .hotspot-title, .runtime-title {
      font-weight: 700;
      margin-top: 6px;
    }
    .hotspot-duration, .skill-total {
      font-size: 22px;
      font-weight: 700;
      margin-top: 8px;
    }
    .kind-badge {
      display: inline-flex;
      align-items: center;
      padding: 2px 8px;
      border-radius: 999px;
      background: rgba(56, 189, 248, 0.12);
      color: #dbeafe;
      font-size: 11px;
      font-weight: 700;
    }
    .skill-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(340px, 1fr));
      gap: 16px;
      margin-top: 12px;
    }
    .skill-card-head {
      display: flex;
      align-items: flex-start;
      justify-content: space-between;
      gap: 12px;
      margin-bottom: 12px;
    }
    .section-label {
      color: #93c5fd;
      font-size: 12px;
      font-weight: 700;
      margin: 14px 0 10px;
      text-transform: uppercase;
      letter-spacing: 0.04em;
    }
    .breakdown-row, .runtime-row {
      display: grid;
      grid-template-columns: minmax(180px, 2fr) minmax(160px, 3fr) 88px;
      gap: 12px;
      align-items: center;
      margin: 10px 0;
    }
    .breakdown-bar-wrap {
      background: #111827;
      border-radius: 999px;
      height: 12px;
      overflow: hidden;
    }
    .breakdown-bar {
      background: linear-gradient(90deg, #38bdf8, #22c55e);
      height: 100%;
      border-radius: 999px;
    }
    .runtime-main {
      grid-column: 1 / span 2;
      min-width: 0;
    }
  </style>
</head>
<body>
  <h1>Skills 全链路观测看板</h1>
  <div class="subtitle">
    产品：${escapeHtml(preparedSnapshot.run.productId)} · Run：${escapeHtml(preparedSnapshot.run.runId)} · 状态：${escapeHtml(preparedSnapshot.run.status)} · 来源：${escapeHtml(preparedSnapshot.run.source)}
  </div>

  <div class="grid">
    <div class="card">
      <div class="metric-label">活跃耗时（重建）</div>
      <div class="metric">${escapeHtml(formatDuration(preparedSnapshot.run.durationMs))}</div>
    </div>
    <div class="card">
      <div class="metric-label">通过用例</div>
      <div class="metric">${escapeHtml(String(execution.summary.passed || 0))}</div>
    </div>
    <div class="card">
      <div class="metric-label">fixme</div>
      <div class="metric">${escapeHtml(String(execution.summary.fixme || 0))}</div>
    </div>
    <div class="card">
      <div class="metric-label">通过率</div>
      <div class="metric">${escapeHtml(String(execution.summary.passRate || 'N/A'))}</div>
    </div>
  </div>

  <div class="panel">
    <h2>运行摘要</h2>
    <div class="muted">开始：${escapeHtml(formatDateTime(preparedSnapshot.run.startedAt))} · 结束：${escapeHtml(formatDateTime(preparedSnapshot.run.endedAt))}</div>
  </div>

  <div class="panel">
    <h2>耗时热点榜</h2>
    <div class="muted">${escapeHtml(timeAttribution.coverageNote)}</div>
    ${renderHotspots(timeAttribution.hotspots)}
  </div>

  <div class="panel">
    <h2>Phase 瀑布图</h2>
    ${renderPhaseWaterfall(phases)}
  </div>

  <div class="panel">
    <h2>Skill 耗时归因</h2>
    <div class="muted">结构：阶段耗时拆解 + 阶段内热点。${escapeHtml(timeAttribution.skillPanelNote)}</div>
    ${renderSkillTimeAttribution(timeAttribution.skills)}
  </div>

  <div class="two-column">
    <div class="panel">
      <h2>执行结果</h2>
      <table>
        <thead>
          <tr><th>TC 编号</th><th>标题</th><th>耗时</th></tr>
        </thead>
        <tbody>
          ${renderPassedTests(execution)}
        </tbody>
      </table>
    </div>

    <div class="panel">
      <h2>失败用例</h2>
      <table>
        <thead>
          <tr><th>TC 编号</th><th>原因</th></tr>
        </thead>
        <tbody>
          ${renderFailedTests(execution)}
        </tbody>
      </table>
    </div>

    <div class="panel">
      <h2>fixme / 残余风险</h2>
      <table>
        <thead>
          <tr><th>TC 编号</th><th>原因</th></tr>
        </thead>
        <tbody>
          ${renderFixmeTests(execution)}
        </tbody>
      </table>
    </div>
  </div>

  <div class="panel">
    <h2>Tool 调用</h2>
    ${renderToolCallSection(preparedSnapshot)}
  </div>

  <div class="panel">
    <h2>Guardrails</h2>
    ${renderGuardrails(preparedSnapshot.guardrails)}
  </div>

  <div class="panel">
    <h2>Artifacts</h2>
    <ul>${renderArtifacts(preparedSnapshot.artifacts || [])}</ul>
  </div>

  <div class="panel">
    <h2>事件时间轴</h2>
    <table>
      <thead>
        <tr><th>时间</th><th>类型</th><th>标题</th><th>状态</th></tr>
      </thead>
      <tbody>
        ${renderTimeline(preparedSnapshot.timeline || [])}
      </tbody>
    </table>
  </div>
</body>
</html>`;
}

module.exports = {
  renderDashboardHtml,
};
