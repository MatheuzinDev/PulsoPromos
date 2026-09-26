"""Estatisticas de preco por relogio sobre o historico proprio (RF12, RNF09).

`price_reading` e um LOG DE MUDANCAS (RF11), nao uma serie de amostras: o preco de um
anuncio num instante T e a ultima leitura valida em ou antes de T, inclusive anterior ao
inicio da janela (que "semeia" o estado inicial). A serie do relogio e o menor preco
entre os anuncios em cada instante; a media de 30 dias pondera cada patamar pela sua
duracao (inicio da janela abre o primeiro patamar semeado, `agora` fecha o ultimo).

Preco considerado: `preco_vista`. Ficam fora do historico, nas duas janelas: leitura
`desatualizado` (RNF09), leitura sem estoque e leitura de anuncio inativo.
Este modulo so calcula: limiar de cobertura e regra de alerta sao do RF15.
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
    """Quanto da janela o historico realmente cobre.

    `amostras`, `dias_com_leitura`, `primeira_leitura` e `ultima_leitura` contam apenas
    leituras OBSERVADAS dentro da janela. `semeada` diz que a serie herdou ao menos um
    valor de leitura anterior a janela (valor arrastado, sem leitura fresca).
    """

    janela_dias: int
    amostras: int  # instantes distintos com leitura observada na janela
    dias_com_leitura: int  # dias UTC distintos com leitura observada
    primeira_leitura: datetime | None
    ultima_leitura: datetime | None
    semeada: bool


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


Linha = tuple[datetime, int, Decimal]  # (coletado_em, listing_id, preco_vista)
Ponto = tuple[datetime, Decimal]


def _serie(eventos: list[Linha], sementes: list[Linha], inicio: datetime) -> list[Ponto]:
    """Menor preco de cada instante; `eventos` (>= inicio) vem ordenada por coletado_em.

    Com sementes, o primeiro ponto cai em `inicio` (ou no primeiro evento, se coincidir).
    """
    atual: dict[int, Decimal] = {listing: preco for _, listing, preco in sementes}
    serie: list[Ponto] = []
    if atual and (not eventos or eventos[0][0] > inicio):
        serie.append((inicio, min(atual.values())))
    i = 0
    while i < len(eventos):
        instante = eventos[i][0]
        while i < len(eventos) and eventos[i][0] == instante:
            atual[eventos[i][1]] = eventos[i][2]
            i += 1
        serie.append((instante, min(atual.values())))
    return serie


def _cobertura(eventos: list[Linha], dias: int, semeada: bool) -> Cobertura:
    instantes = sorted({instante for instante, _, _ in eventos})
    return Cobertura(
        janela_dias=dias,
        amostras=len(instantes),
        dias_com_leitura=len({instante.date() for instante in instantes}),
        primeira_leitura=instantes[0] if instantes else None,
        ultima_leitura=instantes[-1] if instantes else None,
        semeada=semeada,
    )


def _media_ponderada(serie: list[Ponto], fim: datetime) -> Decimal | None:
    if not serie:
        return None
    total = Decimal(0)
    peso = Decimal(0)
    for (instante, preco), proximo in zip(serie, [*(p[0] for p in serie[1:]), fim], strict=True):
        duracao = Decimal(max((proximo - instante).total_seconds(), 0))
        total += preco * duracao
        peso += duracao
    media = total / peso if peso else serie[-1][1]
    return media.quantize(_CENTAVOS, rounding=ROUND_HALF_UP)


def _validas(watch_id: int):  # type: ignore[no-untyped-def]
    return (
        Listing.watch_id == watch_id,
        Listing.ativo.is_(True),
        PriceReading.em_estoque.is_(True),
        PriceReading.desatualizado.is_(False),
    )


def _sementes(session: Session, watch_id: int, inicio: datetime) -> list[Linha]:
    """Ultima leitura valida de cada anuncio estritamente antes de `inicio`."""
    return [
        (r.coletado_em.astimezone(UTC), r.listing_id, r.preco_vista)
        for r in session.execute(
            select(PriceReading.coletado_em, PriceReading.listing_id, PriceReading.preco_vista)
            .join(Listing, Listing.id == PriceReading.listing_id)
            .where(*_validas(watch_id), PriceReading.coletado_em < inicio)
            .distinct(PriceReading.listing_id)
            .order_by(
                PriceReading.listing_id, PriceReading.coletado_em.desc(), PriceReading.id.desc()
            )
        )
    ]


def calcular_estatisticas(
    session: Session, watch_id: int, *, agora: datetime | None = None
) -> EstatisticasPreco:
    """`agora` (aware) fixa o instante de referencia; padrao: agora em UTC."""
    referencia = (agora or datetime.now(UTC)).astimezone(UTC)
    if session.get(Watch, watch_id) is None:
        raise NotFoundError(f"Relogio {watch_id} nao encontrado")

    inicio_90 = referencia - timedelta(days=JANELA_MINIMO_DIAS)
    inicio_30 = referencia - timedelta(days=JANELA_MEDIA_DIAS)
    linhas: list[Linha] = [
        (r.coletado_em.astimezone(UTC), r.listing_id, r.preco_vista)
        for r in session.execute(
            select(PriceReading.coletado_em, PriceReading.listing_id, PriceReading.preco_vista)
            .join(Listing, Listing.id == PriceReading.listing_id)
            .where(
                *_validas(watch_id),
                PriceReading.coletado_em >= inicio_90,
                PriceReading.coletado_em <= referencia,
            )
            .order_by(PriceReading.coletado_em, PriceReading.id)
        )
    ]
    eventos_30 = [linha for linha in linhas if linha[0] >= inicio_30]
    sementes_30 = _sementes(session, watch_id, inicio_30)
    sementes_90 = _sementes(session, watch_id, inicio_90)

    serie_30 = _serie(eventos_30, sementes_30, inicio_30)
    serie_90 = _serie(linhas, sementes_90, inicio_90)

    media = _media_ponderada(serie_30, referencia)
    menor = min(serie_90, key=lambda ponto: (ponto[1], ponto[0])) if serie_90 else None

    return EstatisticasPreco(
        watch_id=watch_id,
        referencia=referencia,
        media_30d=MediaJanela(media, _cobertura(eventos_30, JANELA_MEDIA_DIAS, bool(sementes_30))),
        menor_90d=MenorPreco(
            menor[1] if menor else None,
            menor[0] if menor else None,
            _cobertura(linhas, JANELA_MINIMO_DIAS, bool(sementes_90)),
        ),
    )
