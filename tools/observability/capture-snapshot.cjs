#!/usr/bin/env node
'use strict';

const fs = require('node:fs');
const path = require('node:path');

const { renderDashboardHtml } = require('./dashboard/render-dashboard.cjs');
const { prepareDashboardSnapshot } = require('./dashboard/time-attribution.cjs');
const { exportSnapshotToOtel } = require('./exporters/otel.cjs');
const { buildArtifactRun } = require('./lib/aggregate-run.cjs');
const { buildArtifactInput, parseFlagArgs, resolveOutputPath } = require('./lib/local-data-loader.cjs');
const { loadLatestRuntimeRun } = require('./runtime/emitter.cjs');

const STAGES = Object.freeze([
  {
    skillName: 'mpt-ins-prd-ana',
    artifactKeys: ['prdAna'],
  },
  {
    skillName: 'mpt-ins-tc-gen',
    artifactKeys: ['tcGen'],
  },
  {
    skillName: 'mpt-ins-ts-gen',
    artifactKeys: ['tsGen', 'healing'],
  },
  {
    skillName: 'mpt-ins-tc-exec',
    artifactKeys: ['tcExec', 'execLog', 'htmlReportPath'],
  },
]);

const MILESTONE_STAGE_INDEX = Object.freeze({
  'prd-ana-gate1': 0,
  'prd-ana-complete': 0,
  'tc-gen-gate2': 1,
  'tc-gen-gate3': 1,
  'ts-gen-gate4': 2,
  'tc-exec-final': 3,
});

function validateMilestone(milestone) {
  const normalized = String(milestone || '').trim();
  if (!/^[a-z0-9][a-z0-9-]*$/.test(normalized)) {
    throw new Error(`非法 milestone: ${milestone}`);
  }
  if (!Object.prototype.hasOwnProperty.call(MILESTONE_STAGE_INDEX, normalized)) {
    throw new Error(`未知 milestone: ${milestone}`);
  }
  return normalized;
}

function normalizeSnapshotTimestamp(value) {
  if (!value) {
    return new Date().toISOString().slice(0, 19).replace(/[-:]/g, '');
  }

  const normalized = String(value).trim();
  const match = normalized.match(/^(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})(\d{2})$/);
  if (!match) {
    throw new Error(`非法 timestamp: ${value}`);
  }

  const [, yearText, monthText, dayText, hourText, minuteText, secondText] = match;
  const year = Number(yearText);
  const month = Number(monthText);
  const day = Number(dayText);
  const hour = Number(hourText);
  const minute = Number(minuteText);
  const second = Number(secondText);
  const instant = new Date(Date.UTC(year, month - 1, day, hour, minute, second));

  if (
    instant.getUTCFullYear() !== year ||
    instant.getUTCMonth() !== month - 1 ||
    instant.getUTCDate() !== day ||
    instant.getUTCHours() !== hour ||
    instant.getUTCMinutes() !== minute ||
    instant.getUTCSeconds() !== second
  ) {
    throw new Error(`非法 timestamp: ${value}`);
  }

  return normalized;
}

function parseBooleanFlag(value) {
  if (value == null) {
    return false;
  }

  const normalized = String(value).trim().toLowerCase();
  if (['1', 'true', 'yes', 'y'].includes(normalized)) {
    return true;
  }
  if (['0', 'false', 'no', 'n'].includes(normalized)) {
    return false;
  }

  throw new Error(`非法布尔参数 with-otel: ${value}`);
}

function writeJson(outputPath, payload) {
  fs.mkdirSync(path.dirname(outputPath), { recursive: true });
  fs.writeFileSync(outputPath, JSON.stringify(payload, null, 2), 'utf8');
}

function writeText(outputPath, text) {
  fs.mkdirSync(path.dirname(outputPath), { recursive: true });
  fs.writeFileSync(outputPath, text, 'utf8');
}

function buildMilestoneScope(milestone) {
  const maxStageIndex = MILESTONE_STAGE_INDEX[milestone];
  const allowedStages = STAGES.slice(0, maxStageIndex + 1);
  return {
    useTcExecRuntimePointer: maxStageIndex === STAGES.length - 1,
    allowedArtifactKeys: new Set(allowedStages.flatMap((stage) => stage.artifactKeys)),
    allowedSkills: new Set(allowedStages.map((stage) => stage.skillName)),
  };
}

function filterArtifactsByScope(artifacts, scope) {
  return {
    prdAna: scope.allowedArtifactKeys.has('prdAna') ? artifacts.prdAna : null,
    tcGen: scope.allowedArtifactKeys.has('tcGen') ? artifacts.tcGen : null,
    tsGen: scope.allowedArtifactKeys.has('tsGen') ? artifacts.tsGen : null,
    healing: scope.allowedArtifactKeys.has('healing') ? artifacts.healing : null,
    tcExec: scope.allowedArtifactKeys.has('tcExec') ? artifacts.tcExec : null,
    execLog: scope.allowedArtifactKeys.has('execLog') ? artifacts.execLog : null,
    htmlReportPath: scope.allowedArtifactKeys.has('htmlReportPath') ? artifacts.htmlReportPath : null,
  };
}

