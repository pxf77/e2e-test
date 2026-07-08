const test = require('node:test');
const assert = require('node:assert/strict');

const { buildArtifactRun } = require('../lib/aggregate-run.cjs');

const repoRoot = require('node:path').resolve(__dirname, '../../..');
const productId = 'demo-product';

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

const tcGenLog = `
# tc-gen 日志

## [tc-gen / 阶段一] 骨架生成
- 状态: completed
- 功能点数量: 35
- 输出文件: products/demo-product/tc-gen/.artifacts/test-cases-skeleton.md
- 最后更新: 2026-04-02T20:08:00+08:00

## [tc-gen / 阶段二] 外部地图导入
- 状态: completed
- source: external
- external_site_map: tools/record-with-snapshot-result-H5-example/site-map.yaml
- synced_site_map: products/demo-product/tc-gen/site-map.yaml
- 最后更新: 2026-04-02T20:12:00+08:00

## [tc-gen / 阶段六] 优化定稿
- 状态: completed
- 输出文件: products/demo-product/tc-gen/test-cases-final.md
- 最后更新: 2026-04-02T20:42:13.3234940+08:00

## [tc-gen] 全流程完成
- 状态: ✅ COMPLETE
- 完成时间: 2026-04-02T20:42:13.3234940+08:00
`.trim();

const tsGenLog = `
# ts-gen 执行日志
- 产品: demo-product

## [ts-gen / 阶段一] 脚本生成
- 状态: completed
- 已生成文件:
- p001-detail-calc/tc-plan-001.spec.ts ✓ [TC-PLAN-001]
- 生成文件数: 1
- 最后更新: 2026-03-31T20:24:39.9396767+08:00

## [ts-gen / 阶段二] 试运行 + 自愈
- 状态: completed
- 首轮试运行: 2026-04-02T21:15:29+08:00
- 最终回归:
  - \`10 passed / 1 skipped\`
- 当前结论: 阶段二已完成
- 最后更新: 2026-04-03T02:40:00+08:00
`.trim();

const healingLog = `
# demo-product ts-gen 自愈日志

## 2026-04-02 首轮试运行
- 结果：\`8 passed / 3 failed\`

### 自愈记录：TC-CALC-003
- 错误类型：降级模式 / 脆弱断言
- 结果：单测通过

### 自愈记录：TC-INFO-001
- 错误类型：类型 A / 定位器失效
- 结果：多轮自愈后仍未稳定，已按 phase2 规则静态标记 \`test.fixme()\`
`.trim();

const testSummary = `
# demo-product 测试执行报告

> 执行时间：2026-04-03T08:53:46.2300275+08:00

## 总体结果

| 指标 | 数值 |
|------|------|
| 总用例数 | 11 |
| 通过 ✅ | 10 |
| 失败 ❌ | 0 |
| fixme ⏭️ | 1 |
| 跳过 ⏸️ | 0 |
| 通过率 | 90.9% |

## fixme 用例清单（待人工处理）

| TC 编号 | fixme 原因 |
|--------|----------|
| TC-INFO-001 | 证件有效期复合组件不稳定 |

## 通过用例列表

| TC 编号 | 用例标题 | 耗时 |
|--------|---------|------|
| TC-CALC-001 | 合法年龄保额缴费组合试算成功 | 6.3s |
| TC-HEAL-002 | 全否健告通过主流程 | 29.4s |
`.trim();

test('buildArtifactRun rebuilds one pipeline run from artifact logs', () => {
  const snapshot = buildArtifactRun({
    repoRoot,
    productId,
    artifacts: {
      prdAna: {
        path: 'products/demo-product/prd-ana/parsing-log.md',
        text: prdAnaLog,
      },
      tcGen: {
        path: 'products/demo-product/tc-gen/.artifacts/tc-gen-log.md',
        text: tcGenLog,
      },
      tsGen: {
        path: 'products/demo-product/ts-gen/.artifacts/ts-gen-log.md',
        text: tsGenLog,
      },
      healing: {
        path: 'products/demo-product/ts-gen/.artifacts/healing-log.md',
        text: healingLog,
      },
      tcExec: {
        path: 'products/demo-product/tc-exec/reports/test-summary.md',
        text: testSummary,
      },
      htmlReportPath: 'products/demo-product/tc-exec/reports/index.html',
    },
  });

  assert.equal(snapshot.run.productId, productId);
  assert.equal(snapshot.run.status, 'warning');
  assert.match(snapshot.run.runId, /20260403005346$/);
  assert.equal(snapshot.skills.length, 4);

  const prdSkill = snapshot.skills.find((skill) => skill.skillName === 'mpt-ins-prd-ana');
  assert.equal(prdSkill.phases.find((phase) => phase.phaseId === 'phase3').status, 'completed');
  assert.ok(prdSkill.phases.find((phase) => phase.phaseId === 'phase4').artifacts.length >= 2);
  assert.equal(snapshot.timeline.some((event) =>
    event.kind === 'phase' &&
    event.skill_name === 'mpt-ins-prd-ana' &&
    event.phase_name === 'phase3'
  ), true);

  const tcGenSkill = snapshot.skills.find((skill) => skill.skillName === 'mpt-ins-tc-gen');
  assert.equal(tcGenSkill.phases.find((phase) => phase.phaseId === 'phase5').status, 'completed');
  assert.equal(tcGenSkill.phases.some((phase) => phase.phaseId === 'phase6'), false);
  assert.equal(snapshot.timeline.some((event) =>
    event.kind === 'phase' &&
    event.skill_name === 'mpt-ins-tc-gen' &&
    event.phase_name === 'phase5'
  ), true);
  assert.equal(snapshot.timeline.some((event) =>
    event.kind === 'phase' &&
    event.skill_name === 'mpt-ins-tc-gen' &&
    event.phase_name === 'phase6'
  ), false);

  const tcExec = snapshot.execution;
  assert.equal(tcExec.summary.total, 11);
  assert.equal(tcExec.summary.passed, 10);
  assert.equal(tcExec.summary.fixme, 1);
  assert.equal(tcExec.passedTests[0].durationMs, 6300);
  assert.equal(tcExec.fixmeTests[0].tcId, 'TC-INFO-001');
  assert.equal(snapshot.timeline.some((event) =>
    event.kind === 'test_case' &&
    event.skill_name === 'mpt-ins-tc-exec' &&
    event.status === 'completed' &&
    event.title === 'TC-CALC-001'
  ), true);
  assert.equal(snapshot.timeline.some((event) =>
    event.kind === 'test_case' &&
    event.skill_name === 'mpt-ins-tc-exec' &&
    event.status === 'warning' &&
    event.title === 'TC-INFO-001'
  ), true);
  assert.equal(snapshot.skills.find((skill) => skill.skillName === 'mpt-ins-tc-exec').status, 'warning');
  assert.equal(snapshot.timeline.some((event) =>
    event.kind === 'action' &&
    event.skill_name === 'mpt-ins-ts-gen' &&
    event.phase_name === 'phase2' &&
    new Date(event.occurred_at).getTime() >= new Date(snapshot.skills.find((skill) => skill.skillName === 'mpt-ins-ts-gen').phases.find((phase) => phase.phaseId === 'phase2').startedAt).getTime()
  ), true);

  assert.ok(snapshot.timeline.length >= 10);
  assert.ok(snapshot.artifacts.some((artifact) => artifact.path.endsWith('test-cases-final.md')));
});

