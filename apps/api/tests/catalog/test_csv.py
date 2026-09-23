from fastapi.testclient import TestClient
from helpers import criar
from httpx import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from pulso.models import Watch

CABECALHO = (
    "ean,marca,referencia_fabricante,tipo_movimento,tamanho_caixa_mm,"
    "vigilancia_ativa,preco_alvo,exige_loja_oficial,exige_reputacao_minima"
)
CSV = {"content-type": "text/csv"}


def importar(client: TestClient, texto: str | bytes) -> Response:
    conteudo = texto.encode("utf-8") if isinstance(texto, str) else texto
    return client.post("/watches/import", content=conteudo, headers=CSV)


def snapshot(session: Session) -> list[tuple[object, ...]]:
    session.expire_all()
    rows = session.scalars(select(Watch).order_by(Watch.ean)).all()
    return [
        (
            w.id, w.ean, w.marca, w.referencia_fabricante, w.tipo_movimento,
            w.tamanho_caixa_mm, w.vigilancia_ativa, w.preco_alvo, w.exige_loja_oficial,
            w.exige_reputacao_minima, w.criado_em, w.atualizado_em,
        )
        for w in rows
    ]  # fmt: skip


def test_export_tem_cabecalho_e_linhas(client: TestClient) -> None:
    criar(client, "7890000000001", preco_alvo="99.90", exige_loja_oficial=True)
    response = client.get("/watches/export")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    linhas = response.text.splitlines()
    assert linhas[0] == CABECALHO
    assert "7890000000001,Seiko,SNK809,automatico,37.0,true,99.90,true,false" in linhas


def test_round_trip_export_import_nao_altera_nenhum_dado(
    client: TestClient, db_session: Session
) -> None:
    criar(client, "7890000000001", preco_alvo="99.90", exige_reputacao_minima=True)
    criar(client, "7890000000002", marca='Marca, "Aspas"', tamanho_caixa_mm="40.5")
    pausado = criar(client, "7890000000003", preco_alvo="1234567890.12")
    client.post(f"/watches/{pausado['id']}/pausar")

    antes = snapshot(db_session)
    exportado = client.get("/watches/export").text
    response = importar(client, exportado)
    assert response.status_code == 200
    resultado = response.json()
    assert resultado["criados"] == 0
    assert resultado["atualizados"] == 0
    assert resultado["inalterados"] >= 3
    assert snapshot(db_session) == antes  # inclui atualizado_em
    assert client.get("/watches/export").text == exportado


def test_import_cria_e_atualiza_por_ean(client: TestClient) -> None:
    existente = criar(client, "7890000000001", preco_alvo="100.00")
    texto = (
        f"{CABECALHO}\n"
        "7890000000001,Seiko,SNK809,automatico,37.0,false,250.50,true,true\n"
        "7890000000002,Casio,F91W,quartzo,35.0,true,,false,false\n"
    )
    response = importar(client, texto)
    assert response.status_code == 200
    assert response.json() == {"criados": 1, "atualizados": 1, "inalterados": 0}

    atualizado = client.get(f"/watches/{existente['id']}").json()
    assert atualizado["id"] == existente["id"]
    assert atualizado["preco_alvo"] == "250.50"
    assert atualizado["vigilancia_ativa"] is False
    assert atualizado["exige_loja_oficial"] is True
    novos = client.get("/watches", params={"marca": "Casio"}).json()["items"]
    assert novos[0]["ean"] == "7890000000002"
    assert novos[0]["preco_alvo"] is None


def test_import_com_linha_invalida_nao_grava_nada_e_aponta_a_linha(
    client: TestClient, db_session: Session
) -> None:
    existente = criar(client, "7890000000001", preco_alvo="100.00")
    antes = snapshot(db_session)
    texto = (
        f"{CABECALHO}\n"
        "7890000000001,Seiko,SNK809,automatico,37.0,true,999.00,false,false\n"  # linha 2 ok
        "7890000000002,Casio,F91W,quartzo,35.0,true,,false,false\n"  # linha 3 ok
        "abc,Orient,Bambino,automatico,40.0,true,,false,false\n"  # linha 4: EAN invalido
        "7890000000004,Timex,T1,pilha,38.0,true,,false,false\n"  # linha 5: movimento invalido
        "7890000000005,Citizen,C1,solar,38.0,talvez,,false,false\n"  # linha 6: booleano
        "7890000000002,Casio,F91W,quartzo,35.0,true,,false,false\n"  # linha 7: EAN repetido
    )
    response = importar(client, texto)
    assert response.status_code == 422
    corpo = response.json()
    assert {e["linha"] for e in corpo["erros"]} == {4, 5, 6, 7}
    assert all(e["motivo"] for e in corpo["erros"])
    assert "linha 3" in next(e["motivo"] for e in corpo["erros"] if e["linha"] == 7)

    assert snapshot(db_session) == antes  # a linha 2, valida, NAO foi aplicada
    assert client.get(f"/watches/{existente['id']}").json()["preco_alvo"] == "100.00"
    assert client.get("/watches", params={"marca": "Casio"}).json()["items"] == []


def test_import_corrigido_e_reenviavel(client: TestClient) -> None:
    ruim = f"{CABECALHO}\n789000000000X,Casio,F91W,quartzo,35.0,true,,false,false\n"
    bom = f"{CABECALHO}\n7890000000009,Casio,F91W,quartzo,35.0,true,,false,false\n"
    assert importar(client, ruim).status_code == 422
    assert importar(client, bom).json() == {"criados": 1, "atualizados": 0, "inalterados": 0}
    assert importar(client, bom).json() == {"criados": 0, "atualizados": 0, "inalterados": 1}


def test_import_cabecalho_invalido_retorna_422_com_linha_1(client: TestClient) -> None:
    response = importar(client, "ean,marca,cor\n7890000000001,Seiko,preto\n")
    assert response.status_code == 422
    erros = response.json()["erros"]
    assert {e["linha"] for e in erros} == {1}
    motivos = " ".join(e["motivo"] for e in erros)
    assert "referencia_fabricante" in motivos
    assert "cor" in motivos


def test_import_colunas_opcionais_ausentes_preservam_valores_existentes(
    client: TestClient,
) -> None:
    existente = criar(client, "7890000000001", preco_alvo="100.00", exige_loja_oficial=True)
    texto = (
        "ean,marca,referencia_fabricante,tipo_movimento,tamanho_caixa_mm\n"
        "7890000000001,Seiko 5,SNK809,automatico,37.0\n"
    )
    assert importar(client, texto).json()["atualizados"] == 1
    atual = client.get(f"/watches/{existente['id']}").json()
    assert atual["marca"] == "Seiko 5"
    assert atual["preco_alvo"] == "100.00"
    assert atual["exige_loja_oficial"] is True


def test_import_arquivo_vazio_ou_nao_utf8_retorna_422(client: TestClient) -> None:
    assert importar(client, "").status_code == 422
    assert importar(client, f"{CABECALHO}\n").status_code == 422
    assert importar(client, b"\xff\xfe\x00bad").status_code == 422


def test_import_aceita_bom_utf8_do_excel(client: TestClient) -> None:
    texto = f"{CABECALHO}\n7890000000007,Seiko,SNK809,automatico,37.0,true,,false,false\n"
    response = importar(client, b"\xef\xbb\xbf" + texto.encode())
    assert response.status_code == 200
    assert response.json()["criados"] == 1
