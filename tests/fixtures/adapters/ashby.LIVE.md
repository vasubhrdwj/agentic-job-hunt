# Ashby fixture provenance and live smoke

- Capture date: `2026-06-21`
- Endpoint: `GET https://api.ashbyhq.com/posting-api/job-board/Ashby`
- Authentication: none
- Live response: HTTP `200`; top-level keys `apiVersion`, `jobs`; `66` jobs
- Checked-in excerpt: job `188cc71b-a625-4022-94dc-7c43fa1a8b06`,
  `Design Engineer, EU`, published `2026-06-12`
- Verified fields: `applyUrl`, `jobUrl`, `descriptionPlain`, `publishedAt`,
  `employmentType`, primary and secondary locations

No secret, cookie, or authorization header was used. The unit test patches
`urlopen`; it never repeats the live request.
