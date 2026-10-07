import { ApiError } from '../errors.mjs';
import { BUNDLE_PROFILE, SENSITIVE_KEY } from './profile.mjs';

const fail = (message) => {
  throw new ApiError('BUNDLE_JSON', message);
};
const encoder = new TextEncoder();
const compareUnicode = (a, b) => {
  const left = Array.from(a),
    right = Array.from(b);
  for (let i = 0; i < Math.min(left.length, right.length); i++) {
    const delta = left[i].codePointAt(0) - right[i].codePointAt(0);
    if (delta) return delta;
  }
  return left.length - right.length;
};
function canonicalNumber(token) {
  // Integers may exceed JS's safe-integer range. Preserve their exact decimal
  // token; converting them through Number would silently change canonical bytes.
  if (/^-?(?:0|[1-9]\d*)$/.test(token)) return token !== '-0';
  const number = Number(token);
  if (!Number.isFinite(number) || Number.isInteger(number)) return false;
  const sign = number < 0 ? '-' : '';
  let text = Math.abs(number).toString();
  if (Math.abs(number) < 0.0001) {
    let mantissa, exponent;
    if (text.includes('e')) [mantissa, exponent] = text.split('e');
    else {
      const digits = text.slice(2),
        first = digits.search(/[1-9]/);
      const significant = digits.slice(first);
      mantissa = significant[0] + (significant.length > 1 ? '.' + significant.slice(1) : '');
      exponent = String(-first - 1);
    }
    const e = Number(exponent);
    text = mantissa + 'e' + (e < 0 ? '-' : '+') + String(Math.abs(e)).padStart(2, '0');
  }
  return token === sign + text;
}
function wellFormed(value) {
  for (let i = 0; i < value.length; i++) {
    const n = value.charCodeAt(i);
    if (n >= 0xd800 && n <= 0xdbff) {
      const next = value.charCodeAt(++i);
      if (!(next >= 0xdc00 && next <= 0xdfff)) return false;
    } else if (n >= 0xdc00 && n <= 0xdfff) return false;
  }
  return true;
}
export const byteLength = (text) => encoder.encode(text).byteLength;
export function object(value) {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}
export function keys(value, allowed, label) {
  if (!object(value) || Object.keys(value).some((key) => !allowed.includes(key))) {
    fail(`${label}结构包含未定义字段`);
  }
}

/** Validate before JSON.parse: duplicate keys must never hide an earlier value.
 * The scanner retains raw numeric spelling; hashes always cover original bytes.
 */
export function parseStrictJson(text, { canonical = true, sensitive = true } = {}) {
  if (typeof text !== 'string') fail('分片须为 UTF-8 JSON 文本');
  let position = 0;
  const numberPattern = /-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?/y;
  const whitespace = () => {
    const start = position;
    while (/\s/.test(text[position] ?? '') && position < text.length) position++;
    if (canonical && position !== start) fail('分片不能含非规范空白');
  };
  const string = () => {
    const start = position++;
    while (position < text.length) {
      const character = text[position++];
      if (character === '"') {
        try {
          const raw = text.slice(start, position),
            decoded = JSON.parse(raw);
          if (!wellFormed(decoded) || (canonical && JSON.stringify(decoded) !== raw))
            fail('JSON 字符串不符合规范编码');
          return decoded;
        } catch {
          fail('JSON 字符串无效');
        }
      }
      if (character === '\\') position++;
    }
    fail('JSON 字符串未结束');
  };
  function value(depth = 0) {
    if (depth > BUNDLE_PROFILE.jsonDepth) fail('JSON 嵌套超过上限');
    whitespace();
    const character = text[position];
    if (character === '"') {
      string();
      return;
    }
    if (character === '{') {
      position++;
      whitespace();
      const observed = new Set();
      let previousKey = null;
      if (text[position] === '}') {
        position++;
        return;
      }
      for (;;) {
        if (text[position] !== '"') fail('JSON 对象键无效');
        const key = string();
        if (observed.has(key)) fail('JSON 对象含重复键');
        if (canonical && previousKey !== null && compareUnicode(previousKey, key) >= 0)
          fail('JSON 对象键未按 Unicode 码点排序');
        previousKey = key;
        if (sensitive && SENSITIVE_KEY.test(key)) fail('研究产物含凭据字段');
        observed.add(key);
        whitespace();
        if (text[position++] !== ':') fail('JSON 对象缺少冒号');
        value(depth + 1);
        whitespace();
        const end = text[position++];
        if (end === '}') return;
        if (end !== ',') fail('JSON 对象分隔符无效');
        whitespace();
      }
    }
    if (character === '[') {
      position++;
      whitespace();
      if (text[position] === ']') {
        position++;
        return;
      }
      for (;;) {
        value(depth + 1);
        whitespace();
        const end = text[position++];
        if (end === ']') return;
        if (end !== ',') fail('JSON 数组分隔符无效');
      }
    }
    for (const literal of ['true', 'false', 'null']) {
      if (text.startsWith(literal, position)) {
        position += literal.length;
        return;
      }
    }
    numberPattern.lastIndex = position;
    const token = numberPattern.exec(text)?.[0];
    if (!token || !Number.isFinite(Number(token))) fail('JSON 需要有限数值');
    if (canonical && !canonicalNumber(token)) fail('JSON 数字不符合原 canonical 编码');
    position += token.length;
  }
  value();
  whitespace();
  if (position !== text.length) fail('JSON 文档包含尾随内容');
  try {
    return JSON.parse(text);
  } catch {
    fail('JSON 文档无效');
  }
}

/** Enforce bytes while reading, independently of Content-Length. */
export async function readBoundedText(request, maximum) {
  const declared = Number(request.headers.get('content-length') ?? 0);
  if (declared > maximum) throw new ApiError('BUNDLE_BUDGET', '分片请求超过字节上限', 413);
  const reader = request.body?.getReader();
  const decoder = new TextDecoder('utf-8', { fatal: true });
  let size = 0,
    text = '';
  try {
    if (reader)
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        size += value.byteLength;
        if (size > maximum) {
          await reader.cancel();
          throw new ApiError('BUNDLE_BUDGET', '分片请求超过字节上限', 413);
        }
        text += decoder.decode(value, { stream: true });
      }
    text += decoder.decode();
    return text;
  } catch (error) {
    if (error instanceof ApiError) throw error;
    throw new ApiError('BUNDLE_JSON', '分片必须是有效 UTF-8');
  } finally {
    reader?.releaseLock();
  }
}