test('buildArtifactRun tolerates missing downstream artifacts and keeps run observable', () => {
  const snapshot = buildArtifactRun({
    repoRoot,
    productId,
    artifacts: {
      prdAna: {
        path: 'products/demo-product/prd-ana/parsing-log.md',
        text: prdAnaLog,
      },
      tcGen: {
        path: 'products/demo-product/tc-gen/.artifacts/tc-gen-log.md',
        text: tcGenLog,
      },
      tsGen: {
        path: 'products/demo-product/ts-gen/.artifacts/ts-gen-log.md',
        text: tsGenLog,
      },
      healing: {
        path: 'products/demo-product/ts-gen/.artifacts/healing-log.md',
        text: healingLog,
      },
      htmlReportPath: 'products/demo-product/tc-exec/reports/index.html',
    },
  });

  assert.equal(snapshot.run.status, 'observed');
  assert.equal(snapshot.execution.summary.total, 0);
  assert.equal(snapshot.skills.find((skill) => skill.skillName === 'mpt-ins-tc-exec').phases[0].status, 'unobserved');
});

test('buildArtifactRun supplements tc-exec summary from exec-log when report markdown is missing', () => {
  const execLog = `
# tc-exec 执行日志

- 产品: demo-product
- 执行时间: 2026-04-03T08:53:46.2300275+08:00

## 执行结果摘要
- 总用例数: 11
- 通过: 10
- 失败: 0
- fixme: 1
- 通过率: 90.9%

## 报告文件
- HTML 报告: tc-exec/reports/index.html
- 文字摘要: tc-exec/reports/test-summary.md
  `.trim();

  const snapshot = buildArtifactRun({
    repoRoot,
    productId,
    artifacts: {
      prdAna: {
        path: 'products/demo-product/prd-ana/parsing-log.md',
        text: prdAnaLog,
      },
      tcGen: {
        path: 'products/demo-product/tc-gen/.artifacts/tc-gen-log.md',
        text: tcGenLog,
      },
      tsGen: {
        path: 'products/demo-product/ts-gen/.artifacts/ts-gen-log.md',
        text: tsGenLog,
      },
      healing: {
        path: 'products/demo-product/ts-gen/.artifacts/healing-log.md',
        text: healingLog,
      },
      execLog: {
        path: 'products/demo-product/tc-exec/.artifacts/exec-log.md',
        text: execLog,
      },
      htmlReportPath: 'products/demo-product/tc-exec/reports/index.html',
    },
  });

  assert.equal(snapshot.execution.summary.total, 11);
  assert.equal(snapshot.execution.summary.fixme, 1);
  assert.equal(snapshot.skills.find((skill) => skill.skillName === 'mpt-ins-tc-exec').status, 'warning');
});

test('buildArtifactRun surfaces upstream failed skill status before tc-exec starts', () => {
  const failedTcGenLog = `
# tc-gen 日志

## [tc-gen / 阶段一] 骨架生成
- 状态: failed
- 最后更新: 2026-04-02T20:08:00+08:00
  `.trim();

  const snapshot = buildArtifactRun({
    repoRoot,
    productId,
    artifacts: {
      prdAna: {
        path: 'products/demo-product/prd-ana/parsing-log.md',
        text: prdAnaLog,
      },
      tcGen: {
        path: 'products/demo-product/tc-gen/.artifacts/tc-gen-log.md',
        text: failedTcGenLog,
      },
      tsGen: {
        path: 'products/demo-product/ts-gen/.artifacts/ts-gen-log.md',
        text: '',
      },
      healing: {
        path: 'products/demo-product/ts-gen/.artifacts/healing-log.md',
        text: '',
      },
    },
  });

  assert.equal(snapshot.run.status, 'failed');
  assert.equal(snapshot.skills.find((skill) => skill.skillName === 'mpt-ins-tc-gen').status, 'failed');
});

