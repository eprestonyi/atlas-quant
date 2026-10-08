# Independent financial forecast bundle audit

Run the standalone standard-library auditor from the repository root:

```sh
python3 scripts/audit-financial-bundle.py private/result-bundle \
  --source-dataset private/source-dataset.tar \
  --registry-pins private/pins.json \
  --output private/financial-audit.json
```

The result bundle can be a directory or the strict Atlas USTAR download. The
source dataset can likewise be a directory or typed archive. Both files are
private research inputs; the command does not upload them. The optional pins
file maps registry UUIDs to local files, resolved relative to the pins file.
Supply only independently authorized registry bytes. Output files must not
already exist and are created with mode `0600`.

The audit independently checks canonical encodings, every chunk and document
hash, bounded layout and record references, complete planned forecast origins,
and recorded forecast arithmetic. With the source dataset it also checks its
complete typed closure, exact scope and ordered rows, provenance and financial
commitment. Dataset version 2 additionally checks the retained original market
snapshot and explicit scope projection. Numeric types and signed zero in the
financial snapshot are preserved during comparison.

Exit codes are `0` for a complete checked closure, `2` for an intact transport
without the required source dataset, and `1` for invalid inputs. A successful
closure check without independent pins remains `registryTrustStatus:unverified`.
Matching pins means exact external registry bytes matched, not that a filing or
PDF was authenticated. Inspect the machine-readable status and limitations;
`PASS` is limited to the checks this auditor performs.

The auditor imports no numerical engine, calls no provider, fits no model, and
does not recompute financial formulas, pandas research fingerprints, or past
provider fingerprints. Those limits remain explicit in every successful
result. Financial execution and replay remain disabled. Legacy bundle/1 audits
continue to use `scripts/audit-bundle.py` and do not accept this new format.
