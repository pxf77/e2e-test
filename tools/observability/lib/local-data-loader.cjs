'use strict';

const fs = require('node:fs');
const path = require('node:path');

const { resolveRepoPath, validateProductId } = require('./path-safety.cjs');
const { loadLatestRuntimeRun, loadRuntimeRunById, validateRunId } = require('../runtime/emitter.cjs');

function normalizeRepoRelative(filePath) {
  return String(filePath || '').replace(/\\/g, '/');
}

function parseFlagArgs(argv, flagMap) {
  const args = {};
  for (let index = 2; index < argv.length; index++) {
    const current = argv[index];
    if (!String(current).startsWith('--')) {
      throw new Error(`未知参数: ${current}`);
    }
    if (!Object.prototype.hasOwnProperty.call(flagMap, current)) {
      throw new Error(`未知参数: ${current}`);
    }
    const next = argv[index + 1];
    if (!next || String(next).startsWith('--')) {
      throw new Error(`参数 ${current} 缺少取值`);
    }
    args[flagMap[current]] = next;
    index++;
  }
  return args;
}

function readOptionalArtifact(repoRoot, relativePath) {
  if (!relativePath) {
    return null;
  }
  try {
    const absolutePath = resolveRepoPath(repoRoot, relativePath);
    if (!fs.existsSync(absolutePath) || !fs.statSync(absolutePath).isFile()) {
      return null;
    }
    return {
      path: normalizeRepoRelative(relativePath),
      absolutePath,
      text: fs.readFileSync(absolutePath, 'utf8'),
    };
  } catch {
    return null;
  }
}

function readOptionalPath(repoRoot, relativePath) {
  try {
    const absolutePath = resolveRepoPath(repoRoot, relativePath);
    if (!fs.existsSync(absolutePath)) {
      return null;
    }
    return normalizeRepoRelative(relativePath);
  } catch {
    return null;
  }
}

function safeParseJson(text) {
  try {
    return JSON.parse(text);
  } catch {
    return null;
  }
}

function resolveRuntimeFromPointer(repoRoot, productId) {
  const pointerPath = readOptionalArtifact(repoRoot, `products/${productId}/tc-exec/observability-run.json`);
  if (!pointerPath?.text) {
    return null;
  }
  const payload = safeParseJson(pointerPath.text);
  if (!payload || typeof payload.runId !== 'string') {
    return null;
  }
  try {
    validateRunId(payload.runId);
  } catch {
    return null;
  }
  return loadRuntimeRunById(repoRoot, productId, payload.runId);
}

function buildArtifactInput(repoRoot, productId) {
  const resolvedRepoRoot = path.resolve(repoRoot || process.cwd());
  const safeProductId = validateProductId(productId);

  const artifacts = {
    prdAna: readOptionalArtifact(resolvedRepoRoot, `products/${safeProductId}/prd-ana/parsing-log.md`),
    tcGen: readOptionalArtifact(resolvedRepoRoot, `products/${safeProductId}/tc-gen/.artifacts/log.md`),
    tsGen: readOptionalArtifact(resolvedRepoRoot, `products/${safeProductId}/ts-gen/.artifacts/log.md`),
    healing: readOptionalArtifact(resolvedRepoRoot, `products/${safeProductId}/ts-gen/.artifacts/healing.md`),
    tcExec: readOptionalArtifact(resolvedRepoRoot, `products/${safeProductId}/tc-exec/reports/test-summary.md`),
    execLog: readOptionalArtifact(resolvedRepoRoot, `products/${safeProductId}/tc-exec/.artifacts/exec-log.md`),
    htmlReportPath: readOptionalPath(resolvedRepoRoot, `products/${safeProductId}/tc-exec/reports/index.html`),
  };

  const runtimeFromPointer = resolveRuntimeFromPointer(resolvedRepoRoot, safeProductId);
  const runtime = runtimeFromPointer || loadLatestRuntimeRun(resolvedRepoRoot, safeProductId);

  return {
    repoRoot: resolvedRepoRoot,
    productId: safeProductId,
    artifacts,
    runtime: runtime || null,
  };
}

function resolveOutputPath(repoRoot, relativePath) {
  return resolveRepoPath(path.resolve(repoRoot || process.cwd()), relativePath);
}

module.exports = {
  buildArtifactInput,
  parseFlagArgs,
  resolveOutputPath,
};
