# Reproduce a frozen financial dataset locally

These commands package already-frozen market and financial inputs, verify an
archive, and recompute its financial states. They do not fetch data, fit an
estimator, publish a hosted dataset or enable trading. Install the locked Python
dependencies as described in the repository README first.

Use a recipe with exactly these fields. Paths are local and resolve relative to
the recipe file; the input JSON files must already use their canonical frozen
representation. The market file contains schemaVersion, rows and provenance.
The financial package is the canonical download from the financial workspace.
The preparedRoot is the previously generated preparation's identity.

```json
{
  "scope": {
    "symbols": ["000651.SZ", "600690.SH"],
    "start": "20250101",
    "end": "20251231"
  },
  "market": "market.json",
  "marketCalendarRef": "11111111-1111-4111-8111-111111111111",
  "financialInputs": [
    {
      "package": "financial.json",
      "preparedRoot": "REPLACE_WITH_64_HEX_PREPARED_ROOT",
      "calendarRef": "11111111-1111-4111-8111-111111111111",
      "proofRefs": []
    }
  ]
}
```

The UUID and root above are placeholders, not registered authority. Financial
inputs must retain the exact matching references; proofRefs must be sorted and
unique. Include one to eight packages with no conflicting security/state
coverage. The frozen scope is sorted and exact; provenance may preserve its
original acquisition order without changing source bytes.

Provide a separate pins file mapping each required registry UUID to its actual
local evidence file. Relative evidence paths resolve against this pins file.
The map must contain exactly the required calendar/proof closure. Get these
records from the authorized operator or another independently assessed source;
an archive cannot authenticate records copied from itself.

```json
{
  "11111111-1111-4111-8111-111111111111": "registry/calendar.json"
}
```

From the checkout, create a new private archive:

```sh
.venv/bin/python scripts/pack-dataset.py \
  --recipe research/recipe.json --registry-pins research/pins.json \
  --output research/dataset.tar
```

The JSON receipt includes datasetRoot. Preserve it separately when transferring
files. Verify and extract with that exact root, then recompute using the external
pins:

```sh
.venv/bin/python scripts/extract-dataset.py research/dataset.tar \
  --dataset-root "$DATASET_ROOT" --output research/extracted
.venv/bin/python scripts/recompose-dataset.py research/extracted \
  --dataset-root "$DATASET_ROOT" --registry-pins research/pins.json
```

Set DATASET_ROOT to the receipt's actual value. Existing output files and
directories are preserved, including concurrent destination creation. Failed
composition publishes no archive. Private staging is removed on failure; final
files use mode 0600 and extracted directories use 0700. URLs, arbitrary provider
arguments, duplicate configuration keys and unknown recipe fields are rejected.
All existing component and aggregate byte limits apply.

`extract-dataset.py` proves transport integrity only. `recompose-dataset.py`
compares external registry bytes, runs the production financial core and requires
all reconstructed components and roots to match. A matching pin does not prove
the underlying report authentic; a report hash does not include its PDF. The
receipt therefore keeps originalDocumentAuthorityVerified=false. Use the
independent standard-library audit in addition to core recomposition when it is
available in your checkout; this CLI is not an independent formula implementation.

The command tests use explicit synthetic inputs and actual subprocesses. A
separate private acceptance on the previously frozen two-company evidence
produced the same seven-component, 486-row, 1,067,520-byte archive as the library,
then extracted and recomposed it without provider calls or F fitting. No licensed
raw provider response, annual report PDF or private registry file is distributed
with this example.
