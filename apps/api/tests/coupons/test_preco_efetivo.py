from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

from pulso.coupons.effective_price import Motivo, calcular_preco_efetivo, preco_efetivo_da_leitura
from pulso.models import Coupon, Listing, PriceReading, Watch

AGORA = datetime(2026, 9, 26, 12, 0, tzinfo=UTC)
D = Decimal


def cup(id: int = 1, **extra: object) -> Coupon:
    base: dict[str, object] = {
        "id": id,
        "codigo": f"C{id}",
        "marketplace": "shopee",
        "regra_texto": "",
        "tipo": "percentual",
        "valor": D("10.00"),
        "minimo_compra": None,
        "teto_desconto": None,
        "loja": None,
        "valido_ate": None,
        "ativo": True,
    }
    return Coupon(**(base | extra))


def test_sem_cupom_declara_inexistente() -> None:
    r = calcular_preco_efetivo(D("500.00"), D("20.00"), [], AGORA)
    assert r.coupon_id is None and r.motivo_sem_cupom is Motivo.INEXISTENTE
    assert r.desconto == D("0.00") and r.preco_efetivo == D("520.00")


def test_percentual_calculado_a_mao() -> None:
    # 10% de 800,00 = 80,00; 800 - 80 + 25 = 745,00
    r = calcular_preco_efetivo(D("800.00"), D("25.00"), [cup()], AGORA)
    assert r.coupon_id == 1 and r.motivo_sem_cupom is None
    assert r.desconto == D("80.00")
    assert r.preco_sem_frete == D("720.00")
    assert r.preco_efetivo == D("745.00")
    assert r.frete_desconhecido is False


def test_percentual_arredonda_centavos() -> None:
    # 15% de 99,99 = 14,9985 -> 15,00
    r = calcular_preco_efetivo(D("99.99"), D("0.00"), [cup(valor=D("15.00"))], AGORA)
    assert r.desconto == D("15.00") and r.preco_efetivo == D("84.99")


def test_valor_fixo() -> None:
    c = cup(tipo="valor_fixo", valor=D("40.00"))
    r = calcular_preco_efetivo(D("300.00"), D("10.00"), [c], AGORA)
    assert r.desconto == D("40.00") and r.preco_efetivo == D("270.00")


def test_teto_limita_o_desconto() -> None:
    # 10% de 1000 = 100, mas o teto e 50
    c = cup(teto_desconto=D("50.00"))
    r = calcular_preco_efetivo(D("1000.00"), D("0.00"), [c], AGORA)
    assert r.desconto == D("50.00") and r.preco_efetivo == D("950.00")


def test_teto_nao_atrapalha_quando_desconto_e_menor() -> None:
    c = cup(teto_desconto=D("50.00"))
    assert calcular_preco_efetivo(D("200.00"), D("0.00"), [c], AGORA).desconto == D("20.00")


def test_minimo_nao_atingido_nao_abate_e_diz_o_motivo() -> None:
    c = cup(minimo_compra=D("200.00"))
    r = calcular_preco_efetivo(D("199.99"), D("10.00"), [c], AGORA)
    assert r.coupon_id is None and r.desconto == D("0.00")
    assert r.motivo_sem_cupom is Motivo.MINIMO_NAO_ATINGIDO
    assert r.preco_efetivo == D("209.99")
    assert r.avaliacoes[0].motivo is Motivo.MINIMO_NAO_ATINGIDO
    # no limite, aplica
    ok = calcular_preco_efetivo(D("200.00"), D("0.00"), [c], AGORA)
    assert ok.coupon_id == 1 and ok.desconto == D("20.00")


def test_cupom_vencido_nao_abate_e_diz_o_motivo() -> None:
    c = cup(valido_ate=AGORA - timedelta(seconds=1))
    r = calcular_preco_efetivo(D("500.00"), D("0.00"), [c], AGORA)
    assert r.coupon_id is None and r.motivo_sem_cupom is Motivo.VENCIDO
    assert r.preco_efetivo == D("500.00")
    # exatamente no instante de validade ainda vale
    limite = calcular_preco_efetivo(D("500.00"), D("0.00"), [cup(valido_ate=AGORA)], AGORA)
    assert limite.coupon_id == 1


