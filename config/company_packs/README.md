# Company pack catalog

These YAML files are user-selectable source inventories. Each record names one
company and the public ATS board used by an existing source adapter. Records do
not embed, infer, or manufacture job openings; the scan worker reads the live
board and stores only validated public posting facts.

| Pack | Entries | Description |
| --- | ---: | --- |
| `backend_india.yaml` | 35 | India backend and location-compatible remote supply |
| `ai_ml.yaml` | 35 | AI/ML, model serving, developer tooling, and data infrastructure |
| `global_remote.yaml` | 35 | Remote-friendly engineering companies; country rules remain posting-specific |
| `fintech.yaml` | 43 | Payments, banking, investing, crypto infrastructure, fraud, and risk |

There are 148 pack entries and 114 distinct company/board identities. Deliberate
cross-pack overlap makes focused packs useful without forcing users to scan an
unrelated catalog. A repeated slug must resolve to the same source and token in
every pack; the registry test suite enforces this.

## Curation rules

- Use only source types already implemented in `job_hunt_agent/sources/`.
- Copy the public board token from the ATS URL or API; never guess a token.
- Trust only the ATS or company-owned hostname that currently publishes the
  application URL.
- Keep a company active only while its board passes the strict live verifier.
- Treat `hire_locations` as discovery metadata, not an eligibility claim. The
  posting-level country and location checks remain authoritative.
- Do not add job titles, descriptions, or opening counts to these files.

Validate a pack offline after every edit:

```bash
.venv/bin/python scripts/verify_registry.py --pack ai_ml
```

Before deployment, probe the public board and its trusted URLs:

```bash
.venv/bin/python scripts/verify_registry.py --pack ai_ml --live --strict-live
```

Run the same commands for each changed pack. Public boards can move or close, so
a successful live check is evidence at verification time rather than a permanent
guarantee.
