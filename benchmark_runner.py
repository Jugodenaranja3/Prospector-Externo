"""
Script de evaluación y benchmark masivo de 52 fuentes externas.
Ejecuta el crawler tal cual para evaluar el comportamiento
y generar un reporte exhaustivo de diagnósticos, accesibilidad y recursos encontrados.
"""

import sys
import os
import json
import time
import logging
import traceback
from pathlib import Path
from urllib.parse import urlparse
import yaml

# Asegurar import de crawler
sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from crawler.sources.base_adapter import BaseSourceAdapter
from crawler.core.orchestrator import CrawlOrchestrator

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("benchmark")

SOURCES = [
    {"code": "ANAPO", "name": "Asociación de Productores de Oleaginosas y Trigo", "url": "https://www.anapobolivia.org"},
    {"code": "APS", "name": "Autoridad de Fiscalización y Control de Pensiones y Seguros", "url": "https://www.aps.gob.bo"},
    {"code": "ASFI-Valores", "name": "Autoridad de Supervisión del Sistema Financiero", "url": "https://www.asfi.gob.bo"},
    {"code": "BCB", "name": "Banco Central de Bolivia", "url": "https://www.bcb.gob.bo"},
    {"code": "BM", "name": "Banco Mundial", "url": "https://www.worldbank.org"},
    {"code": "CEPAL", "name": "Comisión Económica para América Latina y el Caribe", "url": "https://www.cepal.org"},
    {"code": "FEGASACRUZ", "name": "Federación de Ganaderos de Santa Cruz", "url": "https://www.fegasacruz.org.bo"},
    {"code": "FINRURAL", "name": "Finrural Bolivia", "url": "https://www.finrural.org.bo"},
    {"code": "FMI", "name": "Fondo Monetario Internacional", "url": "https://www.imf.org"},
    {"code": "MEFP", "name": "Ministerio de Economía y Finanzas Públicas", "url": "https://www.economiayfinanzas.gob.bo"},
    {"code": "MMYM", "name": "Ministerio de Minería y Metalurgia", "url": "https://www.mineria.gob.bo"},
    {"code": "OMC", "name": "Organización Mundial del Comercio", "url": "https://www.wto.org"},
    {"code": "Statistics Denmark", "name": "Danmarks Statistik", "url": "https://www.dst.dk"},
    {"code": "VIPFE", "name": "Viceministerio de Inversión Pública y Financiamiento Externo", "url": "https://www.vipfe.gob.bo"},
    {"code": "ADA", "name": "ADA Bolivia", "url": "https://www.adascz.com.bo/"},
    {"code": "ATC", "name": "Administradora de Tarjetas de Crédito", "url": "https://www.redenlace.com.bo"},
    {"code": "CADEXCO", "name": "Cámara de Exportadores de Cochabamba", "url": "https://www.cadexco.org.bo"},
    {"code": "CNDC", "name": "Comisión Nacional de Defensa de la Competencia", "url": "https://www.cndc.bo/"},
    {"code": "DGAC", "name": "Dirección General de Aeronáutica Civil", "url": "https://www.dgac.gob.bo"},
    {"code": "FAM", "name": "Federación de Asociaciones Municipales de Bolivia", "url": "https://www.fam.bo"},
    {"code": "FIFA", "name": "Federación Internacional de Fútbol Asociación", "url": "https://www.fifa.com"},
    {"code": "FUNDEMPRESA", "name": "Registro de Comercio de Bolivia", "url": "https://www.fundempresa.org.bo"},
    {"code": "ICCO", "name": "International Cocoa Organization", "url": "https://www.icco.org"},
    {"code": "ITU", "name": "International Telecommunication Union", "url": "https://www.itu.int"},
    {"code": "MDRyT/OAP", "name": "Ministerio de Desarrollo Rural y Tierras - OAP", "url": "https://www.ruralytierras.gob.bo"},
    {"code": "OTROS HISTORICOS", "name": "Doing Business (Banco Mundial)", "url": "https://www.doingbusiness.org"},
    {"code": "SABSA", "name": "Servicios de Aeropuertos Bolivianos S.A.", "url": "https://www.sabsa.aero"},
    {"code": "SICOES", "name": "Sistema de Contrataciones Estatales", "url": "https://www.sicoes.gob.bo"},
    {"code": "APS/SOAT", "name": "APS - Seguros SOAT", "url": "https://www.aps.gob.bo"},
    {"code": "ASFI", "name": "Autoridad de Supervisión del Sistema Financiero", "url": "https://www.asfi.gob.bo"},
    {"code": "ASFI - FINRURAL", "name": "ASFI / Finrural", "url": "https://www.asfi.gob.bo"},
    {"code": "BOLCEREALES", "name": "Bolcereales", "url": "https://www.bolsadecereales.org.bo"},
    {"code": "CEPROBOL", "name": "Centro Promoción Bolivia", "url": "https://www.ceprobol.gob.bo"},
    {"code": "FDTA-Valles", "name": "Fundación para el Desarrollo Tecnológico Agropecuario de los Valles", "url": "https://www.icco.org"},
    {"code": "IBCH", "name": "Instituto Boliviano del Cemento y Hormigón", "url": "https://www.ibch.org.bo"},
    {"code": "INE", "name": "Instituto Nacional de Estadística", "url": "https://www.ine.gob.bo"},
    {"code": "MHE", "name": "Ministerio de Hidrocarburos y Energías", "url": "https://www.hidrocarburos.gob.bo"},
    {"code": "SEPREC", "name": "Servicio Plurinacional de Registro de Comercio", "url": "https://www.seprec.gob.bo"},
    {"code": "UNDATA", "name": "United Nations Data", "url": "https://data.un.org"},
    {"code": "AE", "name": "Autoridad de Fiscalizacion de Electricidad dey Tecnologia Nuclear", "url": "https://aetn.gob.bo/"},
    {"code": "ASFI - BCB", "name": "ASFI / Banco Central de Bolivia", "url": "https://www.bcb.gob.bo"},
    {"code": "ASOFIN", "name": "Asociación de Instituciones Financieras de Microfinanzas de Bolivia", "url": "https://www.asofinbolivia.com"},
    {"code": "ATT", "name": "Autoridad de Regulación y Fiscalización de Telecomunicaciones y Transportes", "url": "https://www.att.gob.bo"},
    {"code": "BBV", "name": "Bolsa Boliviana de Valores", "url": "https://www.bbv.com.bo"},
    {"code": "Data.Gov", "name": "Portal de Datos Abiertos USA", "url": "https://www.data.gov"},
    {"code": "IBCE - CAO", "name": "Instituto Boliviano de Comercio Exterior / Cámara Agropecuaria del Oriente", "url": "https://www.ibce.org.bo"},
    {"code": "MDRyT", "name": "Ministerio de Desarrollo Rural y Tierras", "url": "https://www.ruralytierras.gob.bo"},
    {"code": "Min. Educacion", "name": "Ministerio de Educación", "url": "https://www.minedu.gob.bo"},
    {"code": "SENAMHI", "name": "Servicio Nacional de Meteorología e Hidrología", "url": "https://www.senamhi.gob.bo"},
    {"code": "SIGMA", "name": "Industria Farmacéutica Sigma", "url": "https://www.sigmacorp.com.bo"},
    {"code": "SNIS", "name": "Sistema Nacional de Información en Salud", "url": "https://snis.minsalud.gob.bo"},
    {"code": "TRANSTATS", "name": "US Bureau of Transportation Statistics", "url": "https://www.transtats.bts.gov"}
]


