from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from pulso.catalog.errors import NotFoundError
from pulso.coupons.errors import CouponInvalidoError
from pulso.coupons.schemas import CouponCreate, CouponUpdate, validar_regra
from pulso.models import Coupon


def get_coupon(session: Session, coupon_id: int) -> Coupon:
    coupon = session.get(Coupon, coupon_id)
    if coupon is None:
        raise NotFoundError(f"Cupom {coupon_id} nao encontrado")
    return coupon


def create_coupon(session: Session, data: CouponCreate) -> Coupon:
    coupon = Coupon(**data.model_dump())
    session.add(coupon)
    session.commit()
    session.refresh(coupon)
    return coupon


def list_coupons(
    session: Session,
    *,
    marketplace: str | None,
    ativo: bool | None,
    limit: int,
    offset: int,
) -> tuple[list[Coupon], int]:
    query = select(Coupon)
    if marketplace is not None:
        query = query.where(Coupon.marketplace == marketplace)
    if ativo is not None:
        query = query.where(Coupon.ativo == ativo)
    total = session.scalar(select(func.count()).select_from(query.subquery())) or 0
    items = session.scalars(query.order_by(Coupon.id).limit(limit).offset(offset)).all()
    return list(items), total


def update_coupon(session: Session, coupon_id: int, data: CouponUpdate) -> Coupon:
    coupon = get_coupon(session, coupon_id)
    changes = data.model_dump(exclude_unset=True)
    tipo = changes.get("tipo", coupon.tipo)
    valor = changes.get("valor", coupon.valor)
    teto = changes["teto_desconto"] if "teto_desconto" in changes else coupon.teto_desconto
    try:
        validar_regra(tipo, valor, teto)
    except ValueError as exc:
        raise CouponInvalidoError(str(exc)) from None
    for campo, valor_novo in changes.items():
        setattr(coupon, campo, valor_novo)
    session.commit()
    session.refresh(coupon)
    return coupon


def set_ativo(session: Session, coupon_id: int, ativo: bool) -> Coupon:
    coupon = get_coupon(session, coupon_id)
    coupon.ativo = ativo
    session.commit()
    session.refresh(coupon)
    return coupon


def registrar_teste(session: Session, coupon_id: int, testado_em: datetime | None) -> Coupon:
    coupon = get_coupon(session, coupon_id)
    coupon.testado_em = testado_em or datetime.now(UTC)
    session.commit()
    session.refresh(coupon)
    return coupon
