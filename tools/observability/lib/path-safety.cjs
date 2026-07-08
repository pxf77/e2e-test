'use strict';

const fs = require('node:fs');
const path = require('node:path');

const PRODUCT_ID_PATTERN = /^[a-z0-9]+(-[a-z0-9]+)*$/;

function validateProductId(productId) {
  if (typeof productId !== 'string' || !productId.length) {
    throw new Error('非法产品目录名: 空值');
  }
  if (productId.includes('..') || productId.includes('/') || productId.includes('\\')) {
    throw new Error('非法产品目录名: 不得包含路径片段');
  }
  if (!PRODUCT_ID_PATTERN.test(productId)) {
    throw new Error(`非法产品目录名: ${productId}`);
  }
  return productId;
}

function resolveFollowingSymlinks(rootReal, relativePath) {
  const joined = path.resolve(rootReal, relativePath);
  try {
    return fs.realpathSync.native ? fs.realpathSync.native(joined) : fs.realpathSync(joined);
  } catch {
    const relFromRoot = path.relative(rootReal, joined);
    if (
      !relFromRoot ||
      relFromRoot === '.' ||
      relFromRoot.startsWith(`..${path.sep}`) ||
      relFromRoot === '..'
    ) {
      return joined;
    }
    const parts = relFromRoot.split(path.sep).filter(Boolean);
    let current = rootReal;
    for (let i = 0; i < parts.length; i++) {
      const next = path.join(current, parts[i]);
      if (!fs.existsSync(next)) {
        return path.join(current, ...parts.slice(i));
      }
      try {
        current = fs.realpathSync.native ? fs.realpathSync.native(next) : fs.realpathSync(next);
      } catch {
        return path.join(current, ...parts.slice(i));
      }
    }
    return current;
  }
}

function resolveRepoPath(repoRoot, relativePath) {
  const rootResolved = path.resolve(repoRoot);
  let rootReal;
  try {
    rootReal = fs.realpathSync.native ? fs.realpathSync.native(rootResolved) : fs.realpathSync(rootResolved);
  } catch {
    rootReal = rootResolved;
  }
  const joinedReal = resolveFollowingSymlinks(rootReal, relativePath);
  const rel = path.relative(rootReal, joinedReal);
  const inside = rel === '' || (!rel.startsWith(`..${path.sep}`) && rel !== '..' && !path.isAbsolute(rel));
  if (!inside) {
    throw new Error(`路径越界: ${relativePath}`);
  }
  return joinedReal;
}

module.exports = {
  resolveRepoPath,
  validateProductId,
};
