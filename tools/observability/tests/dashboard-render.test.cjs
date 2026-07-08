const test = require('node:test');
const assert = require('node:assert/strict');

const { renderDashboardHtml } = require('../dashboard/render-dashboard.cjs');
const { prepareDashboardSnapshot } = require('../dashboard/time-attribution.cjs');

test('renderDashboardHtml builds a self-contained dashboard page', () => {
  const html = renderDashboardHtml({
    run: {
      runId: 'run_demo_product_artifact_20260403085346',
      productId: 'demo-product',
      status: 'completed',
      startedAt: '2026-03-31T03:44:00.000Z',
      endedAt: '2026-04-03T00:53:46.230Z',
      durationMs: 123456,
      source: 'artifact',
    },
    skills: [
      {
        skillName: 'mpt-ins-prd-ana',
        displayName: 'PRD 解析',
        phases: [
          {
            phaseId: 'phase1',
            title: 'PRD图片预处理',
            status: 'completed',
            startedAt: '2026-03-31T03:44:00.000Z',
            endedAt: '2026-03-31T03:47:00.000Z',
            durationMs: 180000,
            artifacts: [{ path: 'products/demo-product/prd-ana/expanded-prd.md' }],
          },
          {
            phaseId: 'phase3',
            title: '域驱动两趟扫描（第一趟）',
            status: 'unobserved',
            startedAt: null,
            endedAt: null,
            durationMs: null,
            artifacts: [],
          },
        ],
      },
    ],
    timeline: [],
    artifacts: [
      {
        path: 'products/demo-product/tc-gen/test-cases-final.md',
        absolutePath: 'D:\\huizecode\\probation\\e2e-test\\products\\demo product\\tc-gen\\示例 final.md',
      },
    ],
    execution: {
      summary: {
        total: 11,
        passed: 10,
        failed: 0,
        fixme: 1,
        skipped: 0,
        passRate: '90.9%',
      },
      passedTests: [
        {
          tcId: 'TC-CALC-001',
          title: '合法年龄保额缴费组合试算成功',
          durationMs: 6300,
        },
      ],
      failedTests: [
        {
          tcId: 'TC-PAY-001',
          reason: '支付页超时',
          reportHtmlPath: 'products/demo-product/tc-exec/reports/index.html',
          reportHtmlAbsolutePath: 'D:\\huizecode\\probation\\e2e-test\\products\\demo-product\\tc-exec\\reports\\index.html',
        },
      ],
      fixmeTests: [
        {
          tcId: 'TC-INFO-001',
          reason: '证件有效期复合组件不稳定',
          reportHtmlPath: 'products/demo-product/tc-exec/reports/index.html',
          reportHtmlAbsolutePath: 'D:\\huizecode\\probation\\e2e-test\\products\\demo-product\\tc-exec\\reports\\index.html',
        },
      ],
      reportMarkdownPath: 'products/demo-product/tc-exec/reports/test-summary.md',
      reportHtmlPath: 'products/demo-product/tc-exec/reports/index.html',
      executedAt: '2026-04-03T00:53:46.230Z',
    },
  });

  assert.match(html, /Skills 全链路观测看板/);
  assert.match(html, /活跃耗时（重建）/);
  assert.match(html, /Phase 瀑布图/);
  assert.match(html, /耗时热点榜/);
  assert.match(html, /Skill 耗时归因/);
  assert.match(html, /阶段耗时拆解/);
  assert.match(html, /阶段内热点/);
  assert.match(html, /暂以 phase 与 artifact 回放结果做归因/);
  assert.match(html, /TC-INFO-001/);
  assert.match(html, /TC-PAY-001/);
  assert.match(html, /查看报告/);
  assert.match(html, /未采集到 runtime tool call/);
  assert.match(html, /当前 skill 尚未采集到带耗时的 runtime 子事件/);
  assert.match(html, /当前快照尚未采集到可归因的 runtime 子事件/);
  assert.match(html, /%E7%A4%BA%E4%BE%8B%20final\.md/);
});

test('renderDashboardHtml tolerates partial execution payloads', () => {
  const html = renderDashboardHtml({
    run: {
      runId: 'run_partial_001',
      productId: 'demo-product',
      status: 'observed',
      startedAt: null,
      endedAt: null,
      durationMs: null,
      source: 'artifact',
    },
    skills: [],
    timeline: [],
    artifacts: [],
    execution: {
      summary: {
        total: 1,
        passed: 0,
        failed: 1,
        fixme: 0,
        skipped: 0,
        passRate: '0%',
      },
    },
  });

  assert.match(html, /Skills 全链路观测看板/);
  assert.match(html, /失败用例/);
  assert.match(html, /耗时热点榜/);
  assert.match(html, /Skill 耗时归因/);
});

test('renderDashboardHtml shows execution fallback marker when fallback hotspot is visible', () => {
  const html = renderDashboardHtml(prepareDashboardSnapshot({
    run: {
      runId: 'run_tc_exec_fallback_001',
      productId: 'demo-product',
      status: 'warning',
      startedAt: '2026-04-03T00:00:00.000Z',
      endedAt: '2026-04-03T00:10:00.000Z',
      durationMs: 600000,
      source: 'artifact',
    },
    skills: [
      {
        skillName: 'mpt-ins-tc-exec',
        displayName: '测试执行',
        status: 'warning',
        durationMs: 600000,
        phases: [
          {
            phaseId: 'phase1',
            title: '执行测试并生成报告',
            status: 'warning',
            durationMs: 600000,
          },
        ],
      },
    ],
    timeline: [],
    artifacts: [],
    execution: {
      summary: {
        total: 1,
        passed: 1,
        failed: 0,
        fixme: 0,
        skipped: 0,
        passRate: '100%',
      },
      passedTests: [
        {
          tcId: 'TC-CALC-001',
          title: '合法年龄保额缴费组合试算成功',
          durationMs: 6300,
        },
      ],
      failedTests: [],
      fixmeTests: [],
    },
  }));

  const fallbackMatches = html.match(/execution fallback/g) || [];
  assert.equal(fallbackMatches.length >= 2, true);
  assert.match(html, /TC-CALC-001/);
});
