"""Preco efetivo (RF14): preco a vista MENOS desconto do cupom MAIS frete.

Regras que o requisito nao diz:
- FRETE DESCONHECIDO NAO E FRETE ZERO. Com `frete` NULL, `preco_efetivo` fica None e
  `frete_desconhecido` fica True; `preco_sem_frete` traz o que se sabe (RF19 mostra ao operador).
- Cupom vencido (`valido_ate` anterior ao instante de referencia) ou inativo nao se aplica.
- Compra abaixo de `minimo_compra` (comparada com o preco a vista, sem frete): nao abate nada.
- `teto_desconto` limita o desconto e o desconto nunca passa do preco (efetivo >= frete).
- ESCOPO: so se aplica sozinho cupom com `loja` NULL (marketplace inteiro). A `listing` nao
  tem identificador de loja e o vendedor vive em `vendedor_raw`, payload cru que o nucleo NAO
  interpreta; cupom de loja fica cadastrado para o operador decidir na revisao, e aparece
  nas `avaliacoes` como `cupom_de_loja`. Isto e decisao do projeto, nao esquecimento.
- Com varios cupons aplicaveis, vence o de maior desconto (empate: o de menor id).
"""

from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum

from sqlalchemy import select
from sqlalchemy.orm import Session

from pulso.catalog.errors import NotFoundError
from pulso.models import Coupon, Listing, PriceReading

_CENTAVOS = Decimal("0.01")


class Motivo(StrEnum):
    APLICADO = "aplicado"
    INEXISTENTE = "inexistente"  # so no motivo geral: nenhum cupom cadastrado para o marketplace
    VENCIDO = "vencido"
    INATIVO = "inativo"
    MINIMO_NAO_ATINGIDO = "minimo_nao_atingido"
    CUPOM_DE_LOJA = "cupom_de_loja"  # exige decisao do operador (ver docstring do modulo)
    NAO_ESCOLHIDO = "nao_escolhido"  # aplicavel, mas outro cupom abate mais


# quando nenhum cupom se aplica, o motivo geral e o mais "informativo" entre as avaliacoes
_PRECEDENCIA = (
    Motivo.MINIMO_NAO_ATINGIDO,
    Motivo.VENCIDO,
    Motivo.INATIVO,
    Motivo.CUPOM_DE_LOJA,
)


@dataclass(frozen=True)
class AvaliacaoCupom:
    coupon_id: int
    codigo: str
    motivo: Motivo
    desconto: Decimal  # o que abateria; 0.00 quando nao aplicavel


@dataclass(frozen=True)
class PrecoEfetivo:
    referencia: datetime
    preco_vista: Decimal
    frete: Decimal | None
    frete_desconhecido: bool
    coupon_id: int | None  # o cupom aplicado; None se nenhum
    desconto: Decimal  # 0.00 sem cupom
    motivo_sem_cupom: Motivo | None  # None quando ha cupom aplicado
    preco_sem_frete: Decimal  # preco_vista - desconto
    preco_efetivo: Decimal | None  # None enquanto o frete for desconhecido
    avaliacoes: tuple[AvaliacaoCupom, ...]


def _desconto_bruto(coupon: Coupon, preco: Decimal) -> Decimal:
    if coupon.tipo == "percentual":
        desconto = (preco * coupon.valor / 100).quantize(_CENTAVOS, rounding=ROUND_HALF_UP)
        if coupon.teto_desconto is not None:
            desconto = min(desconto, coupon.teto_desconto)
    else:
        desconto = coupon.valor
    return min(desconto, preco)  # nunca deixa o preco negativo


def _avaliar(coupon: Coupon, preco: Decimal, referencia: datetime) -> AvaliacaoCupom:
    def nao(motivo: Motivo) -> AvaliacaoCupom:
        return AvaliacaoCupom(coupon.id, coupon.codigo, motivo, Decimal("0.00"))

    if not coupon.ativo:
        return nao(Motivo.INATIVO)
    if coupon.valido_ate is not None and coupon.valido_ate < referencia:
        return nao(Motivo.VENCIDO)
    if coupon.loja is not None:
        return nao(Motivo.CUPOM_DE_LOJA)
    if coupon.minimo_compra is not None and preco < coupon.minimo_compra:
        return nao(Motivo.MINIMO_NAO_ATINGIDO)
    return AvaliacaoCupom(coupon.id, coupon.codigo, Motivo.APLICADO, _desconto_bruto(coupon, preco))


def calcular_preco_efetivo(
    preco_vista: Decimal,
    frete: Decimal | None,
    cupons: list[Coupon],
    referencia: datetime,
) -> PrecoEfetivo:
    """Nucleo puro. `cupons` sao os do marketplace do anuncio; `referencia` precisa ter fuso."""
    if referencia.tzinfo is None:
        raise ValueError("referencia precisa ter fuso (timestamptz em UTC)")
    avaliacoes = [_avaliar(c, preco_vista, referencia) for c in sorted(cupons, key=lambda c: c.id)]
    aplicaveis = [a for a in avaliacoes if a.motivo is Motivo.APLICADO]
    escolhido = max(aplicaveis, key=lambda a: (a.desconto, -a.coupon_id), default=None)

    if escolhido is not None:
        avaliacoes = [
            a
            if a is escolhido or a.motivo is not Motivo.APLICADO
            else AvaliacaoCupom(a.coupon_id, a.codigo, Motivo.NAO_ESCOLHIDO, a.desconto)
            for a in avaliacoes
        ]
        motivo_sem_cupom = None
    else:
        motivos = {a.motivo for a in avaliacoes}
        motivo_sem_cupom = next((m for m in _PRECEDENCIA if m in motivos), Motivo.INEXISTENTE)

    desconto = escolhido.desconto if escolhido else Decimal("0.00")
    sem_frete = preco_vista - desconto
    return PrecoEfetivo(
        referencia=referencia,
        preco_vista=preco_vista,
        frete=frete,
        frete_desconhecido=frete is None,
        coupon_id=escolhido.coupon_id if escolhido else None,
        desconto=desconto,
        motivo_sem_cupom=motivo_sem_cupom,
        preco_sem_frete=sem_frete,
        preco_efetivo=None if frete is None else sem_frete + frete,
        avaliacoes=tuple(avaliacoes),
    )


def preco_efetivo_da_leitura(
    session: Session, reading: PriceReading, referencia: datetime
) -> PrecoEfetivo:
    listing = session.get(Listing, reading.listing_id)
    if listing is None:
        raise NotFoundError(f"Anuncio {reading.listing_id} nao encontrado")
    cupons = list(session.scalars(select(Coupon).where(Coupon.marketplace == listing.marketplace)))
    return calcular_preco_efetivo(reading.preco_vista, reading.frete, cupons, referencia)


def preco_efetivo_do_anuncio(
    session: Session, listing_id: int, referencia: datetime
) -> PrecoEfetivo:
    """Usa a leitura mais recente em ou antes de `referencia`."""
    reading = session.scalars(
        select(PriceReading)
        .where(PriceReading.listing_id == listing_id, PriceReading.coletado_em <= referencia)
        .order_by(PriceReading.coletado_em.desc())
        .limit(1)
    ).first()
    if reading is None:
        raise NotFoundError(f"Anuncio {listing_id} nao tem leitura de preco")
    return preco_efetivo_da_leitura(session, reading, referencia)
