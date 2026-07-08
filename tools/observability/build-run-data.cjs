#!/usr/bin/env node
'use strict';

const fs = require('node:fs');
const path = require('node:path');

const { buildArtifactRun } = require('./lib/aggregate-run.cjs');
const { prepareDashboardSnapshot } = require('./dashboard/time-attribution.cjs');
const { buildArtifactInput, parseFlagArgs, resolveOutputPath } = require('./lib/local-data-loader.cjs');

function main() {
  try {
    const args = {
      repoRoot: process.cwd(),
      ...parseFlagArgs(process.argv, {
        '--product': 'product',
        '--repo-root': 'repoRoot',
        '--output': 'output',
      }),
    };
    if (args.repoRoot) {
      args.repoRoot = path.resolve(args.repoRoot);
    }
    if (!args.product) {
      console.error('用法: node tools/observability/build-run-data.cjs --product <product> [--output <file>]');
      process.exit(1);
    }

    const snapshot = prepareDashboardSnapshot(buildArtifactRun(buildArtifactInput(args.repoRoot, args.product)));
    const payload = JSON.stringify(snapshot, null, 2);

    if (args.output) {
      const outputPath = resolveOutputPath(args.repoRoot, args.output);
      fs.mkdirSync(path.dirname(outputPath), { recursive: true });
      fs.writeFileSync(outputPath, payload, 'utf8');
      console.log(`已写入 ${outputPath}`);
      return;
    }

    process.stdout.write(payload);
  } catch (error) {
    console.error(`生成 run-data 失败: ${error.message}`);
    process.exit(1);
  }
}

main();