test('buildArtifactRun captures failed cases and mixed duration formats', () => {
  const failedSummary = `
# demo-product 测试执行报告

> 执行时间：2026-04-03T08:53:46.2300275+08:00

## 总体结果

| 指标 | 数值 |
|------|------|
| 总用例数 | 3 |
| 通过 ✅ | 1 |
| 失败 ❌ | 1 |
| fixme ⏭️ | 1 |
| 跳过 ⏸️ | 0 |
| 通过率 | 33.3% |

## 失败用例清单

| TC 编号 | 失败原因 |
|--------|----------|
| TC-PAY-001 | 支付页超时 |

## fixme 用例清单（待人工处理）

| TC 编号 | fixme 原因 |
|--------|----------|
| TC-INFO-001 | 证件有效期复合组件不稳定 |

## 通过用例列表

| TC 编号 | 用例标题 | 耗时 |
|--------|---------|------|
| TC-CALC-001 | 合法年龄保额缴费组合试算成功 | 1m 02s |
`.trim();

  const snapshot = buildArtifactRun({
    repoRoot,
    productId,
    artifacts: {
      prdAna: {
        path: 'products/demo-product/prd-ana/parsing-log.md',
        text: prdAnaLog,
      },
      tcGen: {
        path: 'products/demo-product/tc-gen/.artifacts/tc-gen-log.md',
        text: tcGenLog,
      },
      tsGen: {
        path: 'products/demo-product/ts-gen/.artifacts/ts-gen-log.md',
        text: tsGenLog,
      },
      healing: {
        path: 'products/demo-product/ts-gen/.artifacts/healing-log.md',
        text: healingLog,
      },
      tcExec: {
        path: 'products/demo-product/tc-exec/reports/test-summary.md',
        text: failedSummary,
      },
      htmlReportPath: 'products/demo-product/tc-exec/reports/index.html',
    },
  });

  assert.equal(snapshot.run.status, 'failed');
  assert.equal(snapshot.execution.failedTests[0].tcId, 'TC-PAY-001');
  assert.match(snapshot.execution.failedTests[0].reportHtmlPath, /index\.html$/);
  assert.equal(snapshot.execution.passedTests[0].durationMs, 62000);
  assert.equal(snapshot.skills.find((skill) => skill.skillName === 'mpt-ins-tc-exec').phases[0].status, 'failed');
});

test('buildArtifactRun preserves failed rows even when markdown cells are empty', () => {
  const failedSummaryWithEmptyReason = `
# demo-product 测试执行报告

> 执行时间：2026-04-03T08:53:46.2300275+08:00

## 总体结果

| 指标 | 数值 |
|------|------|
| 总用例数 | 1 |
| 通过 ✅ | 0 |
| 失败 ❌ | 1 |
| fixme ⏭️ | 0 |
| 跳过 ⏸️ | 0 |
| 通过率 | 0% |

## 失败用例清单

| TC 编号 | 失败原因 |
|--------|----------|
| TC-PAY-001 |  |
  `.trim();

  const snapshot = buildArtifactRun({
    repoRoot,
    productId,
    artifacts: {
      prdAna: {
        path: 'products/demo-product/prd-ana/parsing-log.md',
        text: prdAnaLog,
      },
      tcGen: {
        path: 'products/demo-product/tc-gen/.artifacts/tc-gen-log.md',
        text: tcGenLog,
      },
      tsGen: {
        path: 'products/demo-product/ts-gen/.artifacts/ts-gen-log.md',
        text: tsGenLog,
      },
      healing: {
        path: 'products/demo-product/ts-gen/.artifacts/healing-log.md',
        text: healingLog,
      },
      tcExec: {
        path: 'products/demo-product/tc-exec/reports/test-summary.md',
        text: failedSummaryWithEmptyReason,
      },
      htmlReportPath: 'products/demo-product/tc-exec/reports/index.html',
    },
  });

  assert.equal(snapshot.execution.failedTests.length, 1);
  assert.equal(snapshot.execution.failedTests[0].tcId, 'TC-PAY-001');
  assert.equal(snapshot.execution.failedTests[0].reason, '');
});

test('buildArtifactRun does not merge unrelated latest runtime sessions into artifact replay', () => {
  const snapshot = buildArtifactRun({
    repoRoot,
    productId,
    artifacts: {
      prdAna: {
        path: 'products/demo-product/prd-ana/parsing-log.md',
        text: prdAnaLog,
      },
      tcGen: {
        path: 'products/demo-product/tc-gen/.artifacts/tc-gen-log.md',
        text: tcGenLog,
      },
      tsGen: {
        path: 'products/demo-product/ts-gen/.artifacts/ts-gen-log.md',
        text: tsGenLog,
      },
      healing: {
        path: 'products/demo-product/ts-gen/.artifacts/healing-log.md',
        text: healingLog,
      },
      tcExec: {
        path: 'products/demo-product/tc-exec/reports/test-summary.md',
        text: testSummary,
      },
      htmlReportPath: 'products/demo-product/tc-exec/reports/index.html',
    },
    runtime: {
      runId: 'run_demo_product_runtime_latest',
      updatedAt: '2026-04-03T03:08:11.171Z',
      events: [
        {
          event_id: 'evt_runtime_1',
          event_type: 'tool_call.completed',
          kind: 'tool_call',
          status: 'completed',
          occurred_at: '2026-04-03T03:08:11.171Z',
          skill_name: 'mpt-ins-tc-exec',
        },
      ],
    },
  });

  assert.match(snapshot.run.runId, /20260403005346$/);
  assert.equal(snapshot.runtime.merged, false);
  assert.equal(snapshot.timeline.some((event) => event.event_id === 'evt_runtime_1'), false);
});

