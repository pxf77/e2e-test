const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { spawnSync } = require('node:child_process');

const buildRunDataCli = path.resolve(__dirname, '..', 'build-run-data.cjs');
const generateDashboardCli = path.resolve(__dirname, '..', 'generate-dashboard.cjs');

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

## [mpt-ins-prd-ana / 阶段五] 质量检查
- 完成时间: 2026-03-31T12:00:00+08:00
- 状态: ✅ COMPLETE
`.trim();

function writeMinimalProductArtifacts(repoRoot) {
  const productRoot = path.join(repoRoot, 'products', 'demo-product');
  fs.mkdirSync(path.join(productRoot, 'prd-ana'), { recursive: true });
  fs.writeFileSync(path.join(productRoot, 'prd-ana', 'parsing-log.md'), prdAnaLog, 'utf8');
  return productRoot;
}

test('build-run-data CLI writes dashboard time attribution structure', () => {
  const repoRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'obs-build-run-data-'));
  writeMinimalProductArtifacts(repoRoot);
  const outputPath = path.join(repoRoot, 'out', 'run-data.json');
  const result = spawnSync(process.execPath, [
    buildRunDataCli,
    '--repo-root', repoRoot,
    '--product', 'demo-product',
    '--output', 'out/run-data.json',
  ], {
    encoding: 'utf8',
  });

  assert.equal(result.status, 0, result.stderr || result.stdout);

  const payload = JSON.parse(fs.readFileSync(outputPath, 'utf8'));
  assert.equal(Array.isArray(payload.dashboard?.timeAttribution?.hotspots), true);
  assert.equal(Array.isArray(payload.dashboard?.timeAttribution?.skills), true);
  assert.match(payload.dashboard.timeAttribution.coverageNote, /runtime|phase/);
});

test('generate-dashboard CLI renders time attribution sections in html output', () => {
  const repoRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'obs-generate-dashboard-'));
  writeMinimalProductArtifacts(repoRoot);
  const outputDir = path.join(repoRoot, 'out', 'dashboard');
  const result = spawnSync(process.execPath, [
    generateDashboardCli,
    '--repo-root', repoRoot,
    '--product', 'demo-product',
    '--output-dir', 'out/dashboard',
  ], {
    encoding: 'utf8',
  });

  assert.equal(result.status, 0, result.stderr || result.stdout);

  const runData = JSON.parse(fs.readFileSync(path.join(outputDir, 'run-data.json'), 'utf8'));
  const html = fs.readFileSync(path.join(outputDir, 'index.html'), 'utf8');

  assert.equal(Array.isArray(runData.dashboard?.timeAttribution?.skills), true);
  assert.match(html, /耗时热点榜/);
  assert.match(html, /Skill 耗时归因/);
  assert.match(html, /阶段耗时拆解/);
});
