"""Pontos do nucleo que consomem adaptadores sem conhecer marketplace nenhum."""

from collections.abc import Iterable
from datetime import UTC, datetime

from pulso.adapters.base import AdaptadorMarketplace
from pulso.adapters.dto import Falha, Resultado, TipoFalha
from pulso.adapters.permissoes import Permissoes, RegistroPermissoes


def permissoes_de(adaptador: AdaptadorMarketplace, registro: RegistroPermissoes) -> Permissoes:
    return registro.de(adaptador.marketplace)


def consultar_isolado(adaptador: AdaptadorMarketplace, item_id: str) -> Resultado:
    """Rede de seguranca: excecao inesperada de um adaptador vira `Falha` (RNF07)."""
    try:
        return adaptador.consultar(item_id)
    except Exception as exc:
        return Falha(
            tipo=TipoFalha.ERRO,
            mensagem=f"{type(exc).__name__}: {exc}",
            origem=adaptador.nome,
            ocorrida_em=datetime.now(UTC),
        )


def coletar(adaptadores: Iterable[tuple[AdaptadorMarketplace, str]]) -> list[Resultado]:
    """Coleta cada (adaptador, item); a falha de um nao interrompe os demais."""
    return [consultar_isolado(adaptador, item_id) for adaptador, item_id in adaptadores]
