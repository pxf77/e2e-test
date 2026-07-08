#!/usr/bin/env node
'use strict';

const fs = require('node:fs');
const path = require('node:path');

const { exportSnapshotToOtel } = require('./exporters/otel.cjs');
const { buildArtifactRun } = require('./lib/aggregate-run.cjs');
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
    if (!args.product || !args.output) {
      console.error('用法: node tools/observability/export-otel.cjs --product <product> --output <file>');
      process.exit(1);
    }

    const snapshot = buildArtifactRun(buildArtifactInput(args.repoRoot, args.product));
    const payload = JSON.stringify(exportSnapshotToOtel(snapshot), null, 2);
    const outputPath = resolveOutputPath(args.repoRoot, args.output);

    fs.mkdirSync(path.dirname(outputPath), { recursive: true });
    fs.writeFileSync(outputPath, payload, 'utf8');
    console.log(`OTel 导出已写入 ${outputPath}`);
  } catch (error) {
    console.error(`导出 OTel 失败: ${error.message}`);
    process.exit(1);
  }
}

main();
