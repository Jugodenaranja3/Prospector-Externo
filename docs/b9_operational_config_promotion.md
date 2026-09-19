# B9B.2 — Promoción del config operacional

El audit B9B.1 confirmó que `config/sources.yaml` solo contenía tres fuentes físicas históricas: FINRURAL, BBV y BCB.

B9B.2 construye la configuración física completa a partir de `config/source_operational_plan.yaml`.

## Modelo lógico vs físico

El plan tiene 41 fuentes lógicas operacionales, pero varias comparten endpoint físico.

Ejemplos:

- APS + APS/SOAT
- ASFI-Valores + ASFI + ASFI-FINRURAL
- BCB + ASFI-BCB
- ICCO + FDTA-Valles
- MDRyT/OAP + MDRyT

Por eso se generan dos artefactos:

- `config/sources.yaml` → fuentes físicas ejecutables;
- `config/source_execution_map.yaml` → relación fuente lógica → config físico.

El checkpoint sigue siendo lógico; el crawler recibe una configuración física de una sola fuente.

## Seguridad

La promoción:

1. preserva intactas las entradas existentes cuando ya representan el endpoint;
2. usa FINRURAL como template genérico para campos comunes;
3. deriva `workflow`, entrypoint y seeds del plan B8;
4. valida 41/41 mappings antes de reemplazar el archivo real;
5. ejecuta la suite global;
6. no hace red.

Los workflows JavaScript y custom quedan representados en el config. La validación final de ejecución de esos workflows se hará antes de B10.
