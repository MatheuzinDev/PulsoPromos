"""Contrato comum dos adaptadores de marketplace (RF04)."""

from typing import Protocol, runtime_checkable

from pulso.adapters.dto import Resultado


@runtime_checkable
class AdaptadorMarketplace(Protocol):
    marketplace: str  # chave nas permissoes (RF06); um dos MARKETPLACES do catalogo
    nome: str  # identifica o adaptador na origem de cada leitura (RNF09)

    def consultar(self, item_id: str) -> Resultado:
        """Consulta um anuncio. Falhas esperadas voltam como `Falha`, nao como excecao."""
        ...
