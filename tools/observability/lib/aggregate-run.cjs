'use strict';

const fs = require('node:fs');
const path = require('node:path');
const { resolveRepoPath } = require('./path-safety.cjs');

const DISPLAY_NAME = {
  'mpt-ins-prd-ana': 'PRD 解析',
  'mpt-ins-tc-gen': '测试用例生成',
  'mpt-ins-ts-gen': '脚本生成与自愈',
  'mpt-ins-tc-exec': '测试执行',
};

const DEFAULT_SKILLS = [
  {
    skillName: 'mpt-ins-prd-ana',
    phases: [
      { phaseId: 'phase1', title: 'PRD图片预处理' },
      { phaseId: 'phase2', title: '领域知识加载与险种识别' },
      { phaseId: 'phase3', title: '域驱动两趟扫描（第一趟）' },
      { phaseId: 'phase4', title: '域驱动两趟扫描（第二趟）' },
      { phaseId: 'phase5', title: '质量检查' },
    ],
  },
  {
    skillName: 'mpt-ins-tc-gen',
    phases: [
      { phaseId: 'phase1', title: '骨架生成' },
      { phaseId: 'phase2', title: '测试计划设计' },
      { phaseId: 'phase3', title: '站点探索' },
      { phaseId: 'phase4', title: '用例精化' },
      { phaseId: 'phase5', title: '优化定稿' },
    ],
  },
  {
    skillName: 'mpt-ins-ts-gen',
    phases: [
      { phaseId: 'phase1', title: '脚本生成' },
      { phaseId: 'phase2', title: '试运行 + 自愈' },
    ],
  },
  {
    skillName: 'mpt-ins-tc-exec',
    phases: [
      { phaseId: 'phase1', title: '执行测试并生成报告' },
    ],
  },
];

const STATUS_PRIORITY = {
  failed: 6,
  warning: 5,
  blocked: 4,
  completed: 3,
  observed: 2,
  started: 2,
  waiting: 2,
  skipped: 2,
  unobserved: 1,
};

function makeEventIdGenerator() {
  let counter = 0;
  return function nextEventId(prefix) {
    counter += 1;
    return `evt_${prefix}_${String(counter).padStart(4, '0')}`;
  };
}

function normalizePath(value) {
  return String(value || '').replace(/\\/g, '/');
}

function toNumber(value) {
  const numeric = Number(String(value || '').trim());
  return Number.isFinite(numeric) ? numeric : 0;
}

function parseChineseNumber(raw) {
  const text = String(raw || '').trim();
  if (/^\d+$/.test(text)) {
    return Number(text);
  }
  const map = {
    一: 1,
    二: 2,
    三: 3,
    四: 4,
    五: 5,
    六: 6,
    七: 7,
    八: 8,
    九: 9,
    十: 10,
  };
  return map[text] || 0;
}

function parseTimestamp(value) {
  const raw = String(value || '').trim();
  if (!raw) {
    return null;
  }
  if (/^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$/.test(raw)) {
    return new Date(`${raw.replace(' ', 'T')}+08:00`).toISOString();
  }
  const instant = new Date(raw);
  if (!Number.isNaN(instant.getTime())) {
    return instant.toISOString();
  }
  return null;
}

function parseDurationMs(value) {
  const raw = String(value || '').trim();
  if (!raw) {
    return null;
  }
  if (/^\d+(\.\d+)?ms$/i.test(raw)) {
    return Math.round(parseFloat(raw));
  }
  if (/^\d+(\.\d+)?s$/i.test(raw)) {
    return Math.round(parseFloat(raw) * 1000);
  }
  const minuteSecondMatch = raw.match(/^(\d+)\s*m\s*(\d+)\s*s$/i);
  if (minuteSecondMatch) {
    return Number(minuteSecondMatch[1]) * 60_000 + Number(minuteSecondMatch[2]) * 1000;
  }
  return null;
}

function phaseDurationMs(startedAt, endedAt) {
  if (!startedAt || !endedAt) {
    return null;
  }
  const startMs = Date.parse(startedAt);
  const endMs = Date.parse(endedAt);
  if (!Number.isFinite(startMs) || !Number.isFinite(endMs)) {
    return null;
  }
  return Math.max(0, endMs - startMs);
}

function pickWorseStatus(left, right) {
  const leftPriority = STATUS_PRIORITY[left] || 0;
  const rightPriority = STATUS_PRIORITY[right] || 0;
  return rightPriority > leftPriority ? right : left;
}

function deriveSummaryStatus(summary) {
  if (!summary || summary.total === 0) {
    return 'observed';
  }
  if ((summary.failed || 0) > 0) {
    return 'failed';
  }
  if ((summary.fixme || 0) > 0) {
    return 'warning';
  }
  return 'completed';
}

function resolveReportPath(repoRoot, productId, reportPath) {
  if (!reportPath) {
    return {
      reportHtmlPath: null,
      reportHtmlAbsolutePath: null,
    };
  }
  const normalized = normalizePath(reportPath);
  const repoRelative = normalized.startsWith('products/') || normalized.startsWith('tools/')
    ? normalized
    : normalizePath(`products/${productId}/${normalized.replace(/^\.?\//, '')}`);
  try {
    const absolutePath = resolveRepoPath(repoRoot, repoRelative);
    return {
      reportHtmlPath: repoRelative,
      reportHtmlAbsolutePath: absolutePath,
    };
  } catch {
    return {
      reportHtmlPath: null,
      reportHtmlAbsolutePath: null,
    };
  }
}

