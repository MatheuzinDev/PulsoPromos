from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from pulso.models import MARKETPLACES, TIPOS_MOVIMENTO

Marketplace = Literal[MARKETPLACES]  # type: ignore[valid-type]
TipoMovimento = Literal[TIPOS_MOVIMENTO]  # type: ignore[valid-type]

Ean = Annotated[str, StringConstraints(pattern=r"^\d{8}$|^\d{12,14}$")]
Texto = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
Dinheiro = Annotated[Decimal, Field(gt=0, max_digits=12, decimal_places=2)]
Milimetros = Annotated[Decimal, Field(gt=0, max_digits=5, decimal_places=1)]


class WatchCreate(BaseModel):
    marca: Texto
    referencia_fabricante: Texto
    ean: Ean
    tipo_movimento: TipoMovimento
    tamanho_caixa_mm: Milimetros
    vigilancia_ativa: bool = True
    preco_alvo: Dinheiro | None = None
    exige_loja_oficial: bool = False
    exige_reputacao_minima: bool = False


class WatchUpdate(BaseModel):
    """Edicao parcial. O EAN e a chave natural e nao muda por aqui."""

    model_config = ConfigDict(extra="forbid")

    marca: Texto | None = None
    referencia_fabricante: Texto | None = None
    tipo_movimento: TipoMovimento | None = None
    tamanho_caixa_mm: Milimetros | None = None
    preco_alvo: Dinheiro | None = None
    exige_loja_oficial: bool | None = None
    exige_reputacao_minima: bool | None = None

    @model_validator(mode="after")
    def _nulo_so_no_preco_alvo(self) -> Self:
        for campo in self.model_fields_set - {"preco_alvo"}:
            if getattr(self, campo) is None:
                raise ValueError(f"{campo} nao pode ser nulo")
        return self


class WatchOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    marca: str
    referencia_fabricante: str
    ean: str
    tipo_movimento: str
    tamanho_caixa_mm: Decimal
    vigilancia_ativa: bool
    preco_alvo: Decimal | None
    exige_loja_oficial: bool
    exige_reputacao_minima: bool
    criado_em: datetime
    atualizado_em: datetime


class WatchPage(BaseModel):
    items: list[WatchOut]
    total: int
    limit: int
    offset: int


class ListingCreate(BaseModel):
    marketplace: Marketplace
    marketplace_item_id: Texto
    url: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]


class ListingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    watch_id: int
    marketplace: str
    marketplace_item_id: str
    url: str
    ativo: bool
    criado_em: datetime
    atualizado_em: datetime


class ImportResult(BaseModel):
    criados: int
    atualizados: int
    inalterados: int
