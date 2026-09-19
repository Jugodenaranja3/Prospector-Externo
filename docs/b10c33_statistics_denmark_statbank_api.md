# B10C.3.3 — Statistics Denmark / StatBank API

B10C.3.2 corrigió correctamente el delta CRLF/LF, pero su localizador textual
asumía que cada item YAML comenzaba con `- source_id:`. La configuración real
no garantiza ese orden: algunos items comienzan con otra clave y contienen
`source_id` después.

Esta revisión localiza el item por fronteras reales de la lista YAML y por el
valor `source_id: statistics_denmark`, sin asumir que `source_id` sea la
primera clave.

Solo se vuelve a serializar ese único item; los otros 34 items se preservan
byte por byte.

Configuración resultante:

- workflow: `api`
- entrypoint: `https://api.statbank.dk/v1/tables?lang=en`
- seed: el mismo endpoint
- allowed host adicional: `api.statbank.dk`

El rerun posterior debe usar `--resume --rerun-empty`, porque el estado
actual de Statistics Denmark es `EMPTY_REVIEW`.
