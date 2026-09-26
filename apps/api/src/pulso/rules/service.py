"""Motor de regras (RF15-RF18): decide quais ofertas viram candidato.

Base de comparacao: `preco_sem_frete` (a vista ja com cupom) contra a base a vista do RF12,
os dois sem frete. O frete nao entra na regra, mas vai no candidato (RF19, RF22).

Regras, avaliadas de forma independente (OR); vence, por relogio, a de maior pontuacao:
- media_30d: queda >= limiar da faixa (medida pela media) e economia >= piso; exige historico
  nao ralo (cobertura e amostras).
- minimo_90d: preco ESTRITAMENTE menor que o minimo ANTERIOR a leitura avaliada, com economia
  >= piso e sem limiar percentual. Dispara na transicao para um novo fundo, nao por estar nele.
- preco_alvo: preco <= Watch.preco_alvo; sem limiar, piso nem historico. Queda e economia
  sao medidas contra a media de 30 dias quando ela existe (0 caso contrario).
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from pulso.adapters.permissoes import RegistroPermissoes
from pulso.coupons.effective_price import PrecoEfetivo, preco_efetivo_da_leitura
from pulso.history.service import EstatisticasPreco, calcular_estatisticas
from pulso.models import Candidate, Listing, PriceReading, Watch
from pulso.rules.parametros import (
    AMOSTRAS_MINIMAS_MEDIA,
    COBERTURA_MINIMA_MEDIA,
    FAIXAS_LIMIAR,
    JANELA_REPETICAO_HORAS,
    PISO_ECONOMIA,
    QUEDA_ADICIONAL_MINIMA,
)
from pulso.rules.vendedor import POLITICAS_VENDEDOR, PoliticaVendedor, vendedor_confiavel

_CENTAVOS = Decimal("0.01")
_PONTUACAO = Decimal("0.0001")
_CEM = Decimal(100)
_MICROSSEGUNDO = timedelta(microseconds=1)


@dataclass(frozen=True)
class _Disparo:
    regra: str
    base: Decimal | None  # contra o que queda e economia sao medidas
    minimo_90d: Decimal | None


def limiar_da_base(base: Decimal) -> Decimal:
    """Queda minima (%) exigida para a faixa da BASE."""
    for teto, limiar in FAIXAS_LIMIAR:
        if teto is None or base <= teto:
            return limiar
    raise AssertionError("FAIXAS_LIMIAR precisa terminar em faixa sem teto")


def _queda_suficiente(base: Decimal, preco: Decimal) -> bool:
    # (base - preco) / base * 100 >= limiar, sem divisao para nao arredondar na fronteira
    economia = base - preco
    return economia >= PISO_ECONOMIA and economia * _CEM >= limiar_da_base(base) * base


def _medidas(base: Decimal | None, preco: Decimal) -> tuple[Decimal, Decimal]:
    """(queda %, economia em reais) contra `base`; 0 sem base ou sem queda."""
    if base is None or base <= 0 or preco >= base:
        return Decimal(0), Decimal("0.00")
    return (base - preco) / base * _CEM, base - preco


def _regra_media(stats: EstatisticasPreco, preco: Decimal) -> _Disparo | None:
    media = stats.media_30d.media
    cob = stats.media_30d.cobertura
    if media is None or cob.fracao_coberta < COBERTURA_MINIMA_MEDIA:
        return None
    if cob.amostras < AMOSTRAS_MINIMAS_MEDIA or not _queda_suficiente(media, preco):
        return None
    return _Disparo("media_30d", media, stats.menor_90d.preco)


def _regra_minimo(
    session: Session, listing: Listing, reading: PriceReading, preco: Decimal
) -> _Disparo | None:
    # O minimo tem de ser o ANTERIOR a esta leitura: menor_90d de calcular_estatisticas inclui a
    # leitura atual, logo menor_90d <= preco sempre e "preco < menor_90d" nunca seria verdade.
    anterior = calcular_estatisticas(
        session, listing.watch_id, agora=reading.coletado_em - _MICROSSEGUNDO
    ).menor_90d
    if anterior.preco is None or anterior.ocorrido_em is None:
        return None
    # so leitura OBSERVADA vale; o ponto semeado cai no inicio da janela, antes da 1a leitura
    primeira = anterior.cobertura.primeira_leitura
    if primeira is None or anterior.ocorrido_em < primeira:
        return None
    if preco >= anterior.preco or anterior.preco - preco < PISO_ECONOMIA:
        return None
    # transicao alertada uma vez so: a leitura e um log de mudancas e pode ficar "atual" por dias
    ja_alertada = session.scalar(
        select(Candidate.id)
        .where(
            Candidate.listing_id == listing.id,
            Candidate.regra == "minimo_90d",
            Candidate.criado_em >= reading.coletado_em,
        )
        .limit(1)
    )
    if ja_alertada is not None:
        return None
    return _Disparo("minimo_90d", anterior.preco, anterior.preco)


def _regra_alvo(watch: Watch, stats: EstatisticasPreco, preco: Decimal) -> _Disparo | None:
    if watch.preco_alvo is None or preco > watch.preco_alvo:
        return None
    return _Disparo("preco_alvo", stats.media_30d.media, stats.menor_90d.preco)


def _montar(
    watch: Watch,
    listing: Listing,
    efetivo: PrecoEfetivo,
    stats: EstatisticasPreco,
    disparo: _Disparo,
    agora: datetime,
) -> Candidate:
    queda, economia = _medidas(disparo.base, efetivo.preco_sem_frete)
    return Candidate(
        watch_id=watch.id,
        listing_id=listing.id,
        coupon_id=efetivo.coupon_id,
        regra=disparo.regra,
        preco_vista=efetivo.preco_vista,
        desconto=efetivo.desconto,
        preco_sem_frete=efetivo.preco_sem_frete,
        frete=efetivo.frete,
        frete_desconhecido=efetivo.frete_desconhecido,
        media_30d=stats.media_30d.media,
        minimo_90d=disparo.minimo_90d,
        preco_alvo=watch.preco_alvo,
        queda_percentual=queda.quantize(_CENTAVOS, rounding=ROUND_HALF_UP),
        economia_absoluta=economia.quantize(_CENTAVOS, rounding=ROUND_HALF_UP),
        pontuacao=queda.quantize(_PONTUACAO, rounding=ROUND_HALF_UP),
        status="pendente",
        criado_em=agora,
    )


def chave_ordenacao(candidato: Candidate) -> tuple[Decimal, Decimal]:
    """RF18: maior queda percentual; empate, maior economia em reais. Troque so aqui."""
    return candidato.pontuacao, candidato.economia_absoluta


def _repeticao_bloqueada(session: Session, watch_id: int, preco: Decimal, agora: datetime) -> bool:
    """RF16: ja houve candidato do relogio em 24h e o preco nao caiu 5% sobre o do ultimo."""
    ultimo = session.scalars(
        select(Candidate)
        .where(
            Candidate.watch_id == watch_id,
            Candidate.criado_em > agora - timedelta(hours=JANELA_REPETICAO_HORAS),
        )
        .order_by(Candidate.criado_em.desc(), Candidate.id.desc())
        .limit(1)
    ).first()
    if ultimo is None:
        return False
    # libera se preco <= anterior * (1 - 5%); sem divisao
    return preco * _CEM > ultimo.preco_sem_frete * (_CEM - QUEDA_ADICIONAL_MINIMA)


def _leitura_atual(session: Session, listing_id: int, agora: datetime) -> PriceReading | None:
    return session.scalars(
        select(PriceReading)
        .where(PriceReading.listing_id == listing_id, PriceReading.coletado_em <= agora)
        .order_by(PriceReading.coletado_em.desc(), PriceReading.id.desc())
        .limit(1)
    ).first()


def _candidato_do_anuncio(
    session: Session,
    watch: Watch,
    listing: Listing,
    stats: EstatisticasPreco,
    agora: datetime,
    permissoes: RegistroPermissoes,
    politicas: Mapping[str, PoliticaVendedor],
) -> Candidate | None:
    if not permissoes.de(listing.marketplace).alerta_preco:  # RF17
        return None
    reading = _leitura_atual(session, listing.id, agora)
    if reading is None or not reading.em_estoque or reading.desatualizado:  # RF17, RNF09
        return None
    if not vendedor_confiavel(politicas, watch, listing, reading):  # RF17
        return None
    efetivo = preco_efetivo_da_leitura(session, reading, agora)
    preco = efetivo.preco_sem_frete
    disparos = [
        d
        for d in (
            _regra_media(stats, preco),
            _regra_minimo(session, listing, reading, preco),
            _regra_alvo(watch, stats, preco),
        )
        if d is not None
    ]
    candidatos = [_montar(watch, listing, efetivo, stats, d, agora) for d in disparos]
    # empate de pontuacao: vence a regra que aparece primeiro (media, minimo, alvo)
    return max(candidatos, key=chave_ordenacao, default=None)


def gerar_candidatos(
    session: Session,
    *,
    agora: datetime | None = None,
    permissoes: RegistroPermissoes | None = None,
    politicas: Mapping[str, PoliticaVendedor] | None = None,
) -> list[Candidate]:
    """Gera os candidatos NOVOS (no maximo um por relogio) e os devolve ordenados (RF18).

    Idempotente (RNF08): sem mudanca nos dados, uma segunda chamada nao cria nada, porque o
    RF16 barra o preco que ja gerou candidato. Faz flush, nao commit.
    """
    agora = (agora or datetime.now(UTC)).astimezone(UTC)
    permissoes = permissoes or RegistroPermissoes()
    politicas = politicas if politicas is not None else POLITICAS_VENDEDOR
    novos: list[Candidate] = []
    for watch in session.scalars(
        select(Watch).where(Watch.vigilancia_ativa.is_(True)).order_by(Watch.id)
    ).all():
        listings = session.scalars(
            select(Listing).where(Listing.watch_id == watch.id, Listing.ativo.is_(True))
        ).all()
        if not listings:
            continue
        stats = calcular_estatisticas(session, watch.id, agora=agora)
        candidatos = [
            c
            for listing in listings
            if (
                c := _candidato_do_anuncio(
                    session, watch, listing, stats, agora, permissoes, politicas
                )
            )
            is not None
        ]
        melhor = max(candidatos, key=chave_ordenacao, default=None)
        if melhor is None or _repeticao_bloqueada(session, watch.id, melhor.preco_sem_frete, agora):
            continue
        session.add(melhor)
        novos.append(melhor)
    session.flush()
    return sorted(novos, key=chave_ordenacao, reverse=True)
