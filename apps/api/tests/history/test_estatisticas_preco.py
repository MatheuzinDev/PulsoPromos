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


def _listing(session: Session, watch: Watch, item: str, *, ativo: bool = True) -> Listing:
    listing = Listing(
        watch_id=watch.id,
        marketplace="shopee",
        marketplace_item_id=f"{watch.id}-{item}",
        url=f"https://exemplo.test/{watch.id}/{item}",
        ativo=ativo,
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
    em_estoque: bool = True,
) -> None:
    session.add(
        PriceReading(
            listing_id=listing.id,
            coletado_em=AGORA - timedelta(days=dias_atras),
            preco_vista=Decimal(preco),
            em_estoque=em_estoque,
            vendedor_raw={},
            origem="teste",
            desatualizado=desatualizado,
        )
    )
    session.flush()


def test_media_30d_pondera_patamares_pela_duracao(db_session: Session) -> None:
    watch = _watch(db_session)
    a = _listing(db_session, watch, "a")
    _leitura(db_session, a, 40, "100.00")  # semeia a janela de 30d
    _leitura(db_session, a, 20, "90.00")
    _leitura(db_session, a, 10, "80.00")
    _leitura(db_session, a, 1, "70.00")

    r = calcular_estatisticas(db_session, watch.id, agora=AGORA)

    # 100 por 10d, 90 por 10d, 80 por 9d, 70 por 1d: (1000+900+720+70)/30
    assert r.media_30d.media == Decimal("89.67")
    assert r.menor_90d.preco == Decimal("70.00")
    assert r.menor_90d.ocorrido_em == AGORA - timedelta(days=1)


def test_preco_longo_pesa_mais_que_preco_curto(db_session: Session) -> None:
    watch = _watch(db_session)
    a = _listing(db_session, watch, "a")
    _leitura(db_session, a, 30, "100.00")  # dura 25 dias
    _leitura(db_session, a, 5, "200.00")  # dura 5 dias

    r = calcular_estatisticas(db_session, watch.id, agora=AGORA)

    # ponderada: (100*25 + 200*5)/30 = 116.67; aritmetica simples daria 150.00
    assert r.media_30d.media == Decimal("116.67")


def test_cenario_a_preco_estavel_sem_leitura_nova_mantem_media(db_session: Session) -> None:
    watch = _watch(db_session)
    a = _listing(db_session, watch, "a")
    _leitura(db_session, a, 45, "200.00")

    r = calcular_estatisticas(db_session, watch.id, agora=AGORA)

    assert r.media_30d.media == Decimal("200.00")
    assert r.menor_90d.preco == Decimal("200.00")


def test_cenario_b_anuncio_barato_estavel_nao_some_da_serie(db_session: Session) -> None:
    watch = _watch(db_session)
    barato = _listing(db_session, watch, "barato")
    caro = _listing(db_session, watch, "caro")
    _leitura(db_session, barato, 40, "100.00")  # estavel, sem leitura nova
    _leitura(db_session, caro, 35, "300.00")
    _leitura(db_session, caro, 21, "310.00")
    _leitura(db_session, caro, 14, "290.00")
    _leitura(db_session, caro, 7, "300.00")

    r = calcular_estatisticas(db_session, watch.id, agora=AGORA)

    assert r.media_30d.media == Decimal("100.00")  # sem o barato seria ~300
    assert r.menor_90d.preco == Decimal("100.00")


def test_menor_de_90d_pode_ser_anterior_a_janela_de_30d(db_session: Session) -> None:
    watch = _watch(db_session)
    a = _listing(db_session, watch, "a")
    _leitura(db_session, a, 80, "40.00")
    _leitura(db_session, a, 5, "100.00")

    r = calcular_estatisticas(db_session, watch.id, agora=AGORA)

    assert r.menor_90d.preco == Decimal("40.00")
    assert r.menor_90d.ocorrido_em == AGORA - timedelta(days=80)
    assert r.media_30d.media == Decimal("50.00")  # 40 por 25d, 100 por 5d


def test_empate_no_minimo_devolve_o_instante_mais_antigo(db_session: Session) -> None:
    watch = _watch(db_session)
    a = _listing(db_session, watch, "a")
    _leitura(db_session, a, 50, "60.00")
    _leitura(db_session, a, 40, "90.00")
    _leitura(db_session, a, 10, "60.00")

    r = calcular_estatisticas(db_session, watch.id, agora=AGORA)

    assert r.menor_90d.preco == Decimal("60.00")
    assert r.menor_90d.ocorrido_em == AGORA - timedelta(days=50)


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

    # 100 por 5d, 80 por 2d, 80 por 1d, 60 por 2d: 860/10
    assert r.media_30d.media == Decimal("86.00")
    assert r.media_30d.cobertura.amostras == 4
    assert r.menor_90d.preco == Decimal("60.00")
    assert r.menor_90d.ocorrido_em == AGORA - timedelta(days=2)


