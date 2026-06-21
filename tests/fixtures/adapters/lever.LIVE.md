# Lever fixture provenance and live smoke

- Capture date: `2026-06-21`
- Endpoint:
  `https://api.lever.co/v0/postings/palantir?mode=json&limit=1`
- Authentication: none; no API key, cookie, or authorization header was used.
- Capture method: one-time read-only request to Lever's public Postings API,
  outside the unit-test suite.
- Response count: `1` posting because the capture request specified `limit=1`.
- Checked-in fixture: `lever.json`.
- Fixture identity: posting ID `0bbfd4f4-41ff-4ec6-b73f-5200efd5d4d3`,
  titled `Administrative Business Partner - Security`.
- Fixture apply URL:
  `https://jobs.lever.co/palantir/0bbfd4f4-41ff-4ec6-b73f-5200efd5d4d3/apply`

The checked-in JSON is an exact field-preserving excerpt of that response for
the documented fields exercised by the adapter: `id`, `text`, `categories`,
`descriptionPlain`, `lists`, `hostedUrl`, and `applyUrl`. Unit tests never
replay the live request; they patch `urlopen` and read `lever.json` only.

## Live adapter invocation

On `2026-06-21`, `LeverAdapter.fetch_open_roles` was invoked directly with:

- Company: `Palantir` (`source_token="palantir"`)
- Role keywords: `["automation"]`
- Seniority: `mid`
- Locations: `["Palo Alto, CA"]`
- Employment types: `["full_time"]`
- Maximum age: disabled because Lever does not document a reliable posting date
- Country: `us`

The adapter returned `1` role:

- Title: `Administrative Business Partner - Security`
- Location: `Palo Alto, CA`
- Employment type: `full_time`
- Source: `lever`
- Confidence: `1.0`
- Trusted apply URL:
  `https://jobs.lever.co/palantir/0bbfd4f4-41ff-4ec6-b73f-5200efd5d4d3/apply`

The invocation used the production adapter path and made one unauthenticated
request to Lever's public API. It did not use the checked-in fixture.
