from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal, Self

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)

from pulso.models import MARKETPLACES, TIPOS_CUPOM

Marketplace = Literal[MARKETPLACES]  # type: ignore[valid-type]
TipoCupom = Literal[TIPOS_CUPOM]  # type: ignore[valid-type]

Dinheiro = Annotated[Decimal, Field(gt=0, max_digits=12, decimal_places=2)]
Codigo = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=60)]
Loja = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=60)]


def validar_regra(tipo: str, valor: Decimal, teto_desconto: Decimal | None) -> None:
    """Coerencia entre tipo, valor e teto. Levanta ValueError."""
    if tipo == "percentual" and not Decimal(0) < valor <= Decimal(100):
        raise ValueError("percentual deve ser maior que 0 e no maximo 100")
    if tipo == "valor_fixo" and valor <= 0:
        raise ValueError("valor_fixo deve ser maior que zero")
    if teto_desconto is not None and tipo != "percentual":
        raise ValueError("teto_desconto so faz sentido em cupom percentual")


class CouponCreate(BaseModel):
    codigo: Codigo
    marketplace: Marketplace
    tipo: TipoCupom
    valor: Dinheiro
    minimo_compra: Dinheiro | None = None
    teto_desconto: Dinheiro | None = None
    loja: Loja | None = None  # None: vale para o marketplace inteiro
    regra_texto: str = ""  # observacao humana; o calculo usa so os campos estruturados
    valido_ate: AwareDatetime | None = None
    ativo: bool = True

    @model_validator(mode="after")
    def _regra_coerente(self) -> Self:
        validar_regra(self.tipo, self.valor, self.teto_desconto)
        return self


class CouponUpdate(BaseModel):
    """Edicao parcial; a coerencia tipo/valor/teto e conferida no service, sobre o resultado."""

    model_config = ConfigDict(extra="forbid")

    codigo: Codigo | None = None
    marketplace: Marketplace | None = None
    tipo: TipoCupom | None = None
    valor: Dinheiro | None = None
    minimo_compra: Dinheiro | None = None
    teto_desconto: Dinheiro | None = None
    loja: Loja | None = None
    regra_texto: str | None = None
    valido_ate: AwareDatetime | None = None
    ativo: bool | None = None

    @model_validator(mode="after")
    def _nulo_so_onde_permitido(self) -> Self:
        anulaveis = {"minimo_compra", "teto_desconto", "loja", "valido_ate"}
        for campo in self.model_fields_set - anulaveis:
            if getattr(self, campo) is None:
                raise ValueError(f"{campo} nao pode ser nulo")
        return self


class TestadoIn(BaseModel):
    testado_em: AwareDatetime | None = None  # padrao: agora, em UTC


class CouponOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    codigo: str
    marketplace: str
    tipo: str
    valor: Decimal
    minimo_compra: Decimal | None
    teto_desconto: Decimal | None
    loja: str | None
    regra_texto: str
    valido_ate: datetime | None
    testado_em: datetime | None
    ativo: bool
    criado_em: datetime
    atualizado_em: datetime


class CouponPage(BaseModel):
    items: list[CouponOut]
    total: int
    limit: int
    offset: int
