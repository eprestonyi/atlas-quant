import test from 'node:test';
import assert from 'node:assert/strict';
import { registryDTO } from '../edge/financial/registry.mjs';

test('registry display metadata cannot replace the stored evidence identity', () => {
  for (const kind of ['calendar', 'unit_proof']) {
    const row = {
      id: '20c903fd-7839-40ed-98d8-4914b41ead31',
      kind,
      sha256: 'a'.repeat(64),
      byte_length: 512,
      metadata: JSON.stringify({
        ref: '8b634eb3-cea8-4699-8047-7d99e80ac0da',
        kind: 'untrusted_override',
        sha256: 'b'.repeat(64),
        byteLength: 0,
        label: 'Retained display label',
        coverageStart: '20240101',
      }),
    };
    const before = structuredClone(row);
    assert.deepEqual(registryDTO(row), {
      ref: row.id,
      kind,
      sha256: row.sha256,
      byteLength: row.byte_length,
      label: 'Retained display label',
      coverageStart: '20240101',
    });
    assert.deepEqual(row, before);
  }
});

test('registry identities survive absent or malformed optional display metadata', () => {
  for (const metadata of [null, 'not JSON', '{}']) {
    assert.deepEqual(
      registryDTO({ id: 'ref', kind: 'calendar', sha256: 'hash', byte_length: 9, metadata }),
      {
        ref: 'ref',
        kind: 'calendar',
        sha256: 'hash',
        byteLength: 9,
      }
    );
  }
});
