# Workable fixture provenance and live smoke

- Capture date: `2026-06-21`
- Account: `peoplecert`
- List endpoint: `POST https://apply.workable.com/api/v3/accounts/peoplecert/jobs`
- Detail endpoint: `GET https://apply.workable.com/api/v2/accounts/peoplecert/jobs/AD5788CFCA`
- Authentication: none
- Live list response: HTTP `200`, `total=35`, ten results and an opaque
  `nextPage` token
- Pagination verification: the next request must send that value as JSON field
  `token`; it returned the next ten distinct jobs
- Checked-in excerpt: `HTML CSS Developer`, Athens, published `2026-06-18`
- Canonical public URL verified live:
  `https://apply.workable.com/peoplecert/j/AD5788CFCA/`

Important correction: the v3 list route returns `404` for `GET`; it is a JSON
`POST` endpoint. No secret, cookie, or authorization header was used. Unit
tests patch all HTTP access.
