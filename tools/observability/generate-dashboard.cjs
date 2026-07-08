#!/usr/bin/env node
'use strict';

const fs = require('node:fs');
const path = require('node:path');

const { buildArtifactRun } = require('./lib/aggregate-run.cjs');
const { prepareDashboardSnapshot } = require('./dashboard/time-attribution.cjs');
const { renderDashboardHtml } = require('./dashboard/render-dashboard.cjs');
const { buildArtifactInput, parseFlagArgs, resolveOutputPath } = require('./lib/local-data-loader.cjs');

function main() {
  try {
    const args = {
      repoRoot: process.cwd(),
      ...parseFlagArgs(process.argv, {
        '--product': 'product',
        '--repo-root': 'repoRoot',
        '--output-dir': 'outputDir',
      }),
    };
    if (args.repoRoot) {
      args.repoRoot = path.resolve(args.repoRoot);
    }
    if (!args.product) {
      console.error('用法: node tools/observability/generate-dashboard.cjs --product <product> [--output-dir <dir>]');
      process.exit(1);
    }

    const input = buildArtifactInput(args.repoRoot, args.product);
    const outputDir = args.outputDir
      ? resolveOutputPath(args.repoRoot, args.outputDir)
      : resolveOutputPath(args.repoRoot, `products/${input.productId}/observability/latest`);
    const snapshot = prepareDashboardSnapshot(buildArtifactRun(input));
    const html = renderDashboardHtml(snapshot);

    fs.mkdirSync(outputDir, { recursive: true });
    fs.writeFileSync(path.join(outputDir, 'run-data.json'), JSON.stringify(snapshot, null, 2), 'utf8');
    fs.writeFileSync(path.join(outputDir, 'index.html'), html, 'utf8');

    console.log(`观测数据已写入 ${path.join(outputDir, 'run-data.json')}`);
    console.log(`观测看板已写入 ${path.join(outputDir, 'index.html')}`);
  } catch (error) {
    console.error(`生成 dashboard 失败: ${error.message}`);
    process.exit(1);
  }
}

main();
