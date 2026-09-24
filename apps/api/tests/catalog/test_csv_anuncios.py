from fastapi.testclient import TestClient
from helpers import criar
from httpx import Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from pulso.models import Listing, Watch

CABECALHO = "ean,marketplace,marketplace_item_id,url,ativo"
CSV = {"content-type": "text/csv"}
EAN_A = "7890000000001"
EAN_B = "7890000000002"


def importar(client: TestClient, texto: str) -> Response:
    return client.post("/listings/import", content=texto.encode("utf-8"), headers=CSV)


def snapshot(session: Session) -> list[tuple[object, ...]]:
    session.expire_all()
    rows = session.scalars(select(Listing).order_by(Listing.id)).all()
    return [
        (x.id, x.watch_id, x.marketplace, x.marketplace_item_id, x.url, x.ativo,
         x.criado_em, x.atualizado_em)
        for x in rows
    ]  # fmt: skip


def total_relogios(session: Session) -> int:
    return session.scalar(select(func.count()).select_from(Watch)) or 0


def vincular(client: TestClient, watch: dict[str, object], item: str, mp: str = "shopee") -> None:
    body = {"marketplace": mp, "marketplace_item_id": item, "url": f"https://{mp}.example/{item}"}
    assert client.post(f"/watches/{watch['id']}/listings", json=body).status_code == 201


def test_export_tem_ean_do_dono(client: TestClient) -> None:
    a = criar(client, EAN_A)
    vincular(client, a, "a-1")
    linhas = client.get("/listings/export").text.splitlines()
    assert linhas[0] == CABECALHO
    assert f"{EAN_A},shopee,a-1,https://shopee.example/a-1,true" in linhas


def test_round_trip_nao_altera_nenhum_dado(client: TestClient, db_session: Session) -> None:
    a = criar(client, EAN_A)
    b = criar(client, EAN_B)
    vincular(client, a, "a-1")
    vincular(client, a, "a-2", "mercado_livre")
    vincular(client, b, "b,1")
    ids = [x["id"] for x in client.get(f"/watches/{b['id']}/listings").json()]
    client.post(f"/watches/{b['id']}/listings/{ids[0]}/desativar")
    antes = snapshot(db_session)
    exportado = client.get("/listings/export").text
    response = importar(client, exportado)
    assert response.status_code == 200
    assert response.json() == {"criados": 0, "atualizados": 0, "inalterados": 3}
    assert snapshot(db_session) == antes
    assert client.get("/listings/export").text == exportado


def test_import_cria_e_atualiza_pela_chave(client: TestClient, db_session: Session) -> None:
    a = criar(client, EAN_A)
    vincular(client, a, "a-1")
    texto = (
        f"{CABECALHO}\n"
        f"{EAN_A},shopee,a-1,https://novo.example/a-1,false\n"
        f"{EAN_A},aliexpress,x-9,https://ali.example/x-9,true\n"
    )
    response = importar(client, texto)
    assert response.status_code == 200
    assert response.json() == {"criados": 1, "atualizados": 1, "inalterados": 0}
    session_rows = {(x.marketplace_item_id): x for x in db_session.scalars(select(Listing))}
    assert session_rows["a-1"].url == "https://novo.example/a-1"
    assert session_rows["a-1"].ativo is False
    assert session_rows["x-9"].watch_id == a["id"]


def test_ativo_e_opcional_e_nao_toca_o_existente(client: TestClient, db_session: Session) -> None:
    a = criar(client, EAN_A)
    vincular(client, a, "a-1")
    ids = db_session.scalars(select(Listing.id)).all()
    client.post(f"/watches/{a['id']}/listings/{ids[0]}/desativar")
    texto = (
        "ean,marketplace,marketplace_item_id,url\n"
        f"{EAN_A},shopee,a-1,https://novo.example/a-1\n"
        f"{EAN_A},shopee,a-2,https://novo.example/a-2\n"
    )
    assert importar(client, texto).status_code == 200
    db_session.expire_all()
    por_item = {x.marketplace_item_id: x for x in db_session.scalars(select(Listing))}
    assert por_item["a-1"].ativo is False
    assert por_item["a-2"].ativo is True


def test_ean_inexistente_nao_grava_nada_nem_cria_relogio(
    client: TestClient, db_session: Session
) -> None:
    criar(client, EAN_A)
    antes = total_relogios(db_session)
    texto = (
        f"{CABECALHO}\n"
        f"{EAN_A},shopee,a-1,https://shopee.example/a-1,true\n"
        "7899999999999,shopee,z-1,https://shopee.example/z-1,true\n"
    )
    response = importar(client, texto)
    assert response.status_code == 422
    erros = response.json()["erros"]
    assert [e["linha"] for e in erros] == [3]
    assert "7899999999999" in erros[0]["motivo"]
    assert snapshot(db_session) == []
    assert total_relogios(db_session) == antes


def test_linha_invalida_nao_grava_nada(client: TestClient, db_session: Session) -> None:
    criar(client, EAN_A)
    texto = (
        f"{CABECALHO}\n"
        f"{EAN_A},shopee,a-1,https://shopee.example/a-1,true\n"
        f"{EAN_A},loja_inventada,a-2,https://x.example/a-2,true\n"
        f"{EAN_A},shopee,a-3,https://shopee.example/a-3,talvez\n"
        f"{EAN_A},shopee,a-4\n"
    )
    response = importar(client, texto)
    assert response.status_code == 422
    assert [e["linha"] for e in response.json()["erros"]] == [3, 4, 5]
    assert snapshot(db_session) == []


def test_chave_repetida_aponta_a_primeira_ocorrencia(
    client: TestClient, db_session: Session
) -> None:
    criar(client, EAN_A)
    criar(client, EAN_B)
    texto = (
        f"{CABECALHO}\n"
        f"{EAN_A},shopee,a-1,https://shopee.example/a-1,true\n"
        f"{EAN_B},shopee,a-1,https://shopee.example/outro,true\n"
    )
    response = importar(client, texto)
    assert response.status_code == 422
    erros = response.json()["erros"]
    assert [e["linha"] for e in erros] == [3]
    assert "linha 2" in erros[0]["motivo"]
    assert snapshot(db_session) == []


def test_import_nao_move_anuncio_para_outro_relogio(
    client: TestClient, db_session: Session
) -> None:
    a = criar(client, EAN_A)
    criar(client, EAN_B)
    vincular(client, a, "a-1")
    antes = snapshot(db_session)
    response = importar(client, f"{CABECALHO}\n{EAN_B},shopee,a-1,https://x.example/a-1,true\n")
    assert response.status_code == 422
    assert response.json()["erros"][0]["linha"] == 2
    assert snapshot(db_session) == antes


def test_cabecalho_invalido_e_arquivo_vazio(client: TestClient) -> None:
    assert importar(client, "ean,url\n").status_code == 422
    assert importar(client, f"{CABECALHO}\n").status_code == 422


def test_rotas_aninhadas_continuam_e_csv_de_relogios_intacto(client: TestClient) -> None:
    a = criar(client, EAN_A, preco_alvo="99.90")
    vincular(client, a, "a-1")
    assert len(client.get(f"/watches/{a['id']}/listings").json()) == 1
    exportado = client.get("/watches/export").text
    resposta = client.post("/watches/import", content=exportado.encode(), headers=CSV)
    assert resposta.json() == {"criados": 0, "atualizados": 0, "inalterados": 1}
    assert client.get("/watches/export").text == exportado