function isRuntimeEventAllowed(event, allowedSkills) {
  if (!event) {
    return false;
  }
  if (event.kind === 'run') {
    return true;
  }
  if (!event.skill_name) {
    return false;
  }
  return allowedSkills.has(event.skill_name);
}

function runtimeHasDisallowedSkillEvents(runtime, allowedSkills) {
  return runtime.events.some((event) => {
    if (event.kind === 'run' || !event.skill_name) {
      return false;
    }
    return !allowedSkills.has(event.skill_name);
  });
}

function runtimeHasAllowedSkillEvents(runtime, allowedSkills) {
  return runtime.events.some((event) => {
    if (event.kind === 'run' || !event.skill_name) {
      return false;
    }
    return allowedSkills.has(event.skill_name);
  });
}

function filterRuntimeByScope(runtime, scope, dropOnDisallowedSkill) {
  if (!runtime) {
    return null;
  }
  if (dropOnDisallowedSkill && runtimeHasDisallowedSkillEvents(runtime, scope.allowedSkills)) {
    return null;
  }
  if (dropOnDisallowedSkill && !runtimeHasAllowedSkillEvents(runtime, scope.allowedSkills)) {
    return null;
  }

  const events = runtime.events.filter((event) => isRuntimeEventAllowed(event, scope.allowedSkills));
  if (!events.length) {
    return null;
  }

  return {
    ...runtime,
    events,
  };
}

function applyMilestoneScope(repoRoot, input, milestone) {
  const scope = buildMilestoneScope(milestone);
  const runtime = scope.useTcExecRuntimePointer
    ? filterRuntimeByScope(input.runtime, scope, false)
    : filterRuntimeByScope(loadLatestRuntimeRun(repoRoot, input.productId), scope, true);

  return {
    ...input,
    artifacts: filterArtifactsByScope(input.artifacts, scope),
    runtime,
  };
}

function captureSnapshot(options) {
  const repoRoot = path.resolve(options.repoRoot || process.cwd());
  const milestone = validateMilestone(options.milestone);
  const timestamp = normalizeSnapshotTimestamp(options.timestamp);
  const withOtel = parseBooleanFlag(options.withOtel);
  const input = applyMilestoneScope(repoRoot, buildArtifactInput(repoRoot, options.product), milestone);
  const snapshot = prepareDashboardSnapshot(buildArtifactRun(input));
  const html = renderDashboardHtml(snapshot);

  const latestDir = resolveOutputPath(repoRoot, path.join('products', input.productId, 'observability', 'latest'));
  const historyDir = resolveOutputPath(repoRoot, path.join('products', input.productId, 'observability', 'history', `${timestamp}-${milestone}`));
  const latestRunDataPath = path.join(latestDir, 'run-data.json');
  const latestDashboardPath = path.join(latestDir, 'index.html');
  const historyRunDataPath = path.join(historyDir, 'run-data.json');
  const historyDashboardPath = path.join(historyDir, 'index.html');
  const latestOtelPath = path.join(latestDir, 'otel.json');

  writeJson(latestRunDataPath, snapshot);
  writeText(latestDashboardPath, html);
  writeJson(historyRunDataPath, snapshot);
  writeText(historyDashboardPath, html);

  if (withOtel) {
    writeJson(latestOtelPath, exportSnapshotToOtel(snapshot));
  } else {
    fs.rmSync(latestOtelPath, { force: true });
  }

  return {
    productId: input.productId,
    milestone,
    timestamp,
    latestDir,
    historyDir,
    latestRunDataPath,
    latestDashboardPath,
    historyRunDataPath,
    historyDashboardPath,
    latestOtelPath: withOtel ? latestOtelPath : null,
  };
}

function main() {
  try {
    const args = {
      repoRoot: process.cwd(),
      ...parseFlagArgs(process.argv, {
        '--product': 'product',
        '--repo-root': 'repoRoot',
        '--milestone': 'milestone',
        '--timestamp': 'timestamp',
        '--with-otel': 'withOtel',
      }),
    };

    if (!args.product || !args.milestone) {
      console.error(
        '用法: node tools/observability/capture-snapshot.cjs --product <product> --milestone <milestone> [--timestamp <YYYYMMDDTHHmmss>] [--with-otel <true|false>]'
      );
      process.exit(1);
    }

    const result = captureSnapshot(args);
    console.log(`观测快照已写入 latest: ${result.latestDir}`);
    console.log(`观测快照已写入 history: ${result.historyDir}`);
    if (result.latestOtelPath) {
      console.log(`OTel 导出已写入 ${result.latestOtelPath}`);
    }
  } catch (error) {
    console.error(`生成观测快照失败: ${error.message}`);
    process.exit(1);
  }
}

if (require.main === module) {
  main();
}

module.exports = {
  applyMilestoneScope,
  buildMilestoneScope,
  captureSnapshot,
  filterArtifactsByScope,
  filterRuntimeByScope,
  normalizeSnapshotTimestamp,
  validateMilestone,
};