class DynamicBenchmarkAdapter(BaseSourceAdapter):
    """Adaptador dinámico para pruebas masivas en frío."""

    def __init__(self, source_id: str, source_name: str, url: str):
        parsed = urlparse(url)
        domain = parsed.netloc or parsed.path.split("/")[0]

        dummy_config = {
            "source": {
                "id": source_id,
                "name": source_name,
                "base_url": url,
                "allowed_domains": [domain, f"www.{domain}".replace("www.www.", "www.")]
            },
            "crawl": {
                "seeds": [url],
                "max_depth": 2,
                "max_pages": 15,
                "rate_limit_per_second": 0.5,
                "allowed_extensions": ["pdf", "xlsx", "xls", "csv", "zip", "rar", "7z"]
            },
            "classification": {
                "dataset_rules": [
                    {"id": "publicaciones_generales", "name": "Publicaciones Generales", "url_patterns": ["/"]}
                ]
            },
            "period_extraction": {
                "filename_pattern": r"(?P<year>20\d{2})"
            }
        }
        
        tmp_yaml = Path(f"/tmp/config_{source_id}.yaml")
        with open(tmp_yaml, "w", encoding="utf-8") as f:
            yaml.dump(dummy_config, f)

        super().__init__(tmp_yaml)

    def is_url_excluded(self, url: str) -> bool:
        return False

    def classify_dataset(self, url: str, anchor_text: str = "") -> str:
        return "publicaciones_generales"


