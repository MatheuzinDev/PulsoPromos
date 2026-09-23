from typing import Any

from fastapi.testclient import TestClient
from helpers import criar


def anuncio(item: str = "item-1", marketplace: str = "shopee") -> dict[str, Any]:
    return {
        "marketplace": marketplace,
        "marketplace_item_id": item,
        "url": f"https://{marketplace}.example/{item}",
    }


def test_vincular_e_listar_anuncios(client: TestClient) -> None:
    watch = criar(client)
    url = f"/watches/{watch['id']}/listings"
    response = client.post(url, json=anuncio("a-1"))
    assert response.status_code == 201
    assert response.json()["ativo"] is True
    assert response.json()["watch_id"] == watch["id"]
    client.post(url, json=anuncio("a-2", "mercado_livre"))
    ids = [x["marketplace_item_id"] for x in client.get(url).json()]
    assert ids == ["a-1", "a-2"]


def test_anuncio_duplicado_retorna_409_mesmo_em_outro_relogio(client: TestClient) -> None:
    a = criar(client, "7890000000001")
    b = criar(client, "7890000000002")
    assert client.post(f"/watches/{a['id']}/listings", json=anuncio("dup")).status_code == 201
    for watch_id in (a["id"], b["id"]):
        response = client.post(f"/watches/{watch_id}/listings", json=anuncio("dup"))
        assert response.status_code == 409
        assert "dup" in response.json()["detail"]


def test_mesmo_item_em_marketplaces_diferentes_e_permitido(client: TestClient) -> None:
    watch = criar(client)
    url = f"/watches/{watch['id']}/listings"
    assert client.post(url, json=anuncio("x", "shopee")).status_code == 201
    assert client.post(url, json=anuncio("x", "aliexpress")).status_code == 201


def test_anuncio_marketplace_invalido_retorna_422(client: TestClient) -> None:
    watch = criar(client)
    response = client.post(f"/watches/{watch['id']}/listings", json=anuncio("x", "olx"))
    assert response.status_code == 422


def test_anuncio_em_relogio_inexistente_e_404(client: TestClient) -> None:
    assert client.post("/watches/999999/listings", json=anuncio()).status_code == 404
    assert client.get("/watches/999999/listings").status_code == 404


def test_desativar_anuncio(client: TestClient) -> None:
    watch = criar(client)
    url = f"/watches/{watch['id']}/listings"
    listing = client.post(url, json=anuncio()).json()
    response = client.post(f"{url}/{listing['id']}/desativar")
    assert response.status_code == 200
    assert response.json()["ativo"] is False
    assert client.get(url).json()[0]["ativo"] is False


def test_desativar_anuncio_de_outro_relogio_ou_inexistente_e_404(client: TestClient) -> None:
    a = criar(client, "7890000000001")
    b = criar(client, "7890000000002")
    listing = client.post(f"/watches/{a['id']}/listings", json=anuncio()).json()
    assert client.post(f"/watches/{b['id']}/listings/{listing['id']}/desativar").status_code == 404
    assert client.post(f"/watches/{a['id']}/listings/999999/desativar").status_code == 404
    assert client.post(f"/watches/999999/listings/{listing['id']}/desativar").status_code == 404
