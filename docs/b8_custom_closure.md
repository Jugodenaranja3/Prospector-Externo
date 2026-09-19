# B8E — Cierre de Custom Workflows

B8E consolida B8B, B8C y B8D y elimina `B8_CUSTOM` del plan 52/52.

Quedan operacionales tres fuentes custom:

- FIFA → `OPERATIONAL_CUSTOM_DATA_API`
- CADEXCO → `OPERATIONAL_HTTP_HTML_CURATED`
- TRANSTATS → `OPERATIONAL_CUSTOM_FORM_RESOURCE`

TranStats conserva `submission_policy: metadata_only_no_post`: discovery identifica el trabajo de adquisición sin ejecutar el POST de descarga.

Cinco fuentes B8 pasan a estado explícito para B10:

- FEGASACRUZ → `NO_PUBLIC_DATA_EVIDENCE`
- FAM → `NO_PUBLIC_DATA_EVIDENCE`
- IBCH → `NO_PUBLIC_DATA_EVIDENCE`
- BOLCEREALES → `IDENTITY_UNRESOLVED`
- ATC → `NO_PUBLIC_DATA_SCOPE`

Resultado esperado:

- 41 → `OPERATIONAL_CONFIG`
- 11 → `B10_STATUS`
- 0 → `B8_CUSTOM`

La resolución queda versionada en `config/custom_resolution.yaml` y `config/source_operational_plan.yaml` se actualiza como plan canónico después de B8.
