"""Decisao do operador sobre um candidato (RF20) e o congelamento da publicacao (RF22, RF30).

O nucleo NAO sabe a loja do anuncio hoje: `listing` nao tem esse campo, e a reputacao do
vendedor fica em `vendedor_raw` (JSONB cru, ver models.Watch e rules/vendedor.py), que este
modulo nao interpreta. Por isso `loja` e sempre informada por quem aprova (RF20). Quando o
adaptador real da Shopee (RF04-RF07) informar a loja do anuncio, isso vira sugestao, nao mais
um campo obrigatorio.

`Publication.link_publicado` usa a URL do anuncio (`listing.url`). O link de afiliado e o RF23,
que depende do RF05 e esta bloqueado pela credencial da Shopee (ver AGENTS.md); ate la, o link
publicado e o mesmo do anuncio.
"""

from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from pulso.catalog.errors import ConflictError, NotFoundError
from pulso.decisions.schemas import AprovarIn, DescartarIn
from pulso.decisions.text import compor_texto
from pulso.models import Candidate, Coupon, Listing, Publication, Watch


def _travar_candidato(session: Session, candidate_id: int) -> Candidate:
    """SELECT ... FOR UPDATE: duas decisoes concorrentes sobre o mesmo candidato nao podem as
    duas passar pelo `if status != "pendente"` e criar duas Publication."""
    candidate = session.scalar(
        select(Candidate).where(Candidate.id == candidate_id).with_for_update()
    )
    if candidate is None:
        raise NotFoundError(f"Candidato {candidate_id} nao encontrado")
    return candidate


def get_candidate(session: Session, candidate_id: int) -> Candidate:
    candidate = session.get(Candidate, candidate_id)
    if candidate is None:
        raise NotFoundError(f"Candidato {candidate_id} nao encontrado")
    return candidate


def _exigir_pendente(candidate: Candidate) -> None:
    if candidate.status != "pendente":
        raise ConflictError(f"Candidato {candidate.id} ja foi decidido ({candidate.status})")


def _carregar_contexto(
    session: Session, candidate: Candidate
) -> tuple[Watch, Listing, Coupon | None]:
    watch = session.get(Watch, candidate.watch_id)
    listing = session.get(Listing, candidate.listing_id)
    assert watch is not None and listing is not None  # garantido pela FK
    coupon = session.get(Coupon, candidate.coupon_id) if candidate.coupon_id is not None else None
    return watch, listing, coupon


def _preco_efetivo_congelado(candidate: Candidate) -> Decimal:
    """preco_sem_frete + frete quando o frete e conhecido; senao, so o que se sabe (sem frete).

    Mesma convencao do RF14 (coupons/effective_price.py): frete desconhecido nao e frete zero,
    mas `Publication.preco_efetivo` e NOT NULL, entao a melhor informacao disponivel no momento
    da decisao e o preco sem frete.
    """
    if candidate.frete_desconhecido or candidate.frete is None:
        return candidate.preco_sem_frete
    return candidate.preco_sem_frete + candidate.frete


def aprovar(
    session: Session,
    candidate_id: int,
    data: AprovarIn,
    *,
    agora: datetime | None = None,
) -> tuple[Candidate, Publication]:
    candidate = _travar_candidato(session, candidate_id)
    _exigir_pendente(candidate)
    watch, listing, coupon = _carregar_contexto(session, candidate)
    agora = (agora or datetime.now(UTC)).astimezone(UTC)

    texto_composto = compor_texto(candidate, watch, listing, coupon, loja=data.loja)
    texto_editado = data.texto is not None
    texto_publicado = data.texto if texto_editado else texto_composto

    publication = Publication(
        watch_id=candidate.watch_id,
        listing_id=candidate.listing_id,
        coupon_id=candidate.coupon_id,
        publicado_em=agora,
        preco_vista_publicado=candidate.preco_vista,
        preco_efetivo=_preco_efetivo_congelado(candidate),
        loja=data.loja,
        link_publicado=listing.url,
        texto_publicado=texto_publicado,
        texto_editado=texto_editado,
    )
    session.add(publication)

    candidate.status = "aprovado"
    candidate.decidido_em = agora
    candidate.decidido_por = data.decidido_por

    session.commit()
    session.refresh(candidate)
    session.refresh(publication)
    return candidate, publication


def descartar(
    session: Session,
    candidate_id: int,
    data: DescartarIn,
    *,
    agora: datetime | None = None,
) -> Candidate:
    candidate = _travar_candidato(session, candidate_id)
    _exigir_pendente(candidate)
    agora = (agora or datetime.now(UTC)).astimezone(UTC)

    candidate.status = "descartado"
    candidate.decidido_em = agora
    candidate.decidido_por = data.decidido_por

    session.commit()
    session.refresh(candidate)
    return candidate
