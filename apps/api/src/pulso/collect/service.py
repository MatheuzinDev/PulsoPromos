"""Coleta de precos do catalogo (RF09) e normalizacao para o historico (RF10).

Percorre relogios com vigilancia ativa e seus anuncios ativos, consulta o adaptador do
marketplace pela interface comum e grava em `price_reading` SOMENTE quando algo mudou (RF11,
RNF08). A falha de um anuncio ou adaptador e registrada em log e nao interrompe os demais
(RNF07, RNF11). Nao ha retry (RF07), nem marcacao de dado desatualizado.
"""

import logging
from collections.abc import Mapping
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from pulso.adapters.base import AdaptadorMarketplace
from pulso.adapters.dto import Falha, Leitura
from pulso.adapters.nucleo import consultar_isolado, permissoes_de
from pulso.adapters.permissoes import RegistroPermissoes
from pulso.models import Listing, PriceReading, Watch

logger = logging.getLogger("pulso.collect")


@dataclass
class ResumoColeta:
    consultados: int = 0
    gravados: int = 0
    inalterados: int = 0
    falhas: int = 0
    sem_permissao: int = 0
    sem_adaptador: int = 0
    marketplaces_sem_permissao: set[str] = field(default_factory=set)


def _mudou(ultima: PriceReading | None, leitura: Leitura) -> bool:
    """Compara so os quatro campos do RF11. Vendedor e cupom ficam de fora de proposito."""
    if ultima is None:
        return True
    return (
        ultima.preco_vista != leitura.preco_vista
        or ultima.preco_parcelado_total != leitura.preco_parcelado_total
        or ultima.parcelas != leitura.parcelas
        or ultima.frete != leitura.frete
        or ultima.em_estoque != leitura.em_estoque
    )


def _para_linha(listing_id: int, leitura: Leitura) -> PriceReading:
    return PriceReading(
        listing_id=listing_id,
        coletado_em=leitura.coletado_em,
        preco_vista=leitura.preco_vista,
        preco_parcelado_total=leitura.preco_parcelado_total,
        parcelas=leitura.parcelas,
        frete=leitura.frete,
        em_estoque=leitura.em_estoque,
        vendedor_raw=leitura.vendedor,  # CRU: quem julga o vendedor e o adaptador (RF06)
        origem=leitura.origem,
    )


def _ultima_leitura(session: Session, listing_id: int) -> PriceReading | None:
    return session.scalars(
        select(PriceReading)
        .where(PriceReading.listing_id == listing_id)
        .order_by(PriceReading.coletado_em.desc())
        .limit(1)
    ).first()


def coletar_catalogo(
    session: Session,
    adaptadores: Mapping[str, AdaptadorMarketplace],
    permissoes: RegistroPermissoes,
    marketplace: str | None = None,
) -> ResumoColeta:
    """Uma rodada de coleta. `marketplace` restringe a rodada a um so (agendamento por mercado)."""
    resumo = ResumoColeta()
    consulta = (
        select(Listing.id, Listing.marketplace, Listing.marketplace_item_id)
        .join(Watch, Watch.id == Listing.watch_id)
        .where(Watch.vigilancia_ativa.is_(True), Listing.ativo.is_(True))
        .order_by(Listing.id)
    )
    if marketplace is not None:
        consulta = consulta.where(Listing.marketplace == marketplace)
    anuncios = [(linha[0], linha[1], linha[2]) for linha in session.execute(consulta).all()]

    for listing_id, mkt, item_id in anuncios:
        adaptador = adaptadores.get(mkt)
        if adaptador is None:
            resumo.sem_adaptador += 1
            logger.warning(
                "coleta_sem_adaptador", extra={"marketplace": mkt, "listing_id": listing_id}
            )
            continue
        if not permissoes_de(adaptador, permissoes).guarda_historico_precos:
            resumo.sem_permissao += 1
            resumo.marketplaces_sem_permissao.add(mkt)
            continue

        resumo.consultados += 1
        resultado = consultar_isolado(adaptador, item_id)
        if isinstance(resultado, Falha):
            resumo.falhas += 1
            logger.error(
                "coleta_falha",
                extra={
                    "listing_id": listing_id,
                    "marketplace": mkt,
                    "tipo": resultado.tipo.value,
                    "mensagem": resultado.mensagem,
                    "origem": resultado.origem,
                },
            )
            continue

        try:
            if not _mudou(_ultima_leitura(session, listing_id), resultado):
                resumo.inalterados += 1
                continue
            session.add(_para_linha(listing_id, resultado))
            session.commit()
            resumo.gravados += 1
        except SQLAlchemyError as exc:  # ex.: anuncio excluido durante a coleta (FK)
            session.rollback()
            resumo.falhas += 1
            logger.error(
                "coleta_falha_persistencia",
                extra={"listing_id": listing_id, "marketplace": mkt, "erro": type(exc).__name__},
            )

    for mkt in sorted(resumo.marketplaces_sem_permissao):
        logger.info("coleta_sem_permissao_historico", extra={"marketplace": mkt})
    return resumo