def test_anuncio_inativo_fica_fora_das_duas_janelas(db_session: Session) -> None:
    watch = _watch(db_session)
    a = _listing(db_session, watch, "a")
    inativo = _listing(db_session, watch, "inativo", ativo=False)
    _leitura(db_session, a, 50, "100.00")
    _leitura(db_session, inativo, 60, "5.00")  # seria o menor e semearia a serie
    _leitura(db_session, inativo, 8, "6.00")

    r = calcular_estatisticas(db_session, watch.id, agora=AGORA)

    assert r.menor_90d.preco == Decimal("100.00")
    assert r.media_30d.media == Decimal("100.00")


def test_esgotado_sai_da_serie_e_o_outro_anuncio_assume(db_session: Session) -> None:
    watch = _watch(db_session)
    barato = _listing(db_session, watch, "barato")
    caro = _listing(db_session, watch, "caro")
    _leitura(db_session, barato, 40, "100.00")
    _leitura(db_session, barato, 20, "100.00", em_estoque=False)
    _leitura(db_session, caro, 40, "300.00")

    r = calcular_estatisticas(db_session, watch.id, agora=AGORA)

    # -30..-20: menor 100 (10d); -20..agora: so o caro, 300 (20d): 7000/30
    assert r.media_30d.media == Decimal("233.33")
    assert r.media_30d.cobertura.fracao_coberta == Decimal("1.0000")
    assert r.menor_90d.preco == Decimal("100.00")


def test_unico_anuncio_esgotado_nao_vira_zero_no_divisor(db_session: Session) -> None:
    watch = _watch(db_session)
    a = _listing(db_session, watch, "a")
    _leitura(db_session, a, 40, "100.00")
    _leitura(db_session, a, 20, "100.00", em_estoque=False)

    r = calcular_estatisticas(db_session, watch.id, agora=AGORA)

    # 10 dias com preco (de -30 a -20); os 20 sem estoque nao entram no divisor
    assert r.media_30d.media == Decimal("100.00")
    c = r.media_30d.cobertura
    assert c.dias_com_preco == Decimal("10.00")
    assert c.fracao_coberta == Decimal("0.3333")
    assert c.semeada is True
    assert c.amostras == 1  # a leitura sem estoque e observada
    assert r.menor_90d.cobertura.dias_com_preco == Decimal("20.00")  # -40..-20


def test_anuncio_esgotado_volta_a_entrar_na_serie(db_session: Session) -> None:
    watch = _watch(db_session)
    a = _listing(db_session, watch, "a")
    _leitura(db_session, a, 40, "100.00")
    _leitura(db_session, a, 20, "100.00", em_estoque=False)
    _leitura(db_session, a, 10, "150.00")

    r = calcular_estatisticas(db_session, watch.id, agora=AGORA)

    # 100 por 10d (-30..-20), ausente 10d, 150 por 10d (-10..agora): 2500/20
    assert r.media_30d.media == Decimal("125.00")
    assert r.media_30d.cobertura.dias_com_preco == Decimal("20.00")
    assert r.menor_90d.preco == Decimal("100.00")


def test_esgotado_desde_antes_da_janela_devolve_ausencia(db_session: Session) -> None:
    watch = _watch(db_session)
    a = _listing(db_session, watch, "a")
    _leitura(db_session, a, 120, "100.00")
    _leitura(db_session, a, 100, "100.00", em_estoque=False)

    r = calcular_estatisticas(db_session, watch.id, agora=AGORA)

    assert r.media_30d.media is None
    assert r.menor_90d.preco is None
    assert r.menor_90d.ocorrido_em is None
    assert r.media_30d.cobertura.dias_com_preco == Decimal("0.00")
    assert r.media_30d.cobertura.semeada is False