test('buildArtifactRun does not merge stale latest runtime into upstream-only artifact replay', () => {
  const snapshot = buildArtifactRun({
    repoRoot,
    productId,
    artifacts: {
      prdAna: {
        path: 'products/demo-product/prd-ana/parsing-log.md',
        text: prdAnaLog,
      },
    },
    runtime: {
      runId: 'run_demo_product_runtime_stale',
      updatedAt: '2026-04-03T03:08:11.171Z',
      events: [
        {
          event_id: 'evt_runtime_old',
          event_type: 'run.failed',
          kind: 'run',
          status: 'failed',
          occurred_at: '2026-04-03T03:08:11.171Z',
          skill_name: 'mpt-ins-tc-exec',
        },
      ],
    },
  });

  assert.equal(snapshot.run.source, 'artifact');
  assert.equal(snapshot.run.runId.includes('runtime_stale'), false);
  assert.equal(snapshot.timeline.some((event) => event.event_id === 'evt_runtime_old'), false);
});

test('buildArtifactRun uses latest runtime event time when updatedAt is missing', () => {
  const snapshot = buildArtifactRun({
    repoRoot,
    productId,
    artifacts: {
      prdAna: {
        path: 'products/demo-product/prd-ana/parsing-log.md',
        text: prdAnaLog,
      },
      tcGen: {
        path: 'products/demo-product/tc-gen/.artifacts/tc-gen-log.md',
        text: tcGenLog,
      },
      tsGen: {
        path: 'products/demo-product/ts-gen/.artifacts/ts-gen-log.md',
        text: tsGenLog,
      },
      healing: {
        path: 'products/demo-product/ts-gen/.artifacts/healing-log.md',
        text: healingLog,
      },
      tcExec: {
        path: 'products/demo-product/tc-exec/reports/test-summary.md',
        text: testSummary,
      },
      htmlReportPath: 'products/demo-product/tc-exec/reports/index.html',
    },
    runtime: {
      runId: 'run_demo_product_runtime_long',
      events: [
        {
          event_id: 'evt_runtime_start',
          event_type: 'run.started',
          kind: 'run',
          status: 'started',
          occurred_at: '2026-04-03T00:40:00.000Z',
          skill_name: 'mpt-ins-tc-exec',
        },
        {
          event_id: 'evt_runtime_end',
          event_type: 'run.completed',
          kind: 'run',
          status: 'completed',
          occurred_at: '2026-04-03T00:53:40.000Z',
          skill_name: 'mpt-ins-tc-exec',
        },
      ],
    },
  });

  assert.equal(snapshot.runtime.merged, true);
  assert.equal(snapshot.timeline.some((event) => event.event_id === 'evt_runtime_end'), true);
});

test('buildArtifactRun keeps richer artifact tc-exec summary when runtime test events are incomplete', () => {
  const snapshot = buildArtifactRun({
    repoRoot,
    productId,
    artifacts: {
      prdAna: {
        path: 'products/demo-product/prd-ana/parsing-log.md',
        text: prdAnaLog,
      },
      tcGen: {
        path: 'products/demo-product/tc-gen/.artifacts/tc-gen-log.md',
        text: tcGenLog,
      },
      tsGen: {
        path: 'products/demo-product/ts-gen/.artifacts/ts-gen-log.md',
        text: tsGenLog,
      },
      healing: {
        path: 'products/demo-product/ts-gen/.artifacts/healing-log.md',
        text: healingLog,
      },
      tcExec: {
        path: 'products/demo-product/tc-exec/reports/test-summary.md',
        text: testSummary,
      },
      htmlReportPath: 'products/demo-product/tc-exec/reports/index.html',
    },
    runtime: {
      runId: 'run_demo_product_runtime_partial_exec',
      updatedAt: '2026-04-03T00:53:40.000Z',
      events: [
        {
          event_id: 'evt_one_runtime_case',
          event_type: 'test_case.completed',
          kind: 'test_case',
          status: 'completed',
          occurred_at: '2026-04-03T00:53:40.000Z',
          skill_name: 'mpt-ins-tc-exec',
          phase_name: 'phase1',
          title: 'TC-CALC-001',
          data: {
            fullTitle: 'TC-CALC-001 合法年龄保额缴费组合试算成功',
          },
        },
      ],
    },
  });

  assert.equal(snapshot.execution.summary.total, 11);
  assert.equal(snapshot.execution.summary.fixme, 1);
  assert.equal(snapshot.execution.summary.passed, 10);
});

test('buildArtifactRun ignores empty or partial runtime sessions', () => {
  const snapshot = buildArtifactRun({
    repoRoot,
    productId,
    artifacts: {
      prdAna: {
        path: 'products/demo-product/prd-ana/parsing-log.md',
        text: prdAnaLog,
      },
      tcGen: {
        path: 'products/demo-product/tc-gen/.artifacts/tc-gen-log.md',
        text: tcGenLog,
      },
      tsGen: {
        path: 'products/demo-product/ts-gen/.artifacts/ts-gen-log.md',
        text: tsGenLog,
      },
      healing: {
        path: 'products/demo-product/ts-gen/.artifacts/healing-log.md',
        text: healingLog,
      },
      tcExec: {
        path: 'products/demo-product/tc-exec/reports/test-summary.md',
        text: testSummary,
      },
      htmlReportPath: 'products/demo-product/tc-exec/reports/index.html',
    },
    runtime: {
      runId: 'run_demo_product_runtime_partial',
      events: [],
    },
  });

  assert.match(snapshot.run.runId, /artifact/);
  assert.equal(snapshot.runtime.merged, false);
});

