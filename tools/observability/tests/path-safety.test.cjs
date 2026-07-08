'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

const {
  resolveRepoPath,
  validateProductId,
} = require('../lib/path-safety.cjs');

test('validateProductId rejects path traversal input', () => {
  assert.throws(() => validateProductId('../../windows/system32'), /非法产品目录名/);
  assert.equal(validateProductId('demo-product'), 'demo-product');
});

test('resolveRepoPath rejects symlink or junction escapes', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'obs-root-'));
  const outside = fs.mkdtempSync(path.join(os.tmpdir(), 'obs-outside-'));
  const linkPath = path.join(root, 'products-link');
  const outsideFile = path.join(outside, 'secret.txt');

  fs.writeFileSync(outsideFile, 'secret', 'utf8');
  fs.symlinkSync(outside, linkPath, process.platform === 'win32' ? 'junction' : 'dir');

  assert.throws(() => resolveRepoPath(root, 'products-link/secret.txt'), /越界/);
  assert.throws(() => resolveRepoPath(root, 'products-link/missing/future.txt'), /越界/);
});

test('resolveRepoPath keeps resolved file inside repo root', () => {
  const repoRoot = 'D:\\huizecode\\probation\\e2e-test';
  const safePath = resolveRepoPath(repoRoot, 'products/demo-product/tc-gen/test-cases-final.md');
  assert.match(safePath, /products[\\/]+demo-product/);

  assert.throws(() => resolveRepoPath(repoRoot, '../../outside.txt'), /越界/);
});
