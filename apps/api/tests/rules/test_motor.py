from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from pulso.adapters.permissoes import Permissoes, RegistroPermissoes
from pulso.history.service import calcular_estatisticas
from pulso.models import Candidate, Coupon, Listing, PriceReading, Watch
from pulso.rules.service import _regra_media, gerar_candidatos, limiar_da_base

AGORA = datetime(2026, 9, 26, 12, 0, tzinfo=UTC)
D = Decimal


def liberado() -> RegistroPermissoes:
    registro = RegistroPermissoes()
    registro.registrar("shopee", Permissoes(alerta_preco=True))
    return registro


def relogio(
    session: Session, ean: str = "7891234567895", preco_alvo: Decimal | None = None
) -> tuple[Watch, Listing]:
    watch = Watch(
        marca="Seiko",
        referencia_fabricante="SNK809",
        ean=ean,
        tipo_movimento="automatico",
        tamanho_caixa_mm=D("37.0"),
        preco_alvo=preco_alvo,
    )
    session.add(watch)
    session.flush()
    listing = Listing(
        watch_id=watch.id, marketplace="shopee", marketplace_item_id=f"i-{ean}", url="http://x"
    )
    session.add(listing)
    session.flush()
    return watch, listing


def leitura(
    session: Session,
    listing: Listing,
    dias_atras: float,
    preco: str,
    *,
    estoque: bool = True,
    frete: str | None = None,
    desde: datetime = AGORA,
) -> PriceReading:
    reading = PriceReading(
        listing_id=listing.id,
        coletado_em=desde - timedelta(days=dias_atras),
        preco_vista=D(preco),
        frete=None if frete is None else D(frete),
        em_estoque=estoque,
        vendedor_raw={},
        origem="teste",
    )
    session.add(reading)
    session.flush()
    return reading


def historico(session: Session, listing: Listing, base: str, atual: str, fundo: str = "1.00"):
    """Media de 30d = `base` (leitura atual em AGORA pesa zero); minimo anterior = `fundo`."""
    leitura(session, listing, 60, fundo)
    for dias in (31, 20, 10):
        leitura(session, listing, dias, base)
    return leitura(session, listing, 0, atual)


def rodar(session: Session, **kwargs):  # type: ignore[no-untyped-def]
    kwargs.setdefault("permissoes", liberado())
    return gerar_candidatos(session, agora=AGORA, **kwargs)


def total(session: Session) -> int:
    return session.scalar(select(func.count()).select_from(Candidate)) or 0


# ---- RF15: limiares por faixa e piso ----------------------------------------------------


def candidato_media(session: Session, base: str, atual: str) -> Candidate | None:
    _, listing = relogio(session)
    historico(session, listing, base, atual)
    novos = rodar(session)
    return novos[0] if novos else None


def test_faixa_barata_queda_17_5_nao_passa(db_session: Session) -> None:
    assert candidato_media(db_session, "200.00", "165.00") is None
    assert total(db_session) == 0


def test_faixa_barata_queda_22_5_passa_e_registra_a_regra(db_session: Session) -> None:
    c = candidato_media(db_session, "200.00", "155.00")
    assert c is not None
    assert c.regra == "media_30d"
    assert c.queda_percentual == D("22.50")
    assert c.economia_absoluta == D("45.00")  # 45 >= piso de 30
    assert c.media_30d == D("200.00")
    assert c.status == "pendente"


def test_piso_no_limite_passa(db_session: Session) -> None:
    c = candidato_media(db_session, "100.00", "70.00")  # 30% e exatamente R$ 30,00
    assert c is not None and c.economia_absoluta == D("30.00")


def test_piso_abaixo_nao_passa_mesmo_com_25_por_cento(db_session: Session) -> None:
    assert candidato_media(db_session, "100.00", "75.00") is None  # economia 25 < 30


def test_faixa_alta_10_5_por_cento_passa(db_session: Session) -> None:
    c = candidato_media(db_session, "2000.00", "1790.00")
    assert c is not None and c.queda_percentual == D("10.50")


def test_faixa_media_12_por_cento_nao_passa(db_session: Session) -> None:
    assert candidato_media(db_session, "1000.00", "880.00") is None  # exige 15%


def test_faixa_e_medida_pela_base_nao_pelo_preco_de_hoje(db_session: Session) -> None:
    # Pelo preco de hoje (1400, faixa media) 12,5% nao bastaria (15%); pela base (1600) basta.
    assert limiar_da_base(D("1600.00")) == D("10")
    assert limiar_da_base(D("1400.00")) == D("15")
    c = candidato_media(db_session, "1600.00", "1400.00")
    assert c is not None and c.queda_percentual == D("12.50")


def test_fronteiras_das_faixas() -> None:
    assert limiar_da_base(D("300.00")) == D("20")
    assert limiar_da_base(D("300.01")) == D("15")
    assert limiar_da_base(D("1500.00")) == D("15")
    assert limiar_da_base(D("1500.01")) == D("10")


