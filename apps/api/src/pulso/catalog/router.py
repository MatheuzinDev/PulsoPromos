from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy.orm import Session

from pulso.catalog import csv_io, service
from pulso.catalog.schemas import (
    ImportResult,
    ListingCreate,
    ListingOut,
    WatchCreate,
    WatchOut,
    WatchPage,
    WatchUpdate,
)
from pulso.db import get_session

router = APIRouter(prefix="/watches", tags=["catalogo"])

SessionDep = Annotated[Session, Depends(get_session)]


@router.post("", response_model=WatchOut, status_code=201)
def criar_relogio(data: WatchCreate, session: SessionDep) -> WatchOut:
    return WatchOut.model_validate(service.create_watch(session, data))


@router.get("", response_model=WatchPage)
def listar_relogios(
    session: SessionDep,
    marca: str | None = None,
    vigilancia_ativa: bool | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> WatchPage:
    items, total = service.list_watches(
        session, marca=marca, vigilancia_ativa=vigilancia_ativa, limit=limit, offset=offset
    )
    return WatchPage(
        items=[WatchOut.model_validate(w) for w in items], total=total, limit=limit, offset=offset
    )


# /export e /import vem antes de /{watch_id} para nao serem lidos como id
@router.get("/export")
def exportar_csv(session: SessionDep) -> Response:
    return Response(
        content=csv_io.export_csv(session),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="catalogo.csv"'},
    )


@router.post("/import", response_model=ImportResult)
async def importar_csv(request: Request, session: SessionDep) -> ImportResult:
    """Corpo cru em text/csv (UTF-8). Atomico: uma linha invalida rejeita o arquivo todo."""
    return csv_io.import_csv(session, await request.body())


@router.get("/{watch_id}", response_model=WatchOut)
def obter_relogio(watch_id: int, session: SessionDep) -> WatchOut:
    return WatchOut.model_validate(service.get_watch(session, watch_id))


@router.patch("/{watch_id}", response_model=WatchOut)
def editar_relogio(watch_id: int, data: WatchUpdate, session: SessionDep) -> WatchOut:
    return WatchOut.model_validate(service.update_watch(session, watch_id, data))


@router.post("/{watch_id}/pausar", response_model=WatchOut)
def pausar_vigilancia(watch_id: int, session: SessionDep) -> WatchOut:
    return WatchOut.model_validate(service.set_vigilancia(session, watch_id, False))


@router.post("/{watch_id}/retomar", response_model=WatchOut)
def retomar_vigilancia(watch_id: int, session: SessionDep) -> WatchOut:
    return WatchOut.model_validate(service.set_vigilancia(session, watch_id, True))


@router.post("/{watch_id}/listings", response_model=ListingOut, status_code=201)
def vincular_anuncio(watch_id: int, data: ListingCreate, session: SessionDep) -> ListingOut:
    return ListingOut.model_validate(service.add_listing(session, watch_id, data))


@router.get("/{watch_id}/listings", response_model=list[ListingOut])
def listar_anuncios(watch_id: int, session: SessionDep) -> list[ListingOut]:
    return [ListingOut.model_validate(x) for x in service.list_listings(session, watch_id)]


@router.post("/{watch_id}/listings/{listing_id}/desativar", response_model=ListingOut)
def desativar_anuncio(watch_id: int, listing_id: int, session: SessionDep) -> ListingOut:
    return ListingOut.model_validate(service.deactivate_listing(session, watch_id, listing_id))