test('buildArtifactRun does not let start-only runtime phases degrade completed artifact phases', () => {
  const snapshot = buildArtifactRun({
    repoRoot,
    productId,
    artifacts: {
      prdAna: {
        path: 'products/demo-product/prd-ana/parsing-log.md',
        text: prdAnaLog,
      },
      tcGen: {
        path: 'products/demo-product/tc-gen/.artifacts/tc-gen-log.md',
        text: tcGenLog,
      },
      tsGen: {
        path: 'products/demo-product/ts-gen/.artifacts/ts-gen-log.md',
        text: tsGenLog,
      },
      healing: {
        path: 'products/demo-product/ts-gen/.artifacts/healing-log.md',
        text: healingLog,
      },
    },
    runtime: {
      runId: 'run_demo_product_runtime_phase_start_only',
      updatedAt: '2026-04-03T02:40:00.000Z',
      events: [
        {
          event_id: 'evt_phase_started_only',
          event_type: 'phase.started',
          kind: 'phase',
          status: 'started',
          occurred_at: '2026-04-03T02:39:00.000Z',
          skill_name: 'mpt-ins-ts-gen',
          phase_name: 'phase2',
          title: '试运行 + 自愈',
        },
      ],
    },
  });

  const tsGenPhase2 = snapshot.skills
    .find((skill) => skill.skillName === 'mpt-ins-ts-gen')
    .phases.find((phase) => phase.phaseId === 'phase2');

  assert.equal(tsGenPhase2.status, 'completed');
  assert.equal(tsGenPhase2.durationMs > 0, true);
});

test('buildArtifactRun treats runtime fixme test cases as warning instead of plain skipped', () => {
  const snapshot = buildArtifactRun({
    repoRoot,
    productId,
    artifacts: {
      prdAna: {
        path: 'products/demo-product/prd-ana/parsing-log.md',
        text: prdAnaLog,
      },
      tcGen: {
        path: 'products/demo-product/tc-gen/.artifacts/tc-gen-log.md',
        text: tcGenLog,
      },
      tsGen: {
        path: 'products/demo-product/ts-gen/.artifacts/ts-gen-log.md',
        text: tsGenLog,
      },
      healing: {
        path: 'products/demo-product/ts-gen/.artifacts/healing-log.md',
        text: healingLog,
      },
      htmlReportPath: 'products/demo-product/tc-exec/reports/index.html',
    },
    runtime: {
      runId: 'run_demo_product_runtime_fixme_case',
      updatedAt: '2026-04-02T18:40:30.000Z',
      events: [
        {
          event_id: 'evt_fixme_case',
          event_type: 'test_case.completed',
          kind: 'test_case',
          status: 'skipped',
          occurred_at: '2026-04-02T18:40:20.000Z',
          skill_name: 'mpt-ins-tc-exec',
          phase_name: 'phase1',
          title: 'TC-INFO-001',
          data: {
            fullTitle: 'TC-INFO-001 证件有效期复合组件',
            isFixme: true,
            reason: '证件有效期复合组件不稳定',
          },
        },
      ],
    },
  });

  assert.equal(snapshot.execution.summary.fixme, 1);
  assert.equal(snapshot.execution.summary.skipped, 0);
  assert.equal(snapshot.run.status, 'warning');
});

test('buildArtifactRun keeps malformed tc-exec summaries observable instead of green', () => {
  const snapshot = buildArtifactRun({
    repoRoot,
    productId,
    artifacts: {
      prdAna: {
        path: 'products/demo-product/prd-ana/parsing-log.md',
        text: prdAnaLog,
      },
      tcGen: {
        path: 'products/demo-product/tc-gen/.artifacts/tc-gen-log.md',
        text: tcGenLog,
      },
      tsGen: {
        path: 'products/demo-product/ts-gen/.artifacts/ts-gen-log.md',
        text: tsGenLog,
      },
      healing: {
        path: 'products/demo-product/ts-gen/.artifacts/healing-log.md',
        text: healingLog,
      },
      tcExec: {
        path: 'products/demo-product/tc-exec/reports/test-summary.md',
        text: '# test summary only',
      },
    },
  });

  assert.equal(snapshot.run.status, 'observed');
  assert.equal(snapshot.execution.summary.total, 0);
  assert.equal(snapshot.skills.find((skill) => skill.skillName === 'mpt-ins-tc-exec').status, 'observed');
});

test('buildArtifactRun tolerates tc-exec markdown without html report path', () => {
  const snapshot = buildArtifactRun({
    repoRoot,
    productId,
    artifacts: {
      prdAna: {
        path: 'products/demo-product/prd-ana/parsing-log.md',
        text: prdAnaLog,
      },
      tcGen: {
        path: 'products/demo-product/tc-gen/.artifacts/tc-gen-log.md',
        text: tcGenLog,
      },
      tsGen: {
        path: 'products/demo-product/ts-gen/.artifacts/ts-gen-log.md',
        text: tsGenLog,
      },
      healing: {
        path: 'products/demo-product/ts-gen/.artifacts/healing-log.md',
        text: healingLog,
      },
      tcExec: {
        path: 'products/demo-product/tc-exec/reports/test-summary.md',
        text: testSummary,
      },
    },
  });

  assert.equal(snapshot.execution.reportHtmlPath, null);
  assert.equal(snapshot.execution.fixmeTests[0].reportHtmlAbsolutePath, null);
});

