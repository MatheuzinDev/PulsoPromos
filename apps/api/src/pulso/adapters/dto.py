"""DTOs do dominio devolvidos pelos adaptadores (RF04, RF10, RNF09).

Nada aqui reflete o formato de resposta de um marketplace: traduzir e trabalho do adaptador.
"""

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Any

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, field_validator


def _rejeita_float(valor: object) -> object:
    if isinstance(valor, float):
        raise ValueError("dinheiro nao pode ser float; use Decimal ou string")
    return valor


# Compativel com NUMERIC(12,2); nunca float.
Dinheiro = Annotated[
    Decimal,
    BeforeValidator(_rejeita_float),
    Field(ge=0, max_digits=12, decimal_places=2),
]


class Leitura(BaseModel):
    """Uma leitura bem-sucedida de um anuncio."""

    model_config = ConfigDict(frozen=True)

    preco_vista: Dinheiro
    preco_parcelado_total: Dinheiro | None = None
    parcelas: int | None = Field(default=None, ge=1)
    frete: Dinheiro | None = None
    em_estoque: bool
    # Payload CRU do marketplace; quem julga o vendedor e a politica do adaptador (RF06, RNF10).
    vendedor: dict[str, Any]
    cupom_aplicavel: str | None = None
    origem: str = Field(min_length=1)  # adaptador + endpoint que produziu a leitura (RNF09)
    coletado_em: datetime  # RNF09

    @field_validator("coletado_em")
    @classmethod
    def _exige_fuso(cls, valor: datetime) -> datetime:
        if valor.tzinfo is None:
            raise ValueError("coletado_em precisa ter fuso (UTC)")
        return valor


class TipoFalha(StrEnum):
    ERRO = "erro"
    DESATUALIZADO = "desatualizado"


class Falha(BaseModel):
    """Falha de um adaptador representada como resultado, nao como excecao (RNF07, RNF09).

    `desatualizado` marca dado velho: nao gera alerta. Se houver, `ultima_leitura` guarda o
    ultimo dado conhecido.
    """

    model_config = ConfigDict(frozen=True)

    tipo: TipoFalha
    mensagem: str
    origem: str
    ocorrida_em: datetime
    ultima_leitura: Leitura | None = None


Resultado = Leitura | Falha