def test_cupom_inativo_nao_se_aplica() -> None:
    r = calcular_preco_efetivo(D("500.00"), D("0.00"), [cup(ativo=False)], AGORA)
    assert r.coupon_id is None and r.motivo_sem_cupom is Motivo.INATIVO


def test_frete_nulo_nao_vira_zero() -> None:
    r = calcular_preco_efetivo(D("800.00"), None, [cup()], AGORA)
    assert r.frete_desconhecido is True and r.frete is None
    assert r.preco_efetivo is None  # nao e 720.00: sem frete conhecido nao ha preco efetivo
    assert r.preco_sem_frete == D("720.00")
    zero = calcular_preco_efetivo(D("800.00"), D("0.00"), [cup()], AGORA)
    assert zero.frete_desconhecido is False and zero.preco_efetivo == D("720.00")


def test_cupom_de_loja_nao_e_aplicado_automaticamente() -> None:
    """Decisao do projeto: a listing nao tem loja e vendedor_raw e cru, nao interpretado.

    O cupom de loja fica cadastrado para o operador decidir na revisao (RF19).
    """
    r = calcular_preco_efetivo(D("500.00"), D("0.00"), [cup(loja="Loja X")], AGORA)
    assert r.coupon_id is None and r.desconto == D("0.00")
    assert r.motivo_sem_cupom is Motivo.CUPOM_DE_LOJA
    assert r.preco_efetivo == D("500.00")
    # nao esconde o cupom do marketplace inteiro, mesmo sendo o de loja mais vantajoso
    cupons = [cup(1, loja="Loja X", valor=D("50.00")), cup(2, valor=D("5.00"))]
    r = calcular_preco_efetivo(D("500.00"), D("0.00"), cupons, AGORA)
    assert r.coupon_id == 2 and r.desconto == D("25.00")


def test_desconto_nunca_deixa_preco_negativo() -> None:
    c = cup(tipo="valor_fixo", valor=D("100.00"))
    r = calcular_preco_efetivo(D("30.00"), D("0.00"), [c], AGORA)
    assert r.desconto == D("30.00") and r.preco_efetivo == D("0.00")
    r = calcular_preco_efetivo(D("30.00"), D("12.00"), [c], AGORA)
    assert r.preco_sem_frete == D("0.00") and r.preco_efetivo == D("12.00")


def test_varios_cupons_vence_o_maior_desconto() -> None:
    cupons = [
        cup(1, valor=D("5.00")),
        cup(2, valor=D("10.00")),
        cup(3, tipo="valor_fixo", valor=D("39.00")),
    ]
    r = calcular_preco_efetivo(D("400.00"), D("0.00"), cupons, AGORA)
    assert r.coupon_id == 2 and r.desconto == D("40.00")
    assert {a.coupon_id: a.motivo for a in r.avaliacoes}[1] is Motivo.NAO_ESCOLHIDO


def test_referencia_sem_fuso_e_rejeitada() -> None:
    with pytest.raises(ValueError):
        calcular_preco_efetivo(D("1.00"), None, [], datetime(2026, 9, 26))


def test_da_leitura_usa_cupons_do_marketplace_do_anuncio(db_session: Session) -> None:
    watch = Watch(
        marca="Seiko",
        referencia_fabricante="X",
        ean="7890000099991",
        tipo_movimento="quartzo",
        tamanho_caixa_mm=D("40.0"),
    )
    db_session.add(watch)
    db_session.flush()
    listing = Listing(watch_id=watch.id, marketplace="shopee", marketplace_item_id="i1", url="u")
    db_session.add(listing)
    db_session.flush()
    reading = PriceReading(
        listing_id=listing.id,
        coletado_em=AGORA,
        preco_vista=D("1000.00"),
        frete=D("30.00"),
        em_estoque=True,
        vendedor_raw={},
        origem="teste",
    )
    shopee = Coupon(
        codigo="SH",
        marketplace="shopee",
        regra_texto="",
        tipo="percentual",
        valor=D("10.00"),
        teto_desconto=D("50.00"),
    )
    ali = Coupon(
        codigo="ALI",
        marketplace="aliexpress",
        regra_texto="",
        tipo="valor_fixo",
        valor=D("500.00"),
    )
    db_session.add_all([reading, shopee, ali])
    db_session.flush()
    r = preco_efetivo_da_leitura(db_session, reading, AGORA)
    assert r.desconto == D("50.00") and r.preco_efetivo == D("980.00")
