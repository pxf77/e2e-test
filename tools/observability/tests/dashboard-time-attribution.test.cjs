const test = require('node:test');
const assert = require('node:assert/strict');

const {
  buildTimeAttributionModel,
  prepareDashboardSnapshot,
} = require('../dashboard/time-attribution.cjs');

function createSnapshot() {
  return {
    run: {
      runId: 'run_dashboard_attr_001',
      productId: 'demo-product',
      status: 'warning',
      source: 'hybrid',
      durationMs: 1920000,
    },
    skills: [
      {
        skillName: 'mpt-ins-prd-ana',
        displayName: 'PRD 解析',
        status: 'completed',
        durationMs: 960000,
        phases: [
          {
            phaseId: 'phase1',
            title: '图片预处理',
            status: 'completed',
            durationMs: 180000,
          },
          {
            phaseId: 'phase3',
            title: '域驱动扫描',
            status: 'completed',
            durationMs: 480000,
          },
          {
            phaseId: 'phase5',
            title: '质量检查',
            status: 'completed',
            durationMs: 300000,
          },
        ],
      },
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
    timeline: [
      {
        event_id: 'evt_tool_001',
        kind: 'tool_call',
        event_type: 'tool_call.completed',
        skill_name: 'mpt-ins-prd-ana',
        phase_name: 'phase3',
        title: 'browser_snapshot',
        status: 'completed',
        duration_ms: 3200,
      },
      {
        event_id: 'evt_action_001',
        kind: 'action',
        event_type: 'action.completed',
        skill_name: 'mpt-ins-prd-ana',
        phase_name: 'phase3',
        title: '补全功能点',
        status: 'completed',
        duration_ms: 120000,
      },
      {
        event_id: 'evt_tool_002',
        kind: 'tool_call',
        event_type: 'tool_call.completed',
        skill_name: 'mpt-ins-tc-exec',
        phase_name: 'phase1',
        title: 'browser_click',
        status: 'warning',
        duration_ms: 4500,
      },
    ],
    execution: {
      summary: {
        total: 2,
        passed: 1,
        failed: 0,
        fixme: 1,
        skipped: 0,
        passRate: '50%',
      },
      passedTests: [
        {
          tcId: 'TC-CALC-001',
          title: '试算成功',
          durationMs: 6300,
        },
      ],
      failedTests: [],
      fixmeTests: [
        {
          tcId: 'TC-INFO-001',
          reason: '证件有效期不稳定',
        },
      ],
    },
  };
}

test('buildTimeAttributionModel mixes phase hotspots and runtime hotspots', () => {
  const model = buildTimeAttributionModel(createSnapshot());

  assert.equal(model.hotspots.length, 5);
  assert.equal(model.hotspots.some((item) => item.kind === 'phase'), true);
  assert.equal(model.hotspots.some((item) => item.kind === 'test_case'), true);
  assert.equal(model.hotspots.some((item) => item.label.includes('TC-CALC-001')), true);

  const prdAna = model.skills.find((skill) => skill.skillName === 'mpt-ins-prd-ana');
  assert.ok(prdAna);
  assert.equal(prdAna.phaseBreakdown[0].label, '域驱动扫描');
  assert.equal(prdAna.phaseBreakdown[0].percent, 50);
});

test('prepareDashboardSnapshot attaches time attribution without dropping snapshot fields', () => {
  const snapshot = prepareDashboardSnapshot(createSnapshot());

  assert.equal(snapshot.run.productId, 'demo-product');
  assert.ok(snapshot.dashboard);
  assert.ok(snapshot.dashboard.timeAttribution);
  assert.equal(Array.isArray(snapshot.dashboard.timeAttribution.skills), true);
});

test('buildTimeAttributionModel does not treat parser events as runtime hotspots or duplicate tc-exec fallback', () => {
  const snapshot = createSnapshot();
  snapshot.run.source = 'artifact';
  snapshot.runtime = { merged: false };
  snapshot.timeline = [
    {
      event_id: 'evt_parser_test_001',
      kind: 'test_case',
      event_type: 'test_case.completed',
      actor_type: 'parser',
      skill_name: 'mpt-ins-tc-exec',
      phase_name: 'phase1',
      title: 'TC-CALC-001',
      status: 'completed',
      duration_ms: 6300,
      data: { tcId: 'TC-CALC-001' },
    },
  ];

  const model = buildTimeAttributionModel(snapshot);
  const tcExec = model.skills.find((skill) => skill.skillName === 'mpt-ins-tc-exec');
  const tcCalcRows = tcExec.runtimeHotspots.filter((item) => item.label === 'TC-CALC-001');

  assert.equal(tcCalcRows.length, 1);
  assert.equal(tcCalcRows[0].source, 'execution');
  assert.match(model.coverageNote, /尚未采集到可归因的 runtime 子事件/);
});

test('buildTimeAttributionModel marks mixed runtime and execution fallback coverage honestly', () => {
  const snapshot = createSnapshot();
  snapshot.run.source = 'hybrid';
  snapshot.runtime = { merged: true };
  snapshot.timeline = [
    {
      event_id: 'evt_runtime_tool_001',
      kind: 'tool_call',
      event_type: 'tool_call.completed',
      actor_type: 'tool',
      skill_name: 'mpt-ins-prd-ana',
      phase_name: 'phase3',
      title: 'browser_snapshot',
      status: 'completed',
      duration_ms: 3200,
    },
  ];

  const model = buildTimeAttributionModel(snapshot);

  assert.match(model.coverageNote, /execution fallback/);
  assert.match(model.skillPanelNote, /execution fallback/);
});

test('buildTimeAttributionModel prefers real runtime testcase over execution fallback with same tcId', () => {
  const snapshot = createSnapshot();
  snapshot.run.source = 'hybrid';
  snapshot.runtime = { merged: true };
  snapshot.timeline = [
    {
      event_id: 'evt_runtime_case_001',
      kind: 'test_case',
      event_type: 'test_case.completed',
      actor_type: 'playwright',
      skill_name: 'mpt-ins-tc-exec',
      phase_name: 'phase1',
      title: 'TC-CALC-001',
      status: 'completed',
      duration_ms: 6300,
      data: { tcId: 'TC-CALC-001' },
    },
  ];

  const model = buildTimeAttributionModel(snapshot);
  const tcExec = model.skills.find((skill) => skill.skillName === 'mpt-ins-tc-exec');
  const tcCalcRows = tcExec.runtimeHotspots.filter((item) => item.label === 'TC-CALC-001');

  assert.equal(tcCalcRows.length, 1);
  assert.equal(tcCalcRows[0].source, 'runtime');
});

test('buildTimeAttributionModel note only mentions execution fallback when fallback is actually displayed', () => {
  const snapshot = createSnapshot();
  snapshot.run.source = 'hybrid';
  snapshot.runtime = { merged: true };
  snapshot.skills[1].phases[0].durationMs = 600000;
  snapshot.timeline = [
    {
      event_id: 'evt_runtime_1',
      kind: 'tool_call',
      event_type: 'tool_call.completed',
      actor_type: 'tool',
      skill_name: 'mpt-ins-prd-ana',
      phase_name: 'phase3',
      title: 'browser_snapshot',
      status: 'completed',
      duration_ms: 9000,
    },
    {
      event_id: 'evt_runtime_2',
      kind: 'tool_call',
      event_type: 'tool_call.completed',
      actor_type: 'tool',
      skill_name: 'mpt-ins-prd-ana',
      phase_name: 'phase3',
      title: 'browser_click',
      status: 'completed',
      duration_ms: 8000,
    },
    {
      event_id: 'evt_runtime_case_2',
      kind: 'test_case',
      event_type: 'test_case.completed',
      actor_type: 'playwright',
      skill_name: 'mpt-ins-tc-exec',
      phase_name: 'phase1',
      title: 'TC-OTHER-001',
      status: 'completed',
      duration_ms: 7000,
      data: { tcId: 'TC-OTHER-001' },
    },
  ];

  const model = buildTimeAttributionModel(snapshot);

  assert.equal(model.hotspots.some((item) => item.source === 'execution'), false);
  assert.doesNotMatch(model.coverageNote, /execution fallback/);
});

test('buildTimeAttributionModel dedupes tc-exec fallback when runtime testcase only exposes title', () => {
  const snapshot = createSnapshot();
  snapshot.run.source = 'hybrid';
  snapshot.runtime = { merged: true };
  snapshot.execution.passedTests = [
    {
      tcId: 'TC-CALC-001',
      title: '合法年龄保额缴费组合试算成功',
      durationMs: 6300,
    },
  ];
  snapshot.timeline = [
    {
      event_id: 'evt_runtime_case_title_only',
      kind: 'test_case',
      event_type: 'test_case.completed',
      actor_type: 'playwright',
      skill_name: 'mpt-ins-tc-exec',
      phase_name: 'phase1',
      title: '合法年龄保额缴费组合试算成功',
      status: 'completed',
      duration_ms: 6300,
      data: {},
    },
  ];

  const model = buildTimeAttributionModel(snapshot);
  const tcExec = model.skills.find((skill) => skill.skillName === 'mpt-ins-tc-exec');
  const titleRows = tcExec.runtimeHotspots.filter((item) => item.label === '合法年龄保额缴费组合试算成功');
  const executionRows = tcExec.runtimeHotspots.filter((item) => item.source === 'execution');

  assert.equal(titleRows.length, 1);
  assert.equal(titleRows[0].source, 'runtime');
  assert.equal(executionRows.length, 0);
});

test('buildTimeAttributionModel uses empty-data notes for snapshots without hotspots or skills', () => {
  const model = buildTimeAttributionModel({
    run: {
      runId: 'run_empty_001',
      productId: 'demo-product',
      status: 'observed',
      source: 'artifact',
    },
    skills: [],
    timeline: [],
    execution: {
      summary: {
        total: 0,
        passed: 0,
        failed: 0,
        fixme: 0,
        skipped: 0,
        passRate: '0%',
      },
    },
  });

  assert.match(model.coverageNote, /暂无可展示的耗时热点/);
  assert.match(model.skillPanelNote, /暂无可展示的 skill 归因数据/);
});

test('buildTimeAttributionModel uses neutral runtime empty state for unobserved skills without phase data', () => {
  const model = buildTimeAttributionModel({
    run: {
      runId: 'run_empty_skill_001',
      productId: 'demo-product',
      status: 'observed',
      source: 'artifact',
    },
    skills: [
      {
        skillName: 'mpt-ins-tc-gen',
        displayName: '测试用例生成',
        status: 'unobserved',
        phases: [
          {
            phaseId: 'phase1',
            title: '骨架生成',
            status: 'unobserved',
            durationMs: null,
          },
        ],
      },
    ],
    timeline: [],
    execution: {
      summary: {
        total: 0,
        passed: 0,
        failed: 0,
        fixme: 0,
        skipped: 0,
        passRate: '0%',
      },
    },
  });

  assert.equal(model.skills[0].phaseBreakdown.length, 0);
  assert.match(model.skills[0].runtimeEmptyState, /暂无可展示的 phase 或 runtime 归因数据/);
});
