from psycopg import errors as pg_errors
from sqlalchemy import delete, func, or_, select
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from pulso.catalog.errors import ConflictError, NotFoundError
from pulso.catalog.schemas import ListingCreate, WatchCreate, WatchUpdate
from pulso.models import Listing, PriceReading, Publication, Watch


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


def _guard_sem_historico(session: Session, watch: Watch, operacao: str) -> None:
    """Recusa (409) se o relogio ja tem leituras de preco ou publicacoes (RF30, RNF09, RNF12)."""
    leituras = (
        session.scalar(
            select(func.count())
            .select_from(PriceReading)
            .join(Listing, Listing.id == PriceReading.listing_id)
            .where(Listing.watch_id == watch.id)
        )
        or 0
    )
    publicacoes = (
        session.scalar(
            select(func.count())
            .select_from(Publication)
            .where(
                or_(
                    Publication.watch_id == watch.id,
                    Publication.listing_id.in_(
                        select(Listing.id).where(Listing.watch_id == watch.id)
                    ),
                )
            )
        )
        or 0
    )
    if leituras or publicacoes:
        raise ConflictError(
            f"Nao e possivel {operacao} (relogio {watch.id}): ele ja tem historico. "
            f"Leituras de preco: {leituras}; publicacoes: {publicacoes}. "
            "Esses registros sao mantidos para auditoria e nao podem ser apagados; "
            "para tirar o relogio de circulacao, pause a vigilancia."
        )


def _travar_relogio(session: Session, watch_id: int) -> Watch:
    """SELECT ... FOR UPDATE na linha do relogio.

    Um INSERT em listing ou publication toma FOR KEY SHARE nesta linha (FK), e so FOR UPDATE
    conflita com FOR KEY SHARE: FOR NO KEY UPDATE deixaria um anuncio novo aparecer.
    Espera sem risco de deadlock porque e o primeiro lock de linha da transacao.
    """
    watch = session.scalar(
        select(Watch)
        .where(Watch.id == watch_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if watch is None:
        # tambem cobre o relogio excluido por outra transacao enquanto esperavamos o lock
        raise NotFoundError(f"Relogio {watch_id} nao encontrado")
    return watch


def _travar_anuncios(session: Session, watch: Watch, operacao: str) -> None:
    """SELECT ... FOR UPDATE NOWAIT nos anuncios do relogio, em ordem de id.

    Um INSERT em price_reading toma FOR KEY SHARE no listing (FK); travar so o relogio nao o
    impediria. NOWAIT, e nao espera: quem grava historico pode travar listing antes de watch
    (o INSERT em publication faz isso, pela ordem dos triggers de FK), e esperar aqui formaria
    um ciclo. Anuncio ocupado e duvida, e duvida vira 409.
    """
    try:
        with session.begin_nested():
            session.execute(
                select(Listing.id)
                .where(Listing.watch_id == watch.id)
                .order_by(Listing.id)
                .with_for_update(nowait=True)
            )
    except OperationalError as exc:
        if not isinstance(exc.orig, pg_errors.LockNotAvailable):
            raise
        raise ConflictError(
            f"Nao e possivel {operacao} (relogio {watch.id}): um anuncio dele esta recebendo "
            "historico ou sendo alterado neste momento. Tente novamente em instantes."
        ) from None


def _travar_sem_historico(session: Session, watch_id: int, operacao: str) -> Watch:
    """Trava relogio e anuncios e so entao aplica a guarda (RF30, RNF09, RNF12).

    Com os locks tomados, nenhum price_reading, publication ou listing novo do relogio pode ser
    gravado ate o fim da transacao, e a guarda enxerga tudo o que ja foi commitado. Isso depende
    de READ COMMITTED (o padrao): cada comando le um snapshot novo, tirado depois dos locks.
    Ordem de lock: watch (espera) -> listings por id (NOWAIT).
    """
    watch = _travar_relogio(session, watch_id)
    _travar_anuncios(session, watch, operacao)
    _guard_sem_historico(session, watch, operacao)
    return watch


def _mensagem_integridade(exc: IntegrityError, watch_id: int, novo_ean: str | None) -> str:
    restricao = exc.orig.diag.constraint_name if isinstance(exc.orig, pg_errors.Error) else None
    # watch_ean_key e o nome que o Postgres da ao unique da coluna ean (models.Watch.ean)
    if isinstance(exc.orig, pg_errors.UniqueViolation) and restricao == "watch_ean_key":
        return f"Ja existe um relogio com o EAN {novo_ean}"
    return (
        f"O banco recusou a alteracao do relogio {watch_id}: "
        f"restricao {restricao or 'desconhecida'} violada"
    )


def update_watch(session: Session, watch_id: int, data: WatchUpdate) -> Watch:
    watch = _get_watch(session, watch_id)
    campos = data.model_dump(exclude_unset=True)
    novo_ean = campos.get("ean")
    if novo_ean is not None and novo_ean != watch.ean:
        watch = _travar_sem_historico(session, watch_id, "trocar o EAN do relogio")
        outro = session.scalar(select(Watch.id).where(Watch.ean == novo_ean, Watch.id != watch_id))
        if outro is not None:
            raise ConflictError(f"Ja existe um relogio com o EAN {novo_ean}")
    try:
        with session.begin_nested():
            for campo, valor in campos.items():
                setattr(watch, campo, valor)
    except IntegrityError as exc:
        raise ConflictError(_mensagem_integridade(exc, watch_id, novo_ean)) from None
    session.commit()
    session.refresh(watch)
    return watch


def delete_watch(session: Session, watch_id: int) -> None:
    """Remove o relogio e os anuncios dele. Nunca toca em price_reading nem publication."""
    operacao = "excluir o relogio"
    watch = _travar_sem_historico(session, watch_id, operacao)
    try:
        with session.begin_nested():
            session.execute(delete(Listing).where(Listing.watch_id == watch.id))
            session.delete(watch)
    except IntegrityError:
        # os locks acima impedem isto; se ainda assim a FK barrar, o savepoint desfaz tudo
        raise ConflictError(
            f"Nao e possivel {operacao} (relogio {watch_id}): ele passou a ter historico "
            "durante a operacao. Nada foi apagado; para tirar o relogio de circulacao, "
            "pause a vigilancia."
        ) from None
    session.commit()


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