def test_compara_preco_sem_frete_com_cupom_aplicado(db_session: Session) -> None:
    _, listing = relogio(db_session)
    db_session.add(
        Coupon(
            codigo="DEZ",
            marketplace="shopee",
            regra_texto="10%",
            tipo="percentual",
            valor=D("10.00"),
        )
    )
    historico(db_session, listing, "200.00", "170.00")  # 15% sozinho nao passa; com cupom 23,5%
    c = rodar(db_session)[0]
    assert c.preco_sem_frete == D("153.00") and c.desconto == D("17.00")
    assert c.coupon_id is not None and c.queda_percentual == D("23.50")


# ---- RF15: minimo de 90 dias ------------------------------------------------------------


def test_minimo_90d_dispara_sem_passar_o_limiar_da_media(db_session: Session) -> None:
    _, listing = relogio(db_session)
    # o minimo anterior (200) vem de leitura observada; 165 e 17,5% abaixo, nao passa na media
    historico(db_session, listing, "200.00", "165.00", fundo="200.00")
    c = rodar(db_session)[0]
    assert c.regra == "minimo_90d"
    assert c.minimo_90d == D(
        "200.00"
    )  # o ANTERIOR: com a leitura atual seria 165 e nunca dispararia
    assert c.economia_absoluta == D("35.00")


def test_preco_estavel_no_minimo_nao_dispara(db_session: Session) -> None:
    _, listing = relogio(db_session)
    historico(db_session, listing, "200.00", "200.00", fundo="200.00")
    assert rodar(db_session) == []


def test_minimo_90d_respeita_o_piso(db_session: Session) -> None:
    _, listing = relogio(db_session)
    historico(db_session, listing, "200.00", "175.00", fundo="200.00")  # economia 25 < 30
    assert rodar(db_session) == []


def test_minimo_semeado_nao_conta(db_session: Session) -> None:
    _, listing = relogio(db_session)
    leitura(db_session, listing, 100, "200.00")  # fora da janela de 90d: so semeia
    leitura(db_session, listing, 10, "250.00")
    leitura(db_session, listing, 0, "165.00")
    assert rodar(db_session) == []


def test_minimo_90d_alerta_a_transicao_uma_vez_so(db_session: Session) -> None:
    _, listing = relogio(db_session)
    historico(db_session, listing, "200.00", "165.00", fundo="200.00")
    assert len(rodar(db_session)) == 1
    # dois dias depois, sem leitura nova: o RF16 ja liberou, mas a transicao nao se repete
    assert (
        gerar_candidatos(db_session, agora=AGORA + timedelta(days=2), permissoes=liberado()) == []
    )


# ---- RF15: preco-alvo e historico ralo --------------------------------------------------


def test_preco_alvo_dispara_sem_historico(db_session: Session) -> None:
    _, listing = relogio(db_session, preco_alvo=D("300.00"))
    leitura(db_session, listing, 0, "290.00")
    c = rodar(db_session)[0]
    assert c.regra == "preco_alvo" and c.preco_alvo == D("300.00")


def test_preco_acima_do_alvo_nao_dispara(db_session: Session) -> None:
    _, listing = relogio(db_session, preco_alvo=D("300.00"))
    leitura(db_session, listing, 0, "300.01")
    assert rodar(db_session) == []


def sem_media(session: Session, atual: str = "155.00") -> bool:
    """A regra da media nao dispara, embora 155 esteja 22,5% abaixo de 200."""
    stats = calcular_estatisticas(session, session.scalars(select(Watch.id)).one(), agora=AGORA)
    return _regra_media(stats, D(atual)) is None


def test_historico_ralo_por_poucas_leituras_bloqueia_a_media(db_session: Session) -> None:
    _, listing = relogio(db_session)
    leitura(db_session, listing, 31, "200.00")  # semente: cobertura total, mas so 2 amostras
    leitura(db_session, listing, 10, "200.00")
    leitura(db_session, listing, 0, "155.00")
    assert sem_media(db_session)


def test_historico_ralo_por_cobertura_bloqueia_a_media(db_session: Session) -> None:
    _, listing = relogio(db_session)
    for dias in (5, 3):  # 3 amostras com a atual, mas so 5 de 30 dias cobertos (< 0,5)
        leitura(db_session, listing, dias, "200.00")
    leitura(db_session, listing, 0, "155.00")
    assert sem_media(db_session)


def test_historico_farto_habilita_a_media(db_session: Session) -> None:
    _, listing = relogio(db_session)
    historico(db_session, listing, "200.00", "155.00")
    assert not sem_media(db_session)


def test_historico_ralo_nao_impede_o_preco_alvo(db_session: Session) -> None:
    _, listing = relogio(db_session, preco_alvo=D("160.00"))
    for dias in (5, 3):
        leitura(db_session, listing, dias, "200.00")
    leitura(db_session, listing, 0, "155.00")
    assert len(rodar(db_session)) == 1


