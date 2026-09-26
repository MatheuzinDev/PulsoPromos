"""Estatisticas de preco por relogio sobre o historico proprio (RF12, RNF09).

Preco considerado: `preco_vista`. Leituras `desatualizado` ficam de fora (RNF09).
A serie do relogio tem um ponto por instante de coleta; o valor do ponto e o menor
preco entre os anuncios naquele instante, onde cada anuncio vale sua ultima leitura
valida dentro da janela ate aquele instante. Leitura anterior a janela nao entra.
Este modulo so calcula: limiar de amostras e regra de alerta sao do RF15.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from pulso.catalog.errors import NotFoundError
from pulso.models import Listing, PriceReading, Watch

JANELA_MEDIA_DIAS = 30
JANELA_MINIMO_DIAS = 90
_CENTAVOS = Decimal("0.01")


@dataclass(frozen=True)
class Cobertura:
    """Quanto da janela o historico realmente cobre."""

    janela_dias: int
    amostras: int  # instantes distintos da serie
    dias_com_leitura: int  # dias UTC distintos com ao menos uma leitura
    primeira_leitura: datetime | None
    ultima_leitura: datetime | None


@dataclass(frozen=True)
class MediaJanela:
    """`media` e None quando nao ha leitura valida na janela (nunca zero)."""

    media: Decimal | None
    cobertura: Cobertura


@dataclass(frozen=True)
class MenorPreco:
    """`preco` e `ocorrido_em` sao None sem leitura valida. Empate: o instante mais antigo."""

    preco: Decimal | None
    ocorrido_em: datetime | None
    cobertura: Cobertura


@dataclass(frozen=True)
class EstatisticasPreco:
    watch_id: int
    referencia: datetime
    media_30d: MediaJanela
    menor_90d: MenorPreco


def _serie(
    linhas: list[tuple[datetime, int, Decimal]], inicio: datetime
) -> list[tuple[datetime, Decimal]]:
    """Menor preco de cada instante; `linhas` vem ordenada por coletado_em."""
    ultimo_por_anuncio: dict[int, Decimal] = {}
    serie: list[tuple[datetime, Decimal]] = []
    i = 0
    janela = [linha for linha in linhas if linha[0] >= inicio]
    while i < len(janela):
        instante = janela[i][0]
        while i < len(janela) and janela[i][0] == instante:
            ultimo_por_anuncio[janela[i][1]] = janela[i][2]
            i += 1
        serie.append((instante, min(ultimo_por_anuncio.values())))
    return serie


def _cobertura(serie: list[tuple[datetime, Decimal]], dias: int) -> Cobertura:
    return Cobertura(
        janela_dias=dias,
        amostras=len(serie),
        dias_com_leitura=len({instante.date() for instante, _ in serie}),
        primeira_leitura=serie[0][0] if serie else None,
        ultima_leitura=serie[-1][0] if serie else None,
    )


def calcular_estatisticas(
    session: Session, watch_id: int, *, agora: datetime | None = None
) -> EstatisticasPreco:
    """`agora` (aware) fixa o instante de referencia; padrao: agora em UTC."""
    referencia = (agora or datetime.now(UTC)).astimezone(UTC)
    if session.get(Watch, watch_id) is None:
        raise NotFoundError(f"Relogio {watch_id} nao encontrado")

    inicio_90 = referencia - timedelta(days=JANELA_MINIMO_DIAS)
    inicio_30 = referencia - timedelta(days=JANELA_MEDIA_DIAS)
    linhas = [
        (r.coletado_em.astimezone(UTC), r.listing_id, r.preco_vista)
        for r in session.execute(
            select(PriceReading.coletado_em, PriceReading.listing_id, PriceReading.preco_vista)
            .join(Listing, Listing.id == PriceReading.listing_id)
            .where(
                Listing.watch_id == watch_id,
                PriceReading.desatualizado.is_(False),
                PriceReading.coletado_em >= inicio_90,
                PriceReading.coletado_em <= referencia,
            )
            .order_by(PriceReading.coletado_em, PriceReading.id)
        )
    ]

    serie_30 = _serie(linhas, inicio_30)
    serie_90 = _serie(linhas, inicio_90)

    media = None
    if serie_30:
        media = (sum((p for _, p in serie_30), Decimal(0)) / len(serie_30)).quantize(
            _CENTAVOS, rounding=ROUND_HALF_UP
        )
    menor = min(serie_90, key=lambda ponto: (ponto[1], ponto[0])) if serie_90 else None

    return EstatisticasPreco(
        watch_id=watch_id,
        referencia=referencia,
        media_30d=MediaJanela(media, _cobertura(serie_30, JANELA_MEDIA_DIAS)),
        menor_90d=MenorPreco(
            menor[1] if menor else None,
            menor[0] if menor else None,
            _cobertura(serie_90, JANELA_MINIMO_DIAS),
        ),
    )
