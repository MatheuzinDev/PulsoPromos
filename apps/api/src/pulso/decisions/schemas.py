from datetime import datetime
from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, ConfigDict, StringConstraints

Loja = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=60)]
Texto = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
DecididoPor = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]


class AprovarIn(BaseModel):
    """RF20: aprovar exige a loja (o nucleo nao sabe qual e - ver decisions/service.py).

    `texto`, quando informado, substitui o texto composto (RF22) e fica marcado como editado.
    """

    loja: Loja
    texto: Texto | None = None
    decidido_por: DecididoPor


class DescartarIn(BaseModel):
    decidido_por: DecididoPor


class CandidateOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    watch_id: int
    listing_id: int
    coupon_id: int | None
    regra: str
    preco_vista: Decimal
    desconto: Decimal
    preco_sem_frete: Decimal
    frete: Decimal | None
    frete_desconhecido: bool
    media_30d: Decimal | None
    minimo_90d: Decimal | None
    preco_alvo: Decimal | None
    queda_percentual: Decimal
    economia_absoluta: Decimal
    pontuacao: Decimal
    status: str
    criado_em: datetime
    decidido_em: datetime | None
    decidido_por: str | None


class PublicationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    watch_id: int
    listing_id: int
    coupon_id: int | None
    publicado_em: datetime
    preco_vista_publicado: Decimal
    preco_efetivo: Decimal
    loja: str
    link_publicado: str
    texto_publicado: str
    texto_editado: bool
    telegram_message_id: int | None
    encerrada_em: datetime | None


class AprovarOut(BaseModel):
    candidate: CandidateOut
    publication: PublicationOut