# ---- RF16 -------------------------------------------------------------------------------


def test_rf16_bloqueia_repeticao_e_libera_com_queda_adicional(db_session: Session) -> None:
    watch, listing = relogio(db_session, preco_alvo=D("300.00"))
    leitura(db_session, listing, 0, "290.00")
    assert len(rodar(db_session)) == 1

    def com_preco(preco: str, horas: int) -> list[Candidate]:
        agora = AGORA + timedelta(hours=horas)
        leitura(db_session, listing, 0, preco, desde=agora)
        return gerar_candidatos(db_session, agora=agora, permissoes=liberado())

    assert com_preco("290.00", 1) == []  # mesmo preco
    assert com_preco("281.30", 2) == []  # queda adicional de 3%
    assert len(com_preco("275.50", 3)) == 1  # exatamente 5% sobre 290,00
    assert total(db_session) == 2
    assert {c.watch_id for c in db_session.scalars(select(Candidate))} == {watch.id}


def test_rf16_libera_depois_de_24_horas(db_session: Session) -> None:
    _, listing = relogio(db_session, preco_alvo=D("300.00"))
    leitura(db_session, listing, 0, "290.00")
    assert len(rodar(db_session)) == 1
    depois = gerar_candidatos(db_session, agora=AGORA + timedelta(hours=25), permissoes=liberado())
    assert len(depois) == 1


def test_rf16_idempotencia_nao_duplica(db_session: Session) -> None:
    _, listing = relogio(db_session, preco_alvo=D("300.00"))
    leitura(db_session, listing, 0, "290.00")
    assert len(rodar(db_session)) == 1
    assert total(db_session) == 1
    assert rodar(db_session) == []
    assert total(db_session) == 1


def test_descartado_tambem_conta_para_o_rf16(db_session: Session) -> None:
    _, listing = relogio(db_session, preco_alvo=D("300.00"))
    leitura(db_session, listing, 0, "290.00")
    primeiro = rodar(db_session)[0]
    primeiro.status = "descartado"
    db_session.flush()
    assert rodar(db_session) == []


# ---- RF17 -------------------------------------------------------------------------------


def test_rf17_sem_estoque_descarta(db_session: Session) -> None:
    _, listing = relogio(db_session, preco_alvo=D("300.00"))
    leitura(db_session, listing, 0, "290.00", estoque=False)
    assert rodar(db_session) == []


def test_rf17_alerta_preco_falso_descarta(db_session: Session) -> None:
    _, listing = relogio(db_session, preco_alvo=D("300.00"))
    leitura(db_session, listing, 0, "290.00")
    registro = RegistroPermissoes()
    registro.registrar("shopee", Permissoes(alerta_preco=False))
    assert rodar(db_session, permissoes=registro) == []


def test_rf17_mapa_de_permissoes_vazio_nao_gera_nada(db_session: Session) -> None:
    # Configuracao real de hoje: nada gera candidato. Correto, nao e bug.
    _, listing = relogio(db_session, preco_alvo=D("300.00"))
    historico(db_session, listing, "200.00", "100.00", fundo="200.00")
    assert gerar_candidatos(db_session, agora=AGORA) == []
    assert total(db_session) == 0


def test_rf17_vendedor_nao_confiavel_descarta(db_session: Session) -> None:
    _, listing = relogio(db_session, preco_alvo=D("300.00"))
    leitura(db_session, listing, 0, "290.00")
    assert rodar(db_session, politicas={"shopee": lambda w, li, r: False}) == []
    assert len(rodar(db_session, politicas={"shopee": lambda w, li, r: True})) == 1


# ---- RF18 e frete -----------------------------------------------------------------------


def test_rf18_ordena_por_queda_e_desempata_por_economia(db_session: Session) -> None:
    casos = [("1", "200.00", "150.00"), ("2", "400.00", "300.00"), ("3", "200.00", "130.00")]
    for ean, base, atual in casos:
        _, listing = relogio(db_session, ean=f"789000000000{ean}")
        historico(db_session, listing, base, atual)
    novos = rodar(db_session)
    # 35% (economia 70), depois 25% com economia 100, depois 25% com economia 50
    assert [(c.queda_percentual, c.economia_absoluta) for c in novos] == [
        (D("35.00"), D("70.00")),
        (D("25.00"), D("100.00")),
        (D("25.00"), D("50.00")),
    ]


def test_candidato_carrega_o_frete(db_session: Session) -> None:
    _, listing = relogio(db_session, preco_alvo=D("300.00"))
    leitura(db_session, listing, 0, "290.00", frete="19.90")
    c = rodar(db_session)[0]
    assert c.frete == D("19.90") and c.frete_desconhecido is False


def test_candidato_marca_frete_desconhecido(db_session: Session) -> None:
    _, listing = relogio(db_session, preco_alvo=D("300.00"))
    leitura(db_session, listing, 0, "290.00")
    c = rodar(db_session)[0]
    assert c.frete is None and c.frete_desconhecido is True
