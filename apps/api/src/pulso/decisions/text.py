"""Composicao do texto do post (RF22 - so a composicao; o envio ao Telegram e o RF19).

RF24 (aviso de afiliado, aviso de que preco/cupom podem mudar, carimbo de data e hora) fica DE
FORA de proposito: o dono do projeto pediu o formato so ate o link "por enquanto" e o RF24 entra
antes da primeira publicacao real. Nao e esquecimento.

Formato (linhas condicionais nunca deixam branco sobrando; so as tres quebras de bloco abaixo
sao fixas):

    (linha 1) emoji de relogio, marca e referencia, travessao, tipo de movimento e tamanho da caixa
    (branco)
    (preco)   "R$ 155,00 a vista"
    (compara) "18% abaixo da media de 30 dias (R$ 190,00)"      -- so quando ha media_30d E o
                                                                    preco esta de fato abaixo dela
    (minimo)  "Menor preco em 90 dias"                          -- so quando regra == minimo_90d
    (branco)
    (cupom)   "Cupom PROMO10 (-R$ 10,00)"                       -- so quando um cupom foi aplicado
    (loja)    "Loja Oficial Seiko - Shopee"
    (frete)   "Frete a calcular" ou "Frete R$ 19,90"
    (branco)
    (link)    o link
"""

from decimal import ROUND_HALF_UP, Decimal

from pulso.models import Candidate, Coupon, Listing, Watch

_CENTAVOS = Decimal("0.01")
_CEM = Decimal(100)

_EMOJI_RELOGIO = "⌚"
_EMOJI_CUPOM = "🎟️"
_EMOJI_LOJA = "🏪"
_EMOJI_FRETE = "🚚"
_EMOJI_LINK = "🔗"

_TIPO_MOVIMENTO_TEXTO = {
    "automatico": "Automático",
    "quartzo": "Quartzo",
    "manual": "Manual",
    "solar": "Solar",
    "hibrido": "Híbrido",
}

_MARKETPLACE_TEXTO = {
    "shopee": "Shopee",
    "aliexpress": "AliExpress",
    "mercado_livre": "Mercado Livre",
    "amazon": "Amazon",
}


def _formatar_dinheiro(valor: Decimal) -> str:
    """1234.50 -> "R$ 1.234,50" (pt-BR, sem depender de locale do sistema)."""
    quantizado = valor.quantize(_CENTAVOS, rounding=ROUND_HALF_UP)
    bruto = f"{quantizado:,.2f}"  # "1,234.50"
    pt_br = bruto.replace(",", "_").replace(".", ",").replace("_", ".")
    return f"R$ {pt_br}"


def _formatar_tamanho_caixa(valor: Decimal) -> str:
    inteiro = valor.to_integral_value()
    if valor == inteiro:
        return f"{int(inteiro)}mm"
    texto = f"{valor:.1f}".replace(".", ",")
    return f"{texto}mm"


def _linha_cabecalho(watch: Watch) -> str:
    tipo = _TIPO_MOVIMENTO_TEXTO[watch.tipo_movimento]
    tamanho = _formatar_tamanho_caixa(watch.tamanho_caixa_mm)
    return f"{_EMOJI_RELOGIO} {watch.marca} {watch.referencia_fabricante} — {tipo}, {tamanho}"


def _linha_preco(candidate: Candidate) -> str:
    return f"{_formatar_dinheiro(candidate.preco_vista)} à vista"


def _linha_comparacao_media(candidate: Candidate) -> str | None:
    """So aparece quando ha media_30d E o preco publicado esta de fato abaixo dela.

    O candidato pode ter media_30d preenchida mesmo quando a regra que disparou foi minimo_90d
    ou preco_alvo (ver rules/service.py); mostrar "X% abaixo da media" com X <= 0 seria enganoso
    para quem le o post, entao a linha some nesse caso em vez de exibir zero ou negativo.
    """
    media = candidate.media_30d
    if media is None or media <= 0 or candidate.preco_vista >= media:
        return None
    percentual = (media - candidate.preco_vista) / media * _CEM
    percentual_inteiro = percentual.quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return f"{percentual_inteiro}% abaixo da média de 30 dias ({_formatar_dinheiro(media)})"


def _linha_cupom(coupon: Coupon, desconto: Decimal) -> str:
    return f"{_EMOJI_CUPOM} Cupom {coupon.codigo} (-{_formatar_dinheiro(desconto)})"


def _linha_loja(loja: str, marketplace: str) -> str:
    return f"{_EMOJI_LOJA} {loja} - {_MARKETPLACE_TEXTO[marketplace]}"


def _linha_frete(candidate: Candidate) -> str:
    if candidate.frete_desconhecido or candidate.frete is None:
        return f"{_EMOJI_FRETE} Frete a calcular"
    return f"{_EMOJI_FRETE} Frete {_formatar_dinheiro(candidate.frete)}"


def _linha_link(url: str) -> str:
    return f"{_EMOJI_LINK} {url}"


def compor_texto(
    candidate: Candidate,
    watch: Watch,
    listing: Listing,
    coupon: Coupon | None,
    *,
    loja: str,
) -> str:
    """Monta o texto do post (RF22) a partir do retrato congelado no candidato.

    `loja` vem de quem aprova (RF20): o nucleo nao sabe a loja hoje (ver decisions/service.py).
    """
    linhas = [_linha_cabecalho(watch), "", _linha_preco(candidate)]

    comparacao = _linha_comparacao_media(candidate)
    if comparacao is not None:
        linhas.append(comparacao)
    if candidate.regra == "minimo_90d":
        linhas.append("Menor preço em 90 dias")

    linhas.append("")

    if candidate.coupon_id is not None and coupon is not None:
        linhas.append(_linha_cupom(coupon, candidate.desconto))
    linhas.append(_linha_loja(loja, listing.marketplace))
    linhas.append(_linha_frete(candidate))

    linhas.append("")
    linhas.append(_linha_link(listing.url))

    return "\n".join(linhas)
