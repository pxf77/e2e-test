const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { spawnSync } = require('node:child_process');

const { createRuntimeRunWriter } = require('../runtime/emitter.cjs');

const captureSnapshotCli = path.resolve(__dirname, '..', 'capture-snapshot.cjs');

const prdAnaLog = `
# mpt-ins-prd-ana 解析日志
- 产品: demo-product

## [mpt-ins-prd-ana / 阶段一] PRD图片预处理
- 扫描时间: 2026-03-31 11:44:00
- expanded文件: products/demo-product/prd-ana/expanded-prd.md
- 状态: ✅ COMPLETE

## [mpt-ins-prd-ana / 阶段二] 领域知识加载与险种识别
- 完成时间: 2026-03-31T11:47:00+08:00
- domain-manifest: products/demo-product/prd-ana/.artifacts/domain-analysis/domain-manifest.md
- 状态: ✅ COMPLETE

## [mpt-ins-prd-ana / 阶段四] 域驱动两趟扫描（第二趟）
- 完成时间: 2026-03-31T11:55:00+08:00
- 第一趟扫描: 识别35个功能点大纲
- 第二趟扫描: 生成35个功能点详细规格
- 输出文件:
  - products/demo-product/prd-ana/features.md（主输出，35个功能点）
  - products/demo-product/prd-ana/traceability-matrix.md（需求追溯矩阵，35行）
- 状态: ✅ COMPLETE

## [mpt-ins-prd-ana / 阶段五] 质量检查
- 完成时间: 2026-03-31T12:00:00+08:00
- 状态: ✅ COMPLETE

## [mpt-ins-prd-ana] 全流程完成
- 状态: ✅ COMPLETE
- 完成时间: 2026-03-31T12:00:00+08:00
`.trim();

const staleTcExecSummary = `
# demo-product 测试执行报告

> 执行时间：2026-04-03T08:53:46.2300275+08:00

## 总体结果

| 指标 | 数值 |
|------|------|
| 总用例数 | 1 |
| 通过 ✅ | 1 |
| 失败 ❌ | 0 |
| fixme ⏭️ | 0 |
| 跳过 ⏸️ | 0 |
| 通过率 | 100% |
`.trim();

function writeMinimalProductArtifacts(repoRoot) {
  const productRoot = path.join(repoRoot, 'products', 'demo-product');
  fs.mkdirSync(path.join(productRoot, 'prd-ana'), { recursive: true });
  fs.writeFileSync(path.join(productRoot, 'prd-ana', 'parsing-log.md'), prdAnaLog, 'utf8');
  return productRoot;
}

function writeStaleTcExecArtifacts(productRoot) {
  fs.mkdirSync(path.join(productRoot, 'tc-exec', 'reports'), { recursive: true });
  fs.writeFileSync(path.join(productRoot, 'tc-exec', 'reports', 'test-summary.md'), staleTcExecSummary, 'utf8');
}

function writeStaleRunOnlyRuntime(repoRoot) {
  const writer = createRuntimeRunWriter({
    repoRoot,
    productId: 'demo-product',
    runId: 'run_stale_runtime_001',
  });

  writer.startRun({
    title: 'stale runtime',
    occurredAt: '2026-03-31T03:58:00.000Z',
  });
  writer.completeRun({
    title: 'stale runtime',
    status: 'completed',
    startedAt: '2026-03-31T03:58:00.000Z',
    endedAt: '2026-03-31T04:00:00.000Z',
  });
}

test('capture-snapshot CLI writes latest and history dashboard outputs', () => {
  const repoRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'obs-capture-'));
  const productRoot = writeMinimalProductArtifacts(repoRoot);
  const result = spawnSync(process.execPath, [
    captureSnapshotCli,
    '--repo-root', repoRoot,
    '--product', 'demo-product',
    '--milestone', 'prd-ana-gate1',
    '--timestamp', '20260403T120000',
  ], {
    encoding: 'utf8',
  });

  assert.equal(result.status, 0, result.stderr || result.stdout);

  const latestDir = path.join(productRoot, 'observability', 'latest');
  const historyDir = path.join(productRoot, 'observability', 'history', '20260403T120000-prd-ana-gate1');
  const latestRunData = JSON.parse(fs.readFileSync(path.join(latestDir, 'run-data.json'), 'utf8'));
  const historyRunData = JSON.parse(fs.readFileSync(path.join(historyDir, 'run-data.json'), 'utf8'));

  assert.equal(fs.existsSync(path.join(latestDir, 'index.html')), true);
  assert.equal(fs.existsSync(path.join(historyDir, 'index.html')), true);
  assert.equal(latestRunData.run.productId, 'demo-product');
  assert.equal(historyRunData.run.runId, latestRunData.run.runId);
  assert.equal(Array.isArray(latestRunData.dashboard?.timeAttribution?.hotspots), true);
  assert.equal(Array.isArray(historyRunData.dashboard?.timeAttribution?.skills), true);
});