def test_source(item: dict, output_dir: Path) -> dict:
    code = item["code"]
    name = item["name"]
    url = item["url"]

    safe_id = code.lower().replace(" ", "_").replace("/", "_").replace("-", "_")
    logger.info(f"\n==========================================")
    logger.info(f"PROBANDO FUENTE [{code}]: {url}")
    logger.info(f"==========================================")

    start_time = time.time()
    result = {
        "code": code,
        "name": name,
        "url": url,
        "status": "UNKNOWN",
        "http_code": None,
        "robots_allowed": None,
        "candidates_discovered": 0,
        "resources_extracted": 0,
        "error_message": None,
        "elapsed_seconds": 0.0
    }

    try:
        adapter = DynamicBenchmarkAdapter(source_id=safe_id, source_name=name, url=url)
        orchestrator = CrawlOrchestrator(adapter=adapter, output_dir=output_dir / "benchmark")

        # Probar conexión inicial
        fetcher = orchestrator.fetcher
        robots_ok = fetcher.is_url_allowed_by_robots(url)
        result["robots_allowed"] = robots_ok

        if not robots_ok:
            result["status"] = "BLOCKED_BY_ROBOTS_TXT"
            result["error_message"] = "URL bloqueada por el archivo robots.txt del servidor"
            result["elapsed_seconds"] = round(time.time() - start_time, 2)
            return result

        ok_html, status_code, html_content = fetcher.fetch_html(url)
        result["http_code"] = status_code

        if not ok_html or not html_content:
            if status_code == 403:
                result["status"] = "ACCES_DENIED_403"
                result["error_message"] = "Acceso denegado (HTTP 403 Forbidden / WAF / Cloudflare)"
            elif status_code == 404:
                result["status"] = "NOT_FOUND_404"
                result["error_message"] = "Página no encontrada (HTTP 404)"
            elif status_code == 0:
                result["status"] = "CONNECTION_OR_DNS_ERROR"
                result["error_message"] = "Error de conexión de red, DNS o Timeout SSL"
            else:
                result["status"] = f"HTTP_ERROR_{status_code}"
                result["error_message"] = f"El servidor respondió con código HTTP {status_code}"
            result["elapsed_seconds"] = round(time.time() - start_time, 2)
            return result

        # Ejecutar orquestación
        source_map = orchestrator.run()
        total_resources = sum(len(ds.resources) for ds in source_map.datasets)

        result["resources_extracted"] = total_resources
        
        if total_resources > 0:
            result["status"] = "SUCCESS_RESOURCES_FOUND"
        else:
            result["status"] = "REACHABLE_NO_RESOURCES_FOUND"
            result["error_message"] = "La página respondió pero no se encontraron hipervínculos directos a archivos descargables (.pdf, .xlsx, etc.) desde la URL semilla"

    except Exception as err:
        logger.error(f"Excepción durante la prueba de {url}: {err}")
        result["status"] = "EXECUTION_EXCEPTION"
        result["error_message"] = str(err)

    result["elapsed_seconds"] = round(time.time() - start_time, 2)
    return result


def main():
    output_dir = Path(__file__).resolve().parent / "output"
    (output_dir / "benchmark").mkdir(parents=True, exist_ok=True)

    results = []
    total = len(SOURCES)

    for idx, src in enumerate(SOURCES, 1):
        logger.info(f"[{idx}/{total}] Evaluando {src['code']} ...")
        res = test_source(src, output_dir)
        results.append(res)

    results_file = output_dir / "benchmark" / "benchmark_results.json"
    with open(results_file, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)

    logger.info(f"\n==========================================")
    logger.info(f"BENCHMARK COMPLETO CONSERVADO EN: {results_file}")
    logger.info(f"==========================================")


if __name__ == "__main__":
    main()
