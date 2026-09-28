from datetime import UTC, datetime
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from pulso.decisions.text import compor_texto
from pulso.models import Candidate, Coupon, Listing, Publication, Watch

D = Decimal


def relogio_com_candidato(
    session: Session,
    *,
    ean: str = "7891234567895",
    regra: str = "media_30d",
    coupon: Coupon | None = None,
    frete: str | None = "19.90",
    frete_desconhecido: bool = False,
) -> Candidate:
    watch = Watch(
        marca="Seiko",
        referencia_fabricante="SNK809",
        ean=ean,
        tipo_movimento="automatico",
        tamanho_caixa_mm=D("37.0"),
    )
    session.add(watch)
    session.flush()
    listing = Listing(
        watch_id=watch.id,
        marketplace="shopee",
        marketplace_item_id=f"i-{ean}",
        url="https://shopee.com.br/produto-1",
    )
    session.add(listing)
    session.flush()
    desconto = coupon.valor if coupon is not None else D("0.00")
    candidate = Candidate(
        watch_id=watch.id,
        listing_id=listing.id,
        coupon_id=coupon.id if coupon is not None else None,
        regra=regra,
        preco_vista=D("155.00"),
        desconto=desconto,
        preco_sem_frete=D("155.00") - desconto,
        frete=None if frete is None else D(frete),
        frete_desconhecido=frete_desconhecido,
        media_30d=D("190.00"),
        minimo_90d=D("150.00") if regra == "minimo_90d" else None,
        preco_alvo=None,
        queda_percentual=D("18.42"),
        economia_absoluta=D("35.00"),
        pontuacao=D("18.4200"),
        status="pendente",
    )
    session.add(candidate)
    session.commit()
    session.refresh(candidate)
    return candidate


def total_publicacoes(session: Session) -> int:
    return session.scalar(select(func.count()).select_from(Publication)) or 0


def test_aprovar_cria_publication_com_loja_texto_e_precos_congelados(
    client: TestClient, db_session: Session
) -> None:
    candidate = relogio_com_candidato(db_session)
    r = client.post(
        f"/candidates/{candidate.id}/aprovar",
        json={"loja": "Loja Oficial Seiko", "decidido_por": "tg:123"},
    )
    assert r.status_code == 200, r.text
    body = r.json()

    assert body["candidate"]["status"] == "aprovado"
    pub = body["publication"]
    assert pub["loja"] == "Loja Oficial Seiko"
    assert pub["preco_vista_publicado"] == "155.00"
    assert pub["preco_efetivo"] == "174.90"  # 155.00 sem frete + 19.90 de frete
    assert pub["texto_editado"] is False
    assert pub["link_publicado"] == "https://shopee.com.br/produto-1"
    assert total_publicacoes(db_session) == 1


def test_aprovar_sem_texto_editado_grava_o_texto_composto(
    client: TestClient, db_session: Session
) -> None:
    candidate = relogio_com_candidato(db_session)
    watch = db_session.get(Watch, candidate.watch_id)
    listing = db_session.get(Listing, candidate.listing_id)
    assert watch is not None and listing is not None
    esperado = compor_texto(candidate, watch, listing, None, loja="Loja Oficial Seiko")

    r = client.post(
        f"/candidates/{candidate.id}/aprovar",
        json={"loja": "Loja Oficial Seiko", "decidido_por": "tg:123"},
    )
    body = r.json()["publication"]
    assert body["texto_publicado"] == esperado
    assert body["texto_editado"] is False


def test_aprovar_com_texto_editado_guarda_o_texto_do_operador(
    client: TestClient, db_session: Session
) -> None:
    candidate = relogio_com_candidato(db_session)
    texto_operador = "Texto reescrito pelo operador, bem diferente do composto."

    r = client.post(
        f"/candidates/{candidate.id}/aprovar",
        json={
            "loja": "Loja Oficial Seiko",
            "decidido_por": "tg:123",
            "texto": texto_operador,
        },
    )
    body = r.json()["publication"]
    assert body["texto_publicado"] == texto_operador
    assert body["texto_editado"] is True


def test_aprovar_sem_informar_loja_e_erro_de_validacao_nao_500(
    client: TestClient, db_session: Session
) -> None:
    candidate = relogio_com_candidato(db_session)
    r = client.post(
        f"/candidates/{candidate.id}/aprovar",
        json={"decidido_por": "tg:123"},
    )
    assert r.status_code == 422
    assert total_publicacoes(db_session) == 0