test('capture-snapshot CLI writes latest OTel only when enabled', () => {
  const repoRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'obs-capture-otel-'));
  const productRoot = writeMinimalProductArtifacts(repoRoot);
  const result = spawnSync(process.execPath, [
    captureSnapshotCli,
    '--repo-root', repoRoot,
    '--product', 'demo-product',
    '--milestone', 'tc-exec-final',
    '--timestamp', '20260403T121500',
    '--with-otel', 'true',
  ], {
    encoding: 'utf8',
  });

  assert.equal(result.status, 0, result.stderr || result.stdout);

  const latestDir = path.join(productRoot, 'observability', 'latest');
  const historyDir = path.join(productRoot, 'observability', 'history', '20260403T121500-tc-exec-final');
  const latestOtel = JSON.parse(fs.readFileSync(path.join(latestDir, 'otel.json'), 'utf8'));

  assert.equal(Array.isArray(latestOtel.resourceSpans), true);
  assert.equal(fs.existsSync(path.join(historyDir, 'otel.json')), false);
});

test('capture-snapshot CLI rejects invalid milestone values', () => {
  const repoRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'obs-capture-invalid-'));
  writeMinimalProductArtifacts(repoRoot);
  const result = spawnSync(process.execPath, [
    captureSnapshotCli,
    '--repo-root', repoRoot,
    '--product', 'demo-product',
    '--milestone', '../outside',
    '--timestamp', '20260403T121500',
  ], {
    encoding: 'utf8',
  });

  assert.notEqual(result.status, 0);
  assert.match(result.stderr, /milestone/);
});

test('capture-snapshot CLI ignores stale downstream artifacts for upstream milestones', () => {
  const repoRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'obs-capture-stage-filter-'));
  const productRoot = writeMinimalProductArtifacts(repoRoot);
  writeStaleTcExecArtifacts(productRoot);
  const result = spawnSync(process.execPath, [
    captureSnapshotCli,
    '--repo-root', repoRoot,
    '--product', 'demo-product',
    '--milestone', 'prd-ana-gate1',
    '--timestamp', '20260403T123000',
  ], {
    encoding: 'utf8',
  });

  assert.equal(result.status, 0, result.stderr || result.stdout);

  const latestRunData = JSON.parse(
    fs.readFileSync(path.join(productRoot, 'observability', 'latest', 'run-data.json'), 'utf8')
  );
  const tcExecSkill = latestRunData.skills.find((skill) => skill.skillName === 'mpt-ins-tc-exec');

  assert.ok(tcExecSkill);
  assert.notEqual(tcExecSkill.status, 'completed');
  assert.notEqual(latestRunData.run.status, 'completed');
});

test('capture-snapshot CLI ignores stale run-only runtime for upstream milestones', () => {
  const repoRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'obs-capture-run-only-runtime-'));
  const productRoot = writeMinimalProductArtifacts(repoRoot);
  writeStaleRunOnlyRuntime(repoRoot);
  const result = spawnSync(process.execPath, [
    captureSnapshotCli,
    '--repo-root', repoRoot,
    '--product', 'demo-product',
    '--milestone', 'prd-ana-gate1',
    '--timestamp', '20260403T124500',
  ], {
    encoding: 'utf8',
  });

  assert.equal(result.status, 0, result.stderr || result.stdout);

  const latestRunData = JSON.parse(
    fs.readFileSync(path.join(productRoot, 'observability', 'latest', 'run-data.json'), 'utf8')
  );

  assert.equal(latestRunData.run.source, 'artifact');
  assert.equal(latestRunData.runtime?.merged ?? false, false);
});

test('capture-snapshot CLI removes stale latest OTel when current milestone does not export it', () => {
  const repoRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'obs-capture-stale-otel-'));
  const productRoot = writeMinimalProductArtifacts(repoRoot);
  const latestDir = path.join(productRoot, 'observability', 'latest');
  fs.mkdirSync(latestDir, { recursive: true });
  fs.writeFileSync(path.join(latestDir, 'otel.json'), '{"stale":true}', 'utf8');

  const result = spawnSync(process.execPath, [
    captureSnapshotCli,
    '--repo-root', repoRoot,
    '--product', 'demo-product',
    '--milestone', 'ts-gen-gate4',
    '--timestamp', '20260403T130000',
  ], {
    encoding: 'utf8',
  });

  assert.equal(result.status, 0, result.stderr || result.stdout);
  assert.equal(fs.existsSync(path.join(latestDir, 'otel.json')), false);
});

test('capture-snapshot CLI rejects semantically invalid timestamp values', () => {
  const repoRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'obs-capture-bad-timestamp-'));
  writeMinimalProductArtifacts(repoRoot);
  const result = spawnSync(process.execPath, [
    captureSnapshotCli,
    '--repo-root', repoRoot,
    '--product', 'demo-product',
    '--milestone', 'prd-ana-gate1',
    '--timestamp', '20261340T256199',
  ], {
    encoding: 'utf8',
  });

  assert.notEqual(result.status, 0);
  assert.match(result.stderr, /timestamp/);
});
