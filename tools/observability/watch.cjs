#!/usr/bin/env node
'use strict';

/**
 * 全链路观测看板文件监听器
 *
 * 用法：node tools/observability/watch.cjs [--repo-root <path>]
 *
 * 监听 products/ 下的工件文件和 Runtime 事件流变化，
 * 检测到变化后防抖 2 秒，自动重建对应产品的看板（index.html）。
 * 重建完成后刷新浏览器（F5）即可看到最新数据。
 *
 * 注意：fs.watch recursive 在 Linux 下不可用，需替换为 chokidar。
 */

const fs = require('node:fs');
const path = require('node:path');
const { spawn } = require('node:child_process');

// ── 配置 ────────────────────────────────────────────────────────────────────

const DEBOUNCE_MS = 2000;

/**
 * 触发重建的文件路径正则（匹配 products/<id>/... 的相对路径，正斜杠）。
 * 路径与 local-data-loader.cjs 中 buildArtifactInput 读取的文件保持一致。
 */
const WATCHED_PATTERNS = [
  // prd-ana 主日志（local-data-loader 读 parsing-log.md，非 .artifacts/log.md）
  /\/prd-ana\/parsing-log\.md$/,
  // tc-gen 日志
  /\/tc-gen\/\.artifacts\/log\.md$/,
  // ts-gen 日志 & 自愈日志
  /\/ts-gen\/\.artifacts\/log\.md$/,
  /\/ts-gen\/\.artifacts\/healing\.md$/,
  // tc-exec 报告摘要
  /\/tc-exec\/reports\/test-summary\.md$/,
  // Runtime 实时事件流
  /\/observability\/runtime\/runs\/[^/]+\/events\.jsonl$/,
];

// ── 工具函数 ─────────────────────────────────────────────────────────────────

function toForwardSlash(p) {
  return p.replace(/\\/g, '/');
}

/** 从相对路径中提取 productId（products/<id>/...） */
function extractProductId(relPath) {
  const normalized = toForwardSlash(relPath);
  const match = normalized.match(/^products\/([^/]+)\//);
  return match ? match[1] : null;
}

/** 判断文件是否应触发重建 */
function isWatchedFile(relPath) {
  const normalized = toForwardSlash(relPath);
  return WATCHED_PATTERNS.some((pattern) => pattern.test(normalized));
}

// ── 防抖重建 ─────────────────────────────────────────────────────────────────

/** productId → { timer, lastTrigger } */
const pendingRebuilds = new Map();

function scheduleRebuild(repoRoot, dashboardScript, productId, triggerFile) {
  const existing = pendingRebuilds.get(productId);
  if (existing) {
    clearTimeout(existing.timer);
  }
  const timer = setTimeout(() => {
    pendingRebuilds.delete(productId);
    runRebuild(repoRoot, dashboardScript, productId, triggerFile);
  }, DEBOUNCE_MS);
  pendingRebuilds.set(productId, { timer, lastTrigger: triggerFile });
}

function runRebuild(repoRoot, dashboardScript, productId, triggerFile) {
  const ts = new Date().toLocaleTimeString('zh-CN', { hour12: false });
  console.log(`\n[${ts}] 检测到变化: ${triggerFile}`);
  console.log(`[${ts}] 重建 ${productId} 看板...`);

  const child = spawn(
    process.execPath,
    [dashboardScript, '--product', productId, '--repo-root', repoRoot],
    { cwd: repoRoot, stdio: 'inherit' },
  );

  child.on('error', (err) => {
    console.error(`[watch] ❌ 启动重建进程失败: ${err.message}`);
  });

  child.on('exit', (code) => {
    const now = new Date().toLocaleTimeString('zh-CN', { hour12: false });
    if (code === 0) {
      console.log(`[${now}] ✅ ${productId} 看板已更新，请刷新浏览器（F5）`);
    } else {
      console.error(`[${now}] ❌ ${productId} 重建失败 (exit ${code})`);
    }
  });
}

// ── 参数解析 ─────────────────────────────────────────────────────────────────

function parseFlagArgs(argv) {
  const args = {};
  for (let i = 2; i < argv.length; i++) {
    if (argv[i] === '--repo-root' && argv[i + 1]) {
      args.repoRoot = argv[++i];
    }
  }
  return args;
}

// ── 主逻辑（仅直接执行时启动） ────────────────────────────────────────────────

function main() {
  const cliArgs = parseFlagArgs(process.argv);
  const repoRoot = path.resolve(cliArgs.repoRoot || process.cwd());
  const productsDir = path.join(repoRoot, 'products');
  const dashboardScript = path.join(repoRoot, 'tools', 'observability', 'generate-dashboard.cjs');

  if (!fs.existsSync(productsDir)) {
    console.error(`[watch] ❌ products/ 目录不存在: ${productsDir}`);
    console.error('[watch] 请在项目根目录下运行此命令，或使用 --repo-root 指定路径');
    process.exit(1);
  }

  if (!fs.existsSync(dashboardScript)) {
    console.error(`[watch] ❌ generate-dashboard.cjs 不存在: ${dashboardScript}`);
    process.exit(1);
  }

  console.log(`[watch] 项目根目录: ${repoRoot}`);
  console.log(`[watch] 监听目录:   ${productsDir}`);
  console.log(`[watch] 防抖时间:   ${DEBOUNCE_MS}ms`);
  console.log('[watch] 监听文件类型:');
  WATCHED_PATTERNS.forEach((p) => console.log(`         ${p.source}`));
  console.log('\n[watch] 等待文件变化... (Ctrl+C 退出)\n');

  fs.watch(productsDir, { recursive: true }, (eventType, filename) => {
    if (!filename) {
      return;
    }
    const relPath = path.join('products', filename);
    if (!isWatchedFile(relPath)) {
      return;
    }
    const productId = extractProductId(relPath);
    if (!productId) {
      return;
    }
    scheduleRebuild(repoRoot, dashboardScript, productId, toForwardSlash(relPath));
  });

  process.on('SIGINT', () => {
    console.log('\n[watch] 已停止监听');
    for (const { timer } of pendingRebuilds.values()) {
      clearTimeout(timer);
    }
    process.exit(0);
  });
}

if (require.main === module) {
  main();
}
