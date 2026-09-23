from fastapi.testclient import TestClient
from helpers import criar, payload


def test_criar_relogio_com_defaults(client: TestClient) -> None:
    watch = criar(client)
    assert watch["id"] > 0
    assert watch["vigilancia_ativa"] is True
    assert watch["preco_alvo"] is None
    assert watch["exige_loja_oficial"] is False
    assert watch["exige_reputacao_minima"] is False


def test_dinheiro_volta_como_string_decimal_nunca_float(client: TestClient) -> None:
    watch = criar(client, preco_alvo="499.90")
    assert watch["preco_alvo"] == "499.90"
    assert watch["tamanho_caixa_mm"] == "37.0"


def test_ean_duplicado_retorna_409(client: TestClient) -> None:
    criar(client)
    response = client.post("/watches", json=payload(marca="Citizen"))
    assert response.status_code == 409
    assert "7891234567895" in response.json()["detail"]


def test_ean_ausente_ou_invalido_retorna_422(client: TestClient) -> None:
    sem_ean = payload()
    del sem_ean["ean"]
    assert client.post("/watches", json=sem_ean).status_code == 422
    assert client.post("/watches", json=payload("abc")).status_code == 422


def test_preco_alvo_com_tres_casas_ou_negativo_retorna_422(client: TestClient) -> None:
    assert client.post("/watches", json=payload(preco_alvo="10.999")).status_code == 422
    assert client.post("/watches", json=payload(preco_alvo="-1")).status_code == 422


def test_tipo_movimento_invalido_retorna_422(client: TestClient) -> None:
    assert client.post("/watches", json=payload(tipo_movimento="pilha")).status_code == 422


def test_obter_por_id_e_404(client: TestClient) -> None:
    watch = criar(client)
    assert client.get(f"/watches/{watch['id']}").json()["ean"] == watch["ean"]
    response = client.get("/watches/999999")
    assert response.status_code == 404
    assert "detail" in response.json()


def test_listar_com_paginacao(client: TestClient) -> None:
    for i in range(3):
        criar(client, f"789000000000{i}")
    page = client.get("/watches", params={"limit": 2, "offset": 0}).json()
    assert page["limit"] == 2
    assert len(page["items"]) == 2
    assert page["total"] >= 3
    resto = client.get("/watches", params={"limit": 2, "offset": 2}).json()
    assert resto["items"][0]["id"] > page["items"][-1]["id"]
    assert client.get("/watches", params={"limit": 0}).status_code == 422


def test_listar_filtra_por_marca_e_vigilancia(client: TestClient) -> None:
    casio = criar(client, "7890000000010", marca="ZzCasioTeste")
    criar(client, "7890000000011", marca="ZzOutraMarca")
    client.post(f"/watches/{casio['id']}/pausar")

    por_marca = client.get("/watches", params={"marca": "zzcasioteste"}).json()
    assert [w["id"] for w in por_marca["items"]] == [casio["id"]]

    ativos = client.get("/watches", params={"marca": "ZzCasioTeste", "vigilancia_ativa": True})
    assert ativos.json()["items"] == []
    pausados = client.get("/watches", params={"marca": "ZzCasioTeste", "vigilancia_ativa": False})
    assert [w["id"] for w in pausados.json()["items"]] == [casio["id"]]


def test_editar_preco_alvo_e_flags(client: TestClient) -> None:
    watch = criar(client)
    response = client.patch(
        f"/watches/{watch['id']}",
        json={"preco_alvo": "350.00", "exige_loja_oficial": True, "exige_reputacao_minima": True},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["preco_alvo"] == "350.00"
    assert body["exige_loja_oficial"] is True
    assert body["exige_reputacao_minima"] is True
    assert body["marca"] == "Seiko"


def test_editar_permite_limpar_preco_alvo(client: TestClient) -> None:
    watch = criar(client, preco_alvo="100.00")
    response = client.patch(f"/watches/{watch['id']}", json={"preco_alvo": None})
    assert response.status_code == 200
    assert response.json()["preco_alvo"] is None


def test_editar_rejeita_nulo_em_campo_obrigatorio(client: TestClient) -> None:
    watch = criar(client)
    url = f"/watches/{watch['id']}"
    assert client.patch(url, json={"marca": None}).status_code == 422


def test_editar_relogio_inexistente_e_404(client: TestClient) -> None:
    assert client.patch("/watches/999999", json={"marca": "X"}).status_code == 404


def test_pausar_e_retomar_vigilancia(client: TestClient) -> None:
    watch = criar(client)
    url = f"/watches/{watch['id']}"
    assert client.post(f"{url}/pausar").json()["vigilancia_ativa"] is False
    assert client.get(url).json()["vigilancia_ativa"] is False
    assert client.post(f"{url}/retomar").json()["vigilancia_ativa"] is True
    assert client.post("/watches/999999/pausar").status_code == 404
    assert client.post("/watches/999999/retomar").status_code == 404
