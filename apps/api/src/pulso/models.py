from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    CHAR,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from pulso.db import Base

MARKETPLACES = ("shopee", "aliexpress", "mercado_livre", "amazon")
TIPOS_MOVIMENTO = ("automatico", "quartzo", "manual", "solar", "hibrido")


class TimestampMixin:
    criado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    atualizado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class Watch(TimestampMixin, Base):
    """O relogio do catalogo (RF01, RF03)."""

    __tablename__ = "watch"
    __table_args__ = (
        CheckConstraint(
            f"tipo_movimento IN {TIPOS_MOVIMENTO!r}", name="ck_watch_tipo_movimento"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    marca: Mapped[str] = mapped_column(String(120), nullable=False)
    referencia_fabricante: Mapped[str] = mapped_column(String(120), nullable=False)
    ean: Mapped[str] = mapped_column(String(14), nullable=False, unique=True)
    tipo_movimento: Mapped[str] = mapped_column(String(20), nullable=False)
    tamanho_caixa_mm: Mapped[Decimal] = mapped_column(Numeric(5, 1), nullable=False)
    vigilancia_ativa: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    preco_alvo: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    exige_loja_oficial: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    exige_reputacao_minima: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class Listing(TimestampMixin, Base):
    """Anuncio vinculado a um relogio (RF02)."""

    __tablename__ = "listing"
    __table_args__ = (
        CheckConstraint(f"marketplace IN {MARKETPLACES!r}", name="ck_listing_marketplace"),
        UniqueConstraint("marketplace", "marketplace_item_id", name="uq_listing_marketplace_item"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    watch_id: Mapped[int] = mapped_column(ForeignKey("watch.id"), nullable=False)
    marketplace: Mapped[str] = mapped_column(String(20), nullable=False)
    marketplace_item_id: Mapped[str] = mapped_column(String(120), nullable=False)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    ativo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class PriceReading(TimestampMixin, Base):
    """Historico de precos de um anuncio (RF11, RNF08, RNF09)."""

    __tablename__ = "price_reading"
    __table_args__ = (
        UniqueConstraint("listing_id", "coletado_em", name="uq_price_reading_listing_coletado"),
        Index(
            "ix_price_reading_listing_coletado_desc",
            "listing_id",
            text("coletado_em DESC"),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    listing_id: Mapped[int] = mapped_column(ForeignKey("listing.id"), nullable=False)
    coletado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    preco_vista: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    preco_parcelado_total: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    parcelas: Mapped[int | None] = mapped_column(Integer, nullable=True)
    frete: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    moeda: Mapped[str] = mapped_column(CHAR(3), nullable=False, default="BRL")
    em_estoque: Mapped[bool] = mapped_column(Boolean, nullable=False)
    vendedor_raw: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    is_loja_oficial: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    origem: Mapped[str] = mapped_column(String(60), nullable=False)
    desatualizado: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class Coupon(TimestampMixin, Base):
    """Cupom de desconto (RF13)."""

    __tablename__ = "coupon"
    __table_args__ = (
        CheckConstraint(f"marketplace IN {MARKETPLACES!r}", name="ck_coupon_marketplace"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    codigo: Mapped[str] = mapped_column(String(60), nullable=False)
    marketplace: Mapped[str] = mapped_column(String(20), nullable=False)
    regra_texto: Mapped[str] = mapped_column(Text, nullable=False)
    valido_ate: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    testado_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ativo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class Publication(TimestampMixin, Base):
    """Um post publicado — congela preco, loja e cupom do momento (RF22, RF44)."""

    __tablename__ = "publication"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    watch_id: Mapped[int] = mapped_column(ForeignKey("watch.id"), nullable=False)
    listing_id: Mapped[int] = mapped_column(ForeignKey("listing.id"), nullable=False)
    coupon_id: Mapped[int | None] = mapped_column(ForeignKey("coupon.id"), nullable=True)
    publicado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    preco_vista_publicado: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    preco_efetivo: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    loja: Mapped[str] = mapped_column(String(60), nullable=False)
    link_publicado: Mapped[str] = mapped_column(Text, nullable=False)
    telegram_message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    encerrada_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