def test_descartar_muda_status_e_nao_cria_publication(
    client: TestClient, db_session: Session
) -> None:
    candidate = relogio_com_candidato(db_session)
    r = client.post(
        f"/candidates/{candidate.id}/descartar",
        json={"decidido_por": "tg:456"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "descartado"
    assert total_publicacoes(db_session) == 0


def test_decidir_duas_vezes_e_conflito_e_so_uma_publication_existe(
    client: TestClient, db_session: Session
) -> None:
    candidate = relogio_com_candidato(db_session)
    ok = client.post(
        f"/candidates/{candidate.id}/aprovar",
        json={"loja": "Loja Oficial Seiko", "decidido_por": "tg:123"},
    )
    assert ok.status_code == 200

    segunda_aprovacao = client.post(
        f"/candidates/{candidate.id}/aprovar",
        json={"loja": "Loja Oficial Seiko", "decidido_por": "tg:999"},
    )
    assert segunda_aprovacao.status_code == 409

    descarte_tardio = client.post(
        f"/candidates/{candidate.id}/descartar",
        json={"decidido_por": "tg:999"},
    )
    assert descarte_tardio.status_code == 409

    assert total_publicacoes(db_session) == 1


def test_descartar_candidato_ja_descartado_e_conflito(
    client: TestClient, db_session: Session
) -> None:
    candidate = relogio_com_candidato(db_session)
    primeiro = client.post(
        f"/candidates/{candidate.id}/descartar",
        json={"decidido_por": "tg:123"},
    )
    assert primeiro.status_code == 200

    segundo = client.post(
        f"/candidates/{candidate.id}/descartar",
        json={"decidido_por": "tg:999"},
    )
    assert segundo.status_code == 409


def test_candidato_inexistente_e_404(client: TestClient) -> None:
    assert client.get("/candidates/999999").status_code == 404
    r = client.post(
        "/candidates/999999/aprovar",
        json={"loja": "Loja Oficial Seiko", "decidido_por": "tg:123"},
    )
    assert r.status_code == 404
    r = client.post("/candidates/999999/descartar", json={"decidido_por": "tg:123"})
    assert r.status_code == 404


def test_decidido_em_e_decidido_por_gravados_ao_aprovar(
    client: TestClient, db_session: Session
) -> None:
    candidate = relogio_com_candidato(db_session)
    antes = datetime.now(UTC)
    r = client.post(
        f"/candidates/{candidate.id}/aprovar",
        json={"loja": "Loja Oficial Seiko", "decidido_por": "tg:123"},
    )
    body = r.json()["candidate"]
    assert body["decidido_por"] == "tg:123"
    assert datetime.fromisoformat(body["decidido_em"]) >= antes


def test_decidido_em_e_decidido_por_gravados_ao_descartar(
    client: TestClient, db_session: Session
) -> None:
    candidate = relogio_com_candidato(db_session)
    antes = datetime.now(UTC)
    r = client.post(
        f"/candidates/{candidate.id}/descartar",
        json={"decidido_por": "tg:456"},
    )
    body = r.json()
    assert body["decidido_por"] == "tg:456"
    assert datetime.fromisoformat(body["decidido_em"]) >= antes


def test_aprovar_com_cupom_congela_o_cupom_do_candidato(
    client: TestClient, db_session: Session
) -> None:
    coupon = Coupon(
        codigo="PROMO10",
        marketplace="shopee",
        regra_texto="",
        tipo="valor_fixo",
        valor=D("10.00"),
    )
    db_session.add(coupon)
    db_session.flush()
    candidate = relogio_com_candidato(db_session, coupon=coupon)

    r = client.post(
        f"/candidates/{candidate.id}/aprovar",
        json={"loja": "Loja Oficial Seiko", "decidido_por": "tg:123"},
    )
    body = r.json()["publication"]
    assert body["coupon_id"] == coupon.id
    assert "Cupom PROMO10" in body["texto_publicado"]


def test_aprovar_com_frete_desconhecido_usa_preco_sem_frete_como_preco_efetivo(
    client: TestClient, db_session: Session
) -> None:
    candidate = relogio_com_candidato(db_session, frete=None, frete_desconhecido=True)
    r = client.post(
        f"/candidates/{candidate.id}/aprovar",
        json={"loja": "Loja Oficial Seiko", "decidido_por": "tg:123"},
    )
    body = r.json()["publication"]
    assert body["preco_efetivo"] == "155.00"
