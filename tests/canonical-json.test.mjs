import test from "node:test";
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { parseStrictJson } from "../edge/bundles/json.mjs";

test("canonical scanner retains Python numeric bytes including small exponents and large integral floats", () => {
  const run = spawnSync(
    "python3",
    [
      "-c",
      `import json,random,math,struct
rng=random.Random(4167)
values=[1e-4,1e-5,1e-6,1e-7,5e-324,-0.0,9007199254740992.,1e23]+[struct.unpack('!d',rng.randbytes(8))[0] for _ in range(10000)]
for value in values:
 if math.isfinite(value):
  if value.is_integer(): value=int(value)
  print(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False))
`,
    ],
    { encoding: "utf8", maxBuffer: 8 * 1024 * 1024 },
  );
  assert.equal(run.status, 0, run.stderr);
  const values = run.stdout.trim().split("\n");
  assert.ok(values.length > 9900);
  for (const raw of values)
    assert.doesNotThrow(() => parseStrictJson(raw), raw);
});

test("canonical scanner rejects alternate encodings even when a producer recalculates every hash", () => {
  for (const raw of [
    "1.0",
    "-0",
    "1e-5",
    "0.00001",
    "1.00",
    "1e-400",
    '{"b":1,"a":2}',
    '{"a":1,"a":2}',
    '"\\u0061"',
    '"\\ud800"',
    '{"\\u0074oken":"test"}',
  ])
    assert.throws(() => parseStrictJson(raw), raw);
  assert.deepEqual(parseStrictJson('{"a":1,"中":2,"😀":3}'), {
    a: 1,
    中: 2,
    "😀": 3,
  });
  // Unicode code-point ordering differs from UTF-16 ordering for astral keys.
  assert.deepEqual(parseStrictJson('{"￿":1,"😀":2}'), { "￿": 1, "😀": 2 });
  assert.throws(() => parseStrictJson('{"😀":2,"￿":1}'));
});
