from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from pulso.coupons import service
from pulso.coupons.schemas import (
    CouponCreate,
    CouponOut,
    CouponPage,
    CouponUpdate,
    Marketplace,
    TestadoIn,
)
from pulso.db import get_session

router = APIRouter(prefix="/coupons", tags=["cupons"])

SessionDep = Annotated[Session, Depends(get_session)]


@router.post("", response_model=CouponOut, status_code=201)
def criar_cupom(data: CouponCreate, session: SessionDep) -> CouponOut:
    return CouponOut.model_validate(service.create_coupon(session, data))


@router.get("", response_model=CouponPage)
def listar_cupons(
    session: SessionDep,
    marketplace: Marketplace | None = None,
    ativo: bool | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> CouponPage:
    items, total = service.list_coupons(
        session, marketplace=marketplace, ativo=ativo, limit=limit, offset=offset
    )
    return CouponPage(
        items=[CouponOut.model_validate(c) for c in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/{coupon_id}", response_model=CouponOut)
def obter_cupom(coupon_id: int, session: SessionDep) -> CouponOut:
    return CouponOut.model_validate(service.get_coupon(session, coupon_id))


@router.patch("/{coupon_id}", response_model=CouponOut)
def editar_cupom(coupon_id: int, data: CouponUpdate, session: SessionDep) -> CouponOut:
    return CouponOut.model_validate(service.update_coupon(session, coupon_id, data))


@router.post("/{coupon_id}/desativar", response_model=CouponOut)
def desativar_cupom(coupon_id: int, session: SessionDep) -> CouponOut:
    return CouponOut.model_validate(service.set_ativo(session, coupon_id, False))


@router.post("/{coupon_id}/testado", response_model=CouponOut)
def registrar_teste(
    coupon_id: int, session: SessionDep, data: TestadoIn | None = None
) -> CouponOut:
    testado_em = data.testado_em if data else None
    return CouponOut.model_validate(service.registrar_teste(session, coupon_id, testado_em))
