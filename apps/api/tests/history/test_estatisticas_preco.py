from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

from pulso.catalog.errors import NotFoundError
from pulso.history.service import calcular_estatisticas
from pulso.models import Listing, PriceReading, Watch

AGORA = datetime(2026, 6, 30, 12, 0, tzinfo=UTC)
_contador = {"n": 0}


def _watch(session: Session) -> Watch:
    _contador["n"] += 1
    watch = Watch(
        marca="Casio",
        referencia_fabricante=f"REF-{_contador['n']}",
        ean=f"789000000{_contador['n']:04d}",
        tipo_movimento="quartzo",
        tamanho_caixa_mm=Decimal("40.0"),
    )
    session.add(watch)
    session.flush()
    return watch


def _listing(session: Session, watch: Watch, item: str) -> Listing:
    listing = Listing(
        watch_id=watch.id,
        marketplace="shopee",
        marketplace_item_id=f"{watch.id}-{item}",
        url=f"https://exemplo.test/{watch.id}/{item}",
    )
    session.add(listing)
    session.flush()
    return listing


def _leitura(
    session: Session,
    listing: Listing,
    dias_atras: float,
    preco: str,
    *,
    desatualizado: bool = False,
) -> None:
    session.add(
        PriceReading(
            listing_id=listing.id,
            coletado_em=AGORA - timedelta(days=dias_atras),
            preco_vista=Decimal(preco),
            em_estoque=True,
            vendedor_raw={},
            origem="teste",
            desatualizado=desatualizado,
        )
    )
    session.flush()


def test_media_30d_e_minimo_90d_calculados_a_mao(db_session: Session) -> None:
    watch = _watch(db_session)
    a = _listing(db_session, watch, "a")
    _leitura(db_session, a, 91, "50.00")  # fora dos 90 dias: senao o minimo seria 50
    _leitura(db_session, a, 60, "120.00")
    _leitura(db_session, a, 31, "100.00")  # fora dos 30 dias: senao a media seria 85
    _leitura(db_session, a, 20, "90.00")
    _leitura(db_session, a, 10, "80.00")
    _leitura(db_session, a, 1, "70.00")

    r = calcular_estatisticas(db_session, watch.id, agora=AGORA)

    assert r.media_30d.media == Decimal("80.00")  # (90+80+70)/3
    assert r.media_30d.cobertura.amostras == 3
    assert r.menor_90d.preco == Decimal("70.00")
    assert r.menor_90d.ocorrido_em == AGORA - timedelta(days=1)
    assert r.menor_90d.cobertura.amostras == 5  # 120, 100, 90, 80, 70


def test_menor_de_90d_pode_ser_anterior_a_janela_de_30d(db_session: Session) -> None:
    watch = _watch(db_session)
    a = _listing(db_session, watch, "a")
    _leitura(db_session, a, 80, "40.00")
    _leitura(db_session, a, 5, "100.00")

    r = calcular_estatisticas(db_session, watch.id, agora=AGORA)

    assert r.menor_90d.preco == Decimal("40.00")
    assert r.menor_90d.ocorrido_em == AGORA - timedelta(days=80)
    assert r.media_30d.media == Decimal("100.00")


def test_dois_anuncios_usam_o_mais_barato_de_cada_momento(db_session: Session) -> None:
    watch = _watch(db_session)
    a = _listing(db_session, watch, "a")
    b = _listing(db_session, watch, "b")
    _leitura(db_session, a, 10, "100.00")  # so A existe: 100
    _leitura(db_session, b, 5, "80.00")  # A segue 100, B 80: 80
    _leitura(db_session, a, 3, "90.00")  # A 90, B 80: 80
    _leitura(db_session, a, 2, "70.00")  # mesmo instante: A 70, B 60 -> 60
    _leitura(db_session, b, 2, "60.00")

    r = calcular_estatisticas(db_session, watch.id, agora=AGORA)

    # serie 100, 80, 80, 60 -> 320/4. So A daria 86.67; media dos anuncios daria outra.
    assert r.media_30d.media == Decimal("80.00")
    assert r.media_30d.cobertura.amostras == 4
    assert r.menor_90d.preco == Decimal("60.00")
    assert r.menor_90d.ocorrido_em == AGORA - timedelta(days=2)


