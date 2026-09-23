from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from pulso.catalog.errors import ConflictError, NotFoundError
from pulso.catalog.schemas import ListingCreate, WatchCreate, WatchUpdate
from pulso.models import Listing, Watch


def _get_watch(session: Session, watch_id: int) -> Watch:
    watch = session.get(Watch, watch_id)
    if watch is None:
        raise NotFoundError(f"Relogio {watch_id} nao encontrado")
    return watch


def create_watch(session: Session, data: WatchCreate) -> Watch:
    mensagem = f"Ja existe um relogio com o EAN {data.ean}"
    if session.scalar(select(Watch.id).where(Watch.ean == data.ean)) is not None:
        raise ConflictError(mensagem)
    watch = Watch(**data.model_dump())
    try:
        with session.begin_nested():
            session.add(watch)
    except IntegrityError:
        # corrida entre dois cadastros do mesmo EAN: o indice unico decide
        raise ConflictError(mensagem) from None
    session.commit()
    session.refresh(watch)
    return watch


def list_watches(
    session: Session,
    *,
    marca: str | None,
    vigilancia_ativa: bool | None,
    limit: int,
    offset: int,
) -> tuple[list[Watch], int]:
    query = select(Watch)
    if marca is not None:
        query = query.where(func.lower(Watch.marca) == marca.strip().lower())
    if vigilancia_ativa is not None:
        query = query.where(Watch.vigilancia_ativa == vigilancia_ativa)
    total = session.scalar(select(func.count()).select_from(query.subquery())) or 0
    items = session.scalars(query.order_by(Watch.id).limit(limit).offset(offset)).all()
    return list(items), total


def get_watch(session: Session, watch_id: int) -> Watch:
    return _get_watch(session, watch_id)


def update_watch(session: Session, watch_id: int, data: WatchUpdate) -> Watch:
    watch = _get_watch(session, watch_id)
    for campo, valor in data.model_dump(exclude_unset=True).items():
        setattr(watch, campo, valor)
    session.commit()
    session.refresh(watch)
    return watch


def set_vigilancia(session: Session, watch_id: int, ativa: bool) -> Watch:
    watch = _get_watch(session, watch_id)
    watch.vigilancia_ativa = ativa
    session.commit()
    session.refresh(watch)
    return watch


def add_listing(session: Session, watch_id: int, data: ListingCreate) -> Listing:
    _get_watch(session, watch_id)
    mensagem = f"O anuncio {data.marketplace_item_id} ja esta vinculado em {data.marketplace}"
    duplicado = session.scalar(
        select(Listing.id).where(
            Listing.marketplace == data.marketplace,
            Listing.marketplace_item_id == data.marketplace_item_id,
        )
    )
    if duplicado is not None:
        raise ConflictError(mensagem)
    listing = Listing(watch_id=watch_id, **data.model_dump())
    try:
        with session.begin_nested():
            session.add(listing)
    except IntegrityError:
        raise ConflictError(mensagem) from None
    session.commit()
    session.refresh(listing)
    return listing


def list_listings(session: Session, watch_id: int) -> list[Listing]:
    _get_watch(session, watch_id)
    return list(
        session.scalars(select(Listing).where(Listing.watch_id == watch_id).order_by(Listing.id))
    )


def deactivate_listing(session: Session, watch_id: int, listing_id: int) -> Listing:
    _get_watch(session, watch_id)
    listing = session.get(Listing, listing_id)
    if listing is None or listing.watch_id != watch_id:
        raise NotFoundError(f"Anuncio {listing_id} nao encontrado neste relogio")
    listing.ativo = False
    session.commit()
    session.refresh(listing)
    return listing
