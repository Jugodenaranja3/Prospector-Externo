# B10C.5 — Data.gov public catalog surface

The B10 remediation reached Data.gov successfully after canonical-host
correction, but the existing `api` workflow returned `EMPTY_RESULT`.

Current Data.gov architecture exposes a newer Catalog API under
`api.gsa.gov/technology/datagov/v4/`. That API requires an `X-Api-Key`
header. The older CKAN-oriented endpoint is not a suitable new production
integration target, and the local probe returned HTTP 404.

The Prospector will not embed `DEMO_KEY`, manufacture credentials, or add an
authentication bypass merely to make the source green.

For unattended public discovery, this batch promotes the public catalog web
surface instead:

`https://catalog.data.gov/`

Changes for the physical `data_gov` source:

- workflow: `api` -> `html`
- entrypoint: `https://catalog.data.gov/`
- seed: same catalog URL
- allowed hosts include `catalog.data.gov`, `data.gov`, and `www.data.gov`

The normal HTML workflow can follow dataset detail pages and catalogue public
download/resource links while preserving robots policy and bounded discovery.

MHE and SIGMA are intentionally untouched:
- MHE: verified TLS/connectivity failure from the DATAX workstation.
- SIGMA: `/robots.txt` returns HTTP 500 on both tested host variants.
