from typing import Any

from fastapi.testclient import TestClient


def payload(ean: str = "7891234567895", **extra: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "marca": "Seiko",
        "referencia_fabricante": "SNK809",
        "ean": ean,
        "tipo_movimento": "automatico",
        "tamanho_caixa_mm": "37.0",
    }
    return base | extra


def criar(client: TestClient, ean: str = "7891234567895", **extra: Any) -> dict[str, Any]:
    response = client.post("/watches", json=payload(ean, **extra))
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    return body