def test_leitura_desatualizada_e_ignorada(db_session: Session) -> None:
    watch = _watch(db_session)
    a = _listing(db_session, watch, "a")
    _leitura(db_session, a, 10, "100.00")
    _leitura(db_session, a, 5, "90.00")
    _leitura(db_session, a, 1, "80.00")

    antes = calcular_estatisticas(db_session, watch.id, agora=AGORA)
    _leitura(db_session, a, 4, "1.00", desatualizado=True)  # mudaria media e minimo
    depois = calcular_estatisticas(db_session, watch.id, agora=AGORA)

    assert antes.media_30d.media == Decimal("90.00")
    assert antes.menor_90d.preco == Decimal("80.00")
    assert depois == antes
    assert depois.media_30d.cobertura.amostras == 3


def test_leituras_fora_das_janelas_ficam_de_fora(db_session: Session) -> None:
    watch = _watch(db_session)
    a = _listing(db_session, watch, "a")
    _leitura(db_session, a, 91, "10.00")
    _leitura(db_session, a, 31, "20.00")
    _leitura(db_session, a, 2, "100.00")
    _leitura(db_session, a, -1, "5.00")  # futuro em relacao a referencia

    r = calcular_estatisticas(db_session, watch.id, agora=AGORA)

    assert r.media_30d.media == Decimal("100.00")
    assert r.media_30d.cobertura.amostras == 1
    assert r.menor_90d.preco == Decimal("20.00")
    assert r.menor_90d.cobertura.amostras == 2


def test_sem_leitura_valida_devolve_ausencia_explicita(db_session: Session) -> None:
    vazio = _watch(db_session)
    so_velho = _watch(db_session)
    a = _listing(db_session, so_velho, "a")
    _leitura(db_session, a, 200, "50.00")
    _leitura(db_session, a, 3, "50.00", desatualizado=True)

    for watch in (vazio, so_velho):
        r = calcular_estatisticas(db_session, watch.id, agora=AGORA)
        assert r.media_30d.media is None
        assert r.menor_90d.preco is None
        assert r.menor_90d.ocorrido_em is None
        assert r.media_30d.cobertura.amostras == 0
        assert r.media_30d.cobertura.dias_com_leitura == 0
        assert r.media_30d.cobertura.primeira_leitura is None
        assert r.menor_90d.cobertura.amostras == 0


def test_cobertura_reflete_o_inserido(db_session: Session) -> None:
    watch = _watch(db_session)
    a = _listing(db_session, watch, "a")
    _leitura(db_session, a, 10.0, "100.00")
    _leitura(db_session, a, 9.75, "99.00")  # mesmo dia UTC? 9.75d atras = 06:00 do dia D-10
    _leitura(db_session, a, 4, "98.00")

    r = calcular_estatisticas(db_session, watch.id, agora=AGORA)
    c = r.media_30d.cobertura

    assert c.janela_dias == 30
    assert c.amostras == 3
    assert c.dias_com_leitura == 2  # 12:00 e 18:00 do mesmo dia, mais outro dia
    assert c.primeira_leitura == AGORA - timedelta(days=10)
    assert c.ultima_leitura == AGORA - timedelta(days=4)
    assert r.menor_90d.cobertura.janela_dias == 90


def test_dinheiro_e_decimal_com_duas_casas(db_session: Session) -> None:
    watch = _watch(db_session)
    a = _listing(db_session, watch, "a")
    b = _listing(db_session, watch, "b")
    _leitura(db_session, a, 3, "100.00")
    _leitura(db_session, b, 2, "80.01")
    _leitura(db_session, a, 1, "90.00")  # serie 100, 80.01, 80.01 -> 260.02/3 = 86.6733

    r = calcular_estatisticas(db_session, watch.id, agora=AGORA)

    assert isinstance(r.media_30d.media, Decimal)
    assert r.media_30d.media == Decimal("86.67")
    assert r.media_30d.media.as_tuple().exponent == -2
    assert isinstance(r.menor_90d.preco, Decimal)
    assert r.menor_90d.preco.as_tuple().exponent == -2


def test_relogio_inexistente_levanta_not_found(db_session: Session) -> None:
    with pytest.raises(NotFoundError):
        calcular_estatisticas(db_session, 999_999, agora=AGORA)
