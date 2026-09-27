from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient


def cupom(**extra: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "codigo": "PULSO10",
        "marketplace": "shopee",
        "tipo": "percentual",
        "valor": "10.00",
        "regra_texto": "10% na Shopee",
    }
    return base | extra


def criar(client: TestClient, **extra: Any) -> dict[str, Any]:
    response = client.post("/coupons", json=cupom(**extra))
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    return body


def ids(client: TestClient, **params: str) -> set[int]:
    return {x["id"] for x in client.get("/coupons", params=params).json()["items"]}


def test_criar_e_obter(client: TestClient) -> None:
    c = criar(client, minimo_compra="200.00", teto_desconto="50.00")
    assert c["ativo"] is True and c["loja"] is None and c["testado_em"] is None
    assert c["valor"] == "10.00" and c["teto_desconto"] == "50.00"
    got = client.get(f"/coupons/{c['id']}")
    assert got.status_code == 200 and got.json() == c


def test_obter_inexistente_404(client: TestClient) -> None:
    assert client.get("/coupons/999999").status_code == 404


def test_listar_com_filtros(client: TestClient) -> None:
    a = criar(client, codigo="A", marketplace="shopee")
    b = criar(client, codigo="B", marketplace="aliexpress")
    client.post(f"/coupons/{b['id']}/desativar")
    assert {a["id"], b["id"]} <= ids(client)
    assert a["id"] in ids(client, marketplace="shopee")
    assert b["id"] not in ids(client, marketplace="shopee")
    assert b["id"] in ids(client, ativo="false") and a["id"] not in ids(client, ativo="false")
    assert a["id"] in ids(client, ativo="true") and b["id"] not in ids(client, ativo="true")
    assert client.get("/coupons", params={"marketplace": "xyz"}).status_code == 422


def test_editar(client: TestClient) -> None:
    c = criar(client)
    r = client.patch(f"/coupons/{c['id']}", json={"valor": "15.00", "loja": "Loja Oficial"})
    assert r.status_code == 200
    assert r.json()["valor"] == "15.00" and r.json()["loja"] == "Loja Oficial"
    r = client.patch(f"/coupons/{c['id']}", json={"loja": None})
    assert r.json()["loja"] is None
    assert client.patch(f"/coupons/{c['id']}", json={"valor": None}).status_code == 422
    assert client.patch("/coupons/999999", json={"valor": "1"}).status_code == 404


def test_editar_para_regra_incoerente_e_422_nao_500(client: TestClient) -> None:
    c = criar(client, teto_desconto="50.00")
    assert client.patch(f"/coupons/{c['id']}", json={"tipo": "valor_fixo"}).status_code == 422
    assert client.patch(f"/coupons/{c['id']}", json={"valor": "150.00"}).status_code == 422
    assert client.get(f"/coupons/{c['id']}").json()["valor"] == "10.00"


def test_desativar(client: TestClient) -> None:
    c = criar(client)
    r = client.post(f"/coupons/{c['id']}/desativar")
    assert r.status_code == 200 and r.json()["ativo"] is False
    assert client.post("/coupons/999999/desativar").status_code == 404


def test_registrar_testado_em(client: TestClient) -> None:
    c = criar(client)
    r = client.post(f"/coupons/{c['id']}/testado")
    assert r.status_code == 200
    assert datetime.fromisoformat(r.json()["testado_em"]).tzinfo is not None
    r = client.post(f"/coupons/{c['id']}/testado", json={"testado_em": "2026-09-01T12:00:00Z"})
    assert datetime.fromisoformat(r.json()["testado_em"]) == datetime(2026, 9, 1, 12, tzinfo=UTC)
    assert client.post("/coupons/999999/testado").status_code == 404


@pytest.mark.parametrize("valor", ["100.01", "150.00", "0", "-5.00"])
def test_percentual_fora_de_0_a_100_rejeitado(client: TestClient, valor: str) -> None:
    assert client.post("/coupons", json=cupom(valor=valor)).status_code == 422


def test_percentual_100_aceito(client: TestClient) -> None:
    criar(client, valor="100.00")


@pytest.mark.parametrize("valor", ["0", "0.00", "-1.00"])
def test_valor_fixo_nao_positivo_rejeitado(client: TestClient, valor: str) -> None:
    assert client.post("/coupons", json=cupom(tipo="valor_fixo", valor=valor)).status_code == 422


def test_teto_so_com_percentual(client: TestClient) -> None:
    r = client.post("/coupons", json=cupom(tipo="valor_fixo", valor="20", teto_desconto="5"))
    assert r.status_code == 422


def test_validacoes_de_forma(client: TestClient) -> None:
    assert client.post("/coupons", json=cupom(valor="10.001")).status_code == 422
    assert client.post("/coupons", json=cupom(marketplace="xyz")).status_code == 422
    assert client.post("/coupons", json=cupom(tipo="outro")).status_code == 422
    assert client.post("/coupons", json=cupom(valido_ate="2026-10-01T00:00:00")).status_code == 422
    assert client.post("/coupons", json=cupom(minimo_compra="0")).status_code == 422
