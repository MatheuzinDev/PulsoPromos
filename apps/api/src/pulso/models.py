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
TIPOS_CUPOM = ("percentual", "valor_fixo")
REGRAS_CANDIDATO = ("media_30d", "minimo_90d", "preco_alvo")
STATUS_CANDIDATO = ("pendente", "aprovado", "descartado")


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
        CheckConstraint(f"tipo_movimento IN {TIPOS_MOVIMENTO!r}", name="ck_watch_tipo_movimento"),
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
    """Cupom de desconto (RF13), estruturado para ser calculado (RF14).

    `valor` e o percentual (10.00 = 10%) ou o valor em reais, conforme `tipo`. `loja` NULL
    significa "vale para o marketplace inteiro". `regra_texto` e so observacao humana.
    """

    __tablename__ = "coupon"
    __table_args__ = (
        CheckConstraint(f"marketplace IN {MARKETPLACES!r}", name="ck_coupon_marketplace"),
        CheckConstraint(f"tipo IN {TIPOS_CUPOM!r}", name="ck_coupon_tipo"),
        CheckConstraint(
            "(tipo = 'percentual' AND valor > 0 AND valor <= 100)"
            " OR (tipo = 'valor_fixo' AND valor > 0)",
            name="ck_coupon_valor",
        ),
        CheckConstraint(
            "teto_desconto IS NULL OR (tipo = 'percentual' AND teto_desconto > 0)",
            name="ck_coupon_teto_so_percentual",
        ),
        CheckConstraint(
            "minimo_compra IS NULL OR minimo_compra > 0", name="ck_coupon_minimo_compra"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    codigo: Mapped[str] = mapped_column(String(60), nullable=False)
    marketplace: Mapped[str] = mapped_column(String(20), nullable=False)
    regra_texto: Mapped[str] = mapped_column(Text, nullable=False)
    tipo: Mapped[str] = mapped_column(String(20), nullable=False)
    valor: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    minimo_compra: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    teto_desconto: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    loja: Mapped[str | None] = mapped_column(String(60), nullable=True)
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
    texto_publicado: Mapped[str] = mapped_column(Text, nullable=False)
    texto_editado: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    telegram_message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    encerrada_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Candidate(Base):
    """Oferta que o motor de regras (RF15) considera digna de revisao (RF19, RF20).

    Existe antes de qualquer `publication`: o RF16 precisa lembrar do que ja foi alertado,
    inclusive o que o operador descartou. Nasce `pendente`; a decisao e de RF20.
    `preco_sem_frete` e o valor comparado pelas regras; frete so e carregado para RF19/RF22.
    """

    __tablename__ = "candidate"
    __table_args__ = (
        CheckConstraint(f"regra IN {REGRAS_CANDIDATO!r}", name="ck_candidate_regra"),
        CheckConstraint(f"status IN {STATUS_CANDIDATO!r}", name="ck_candidate_status"),
        Index("ix_candidate_watch_criado_desc", "watch_id", text("criado_em DESC")),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    watch_id: Mapped[int] = mapped_column(ForeignKey("watch.id"), nullable=False)
    listing_id: Mapped[int] = mapped_column(ForeignKey("listing.id"), nullable=False)
    coupon_id: Mapped[int | None] = mapped_column(ForeignKey("coupon.id"), nullable=True)
    regra: Mapped[str] = mapped_column(String(20), nullable=False)
    preco_vista: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    desconto: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    preco_sem_frete: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    frete: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    frete_desconhecido: Mapped[bool] = mapped_column(Boolean, nullable=False)
    media_30d: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    minimo_90d: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    preco_alvo: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    queda_percentual: Mapped[Decimal] = mapped_column(Numeric(7, 2), nullable=False)
    economia_absoluta: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    pontuacao: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pendente")
    criado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    decidido_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decidido_por: Mapped[str | None] = mapped_column(String(120), nullable=True)
