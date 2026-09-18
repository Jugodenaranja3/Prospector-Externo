"""Fingerprint determinista y acotado del contenido API muestreado.

No persiste payloads. Normaliza únicamente aquello que puede variar sin alterar
el valor semántico observado (por ejemplo, espacios/orden de claves JSON).
"""

from __future__ import annotations

import hashlib
import json
from typing import Iterable, List


class ApiContentFingerprint:
    """Calcula huellas por página y una huella agregada del muestreo API."""

    VERSION = "api-sample-v1"
    JSON_FORMATS = {"json", "geojson", "jsonstat", "topojson", "openapi"}

    @staticmethod
    def _normalize_text(body: str) -> str:
        text = (body or "").lstrip("\ufeff")
        return text.replace("\r\n", "\n").replace("\r", "\n")

    @classmethod
    def canonical_bytes(cls, body: str, api_format: str) -> bytes:
        normalized_format = (api_format or "").strip().lower()
        text = cls._normalize_text(body)

        if normalized_format in cls.JSON_FORMATS:
            try:
                value = json.loads(text)
            except (TypeError, ValueError, json.JSONDecodeError):
                return text.encode("utf-8", errors="replace")
            canonical = json.dumps(
                value,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            return canonical.encode("utf-8")

        if normalized_format == "ndjson":
            canonical_lines: List[str] = []
            for raw_line in text.split("\n"):
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    value = json.loads(line)
                    canonical_lines.append(
                        json.dumps(
                            value,
                            ensure_ascii=False,
                            sort_keys=True,
                            separators=(",", ":"),
                        )
                    )
                except (TypeError, ValueError, json.JSONDecodeError):
                    canonical_lines.append(line)
            return "\n".join(canonical_lines).encode("utf-8")

        return text.encode("utf-8", errors="replace")

    @classmethod
    def page_hash(cls, body: str, api_format: str) -> str:
        payload = cls.canonical_bytes(body, api_format)
        return hashlib.sha256(payload).hexdigest()

    @classmethod
    def aggregate_hash(cls, page_hashes: Iterable[str], api_format: str) -> str:
        hashes = list(page_hashes)
        material = [cls.VERSION, (api_format or "").lower(), str(len(hashes))]
        material.extend(f"{index}:{page_hash}" for index, page_hash in enumerate(hashes, start=1))
        return hashlib.sha256("|".join(material).encode("utf-8")).hexdigest()