test('buildArtifactRun backfills tc-exec status and execution from runtime telemetry', () => {
  const snapshot = buildArtifactRun({
    repoRoot,
    productId,
    artifacts: {
      prdAna: {
        path: 'products/demo-product/prd-ana/parsing-log.md',
        text: prdAnaLog,
      },
      tcGen: {
        path: 'products/demo-product/tc-gen/.artifacts/tc-gen-log.md',
        text: tcGenLog,
      },
      tsGen: {
        path: 'products/demo-product/ts-gen/.artifacts/ts-gen-log.md',
        text: tsGenLog,
      },
      healing: {
        path: 'products/demo-product/ts-gen/.artifacts/healing-log.md',
        text: healingLog,
      },
      htmlReportPath: 'products/demo-product/tc-exec/reports/index.html',
    },
    runtime: {
      runId: 'run_demo_product_playwright_20260403030000',
      updatedAt: '2026-04-02T18:40:30.000Z',
      events: [
        {
          event_id: 'evt_phase_start',
          run_id: 'run_demo_product_playwright_20260403030000',
          trace_id: 'trace_runtime_phase',
          kind: 'phase',
          event_type: 'phase.started',
          actor_type: 'skill',
          status: 'started',
          product_id: productId,
          skill_name: 'mpt-ins-tc-exec',
          phase_name: 'phase1',
          title: '执行测试并生成报告',
          occurred_at: '2026-04-02T18:40:00.000Z',
        },
        {
          event_id: 'evt_test_pass',
          run_id: 'run_demo_product_playwright_20260403030000',
          trace_id: 'trace_runtime_case',
          parent_event_id: 'evt_phase_start',
          kind: 'test_case',
          event_type: 'test_case.completed',
          actor_type: 'playwright',
          status: 'completed',
          product_id: productId,
          skill_name: 'mpt-ins-tc-exec',
          phase_name: 'phase1',
          title: 'TC-CALC-001',
          occurred_at: '2026-04-02T18:40:10.000Z',
          started_at: '2026-04-02T18:40:05.000Z',
          ended_at: '2026-04-02T18:40:10.000Z',
          duration_ms: 5000,
          data: {
            fullTitle: 'TC-CALC-001 合法年龄保额缴费组合试算成功',
          },
        },
        {
          event_id: 'evt_test_fail',
          run_id: 'run_demo_product_playwright_20260403030000',
          trace_id: 'trace_runtime_case',
          parent_event_id: 'evt_phase_start',
          kind: 'test_case',
          event_type: 'test_case.completed',
          actor_type: 'playwright',
          status: 'failed',
          product_id: productId,
          skill_name: 'mpt-ins-tc-exec',
          phase_name: 'phase1',
          title: 'TC-PAY-001',
          occurred_at: '2026-04-02T18:40:20.000Z',
          started_at: '2026-04-02T18:40:15.000Z',
          ended_at: '2026-04-02T18:40:20.000Z',
          duration_ms: 5000,
          error_message: '支付页超时',
          data: {
            fullTitle: 'TC-PAY-001 支付超时',
          },
        },
        {
          event_id: 'evt_phase_end',
          run_id: 'run_demo_product_playwright_20260403030000',
          trace_id: 'trace_runtime_phase',
          parent_event_id: 'evt_phase_start',
          kind: 'phase',
          event_type: 'phase.completed',
          actor_type: 'skill',
          status: 'failed',
          product_id: productId,
          skill_name: 'mpt-ins-tc-exec',
          phase_name: 'phase1',
          title: '执行测试并生成报告',
          occurred_at: '2026-04-02T18:40:30.000Z',
          started_at: '2026-04-02T18:40:00.000Z',
          ended_at: '2026-04-02T18:40:30.000Z',
          duration_ms: 30000,
        },
        {
          event_id: 'evt_run_end',
          run_id: 'run_demo_product_playwright_20260403030000',
          trace_id: 'trace_runtime_run',
          kind: 'run',
          event_type: 'run.completed',
          actor_type: 'system',
          status: 'failed',
          product_id: productId,
          occurred_at: '2026-04-02T18:40:31.000Z',
          started_at: '2026-04-02T18:40:00.000Z',
          ended_at: '2026-04-02T18:40:31.000Z',
        },
      ],
    },
  });

  assert.equal(snapshot.run.source, 'hybrid');
  assert.equal(snapshot.run.runId, 'run_demo_product_playwright_20260403030000');
  assert.equal(snapshot.run.status, 'failed');
  assert.equal(snapshot.execution.summary.total, 2);
  assert.equal(snapshot.execution.summary.failed, 1);
  assert.equal(snapshot.execution.passedTests[0].tcId, 'TC-CALC-001');
  assert.equal(snapshot.execution.failedTests[0].tcId, 'TC-PAY-001');
  assert.equal(snapshot.skills.find((skill) => skill.skillName === 'mpt-ins-tc-exec').phases[0].status, 'failed');
  assert.equal(snapshot.timeline.every((event) => event.run_id === 'run_demo_product_playwright_20260403030000'), true);
  assert.equal(snapshot.artifacts.some((artifact) => artifact.path.endsWith('tc-exec/reports/index.html')), true);
  assert.equal(snapshot.timeline.filter((event) =>
    event.kind === 'phase' &&
    event.skill_name === 'mpt-ins-tc-exec' &&
    event.phase_name === 'phase1' &&
    event.event_type === 'phase.completed'
  ).length, 1);
});

test('buildArtifactRun keeps warning-only runtime runs in warning state', () => {
  const snapshot = buildArtifactRun({
    repoRoot,
    productId,
    artifacts: {},
    runtime: {
      runId: 'run_demo_product_runtime_warning',
      updatedAt: '2026-04-03T00:53:46.230Z',
      events: [
        {
          event_id: 'evt_runtime_warning',
          event_type: 'run.completed',
          kind: 'run',
          status: 'warning',
          occurred_at: '2026-04-03T00:53:46.230Z',
        },
      ],
    },
  });

  assert.equal(snapshot.run.source, 'hybrid');
  assert.equal(snapshot.run.status, 'warning');
});

