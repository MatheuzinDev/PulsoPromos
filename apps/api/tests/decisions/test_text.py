from decimal import Decimal

from pulso.decisions.text import compor_texto
from pulso.models import Candidate, Coupon, Listing, Watch

D = Decimal


def watch(**extra: object) -> Watch:
    base: dict[str, object] = dict(
        id=1,
        marca="Seiko",
        referencia_fabricante="SNK809",
        ean="7891234567895",
        tipo_movimento="automatico",
        tamanho_caixa_mm=D("37.0"),
    )
    return Watch(**(base | extra))  # type: ignore[arg-type]


def listing(**extra: object) -> Listing:
    base: dict[str, object] = dict(
        id=1,
        watch_id=1,
        marketplace="shopee",
        marketplace_item_id="i-1",
        url="https://shopee.com.br/produto-1",
    )
    return Listing(**(base | extra))  # type: ignore[arg-type]


def candidate(**extra: object) -> Candidate:
    base: dict[str, object] = dict(
        id=1,
        watch_id=1,
        listing_id=1,
        coupon_id=None,
        regra="media_30d",
        preco_vista=D("155.00"),
        desconto=D("0.00"),
        preco_sem_frete=D("155.00"),
        frete=D("19.90"),
        frete_desconhecido=False,
        media_30d=D("190.00"),
        minimo_90d=None,
        preco_alvo=None,
        queda_percentual=D("18.42"),
        economia_absoluta=D("35.00"),
        pontuacao=D("18.4200"),
        status="pendente",
    )
    return Candidate(**(base | extra))  # type: ignore[arg-type]


def coupon(**extra: object) -> Coupon:
    base: dict[str, object] = dict(
        id=1,
        codigo="PROMO10",
        marketplace="shopee",
        regra_texto="",
        tipo="valor_fixo",
        valor=D("10.00"),
        ativo=True,
    )
    return Coupon(**(base | extra))  # type: ignore[arg-type]


def test_texto_completo_escrito_a_mao_linha_por_linha() -> None:
    c = candidate(
        coupon_id=1, desconto=D("10.00"), preco_vista=D("155.00"), preco_sem_frete=D("145.00")
    )
    texto = compor_texto(c, watch(), listing(), coupon(), loja="Loja Oficial Seiko")

    esperado = (
        "⌚ Seiko SNK809 — Automático, 37mm\n"
        "\n"
        "R$ 155,00 a vista\n"
        "18% abaixo da media de 30 dias (R$ 190,00)\n"
        "\n"
        "🎟️ Cupom PROMO10 (-R$ 10,00)\n"
        "🏪 Loja Oficial Seiko - Shopee\n"
        "🚚 Frete R$ 19,90\n"
        "\n"
        "🔗 https://shopee.com.br/produto-1"
    )
    assert texto == esperado


def test_sem_cupom_aplicado_linha_de_cupom_some_sem_deixar_branco_sobrando() -> None:
    c = candidate(coupon_id=None)
    texto = compor_texto(c, watch(), listing(), None, loja="Loja Oficial Seiko")

    linhas = texto.split("\n")
    assert not any("Cupom" in linha for linha in linhas)
    # so os 3 blocos fixos de blanco (apos cabecalho, apos preco/comparacao, antes do link)
    assert linhas.count("") == 3
    assert linhas[linhas.index("🏪 Loja Oficial Seiko - Shopee") - 1] == ""


def test_regra_media_30d_nao_mostra_menor_preco_em_90_dias() -> None:
    c = candidate(regra="media_30d")
    texto = compor_texto(c, watch(), listing(), None, loja="Loja Oficial Seiko")
    assert "Menor preco em 90 dias" not in texto


def test_regra_minimo_90d_mostra_menor_preco_em_90_dias() -> None:
    c = candidate(regra="minimo_90d", minimo_90d=D("150.00"))
    texto = compor_texto(c, watch(), listing(), None, loja="Loja Oficial Seiko")
    assert "Menor preco em 90 dias" in texto


def test_frete_desconhecido() -> None:
    c = candidate(frete=None, frete_desconhecido=True)
    texto = compor_texto(c, watch(), listing(), None, loja="Loja Oficial Seiko")
    linha_frete = next(linha for linha in texto.split("\n") if "Frete" in linha)
    assert linha_frete == "🚚 Frete a calcular"


def test_frete_conhecido_mostra_valor_formatado() -> None:
    c = candidate(frete=D("19.90"), frete_desconhecido=False)
    texto = compor_texto(c, watch(), listing(), None, loja="Loja Oficial Seiko")
    assert "Frete R$ 19,90" in texto


def test_dinheiro_formatado_em_pt_br_milhar_e_decimal() -> None:
    c = candidate(preco_vista=D("1234.50"), media_30d=None)
    texto = compor_texto(c, watch(), listing(), None, loja="Loja Oficial Seiko")
    assert "R$ 1.234,50 a vista" in texto


def test_comparacao_com_media_so_aparece_quando_media_30d_existe() -> None:
    c = candidate(media_30d=None)
    texto = compor_texto(c, watch(), listing(), None, loja="Loja Oficial Seiko")
    assert "abaixo da media" not in texto


def test_comparacao_some_quando_preco_nao_esta_abaixo_da_media() -> None:
    """media_30d pode existir mesmo quando quem disparou foi minimo_90d ou preco_alvo (ver
    rules/service.py); mostrar "0% abaixo" ou um percentual negativo seria enganoso."""
    c = candidate(regra="preco_alvo", media_30d=D("100.00"), preco_vista=D("120.00"))
    texto = compor_texto(c, watch(), listing(), None, loja="Loja Oficial Seiko")
    assert "abaixo da media" not in texto


def test_texto_nao_tem_aviso_de_afiliado_nem_carimbo_de_data_hora() -> None:
    """RF24 (aviso de afiliado, aviso de preco/cupom variavel, carimbo de data e hora) fica
    fora desta task de proposito: o dono do projeto pediu o formato so ate o link "por
    enquanto"; entra antes da primeira publicacao real."""
    c = candidate(coupon_id=1, desconto=D("10.00"))
    texto = compor_texto(c, watch(), listing(), coupon(), loja="Loja Oficial Seiko")

    baixo = texto.lower()
    for termo in ("afiliad", "pode mudar", "sujeito a", "publicado em", "atualizado em"):
        assert termo not in baixo
    for ano in ("2024", "2025", "2026", "2027"):
        assert ano not in texto
