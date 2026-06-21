# Amazon Jobs fixture verification

Verified live on 2026-06-21 against Amazon's public careers site.

- Endpoint: `GET https://www.amazon.jobs/en/search.json`
- Verified query: `base_query=software development engineer`
- Verified country facet: `normalized_country_code[]=IND`
- Pagination: `offset=0&result_limit=10`
- Response: HTTP 200, JSON, 336 hits, 10 returned jobs, all sampled jobs had
  `country_code=IND`
- Fixture posting: iCIMS ID `10454374`, “Software Development Engineer,”
  Bengaluru, posted June 19, 2026
- Public posting URL returned HTTP 200:
  `https://www.amazon.jobs/en/jobs/10454374/software-development-engineer`
- First-party apply URL returned HTTP 200 and redirected to Amazon Jobs Passport:
  `https://account.amazon.jobs/jobs/10454374/apply`

Important contract finding: `loc_query=India` alone did not constrain the JSON
response. Amazon's current search client serializes its country filter as
`normalized_country_code[]`, so the adapter uses the verified ISO-3 facet.

`amazon.json` is a response excerpt containing the first returned posting and
the source-provided fields used by the adapter. Unit tests never access the
network.
