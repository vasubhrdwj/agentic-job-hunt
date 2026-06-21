# SmartRecruiters fixture provenance and live smoke

- Capture date: `2026-06-21`
- List endpoint: `GET https://api.smartrecruiters.com/v1/companies/Freshworks/postings?limit=100`
- Detail endpoint: `GET https://api.smartrecruiters.com/v1/companies/Freshworks/postings/744000132768109`
- Authentication: none
- Live list response: HTTP `200`, `totalFound=97`
- Checked-in excerpt: `Senior Software Engineer - Site Reliability`, Chennai,
  released `2026-06-18`
- Detail verification: first-party `postingUrl` and `applyUrl`, full HTML
  `jobAd.sections`, location, and employment type

The public `q` parameter was also verified live. No secret, cookie, or
authorization header was used; unit tests are fully hermetic.
