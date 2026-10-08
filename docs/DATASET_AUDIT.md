# Independent dataset closure audit

`scripts/audit-dataset.py` audits the local `atlas.quant.research_dataset/1`
format described in [DATASET_COMPONENTS.md](DATASET_COMPONENTS.md). It uses only
Python's standard library. It does not import the production dataset reader,
financial engine, pandas, a provider, or a model. The tests use the production
writer only to create fixtures, then exercise this separate reader.

Run from the repository root with Python 3.12 or later:

```sh
python3 -S scripts/audit-dataset.py /private/path/dataset.tar \
  --expected-root <independently-recorded-dataset-root> \
  --output /private/path/new-audit.json
```

The input may also be an extracted dataset directory. The command is read-only;
it does not extract files. `--output` is optional, creates a new file with mode
0600, and never replaces an existing path. JSON is always printed to stdout.
Exit status is 0 for a passing audit, 1 for invalid input, and 2 if the optional
output cannot be created. A failed audit never returns a partial pass.

## External evidence pins

Without separate pins, a complete archive still reports
`trustStatus: "unverified"`. A root supplied with `--expected-root` pins byte
identity; it does not by itself authenticate registry authorship.

For separately authorized registry evidence, add `--registry-pins pins.json`.
The pins file is an exact mapping from UUID to local registry file:

```json
{
  "12345678-1234-1234-1234-123456789abc": "registry/calendar.json"
}
```

Relative filenames resolve against the pins file's directory. URLs, duplicate
references, missing/extra references and byte differences are rejected. Files
must be supplied independently of the archive by an authorized operator. The
auditor does not discover, authorize or fetch pins from the archive itself.

A match reports `trustStatus: "external_registry_bytes_matched"`. It **does not**
claim that a PDF is authentic, that a provider's reported values match a filing,
or that original disclosure and revision history have been established.
`sourceAuthorityVerified` and `pdfAuthenticityVerified` remain false.

## Checks performed

- Exact finite canonical UTF-8 JSON, including distinct identities for `1`,
  `1.0`, `0.0`, `-0.0` and `null`; duplicate keys and noncanonical bytes fail.
- Manifest shape, registered component types, component IDs, semantic-root
  links, dependencies to earlier components, maximum three-edge DAG depth,
  exact required closure and source/reference ordering.
- Every part's ordinal, length and SHA-256; each full component's byte length
  and SHA-256; descriptor component roots and the full manifest dataset root.
- The complete 64 MiB encoded closure ceiling, 256 KiB manifest, 512 KiB parts,
  256 parts, 32 components, 24 MiB market/package/joined bounds, and separate
  registry bounds. JSON container nesting is additionally capped at 64 before
  decoding. These are byte/structure limits, not a guarantee of peak Python RSS.
- Strict deterministic USTAR: manifest first, parts in manifest order, exact
  registered regular-file headers, checksum, mode 0600, zero uid/gid/mtime,
  zero padding and **exactly two EOF blocks**. Truncation, trailing bytes,
  symlinks, PAX/GNU metadata, path traversal, duplicate or extra members fail.
- Registry raw bytes and calendar scope; financial raw-input/package roots,
  normalized snapshot hashes, proof scope and coordinates, declaration status,
  prepared roots, event/lineage roots and retained dependency source values.
- Complete calendar/security/state panel, assignment coverage without gaps or
  overlaps, nonfuture event references, strictly later disclosure availability,
  decimal-to-float panel values, all coverage summary fields, retained missing
  reasons and left joins onto the existing market row keys.
- The joined dataset's financial root and field evidence, schema/coverage
  components, source classification and financial root references.

Archive member order must match its manifest. Valid alternative topological
component order is accepted when the manifest and complete dependency graph
agree; it is not confused with missing or reordered archive members.

## Deliberate limits

This is a transport, identity and evidence-closure audit. It does **not** rederive
all financial formulas or the internal normalized `StatementRecord.record_hash`;
it checks dependency coordinates, raw values and unit references against retained
source evidence. `financialFormulasRecomputed` is false. For actual financial
recomputation use the separate `recompose-dataset.py` command with independent
registry pins and compare its prepared/joined roots.

The historical provider `dataFingerprint` uses pandas-specific 12-digit JSON
rounding and is not recomputed here. Exact retained source/joined bytes and their
separate semantic roots are independently verified. Formula correctness,
original source accuracy, information available at the historical time, and
predictive usefulness require their own evidence. An internally consistent
fabrication cannot authenticate itself through hashes.

The auditor does not grant research admission, reconstruct process-local trust
markers, run a model, enable execution, or change the existing `bundle/1` format.
A valid financial dataset is not a validated forecast or executable strategy.