test('buildArtifactRun keeps upstream artifact timeline when tc-exec runtime is merged', () => {
  const snapshot = buildArtifactRun({
    repoRoot,
    productId,
    artifacts: {
      prdAna: {
        path: 'products/demo-product/prd-ana/parsing-log.md',
        text: prdAnaLog,
      },
      tcGen: {
        path: 'products/demo-product/tc-gen/.artifacts/tc-gen-log.md',
        text: tcGenLog,
      },
      tsGen: {
        path: 'products/demo-product/ts-gen/.artifacts/ts-gen-log.md',
        text: tsGenLog,
      },
      healing: {
        path: 'products/demo-product/ts-gen/.artifacts/healing-log.md',
        text: healingLog,
      },
      htmlReportPath: 'products/demo-product/tc-exec/reports/index.html',
    },
    runtime: {
      runId: 'run_demo_product_hybrid_timeline_20260403030000',
      updatedAt: '2026-04-02T18:40:30.000Z',
      events: [
        {
          event_id: 'evt_runtime_phase_start',
          run_id: 'run_demo_product_hybrid_timeline_20260403030000',
          kind: 'phase',
          event_type: 'phase.started',
          actor_type: 'skill',
          status: 'started',
          product_id: productId,
          skill_name: 'mpt-ins-tc-exec',
          phase_name: 'phase1',
          title: '执行测试并生成报告',
          occurred_at: '2026-04-02T18:40:00.000Z',
        },
        {
          event_id: 'evt_runtime_case',
          run_id: 'run_demo_product_hybrid_timeline_20260403030000',
          parent_event_id: 'evt_runtime_phase_start',
          kind: 'test_case',
          event_type: 'test_case.completed',
          actor_type: 'playwright',
          status: 'completed',
          product_id: productId,
          skill_name: 'mpt-ins-tc-exec',
          phase_name: 'phase1',
          title: 'TC-CALC-001',
          occurred_at: '2026-04-02T18:40:10.000Z',
          started_at: '2026-04-02T18:40:05.000Z',
          ended_at: '2026-04-02T18:40:10.000Z',
          duration_ms: 5000,
          data: {
            fullTitle: 'TC-CALC-001 合法年龄保额缴费组合试算成功',
          },
        },
        {
          event_id: 'evt_runtime_run_end',
          run_id: 'run_demo_product_hybrid_timeline_20260403030000',
          kind: 'run',
          event_type: 'run.completed',
          actor_type: 'system',
          status: 'completed',
          product_id: productId,
          occurred_at: '2026-04-02T18:40:30.000Z',
        },
      ],
    },
  });

  assert.equal(snapshot.run.source, 'hybrid');
  assert.equal(snapshot.timeline.some((event) =>
    event.kind === 'phase' &&
    event.skill_name === 'mpt-ins-prd-ana' &&
    event.phase_name === 'phase4'
  ), true);
  assert.equal(snapshot.timeline.some((event) =>
    event.kind === 'action' &&
    event.skill_name === 'mpt-ins-ts-gen' &&
    event.phase_name === 'phase2'
  ), true);
  assert.equal(snapshot.timeline.some((event) => event.event_id === 'evt_runtime_case'), true);
});

test('buildArtifactRun prefers richer runtime case details when artifact summary only has counts', () => {
  const execLog = `
# tc-exec 执行日志

- 产品: demo-product
- 执行时间: 2026-04-03T02:40:30+08:00

## 执行结果摘要
- 总用例数: 2
- 通过: 1
- 失败: 1
- fixme: 0
  `.trim();

  const snapshot = buildArtifactRun({
    repoRoot,
    productId,
    artifacts: {
      prdAna: {
        path: 'products/demo-product/prd-ana/parsing-log.md',
        text: prdAnaLog,
      },
      tcGen: {
        path: 'products/demo-product/tc-gen/.artifacts/tc-gen-log.md',
        text: tcGenLog,
      },
      tsGen: {
        path: 'products/demo-product/ts-gen/.artifacts/ts-gen-log.md',
        text: tsGenLog,
      },
      healing: {
        path: 'products/demo-product/ts-gen/.artifacts/healing-log.md',
        text: healingLog,
      },
      execLog: {
        path: 'products/demo-product/tc-exec/.artifacts/exec-log.md',
        text: execLog,
      },
      htmlReportPath: 'products/demo-product/tc-exec/reports/index.html',
    },
    runtime: {
      runId: 'run_demo_product_runtime_detail_20260403030000',
      updatedAt: '2026-04-02T18:40:30.000Z',
      events: [
        {
          event_id: 'evt_runtime_pass',
          run_id: 'run_demo_product_runtime_detail_20260403030000',
          kind: 'test_case',
          event_type: 'test_case.completed',
          actor_type: 'playwright',
          status: 'completed',
          product_id: productId,
          skill_name: 'mpt-ins-tc-exec',
          phase_name: 'phase1',
          title: 'TC-CALC-001',
          occurred_at: '2026-04-02T18:40:10.000Z',
          started_at: '2026-04-02T18:40:05.000Z',
          ended_at: '2026-04-02T18:40:10.000Z',
          duration_ms: 5000,
          data: {
            fullTitle: 'TC-CALC-001 合法年龄保额缴费组合试算成功',
          },
        },
        {
          event_id: 'evt_runtime_fail',
          run_id: 'run_demo_product_runtime_detail_20260403030000',
          kind: 'test_case',
          event_type: 'test_case.completed',
          actor_type: 'playwright',
          status: 'failed',
          product_id: productId,
          skill_name: 'mpt-ins-tc-exec',
          phase_name: 'phase1',
          title: 'TC-PAY-001',
          occurred_at: '2026-04-02T18:40:20.000Z',
          started_at: '2026-04-02T18:40:15.000Z',
          ended_at: '2026-04-02T18:40:20.000Z',
          duration_ms: 5000,
          error_message: '支付页超时',
          data: {
            fullTitle: 'TC-PAY-001 支付超时',
          },
        },
      ],
    },
  });

  assert.equal(snapshot.execution.summary.total, 2);
  assert.equal(snapshot.execution.failedTests[0].reason, '支付页超时');
  assert.equal(snapshot.execution.passedTests[0].durationMs, 5000);
});

