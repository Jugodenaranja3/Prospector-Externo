# B8D — TranStats como recurso de formulario de adquisición

B8C confirmó que TranStats usa formularios POST de consulta, pero el Prospector no necesita ejecutar la descarga para descubrir el recurso.

La arquitectura SOURCE → FILE permite que FILE represente un trabajo/configuración de adquisición y no necesariamente un archivo físico ya descargado.

Por eso B8D modela cada página pública:

`/DL_SelectFields.aspx?...`

como un recurso de adquisición.

## Política

- workflow: `custom`
- discovery: GET/HEAD
- form POST: no se envía
- login: no
- captcha/WAF bypass: no
- descarga CSV durante discovery: no

El downstream de adquisición puede implementar la descarga CSV en una etapa separada y explícita.

## Smoke live

```powershell
python -m apps.transtats_form_resource_probe.main `
  --policy .\config\transtats_custom_resource_policy.yaml `
  --output-dir .\.runtime\transtats_form_resource_probe
```

El smoke confirma que una página pública `DL_SelectFields.aspx` contiene señales suficientes de un trabajo de descarga: instrucciones, filtros temporales y campos seleccionables.
