"""Registro de permissoes por marketplace (RF06). Unico lugar onde elas sao declaradas.

O nucleo consulta este registro; o adaptador nao decide por conta propria durante a execucao.
Sem configuracao (RF29) e sem termos lidos: o padrao e o mais restritivo.
"""

from dataclasses import dataclass, field

AVISO_AFILIADO = "aviso_afiliado"
AVISO_CARIMBO_DATA_HORA = "carimbo_data_hora"


@dataclass(frozen=True)
class Permissoes:
    guarda_historico_precos: bool = False
    alerta_preco: bool = False
    canais_permitidos: frozenset[str] = frozenset()
    avisos_exigidos: frozenset[str] = frozenset({AVISO_AFILIADO, AVISO_CARIMBO_DATA_HORA})


PERMISSOES_PADRAO = Permissoes()

# Valores por marketplace so entram depois de lidos os termos (ver Riscos em docs/requisitos.md).
PERMISSOES_POR_MARKETPLACE: dict[str, Permissoes] = {}


@dataclass
class RegistroPermissoes:
    padrao: Permissoes = PERMISSOES_PADRAO
    por_marketplace: dict[str, Permissoes] = field(
        default_factory=lambda: dict(PERMISSOES_POR_MARKETPLACE)
    )

    def registrar(self, marketplace: str, permissoes: Permissoes) -> None:
        self.por_marketplace[marketplace] = permissoes

    def de(self, marketplace: str) -> Permissoes:
        return self.por_marketplace.get(marketplace, self.padrao)