test('buildArtifactRun prefers richer runtime case details when totals and row counts are equal', () => {
  const sparseSummary = `
# demo-product 测试执行报告

> 执行时间：2026-04-03T02:40:30+08:00

## 总体结果

| 指标 | 数值 |
|------|------|
| 总用例数 | 1 |
| 通过 ✅ | 1 |
| 失败 ❌ | 0 |
| fixme ⏭️ | 0 |
| 跳过 ⏸️ | 0 |
| 通过率 | 100% |

## 通过用例列表

| TC 编号 | 用例标题 | 耗时 |
|--------|---------|------|
| TC-CALC-001 | 合法年龄保额缴费组合试算成功 |  |
  `.trim();

  const snapshot = buildArtifactRun({
    repoRoot,
    productId,
    artifacts: {
      prdAna: {
        path: 'products/demo-product/prd-ana/parsing-log.md',
        text: prdAnaLog,
      },
      tcGen: {
        path: 'products/demo-product/tc-gen/.artifacts/tc-gen-log.md',
        text: tcGenLog,
      },
      tsGen: {
        path: 'products/demo-product/ts-gen/.artifacts/ts-gen-log.md',
        text: tsGenLog,
      },
      healing: {
        path: 'products/demo-product/ts-gen/.artifacts/healing-log.md',
        text: healingLog,
      },
      tcExec: {
        path: 'products/demo-product/tc-exec/reports/test-summary.md',
        text: sparseSummary,
      },
      htmlReportPath: 'products/demo-product/tc-exec/reports/index.html',
    },
    runtime: {
      runId: 'run_demo_product_runtime_richer_20260403030000',
      updatedAt: '2026-04-02T18:40:30.000Z',
      events: [
        {
          event_id: 'evt_runtime_pass',
          run_id: 'run_demo_product_runtime_richer_20260403030000',
          kind: 'test_case',
          event_type: 'test_case.completed',
          actor_type: 'playwright',
          status: 'completed',
          product_id: productId,
          skill_name: 'mpt-ins-tc-exec',
          phase_name: 'phase1',
          title: 'TC-CALC-001',
          occurred_at: '2026-04-02T18:40:10.000Z',
          started_at: '2026-04-02T18:40:05.000Z',
          ended_at: '2026-04-02T18:40:10.000Z',
          duration_ms: 5000,
          data: {
            fullTitle: 'TC-CALC-001 合法年龄保额缴费组合试算成功',
          },
        },
      ],
    },
  });

  assert.equal(snapshot.execution.summary.total, 1);
  assert.equal(snapshot.execution.passedTests[0].durationMs, 5000);
});

test('buildArtifactRun ignores unsafe artifact and report paths from artifact text', () => {
  const unsafeTcGenLog = `
# tc-gen 日志

## [tc-gen / 阶段六] 优化定稿
- 状态: completed
- 输出文件: products/../../outside.txt
- 最后更新: 2026-04-02T20:42:13.3234940+08:00
  `.trim();

  const unsafeExecLog = `
# tc-exec 执行日志

- 产品: demo-product
- 执行时间: 2026-04-03T08:53:46.2300275+08:00

## 执行结果摘要
- 总用例数: 1
- 通过: 1
- 失败: 0
- fixme: 0

## 报告文件
- HTML 报告: ../../../outside.html
  `.trim();

  const snapshot = buildArtifactRun({
    repoRoot,
    productId,
    artifacts: {
      prdAna: {
        path: 'products/demo-product/prd-ana/parsing-log.md',
        text: prdAnaLog,
      },
      tcGen: {
        path: 'products/demo-product/tc-gen/.artifacts/tc-gen-log.md',
        text: unsafeTcGenLog,
      },
      execLog: {
        path: 'products/demo-product/tc-exec/.artifacts/exec-log.md',
        text: unsafeExecLog,
      },
    },
  });

  assert.equal(snapshot.artifacts.some((artifact) => artifact.path.includes('outside.txt')), false);
  assert.equal(snapshot.execution.reportHtmlAbsolutePath, null);
});

test('buildArtifactRun uses canonical synthetic event types for artifact replay', () => {
  const failedSummary = `
# demo-product 测试执行报告

> 执行时间：2026-04-03T08:53:46.2300275+08:00

## 总体结果

| 指标 | 数值 |
|------|------|
| 总用例数 | 2 |
| 通过 ✅ | 1 |
| 失败 ❌ | 1 |
| fixme ⏭️ | 0 |
| 跳过 ⏸️ | 0 |
| 通过率 | 50% |

## 失败用例清单

| TC 编号 | 失败原因 |
|--------|----------|
| TC-PAY-001 | 支付页超时 |

## 通过用例列表

| TC 编号 | 用例标题 | 耗时 |
|--------|---------|------|
| TC-CALC-001 | 合法年龄保额缴费组合试算成功 | 6.3s |
  `.trim();

  const snapshot = buildArtifactRun({
    repoRoot,
    productId,
    artifacts: {
      prdAna: {
        path: 'products/demo-product/prd-ana/parsing-log.md',
        text: prdAnaLog,
      },
      tcGen: {
        path: 'products/demo-product/tc-gen/.artifacts/tc-gen-log.md',
        text: tcGenLog,
      },
      tsGen: {
        path: 'products/demo-product/ts-gen/.artifacts/ts-gen-log.md',
        text: tsGenLog,
      },
      healing: {
        path: 'products/demo-product/ts-gen/.artifacts/healing-log.md',
        text: healingLog,
      },
      tcExec: {
        path: 'products/demo-product/tc-exec/reports/test-summary.md',
        text: failedSummary,
      },
    },
  });

  const failedCaseEvent = snapshot.timeline.find((event) => event.kind === 'test_case' && event.title === 'TC-PAY-001');
  const failedPhaseEvent = snapshot.timeline.find((event) =>
    event.kind === 'phase' &&
    event.skill_name === 'mpt-ins-tc-exec' &&
    event.phase_name === 'phase1'
  );

  assert.equal(failedCaseEvent.event_type, 'test_case.completed');
  assert.equal(failedPhaseEvent.event_type, 'phase.completed');
});
