# Greenhouse fixture provenance and live smoke

- Capture date: `2026-06-21`
- Endpoint: `https://boards-api.greenhouse.io/v1/boards/mongodb/jobs?content=true`
- Authentication: none; the request contained only an `Accept: application/json` header.
- Capture method: one-time Python standard-library `urllib.request` request outside CI.
- Response: HTTP `200`, with `424` jobs in both the returned `jobs` list and
  `meta.total`.
- Checked-in excerpt: job ID `7704173`, titled `Senior Staff Engineer`, with the
  first-party URL `https://www.mongodb.com/careers/job/?gh_jid=7704173`.

Post-implementation live smoke used:

- Company: MongoDB
- Keywords: `distributed systems`
- Seniority: `staff`
- Location: `Bengaluru`
- Result count: `2`
- Both results had HTTPS URLs under `mongodb.com`, non-empty plain-text job
  descriptions, and timezone-qualified `updated_at` values.

No API key, cookie, authorization header, account identifier, or other secret was
used or recorded. Unit tests never replay this request; they patch `urlopen` and
read `greenhouse.json` only.
