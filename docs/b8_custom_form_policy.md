# B8C — Resolución de política para formularios custom

B8B dejó tres fuentes en `B8_POLICY_REVIEW`.

B8C evita asumir que todo formulario POST es una consulta segura.

## ATC

Se cierra como `RESOLVED_NO_PUBLIC_DATA_SCOPE`.

Los formularios públicos observados son de soporte/solicitudes y el Portal de Comercios requiere afiliación/autenticación. B8C no intenta login ni envía solicitudes.

## CADEXCO

El formulario detectado en raíz no se usa.

Se prueban únicamente páginas públicas GET con contenido de estadísticas/publicaciones:

- `/publicaciones/`
- `/cochabamba/`
- `/inteligencia/`
- `/lcoext/`
- `/ltributaria/`

Si exponen recursos, CADEXCO puede volver al workflow HTML genérico usando seeds curados.

## TRANSTATS

La documentación oficial describe selección interactiva y descarga CSV.

B8C carga la interfaz y captura:

- method;
- action;
- campos;
- opciones;
- texto;
- señales de consulta;
- señales de mutación.

No envía POST.

Si encuentra un POST same-site con semántica clara de consulta y sin señales de mutación, queda como `READ_ONLY_POST_QUERY_CANDIDATES`. B8D podrá allowlistear solamente esas acciones.