def test_leitura_desatualizada_e_ignorada(db_session: Session) -> None:
    watch = _watch(db_session)
    a = _listing(db_session, watch, "a")
    _leitura(db_session, a, 10, "100.00")
    _leitura(db_session, a, 5, "90.00")
    _leitura(db_session, a, 1, "80.00")

    antes = calcular_estatisticas(db_session, watch.id, agora=AGORA)
    _leitura(db_session, a, 4, "1.00", desatualizado=True)  # mudaria media e minimo
    depois = calcular_estatisticas(db_session, watch.id, agora=AGORA)

    # 100 por 5d, 90 por 4d, 80 por 1d: 940/10
    assert antes.media_30d.media == Decimal("94.00")
    assert antes.menor_90d.preco == Decimal("80.00")
    assert depois == antes
    assert depois.media_30d.cobertura.amostras == 3


def test_leituras_fora_das_janelas(db_session: Session) -> None:
    watch = _watch(db_session)
    a = _listing(db_session, watch, "a")
    _leitura(db_session, a, 91, "10.00")
    _leitura(db_session, a, 31, "20.00")
    _leitura(db_session, a, 2, "100.00")
    _leitura(db_session, a, -1, "5.00")  # futuro em relacao a referencia

    r = calcular_estatisticas(db_session, watch.id, agora=AGORA)

    # 30d: semeada com 20 (31d atras) por 28d, 100 por 2d: 760/30
    assert r.media_30d.media == Decimal("25.33")
    assert r.media_30d.cobertura.amostras == 1
    # 90d: a leitura de 91d atras e o preco vigente no inicio da janela
    assert r.menor_90d.preco == Decimal("10.00")
    assert r.menor_90d.ocorrido_em == AGORA - timedelta(days=90)
    assert r.menor_90d.cobertura.amostras == 2  # so 31d e 2d foram observadas


def test_sem_leitura_valida_devolve_ausencia_explicita(db_session: Session) -> None:
    vazio = _watch(db_session)
    so_invalido = _watch(db_session)
    a = _listing(db_session, so_invalido, "a")
    _leitura(db_session, a, 3, "50.00", desatualizado=True)
    _leitura(db_session, a, 200, "50.00", em_estoque=False)

    for watch in (vazio, so_invalido):
        r = calcular_estatisticas(db_session, watch.id, agora=AGORA)
        assert r.media_30d.media is None
        assert r.menor_90d.preco is None
        assert r.menor_90d.ocorrido_em is None
        assert r.media_30d.cobertura.amostras == 0
        assert r.media_30d.cobertura.dias_com_leitura == 0
        assert r.media_30d.cobertura.primeira_leitura is None
        assert r.media_30d.cobertura.semeada is False
        assert r.menor_90d.cobertura.amostras == 0


def test_cobertura_distingue_observada_de_semeada(db_session: Session) -> None:
    watch = _watch(db_session)
    a = _listing(db_session, watch, "a")
    _leitura(db_session, a, 45, "200.00")  # so valor arrastado nos 30d

    c = calcular_estatisticas(db_session, watch.id, agora=AGORA).media_30d.cobertura
    assert (c.amostras, c.semeada, c.primeira_leitura) == (0, True, None)

    _leitura(db_session, a, 5, "210.00")
    c = calcular_estatisticas(db_session, watch.id, agora=AGORA).media_30d.cobertura
    assert (c.amostras, c.semeada) == (1, True)
    assert c.ultima_leitura == AGORA - timedelta(days=5)


def test_cobertura_reflete_o_inserido(db_session: Session) -> None:
    watch = _watch(db_session)
    a = _listing(db_session, watch, "a")
    _leitura(db_session, a, 10.0, "100.00")
    _leitura(db_session, a, 9.75, "99.00")  # 9.75d atras = 06:00 do dia D-10
    _leitura(db_session, a, 4, "98.00")

    r = calcular_estatisticas(db_session, watch.id, agora=AGORA)
    c = r.media_30d.cobertura

    assert c.janela_dias == 30
    assert c.semeada is False
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
    _leitura(db_session, a, 1, "90.00")  # 100, 80.01, 80.01 por 1d cada -> 86.6733

    r = calcular_estatisticas(db_session, watch.id, agora=AGORA)

    assert isinstance(r.media_30d.media, Decimal)
    assert r.media_30d.media == Decimal("86.67")
    assert r.media_30d.media.as_tuple().exponent == -2
    assert isinstance(r.menor_90d.preco, Decimal)
    assert r.menor_90d.preco.as_tuple().exponent == -2


def test_relogio_inexistente_levanta_not_found(db_session: Session) -> None:
    with pytest.raises(NotFoundError):
        calcular_estatisticas(db_session, 999_999, agora=AGORA)
