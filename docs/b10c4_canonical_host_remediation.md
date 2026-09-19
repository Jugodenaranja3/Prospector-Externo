# B10C.4 — Canonical host remediation

Live diagnostics showed five remaining failures were caused by canonical-host
redirects rather than extraction logic.

Observed from the DATAX workstation:

- `www.ruralytierras.gob.bo` -> `ruralytierras.gob.bo`
- `www.asofinbolivia.com` -> `asofinbolivia.com`
- `www.data.gov` -> `data.gov`
- `www.ibce.org.bo` -> `ibce.org.bo`
- `www.senamhi.gob.bo` -> `senamhi.gob.bo`

The bare hosts were reachable and their robots behavior was usable for the
crawler. This batch promotes those canonical hosts and explicitly permits both
official host aliases so that legitimate same-site redirects do not become
`REDIRECT_OUT_OF_SCOPE`.

`mdryt` is a logical source sharing the `mdryt_oap` physical configuration, so
only the physical config is changed.

Not changed in this batch:

- `mhe`: local TLS verification fails; no TLS verification bypass is added.
- `sigma`: `/robots.txt` returns HTTP 500 on both host variants; no robots
  bypass is added.

Data.gov remains on the existing `api` workflow. This batch only removes the
www->bare robots redirect failure; API strategy is evaluated again by the live
rerun.