function isoToRunSuffix(isoString) {
  const instant = new Date(isoString || 0);
  if (Number.isNaN(instant.getTime())) {
    return 'artifact';
  }
  const pad = (value) => String(value).padStart(2, '0');
  return [
    instant.getUTCFullYear(),
    pad(instant.getUTCMonth() + 1),
    pad(instant.getUTCDate()),
    pad(instant.getUTCHours()),
    pad(instant.getUTCMinutes()),
    pad(instant.getUTCSeconds()),
  ].join('');
}

function buildArtifactDescriptor(repoRoot, repoRelativePath) {
  const normalized = normalizePath(repoRelativePath);
  try {
    return {
      path: normalized,
      absolutePath: resolveRepoPath(repoRoot, normalized),
    };
  } catch {
    return null;
  }
}

function extractArtifactPaths(text) {
  const matches = String(text || '').match(/((?:products|tools)\/[^\s）)]+(?:\.[A-Za-z0-9-]+)?)/g);
  return Array.from(new Set((matches || []).map((item) => normalizePath(item))));
}

function splitSections(text) {
  const lines = String(text || '').split(/\r?\n/);
  const sections = [];
  let current = null;
  for (const line of lines) {
    if (/^##\s+/.test(line)) {
      if (current) {
        sections.push(current);
      }
      current = {
        heading: line.replace(/^##\s+/, '').trim(),
        lines: [],
      };
      continue;
    }
    if (current) {
      current.lines.push(line);
    }
  }
  if (current) {
    sections.push(current);
  }
  return sections;
}

function parseStatus(text) {
  const raw = String(text || '').trim().toLowerCase();
  if (!raw) {
    return 'observed';
  }
  if (raw.includes('fail')) {
    return 'failed';
  }
  if (raw.includes('warning')) {
    return 'warning';
  }
  if (raw.includes('block')) {
    return 'blocked';
  }
  if (raw.includes('skip')) {
    return 'skipped';
  }
  if (raw.includes('wait')) {
    return 'waiting';
  }
  if (raw.includes('start')) {
    return 'started';
  }
  if (raw.includes('complete')) {
    return 'completed';
  }
  if (raw.includes('✅')) {
    return 'completed';
  }
  return 'observed';
}

function parseBulletValue(lines, labelPatterns) {
  for (const line of lines) {
    for (const pattern of labelPatterns) {
      const match = line.match(pattern);
      if (match) {
        return match[1].trim();
      }
    }
  }
  return null;
}

function parseStageSections(skillName, text) {
  const sections = splitSections(text);
  const parsed = [];
  let fullCompletion = null;

  for (const section of sections) {
    const prdMatch = section.heading.match(/^\[mpt-ins-prd-ana \/ 阶段(.+?)\]\s+(.+)$/);
    const tcGenMatch = section.heading.match(/^\[tc-gen \/ 阶段(.+?)\]\s+(.+)$/);
    const tsGenMatch = section.heading.match(/^\[ts-gen \/ 阶段(.+?)\]\s+(.+)$/);

    const matched = skillName === 'mpt-ins-prd-ana'
      ? prdMatch
      : skillName === 'mpt-ins-tc-gen'
        ? tcGenMatch
        : skillName === 'mpt-ins-ts-gen'
          ? tsGenMatch
          : null;

    if (matched) {
      parsed.push({
        stageNumber: parseChineseNumber(matched[1]),
        title: matched[2],
        status: parseStatus(parseBulletValue(section.lines, [/-\s*状态:\s*(.+)$/])),
        timestamp: parseTimestamp(parseBulletValue(section.lines, [
          /-\s*完成时间:\s*(.+)$/,
          /-\s*最后更新:\s*(.+)$/,
          /-\s*扫描时间:\s*(.+)$/,
          /-\s*首轮试运行:\s*(.+)$/,
        ])),
        artifacts: extractArtifactPaths(section.lines.join('\n')),
        body: section.lines.join('\n'),
      });
      continue;
    }

    if (section.heading.includes('全流程完成')) {
      fullCompletion = {
        status: parseStatus(parseBulletValue(section.lines, [/-\s*状态:\s*(.+)$/])),
        timestamp: parseTimestamp(parseBulletValue(section.lines, [/-\s*完成时间:\s*(.+)$/])),
      };
    }
  }

  return { stages: parsed, fullCompletion };
}

function buildEmptySkills() {
  return DEFAULT_SKILLS.map((item) => ({
    skillName: item.skillName,
    displayName: DISPLAY_NAME[item.skillName],
    status: 'unobserved',
    startedAt: null,
    endedAt: null,
    durationMs: null,
    phases: item.phases.map((phase) => ({
      phaseId: phase.phaseId,
      title: phase.title,
      stageLabel: null,
      status: 'unobserved',
      startedAt: null,
      endedAt: null,
      durationMs: null,
      artifacts: [],
    })),
  }));
}

function getSkill(skills, skillName) {
  return skills.find((skill) => skill.skillName === skillName);
}

function getOrCreatePhase(skill, phaseId, title) {
  let phase = skill.phases.find((item) => item.phaseId === phaseId);
  if (!phase) {
    phase = {
      phaseId,
      title: title || phaseId,
      stageLabel: null,
      status: 'unobserved',
      startedAt: null,
      endedAt: null,
      durationMs: null,
      artifacts: [],
    };
    skill.phases.push(phase);
  }
  if (title) {
    phase.title = title;
  }
  return phase;
}

function applyPhaseObservation(skill, phaseId, title, status, endedAt, artifacts) {
  const phase = getOrCreatePhase(skill, phaseId, title);
  phase.status = phase.status === 'unobserved' ? status : pickWorseStatus(phase.status, status);
  phase.endedAt = endedAt || phase.endedAt;
  if (Array.isArray(artifacts) && artifacts.length > 0) {
    phase.artifacts = Array.from(new Set([...phase.artifacts, ...artifacts]));
  }
  return phase;
}

function ingestArtifactSkill(skill, artifact, skillName, artifactPaths) {
  if (!artifact?.text) {
    return;
  }
  const parsed = parseStageSections(skillName, artifact.text);

  if (skillName === 'mpt-ins-prd-ana') {
    for (const stage of parsed.stages) {
      if (stage.stageNumber === 1) {
        applyPhaseObservation(skill, 'phase1', stage.title, stage.status, stage.timestamp, stage.artifacts);
      } else if (stage.stageNumber === 2) {
        applyPhaseObservation(skill, 'phase2', stage.title, stage.status, stage.timestamp, stage.artifacts);
      } else if (stage.stageNumber === 4) {
        if (/第一趟扫描/.test(stage.body)) {
          applyPhaseObservation(skill, 'phase3', '域驱动两趟扫描（第一趟）', 'completed', stage.timestamp, []);
        }
        applyPhaseObservation(skill, 'phase4', stage.title, stage.status, stage.timestamp, stage.artifacts);
      } else if (stage.stageNumber === 5) {
        applyPhaseObservation(skill, 'phase5', stage.title, stage.status, stage.timestamp, stage.artifacts);
      }
      artifactPaths.push(...stage.artifacts);
    }
  } else if (skillName === 'mpt-ins-tc-gen') {
    for (const stage of parsed.stages) {
      const phaseId = {
        1: 'phase1',
        2: 'phase2',
        3: 'phase3',
        4: 'phase4',
        5: 'phase5',
        6: 'phase5',
      }[stage.stageNumber];
      if (!phaseId) {
        continue;
      }
      applyPhaseObservation(skill, phaseId, stage.title, stage.status, stage.timestamp, stage.artifacts);
      artifactPaths.push(...stage.artifacts);
    }
  } else if (skillName === 'mpt-ins-ts-gen') {
    for (const stage of parsed.stages) {
      const phaseId = {
        1: 'phase1',
        2: 'phase2',
      }[stage.stageNumber];
      if (!phaseId) {
        continue;
      }
      applyPhaseObservation(skill, phaseId, stage.title, stage.status, stage.timestamp, stage.artifacts);
      artifactPaths.push(...stage.artifacts);
    }
  }
}

function finalizeSkillTimings(skill) {
  const observedPhases = skill.phases
    .filter((phase) => phase.status !== 'unobserved')
    .sort((left, right) => {
      const leftTime = Date.parse(left.endedAt || left.startedAt || 0);
      const rightTime = Date.parse(right.endedAt || right.startedAt || 0);
      return leftTime - rightTime;
    });

  let previousEnd = null;
  for (const phase of observedPhases) {
    phase.startedAt = phase.startedAt || previousEnd || phase.endedAt || null;
    phase.durationMs = phaseDurationMs(phase.startedAt, phase.endedAt);
    previousEnd = phase.endedAt || previousEnd;
  }

  if (observedPhases.length > 0) {
    skill.startedAt = observedPhases[0].startedAt || observedPhases[0].endedAt;
    skill.endedAt = observedPhases.at(-1).endedAt || observedPhases.at(-1).startedAt;
    skill.durationMs = observedPhases.reduce((sum, phase) => sum + (phase.durationMs || 0), 0) || phaseDurationMs(skill.startedAt, skill.endedAt);
    skill.status = observedPhases.reduce((status, phase) => pickWorseStatus(status, phase.status), observedPhases[0].status);
  }
}

/**
 * 判断 ISO 字符串是否来自纯日期解析（UTC 午夜，精度仅到天）。
 * 已知局限：UTC 真实零点（北京时间 08:00）会被误判。
 * 在正常工作时间（北京时间 09:00-23:00）不影响实际使用。
 */
function isDateOnlyTimestamp(iso) {
  return typeof iso === 'string' && iso.endsWith('T00:00:00.000Z');
}

/**
 * 对 durationMs=0 且时间戳精度仅到天的 phase，尝试用工件文件的 mtime 修正 endedAt。
 * 若 mtime 与日期一致则更新，否则将 durationMs 置为 null（看板显示"未知"而非 0ms）。
 */
function refinePhaseTimingsFromMtime(repoRoot, skill) {
  let skillDirty = false;

  for (const phase of skill.phases) {
    if (
      phase.durationMs !== 0
      || !phase.endedAt
      || !isDateOnlyTimestamp(phase.endedAt)
      || phase.startedAt !== phase.endedAt
    ) {
      continue;
    }

    const phaseDateCst = new Date(
      new Date(phase.endedAt).getTime() + 8 * 60 * 60 * 1000,
    ).toISOString().slice(0, 10);

    let bestMtime = null;
    for (const artifactRelPath of (phase.artifacts || [])) {
      if (!artifactRelPath || typeof artifactRelPath !== 'string') {
        continue;
      }
      try {
        const absPath = path.resolve(repoRoot, artifactRelPath);
        const stat = fs.statSync(absPath);
        const mtime = stat.mtime.toISOString();
        const mtimeDateCst = new Date(
          stat.mtime.getTime() + 8 * 60 * 60 * 1000,
        ).toISOString().slice(0, 10);
        if (mtimeDateCst === phaseDateCst && (!bestMtime || mtime > bestMtime)) {
          bestMtime = mtime;
        }
      } catch {
        // 文件不存在或无读权限，跳过
      }
    }

    if (bestMtime && bestMtime > phase.startedAt) {
      phase.endedAt = bestMtime;
      phase.durationMs = phaseDurationMs(phase.startedAt, phase.endedAt);
    } else {
      // 无法修正，置为 null 避免误导性的 0ms
      phase.durationMs = null;
    }
    skillDirty = true;
  }

  if (skillDirty) {
    const observed = skill.phases.filter((p) => p.status !== 'unobserved');
    if (observed.length > 0) {
      // 更新 skill 的边界时间（取首尾 phase 中有效的时间戳）
      const validStarts = observed.map((p) => p.startedAt).filter(Boolean).sort();
      const validEnds = observed.map((p) => p.endedAt).filter(Boolean).sort();
      if (validStarts.length > 0) {
        skill.startedAt = validStarts[0];
      }
      if (validEnds.length > 0) {
        skill.endedAt = validEnds.at(-1);
      }
      const total = observed.reduce((sum, p) => sum + (p.durationMs || 0), 0);
      skill.durationMs = total || phaseDurationMs(skill.startedAt, skill.endedAt);
    }
  }
}

function sectionBodyByHeading(text, headingPrefix) {
  const sections = splitSections(text);
  const matched = sections.find((section) => section.heading.startsWith(headingPrefix));
  return matched ? matched.lines.join('\n') : '';
}

function parseTable(sectionText) {
  const lines = String(sectionText || '')
    .split(/\r?\n/)
    .filter((line) => /^\|/.test(line.trim()));
  if (lines.length < 2) {
    return [];
  }
  const headers = lines[0]
    .split('|')
    .map((cell) => cell.trim())
    .filter(Boolean);
  const rows = [];
  for (const line of lines.slice(2)) {
    const cells = line
      .split('|')
      .map((cell) => cell.trim())
      .filter(Boolean);
    if (cells.length === 0) {
      continue;
    }
    const row = {};
    headers.forEach((header, index) => {
      row[header] = cells[index] ?? '';
    });
    rows.push(row);
  }
  return rows;
}

function parseExecutionSummaryMarkdown(repoRoot, productId, artifactText, htmlReportPath, markdownPath) {
  const executedAt = parseTimestamp((String(artifactText || '').match(/执行时间：([^\n]+)/) || [])[1] || null);
  const summaryRows = parseTable(sectionBodyByHeading(artifactText, '总体结果'));
  const summaryRowMap = Object.fromEntries(summaryRows.map((row) => [row['指标'], row['数值']]));

  const total = toNumber(summaryRowMap['总用例数']);
  const passed = toNumber(summaryRowMap['通过 ✅']);
  const failed = toNumber(summaryRowMap['失败 ❌']);
  const fixme = toNumber(summaryRowMap['fixme ⏭️']);
  const skipped = toNumber(summaryRowMap['跳过 ⏸️']);

  const resolvedReportPath = resolveReportPath(repoRoot, productId, htmlReportPath);
  const reportMarkdownPath = markdownPath ? normalizePath(markdownPath) : null;

  if (total === 0 && passed === 0 && failed === 0 && fixme === 0 && skipped === 0) {
    return {
      valid: false,
      summary: {
        total: 0,
        passed: 0,
        failed: 0,
        fixme: 0,
        skipped: 0,
        passRate: '0%',
      },
      passedTests: [],
      failedTests: [],
      fixmeTests: [],
      reportHtmlPath: resolvedReportPath.reportHtmlPath,
      reportHtmlAbsolutePath: resolvedReportPath.reportHtmlAbsolutePath,
      reportMarkdownPath,
      executedAt,
    };
  }

  const failedTests = parseTable(sectionBodyByHeading(artifactText, '失败用例清单'))
    .map((row) => ({
      tcId: row['TC 编号'] || '',
      title: row['用例标题'] || '',
      reason: Object.prototype.hasOwnProperty.call(row, '失败原因') ? row['失败原因'] : '',
      durationMs: null,
      fullTitle: row['用例标题'] || row['TC 编号'] || '',
      reportHtmlPath: resolvedReportPath.reportHtmlPath,
      reportHtmlAbsolutePath: resolvedReportPath.reportHtmlAbsolutePath,
    }))
    .filter((item) => item.tcId);

  const fixmeTests = parseTable(sectionBodyByHeading(artifactText, 'fixme 用例清单'))
    .map((row) => ({
      tcId: row['TC 编号'] || '',
      title: row['用例标题'] || '',
      reason: row['fixme 原因'] || row['说明'] || '',
      durationMs: null,
      fullTitle: row['用例标题'] || row['TC 编号'] || '',
      reportHtmlPath: resolvedReportPath.reportHtmlPath,
      reportHtmlAbsolutePath: resolvedReportPath.reportHtmlAbsolutePath,
    }))
    .filter((item) => item.tcId);

  const passedTests = parseTable(sectionBodyByHeading(artifactText, '通过用例列表'))
    .map((row) => ({
      tcId: row['TC 编号'] || '',
      title: row['用例标题'] || '',
      reason: '',
      durationMs: parseDurationMs(row['耗时']),
      fullTitle: row['用例标题'] || row['TC 编号'] || '',
      reportHtmlPath: resolvedReportPath.reportHtmlPath,
      reportHtmlAbsolutePath: resolvedReportPath.reportHtmlAbsolutePath,
    }))
    .filter((item) => item.tcId);

  return {
    valid: true,
    summary: {
      total,
      passed,
      failed,
      fixme,
      skipped,
      passRate: summaryRowMap['通过率'] || (total > 0 ? `${Math.round((passed / total) * 1000) / 10}%` : '0%'),
    },
    passedTests,
    failedTests,
    fixmeTests,
    reportHtmlPath: resolvedReportPath.reportHtmlPath,
    reportHtmlAbsolutePath: resolvedReportPath.reportHtmlAbsolutePath,
    reportMarkdownPath,
    executedAt,
  };
}

function parseExecutionSummaryFromExecLog(repoRoot, productId, execLogText, htmlReportPath, markdownPath) {
  const executedAt = parseTimestamp((String(execLogText || '').match(/执行时间:\s*([^\n]+)/) || [])[1] || null);
  const total = toNumber((String(execLogText || '').match(/总用例数:\s*(\d+)/) || [])[1]);
  const passed = toNumber((String(execLogText || '').match(/通过:\s*(\d+)/) || [])[1]);
  const failed = toNumber((String(execLogText || '').match(/失败:\s*(\d+)/) || [])[1]);
  const fixme = toNumber((String(execLogText || '').match(/fixme:\s*(\d+)/) || [])[1]);
  const reportFromExecLog = (String(execLogText || '').match(/HTML 报告:\s*([^\n]+)/) || [])[1] || htmlReportPath;
  const resolvedReportPath = resolveReportPath(repoRoot, productId, reportFromExecLog);

  return {
    valid: total > 0 || passed > 0 || failed > 0 || fixme > 0,
    summary: {
      total,
      passed,
      failed,
      fixme,
      skipped: 0,
      passRate: total > 0 ? `${Math.round((passed / total) * 1000) / 10}%` : '0%',
    },
    passedTests: [],
    failedTests: [],
    fixmeTests: [],
    reportHtmlPath: resolvedReportPath.reportHtmlPath,
    reportHtmlAbsolutePath: resolvedReportPath.reportHtmlAbsolutePath,
    reportMarkdownPath: markdownPath ? normalizePath(markdownPath) : null,
    executedAt,
  };
}

function extractTcId(event) {
  if (event?.data?.tcId) {
    return event.data.tcId;
  }
  if (event?.title) {
    return String(event.title).trim();
  }
  const fullTitle = String(event?.data?.fullTitle || '');
  const match = fullTitle.match(/(TC-[A-Z]+-\d+)/);
  return match ? match[1] : fullTitle.trim();
}

function buildExecutionFromRuntime(repoRoot, productId, runtime, htmlReportPath, markdownPath) {
  const resolvedReportPath = resolveReportPath(repoRoot, productId, htmlReportPath);
  const testEvents = (runtime?.events || []).filter((event) =>
    event?.kind === 'test_case' && event?.skill_name === 'mpt-ins-tc-exec'
  );

  const passedTests = [];
  const failedTests = [];
  const fixmeTests = [];
  let skipped = 0;

  for (const event of testEvents) {
    const record = {
      tcId: extractTcId(event),
      title: event?.data?.fullTitle || event?.title || '',
      fullTitle: event?.data?.fullTitle || event?.title || '',
      reason: event?.data?.reason ?? event?.error_message ?? '',
      durationMs: Number.isFinite(event?.duration_ms) ? event.duration_ms : phaseDurationMs(event?.started_at, event?.ended_at),
      reportHtmlPath: resolvedReportPath.reportHtmlPath,
      reportHtmlAbsolutePath: resolvedReportPath.reportHtmlAbsolutePath,
    };
    if (event?.status === 'failed') {
      failedTests.push(record);
    } else if (event?.status === 'warning' || (event?.status === 'skipped' && event?.data?.isFixme)) {
      fixmeTests.push(record);
    } else if (event?.status === 'completed') {
      passedTests.push(record);
    } else if (event?.status === 'skipped') {
      skipped += 1;
    }
  }

  const total = passedTests.length + failedTests.length + fixmeTests.length + skipped;
  const latestEvent = [...(runtime?.events || [])]
    .sort((left, right) => Date.parse(left?.occurred_at || 0) - Date.parse(right?.occurred_at || 0))
    .at(-1);

  return {
    valid: total > 0,
    summary: {
      total,
      passed: passedTests.length,
      failed: failedTests.length,
      fixme: fixmeTests.length,
      skipped,
      passRate: total > 0 ? `${Math.round((passedTests.length / total) * 1000) / 10}%` : '0%',
    },
    passedTests,
    failedTests,
    fixmeTests,
    reportHtmlPath: resolvedReportPath.reportHtmlPath,
    reportHtmlAbsolutePath: resolvedReportPath.reportHtmlAbsolutePath,
    reportMarkdownPath: markdownPath ? normalizePath(markdownPath) : null,
    executedAt: latestEvent?.occurred_at || runtime?.updatedAt || null,
  };
}

function chooseExecution(artifactExecution, runtimeExecution) {
  if (artifactExecution?.valid && runtimeExecution?.valid) {
    if (artifactExecution.summary.total !== runtimeExecution.summary.total) {
      return artifactExecution.summary.total > runtimeExecution.summary.total ? artifactExecution : runtimeExecution;
    }
    const artifactDetailCount = (artifactExecution.passedTests?.length || 0)
      + (artifactExecution.failedTests?.length || 0)
      + (artifactExecution.fixmeTests?.length || 0);
    const runtimeDetailCount = (runtimeExecution.passedTests?.length || 0)
      + (runtimeExecution.failedTests?.length || 0)
      + (runtimeExecution.fixmeTests?.length || 0);
    if (runtimeDetailCount !== artifactDetailCount) {
      return runtimeDetailCount > artifactDetailCount ? runtimeExecution : artifactExecution;
    }

    const detailRichness = (execution) => {
      const allTests = [
        ...(execution?.passedTests || []),
        ...(execution?.failedTests || []),
        ...(execution?.fixmeTests || []),
      ];
      return allTests.reduce((score, testCase) => score
        + (Number.isFinite(testCase?.durationMs) ? 1 : 0)
        + (testCase?.reason ? 1 : 0)
        + (testCase?.fullTitle ? 1 : 0)
        + (testCase?.reportHtmlPath ? 1 : 0), 0);
    };
    const artifactRichness = detailRichness(artifactExecution);
    const runtimeRichness = detailRichness(runtimeExecution);
    if (runtimeRichness !== artifactRichness) {
      return runtimeRichness > artifactRichness ? runtimeExecution : artifactExecution;
    }
    return artifactExecution;
  }
  if (artifactExecution?.valid) {
    return artifactExecution;
  }
  if (runtimeExecution?.valid) {
    return runtimeExecution;
  }
  return artifactExecution || runtimeExecution || {
    valid: false,
    summary: {
      total: 0,
      passed: 0,
      failed: 0,
      fixme: 0,
      skipped: 0,
      passRate: '0%',
    },
    passedTests: [],
    failedTests: [],
    fixmeTests: [],
    reportHtmlPath: null,
    reportHtmlAbsolutePath: null,
    reportMarkdownPath: null,
    executedAt: null,
  };
}

function shouldMergeRuntime(runtime, artifactEndedAt, hasAnyArtifactSignal, options = {}) {
  if (!runtime?.runId || !Array.isArray(runtime?.events) || runtime.events.length === 0) {
    return false;
  }
  if (!hasAnyArtifactSignal) {
    return true;
  }
  if (options.preferTcExecRuntimeBackfill) {
    return true;
  }
  if (!artifactEndedAt) {
    return false;
  }
  const runtimeLastTime = parseTimestamp(runtime.updatedAt)
    || [...runtime.events]
      .map((event) => parseTimestamp(event?.ended_at || event?.occurred_at || event?.started_at))
      .filter(Boolean)
      .sort()
      .at(-1)
    || null;
  if (!runtimeLastTime) {
    return false;
  }
  const deltaMs = Math.abs(Date.parse(runtimeLastTime) - Date.parse(artifactEndedAt));
  return deltaMs <= 15 * 60 * 1000;
}

function buildSyntheticEvent(nextEventId, runId, productId, kind, skillName, phaseName, title, status, startedAt, endedAt, data, errorMessage) {
  let eventType = `${kind}.completed`;
  if (kind === 'phase') {
    eventType = status === 'blocked' ? 'phase.blocked' : status === 'started' ? 'phase.started' : 'phase.completed';
  } else if (kind === 'test_case') {
    eventType = status === 'started' ? 'test_case.started' : 'test_case.completed';
  } else if (kind === 'action') {
    eventType = status === 'started' ? 'action.started' : 'action.completed';
  }
  return {
    event_id: nextEventId(kind),
    run_id: runId,
    product_id: productId,
    kind,
    event_type: eventType,
    actor_type: kind === 'test_case' || kind === 'action' ? 'playwright' : 'skill',
    skill_name: skillName,
    phase_name: phaseName || null,
    title: title || phaseName || kind,
    status,
    started_at: startedAt || null,
    ended_at: endedAt || null,
    occurred_at: endedAt || startedAt || null,
    duration_ms: phaseDurationMs(startedAt, endedAt),
    data: data || null,
    error_message: errorMessage || null,
  };
}

function buildTimelineEventKey(event) {
  return [
    event?.kind || '',
    event?.skill_name || '',
    event?.phase_name || '',
    event?.title || '',
    event?.event_type || '',
    event?.status || '',
  ].join('|');
}

function sortTimelineEvents(events) {
  return [...(events || [])].sort((left, right) => {
    const leftTime = Date.parse(left?.occurred_at || left?.ended_at || left?.started_at || 0);
    const rightTime = Date.parse(right?.occurred_at || right?.ended_at || right?.started_at || 0);
    return leftTime - rightTime;
  });
}

function applyRuntimeToSkills(skills, runtime) {
  for (const event of runtime?.events || []) {
    if (!event?.skill_name) {
      continue;
    }
    const skill = getSkill(skills, event.skill_name);
    if (!skill) {
      continue;
    }
    if (event.kind === 'phase' && event.phase_name) {
      const phase = getOrCreatePhase(skill, event.phase_name, event.title || event.phase_name);
      if (event.event_type === 'phase.started' && ['completed', 'failed', 'warning', 'blocked'].includes(phase.status)) {
        continue;
      }
      phase.title = event.title || phase.title;
      phase.status = phase.status === 'unobserved' ? (event.status || 'observed') : pickWorseStatus(phase.status, event.status || 'observed');
      phase.startedAt = phase.startedAt || event.started_at || event.occurred_at || null;
      phase.endedAt = event.ended_at || event.occurred_at || phase.endedAt;
      phase.durationMs = Number.isFinite(event.duration_ms) ? event.duration_ms : phase.durationMs;
    }
  }
}

function addArtifactTimeline(snapshot, execution, healingText) {
  const nextEventId = makeEventIdGenerator();
  const timeline = [];
  for (const skill of snapshot.skills) {
    for (const phase of skill.phases) {
      if (phase.status === 'unobserved') {
        continue;
      }
      timeline.push(buildSyntheticEvent(
        nextEventId,
        snapshot.run.runId,
        snapshot.run.productId,
        'phase',
        skill.skillName,
        phase.phaseId,
        phase.title,
        phase.status,
        phase.startedAt,
        phase.endedAt,
        null,
        null,
      ));
    }
  }

  if (healingText) {
    const phase2 = getSkill(snapshot.skills, 'mpt-ins-ts-gen')?.phases.find((phase) => phase.phaseId === 'phase2');
    const entries = [...String(healingText).matchAll(/###\s+自愈记录：([^\n]+)/g)];
    entries.forEach((match, index) => {
      timeline.push(buildSyntheticEvent(
        nextEventId,
        snapshot.run.runId,
        snapshot.run.productId,
        'action',
        'mpt-ins-ts-gen',
        'phase2',
        `自愈记录：${match[1].trim()}`,
        'completed',
        phase2?.startedAt || phase2?.endedAt || null,
        phase2?.startedAt || phase2?.endedAt || null,
        { tcId: match[1].trim(), index },
        null,
      ));
    });
  }

  for (const testCase of execution.passedTests || []) {
    timeline.push(buildSyntheticEvent(
      nextEventId,
      snapshot.run.runId,
      snapshot.run.productId,
      'test_case',
      'mpt-ins-tc-exec',
      'phase1',
      testCase.tcId,
      'completed',
      execution.executedAt,
      execution.executedAt,
      { tcId: testCase.tcId, fullTitle: testCase.title || testCase.fullTitle || testCase.tcId },
      null,
    ));
    timeline.at(-1).duration_ms = testCase.durationMs ?? null;
  }

  for (const testCase of execution.failedTests || []) {
    timeline.push(buildSyntheticEvent(
      nextEventId,
      snapshot.run.runId,
      snapshot.run.productId,
      'test_case',
      'mpt-ins-tc-exec',
      'phase1',
      testCase.tcId,
      'failed',
      execution.executedAt,
      execution.executedAt,
      { tcId: testCase.tcId, fullTitle: testCase.title || testCase.fullTitle || testCase.tcId },
      testCase.reason,
    ));
  }

  for (const testCase of execution.fixmeTests || []) {
    const event = buildSyntheticEvent(
      nextEventId,
      snapshot.run.runId,
      snapshot.run.productId,
      'test_case',
      'mpt-ins-tc-exec',
      'phase1',
      testCase.tcId,
      'warning',
      execution.executedAt,
      execution.executedAt,
      {
        tcId: testCase.tcId,
        fullTitle: testCase.title || testCase.fullTitle || testCase.tcId,
        isFixme: true,
        reason: testCase.reason,
      },
      null,
    );
    event.duration_ms = testCase.durationMs ?? null;
    timeline.push(event);
  }

  return timeline;
}

function buildArtifactRun(input) {
  const repoRoot = path.resolve(input?.repoRoot || process.cwd());
  const productId = input?.productId || 'unknown-product';
  const artifacts = input?.artifacts || {};
  const skills = buildEmptySkills();
  const collectedArtifactPaths = [];

  for (const artifact of Object.values(artifacts)) {
    if (artifact?.path) {
      collectedArtifactPaths.push(normalizePath(artifact.path));
    } else if (typeof artifact === 'string') {
      collectedArtifactPaths.push(normalizePath(artifact));
    }
  }

  ingestArtifactSkill(getSkill(skills, 'mpt-ins-prd-ana'), artifacts.prdAna, 'mpt-ins-prd-ana', collectedArtifactPaths);
  ingestArtifactSkill(getSkill(skills, 'mpt-ins-tc-gen'), artifacts.tcGen, 'mpt-ins-tc-gen', collectedArtifactPaths);
  ingestArtifactSkill(getSkill(skills, 'mpt-ins-ts-gen'), artifacts.tsGen, 'mpt-ins-ts-gen', collectedArtifactPaths);

  for (const skill of skills) {
    finalizeSkillTimings(skill);
  }

  const artifactExecution = artifacts.tcExec?.text
    ? parseExecutionSummaryMarkdown(repoRoot, productId, artifacts.tcExec.text, artifacts.htmlReportPath, artifacts.tcExec.path)
    : parseExecutionSummaryFromExecLog(repoRoot, productId, artifacts.execLog?.text || '', artifacts.htmlReportPath, artifacts.execLog?.path);

  const hasObservedArtifact = skills.some((skill) => skill.status !== 'unobserved') || artifactExecution.valid;
  const artifactEndedAt = [
    ...skills.map((skill) => skill.endedAt).filter(Boolean),
    artifactExecution.executedAt,
  ].filter(Boolean).sort().at(-1) || null;

  const runtimeHasTcExecEvents = Boolean((input?.runtime?.events || []).some((event) => event?.skill_name === 'mpt-ins-tc-exec'));
  const hasPreTcExecArtifacts = Boolean(artifacts.tsGen?.text || artifacts.healing?.text);
  const runtimeMerged = shouldMergeRuntime(input?.runtime || null, artifactEndedAt, hasObservedArtifact, {
    preferTcExecRuntimeBackfill: runtimeHasTcExecEvents && !artifactExecution.valid && hasPreTcExecArtifacts,
  });
  const runtimeExecution = runtimeMerged
    ? buildExecutionFromRuntime(repoRoot, productId, input.runtime, artifacts.htmlReportPath, artifacts.tcExec?.path || artifacts.execLog?.path || null)
    : null;
  const execution = chooseExecution(artifactExecution, runtimeExecution);

  const tcExecSkill = getSkill(skills, 'mpt-ins-tc-exec');
  const tcExecPhase = tcExecSkill.phases[0];
  if (execution.summary.total > 0) {
    tcExecPhase.status = deriveSummaryStatus(execution.summary);
    tcExecPhase.startedAt = tcExecPhase.startedAt || execution.executedAt;
    tcExecPhase.endedAt = execution.executedAt;
    tcExecPhase.durationMs = tcExecPhase.durationMs || 0;
    tcExecSkill.status = tcExecPhase.status;
  } else if (artifacts.tcExec?.text || artifacts.execLog?.text) {
    tcExecPhase.status = 'observed';
    tcExecSkill.status = 'observed';
  }

  if (runtimeMerged) {
    applyRuntimeToSkills(skills, input.runtime);
  }

  for (const skill of skills) {
    finalizeSkillTimings(skill);
  }

  for (const skill of skills) {
    refinePhaseTimingsFromMtime(repoRoot, skill);
  }

  if (tcExecSkill.status === 'unobserved' && execution.summary.total > 0) {
    tcExecSkill.status = deriveSummaryStatus(execution.summary);
  }

  const runSource = runtimeMerged ? 'hybrid' : 'artifact';
  const runtimeEvents = runtimeMerged ? [...(input.runtime?.events || [])] : [];
  const runtimeRunId = input.runtime?.runId || null;
  const runId = runtimeMerged && runtimeRunId
    ? runtimeRunId
    : `run_${productId}_artifact_${isoToRunSuffix(execution.executedAt || artifactEndedAt)}`;

  const artifactTimeline = addArtifactTimeline(
    {
      run: { runId, productId },
      skills,
    },
    execution,
    artifacts.healing?.text || '',
  );
  const timeline = runtimeMerged
    ? (() => {
        const normalizedRuntimeTimeline = runtimeEvents.map((event) => ({
          ...event,
          run_id: runId,
          product_id: event.product_id || productId,
        }));
        const runtimeKeys = new Set(normalizedRuntimeTimeline.map((event) => buildTimelineEventKey(event)));
        const preservedArtifactTimeline = artifactTimeline
          .map((event) => ({
            ...event,
            run_id: runId,
            product_id: productId,
          }))
          .filter((event) => !runtimeKeys.has(buildTimelineEventKey(event)));
        return sortTimelineEvents([...preservedArtifactTimeline, ...normalizedRuntimeTimeline]);
      })()
    : artifactTimeline;

  if (!runtimeMerged && timeline.length === 0 && execution.summary.total > 0) {
    timeline.push(...addArtifactTimeline({ run: { runId, productId }, skills }, execution, ''));
  }

  const artifactRunStatus = skills.reduce(
    (status, skill) => pickWorseStatus(status, skill.status),
    execution.summary.total > 0 ? deriveSummaryStatus(execution.summary) : 'unobserved'
  );
  const runtimeRunStatus = runtimeMerged
    ? [...(input?.runtime?.events || [])]
      .filter((event) => event?.kind === 'run')
      .sort((left, right) => Date.parse(left?.occurred_at || 0) - Date.parse(right?.occurred_at || 0))
      .at(-1)?.status || null
    : null;
  let normalizedRunStatus = runtimeRunStatus
    ? pickWorseStatus(artifactRunStatus, runtimeRunStatus)
    : artifactRunStatus;
  if (normalizedRunStatus === 'completed' && execution.summary.total === 0 && ['unobserved', 'observed'].includes(tcExecSkill.status)) {
    normalizedRunStatus = 'observed';
  }
  if ((hasObservedArtifact || runtimeMerged) && normalizedRunStatus === 'unobserved') {
    normalizedRunStatus = 'observed';
  }

  const artifactDescriptors = Array.from(new Set(collectedArtifactPaths))
    .map((artifactPath) => buildArtifactDescriptor(repoRoot, artifactPath))
    .filter(Boolean);

  return {
    run: {
      runId,
      productId,
      status: normalizedRunStatus,
      startedAt: [...skills.map((skill) => skill.startedAt).filter(Boolean), execution.executedAt].filter(Boolean).sort()[0] || null,
      endedAt: [...skills.map((skill) => skill.endedAt).filter(Boolean), execution.executedAt].filter(Boolean).sort().at(-1) || null,
      durationMs: null,
      source: runSource,
    },
    skills,
    timeline,
    artifacts: artifactDescriptors,
    execution,
    runtime: {
      merged: runtimeMerged,
      runId: input?.runtime?.runId || null,
      updatedAt: input?.runtime?.updatedAt || null,
      eventCount: Array.isArray(input?.runtime?.events) ? input.runtime.events.length : 0,
    },
  };
}

module.exports = {
  buildArtifactRun,
};
