"""Politica de vendedor confiavel (RF17): ESTRUTURA apenas.

Quem julga o vendedor e o adaptador de cada marketplace (RF06, RNF10); o nucleo nao le
`vendedor_raw`. A politica real ainda nao existe. Sem politica registrada para o
marketplace o vendedor passa: quem barra hoje e `alerta_preco`, falso por padrao.
"""

from collections.abc import Callable, Mapping

from pulso.models import Listing, PriceReading, Watch

# True = vendedor confiavel para este relogio
PoliticaVendedor = Callable[[Watch, Listing, PriceReading], bool]

POLITICAS_VENDEDOR: dict[str, PoliticaVendedor] = {}


def vendedor_confiavel(
    politicas: Mapping[str, PoliticaVendedor],
    watch: Watch,
    listing: Listing,
    reading: PriceReading,
) -> bool:
    politica = politicas.get(listing.marketplace)
    return True if politica is None else politica(watch, listing, reading)
