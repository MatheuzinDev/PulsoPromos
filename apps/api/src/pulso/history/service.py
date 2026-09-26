"""Estatisticas de preco por relogio sobre o historico proprio (RF12, RNF09).

`price_reading` e um LOG DE MUDANCAS (RF11), nao uma serie de amostras: o preco de um
anuncio num instante T e a ultima leitura em ou antes de T, inclusive anterior ao inicio
da janela (que "semeia" o estado inicial). A serie do relogio e o menor preco entre os
anuncios PRESENTES em cada instante; a media de 30 dias pondera cada patamar pela sua
duracao (inicio da janela abre o primeiro patamar semeado, `agora` fecha o ultimo).

Leitura sem estoque significa "sem preco valido a partir deste instante": o anuncio SAI da
serie ate uma leitura posterior com estoque. Sem nenhum anuncio presente, o relogio nao tem
preco valido no trecho: ele fica fora do divisor da media (nao vale zero) e da cobertura.

Preco considerado: `preco_vista`. Ficam fora do historico, nas duas janelas: leitura
`desatualizado` (RNF09) e anuncio inativo por inteiro.
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
_FRACAO = Decimal("0.0001")
_SEGUNDOS_DIA = 86400


@dataclass(frozen=True)
class Cobertura:
    """Quanto da janela o historico realmente cobre.

    `amostras`, `dias_com_leitura`, `primeira_leitura` e `ultima_leitura` contam apenas
    leituras OBSERVADAS dentro da janela (inclusive sem estoque, que tambem sao
    observacoes). `semeada` diz que a serie herdou ao menos um preco de leitura anterior a
    janela (valor arrastado, sem leitura fresca). `dias_com_preco` e `fracao_coberta` dizem
    quanto da janela teve preco valido (algum anuncio presente): o RF15 usa para saber que
    a media/minimo veio so de um pedaco da janela.
    """

    janela_dias: int
    amostras: int  # instantes distintos com leitura observada na janela
    dias_com_leitura: int  # dias UTC distintos com leitura observada
    primeira_leitura: datetime | None
    ultima_leitura: datetime | None
    semeada: bool
    dias_com_preco: Decimal  # tempo da janela com preco valido, em dias (2 casas)
    fracao_coberta: Decimal  # dias_com_preco / janela_dias, de 0 a 1 (4 casas)


@dataclass(frozen=True)
class MediaJanela:
    """`media` e None quando nao ha preco valido na janela (nunca zero)."""

    media: Decimal | None
    cobertura: Cobertura


@dataclass(frozen=True)
class MenorPreco:
    """`preco` e `ocorrido_em` sao None sem preco valido. Empate: o instante mais antigo."""

    preco: Decimal | None
    ocorrido_em: datetime | None
    cobertura: Cobertura


@dataclass(frozen=True)
class EstatisticasPreco:
    watch_id: int
    referencia: datetime
    media_30d: MediaJanela
    menor_90d: MenorPreco


# (coletado_em, listing_id, preco_vista); preco None = leitura sem estoque (anuncio ausente)
Linha = tuple[datetime, int, Decimal | None]
Ponto = tuple[datetime, Decimal | None]  # preco None = nenhum anuncio presente


def _menor(atual: dict[int, Decimal | None]) -> Decimal | None:
    presentes = [preco for preco in atual.values() if preco is not None]
    return min(presentes) if presentes else None


def _serie(eventos: list[Linha], sementes: list[Linha], inicio: datetime) -> list[Ponto]:
    """Menor preco entre os anuncios presentes em cada instante; `eventos` (>= inicio) vem
    ordenada por coletado_em.

    Com sementes, o primeiro ponto cai em `inicio` (ou no primeiro evento, se coincidir).
    """
    atual: dict[int, Decimal | None] = {listing: preco for _, listing, preco in sementes}
    serie: list[Ponto] = []
    if atual and (not eventos or eventos[0][0] > inicio):
        serie.append((inicio, _menor(atual)))
    i = 0
    while i < len(eventos):
        instante = eventos[i][0]
        while i < len(eventos) and eventos[i][0] == instante:
            atual[eventos[i][1]] = eventos[i][2]
            i += 1
        serie.append((instante, _menor(atual)))
    return serie


def _trechos(serie: list[Ponto], fim: datetime) -> list[tuple[Decimal | None, Decimal]]:
    """(preco, duracao em segundos) de cada patamar da serie."""
    if not serie:
        return []
    return [
        (preco, Decimal(max((proximo - instante).total_seconds(), 0)))
        for (instante, preco), proximo in zip(serie, [*(p[0] for p in serie[1:]), fim], strict=True)
    ]


def _cobertura(
    eventos: list[Linha], dias: int, semeada: bool, serie: list[Ponto], fim: datetime
) -> Cobertura:
    segundos = sum(
        (duracao for preco, duracao in _trechos(serie, fim) if preco is not None), Decimal(0)
    )
    instantes = sorted({instante for instante, _, _ in eventos})
    return Cobertura(
        janela_dias=dias,
        amostras=len(instantes),
        dias_com_leitura=len({instante.date() for instante in instantes}),
        primeira_leitura=instantes[0] if instantes else None,
        ultima_leitura=instantes[-1] if instantes else None,
        semeada=semeada,
        dias_com_preco=(segundos / _SEGUNDOS_DIA).quantize(_CENTAVOS, rounding=ROUND_HALF_UP),
        fracao_coberta=(segundos / (dias * _SEGUNDOS_DIA)).quantize(
            _FRACAO, rounding=ROUND_HALF_UP
        ),
    )


def _media_ponderada(serie: list[Ponto], fim: datetime) -> Decimal | None:
    trechos: list[tuple[Decimal, Decimal]] = [
        (preco, duracao) for preco, duracao in _trechos(serie, fim) if preco is not None
    ]
    if not trechos:
        return None
    total = sum((preco * duracao for preco, duracao in trechos), Decimal(0))
    peso = sum((duracao for _, duracao in trechos), Decimal(0))
    media = total / peso if peso else trechos[-1][0]
    return media.quantize(_CENTAVOS, rounding=ROUND_HALF_UP)


def _validas(watch_id: int):  # type: ignore[no-untyped-def]
    return (
        Listing.watch_id == watch_id,
        Listing.ativo.is_(True),
        PriceReading.desatualizado.is_(False),
    )


def _preco(preco_vista: Decimal, em_estoque: bool) -> Decimal | None:
    return preco_vista if em_estoque else None


def _sementes(session: Session, watch_id: int, inicio: datetime) -> list[Linha]:
    """Ultima leitura (com ou sem estoque) de cada anuncio estritamente antes de `inicio`."""
    return [
        (r.coletado_em.astimezone(UTC), r.listing_id, _preco(r.preco_vista, r.em_estoque))
        for r in session.execute(
            select(
                PriceReading.coletado_em,
                PriceReading.listing_id,
                PriceReading.preco_vista,
                PriceReading.em_estoque,
            )
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
        (r.coletado_em.astimezone(UTC), r.listing_id, _preco(r.preco_vista, r.em_estoque))
        for r in session.execute(
            select(
                PriceReading.coletado_em,
                PriceReading.listing_id,
                PriceReading.preco_vista,
                PriceReading.em_estoque,
            )
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
    com_preco = [(t, p) for t, p in serie_90 if p is not None]
    menor = min(com_preco, key=lambda ponto: (ponto[1], ponto[0])) if com_preco else None

    return EstatisticasPreco(
        watch_id=watch_id,
        referencia=referencia,
        media_30d=MediaJanela(
            media,
            _cobertura(
                eventos_30,
                JANELA_MEDIA_DIAS,
                any(p is not None for _, _, p in sementes_30),
                serie_30,
                referencia,
            ),
        ),
        menor_90d=MenorPreco(
            menor[1] if menor else None,
            menor[0] if menor else None,
            _cobertura(
                linhas,
                JANELA_MINIMO_DIAS,
                any(p is not None for _, _, p in sementes_90),
                serie_90,
                referencia,
            ),
        ),
    )
