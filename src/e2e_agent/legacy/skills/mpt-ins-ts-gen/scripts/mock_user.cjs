#!/usr/bin/env node
'use strict';

const FAMILY_NAMES = ['赵', '钱', '孙', '李', '周', '吴', '郑', '王', '冯', '陈', '杨', '曹', '石', '高'];
const GIVEN_NAMES = ['子轩', '浩然', '雨辰', '梓涵', '诗雨', '佳怡', '思源', '文博', '明轩', '嘉豪'];
const REGION_BY_KEY = {
  北京: ['北京市', '市辖区', ['东城区', '西城区', '朝阳区', '海淀区']],
  上海: ['上海市', '市辖区', ['黄浦区', '徐汇区', '浦东新区']],
  广东: ['广东省', '广州市', ['越秀区', '天河区', '海珠区']],
};

function argValue(name, fallback = '') {
  const flag = `--${name}`;
  const index = process.argv.indexOf(flag);
  if (index >= 0 && index + 1 < process.argv.length) return process.argv[index + 1];
  return fallback;
}

function hashText(text) {
  let hash = 2166136261;
  for (const char of String(text || '')) {
    hash ^= char.codePointAt(0);
    hash = Math.imul(hash, 16777619);
  }
  return hash >>> 0;
}

function pad(value, length) {
  return String(value).padStart(length, '0');
}

function formatDate(date) {
  return `${date.getFullYear()}-${pad(date.getMonth() + 1, 2)}-${pad(date.getDate(), 2)}`;
}

function addYears(date, years) {
  const next = new Date(date.getTime());
  next.setFullYear(next.getFullYear() + years);
  return next;
}

function checksumId(body17) {
  const weights = [7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2];
  const checks = ['1', '0', 'X', '9', '8', '7', '6', '5', '4', '3', '2'];
  const sum = Array.from(body17).reduce((total, digit, index) => total + Number(digit) * weights[index], 0);
  return checks[sum % 11];
}

function seededNumber(seed, min, max) {
  const normalized = Math.abs(Number(seed) || 0);
  return min + (normalized % (max - min + 1));
}

function buildIdNo({ birthDate, gender, seed }) {
  const sequenceBase = seededNumber(seed, 100, 998);
  const oddSequence = sequenceBase % 2 === 1 ? sequenceBase : sequenceBase + 1;
  const evenSequence = sequenceBase % 2 === 0 ? sequenceBase : sequenceBase + 1;
  const sequence = gender === '女' ? evenSequence : oddSequence;
  const body = `110105${birthDate.replace(/-/g, '')}${pad(sequence, 3)}`;
  return `${body}${checksumId(body)}`;
}

function generateUser() {
  const scenario = argValue('scenario', 'self');
  const gender = argValue('gender', '男') === '女' ? '女' : '男';
  const age = Number.parseInt(argValue('age', '32'), 10) || 32;
  const regionKey = argValue('region', '北京');
  const bank = argValue('bank', '工商银行') || '工商银行';
  const seed = hashText(`${Date.now()}-${process.pid}-${scenario}-${gender}-${age}-${regionKey}-${bank}`);
  const name = `${FAMILY_NAMES[seededNumber(seed, 0, FAMILY_NAMES.length - 1)]}${GIVEN_NAMES[seededNumber(seed >>> 4, 0, GIVEN_NAMES.length - 1)]}`;
  const today = new Date();
  const birthDate = new Date(today.getFullYear() - age, seededNumber(seed >>> 8, 0, 11), seededNumber(seed >>> 12, 1, 26));
  const idNo = buildIdNo({ birthDate: formatDate(birthDate), gender, seed });
  const regionParts = REGION_BY_KEY[regionKey] || REGION_BY_KEY['北京'];
  const district = regionParts[2][seededNumber(seed, 0, regionParts[2].length - 1)];
  const cityText = `${regionParts[0]}-${regionParts[1]}-${district}`;
  const mobilePrefix = ['138', '139', '150', '151', '158', '160', '187'][seededNumber(seed, 0, 6)];
  const mobile = `${mobilePrefix}${pad(seededNumber(seed >>> 6, 10000000, 99999999), 8)}`;
  const account = `621226${pad(seededNumber(seed >>> 10, 1000000000000, 9999999999999), 13)}`;
  const validStart = addYears(today, -2);
  const validEnd = addYears(today, 18);

  return {
    scenario,
    relation: '本人',
    applicant: {
      姓名: name,
      性别: gender,
      出生日期: formatDate(birthDate),
      年龄: age,
      证件类型: '居民身份证',
      证件号码: idNo,
      '证件有效期(起始)': formatDate(validStart),
      '证件有效期(截止)': formatDate(validEnd),
      '证件有效期(类型)': '短期',
      手机号: mobile,
      邮箱: `${seed}@qq.com`,
      居住省市: cityText,
      地址: `${regionParts[0]}${regionParts[1]}${district}测试路${seed % 300}号`,
      邮政编码: pad(seededNumber(seed >>> 14, 100000, 999999), 6),
      '身高(cm)': seededNumber(seed >>> 16, 165, 180),
      '体重(kg)': seededNumber(seed >>> 18, 55, 85),
      '年收入(万元)': seededNumber(seed >>> 20, 20, 80),
      银行: bank,
      银行卡号: account,
    },
    beneficiaries: '法定',
  };
}

if (require.main === module) {
  process.stdout.write(`${JSON.stringify(generateUser(), null, 2)}\n`);
}

module.exports = { generateUser };
