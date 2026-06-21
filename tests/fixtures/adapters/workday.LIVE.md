# Workday fixture provenance and live smoke

- Capture date: `2026-06-21`
- Tenant/site: `browserstack:External`
- Host: `browserstack.wd3.myworkdayjobs.com`
- List endpoint: `POST https://browserstack.wd3.myworkdayjobs.com/wday/cxs/browserstack/External/jobs`
- List body: `{"limit": 2, "offset": 0, "searchText": ""}`
- Detail endpoint: `GET .../wday/cxs/browserstack/External/job/Mumbai-Remote/Account-Manager---Strategic-Sales_JR103397`
- Authentication: none
- Live list response: HTTP `200`, `total=17`
- Detail response: HTTP `200`; exact `startDate`, `timeType`, HTML job
  description, location, and first-party `externalUrl`

The `tenant:site` token and configured `*.myworkdayjobs.com` host are both
required; the adapter does not guess either. Unit tests patch all HTTP access.
