"""Agendamento da coleta por marketplace (RF09). Intervalos vem do ambiente (config.py)."""

from collections.abc import Callable, Mapping

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy.orm import Session

from pulso.adapters.base import AdaptadorMarketplace
from pulso.adapters.permissoes import RegistroPermissoes
from pulso.collect.service import ResumoColeta, coletar_catalogo
from pulso.config import Settings
from pulso.models import MARKETPLACES


def executar_coleta(
    marketplace: str,
    session_factory: Callable[[], Session],
    adaptadores: Mapping[str, AdaptadorMarketplace],
    permissoes: RegistroPermissoes,
) -> ResumoColeta:
    """Corpo do job; pode ser chamado direto, sem esperar o relogio."""
    with session_factory() as session:
        return coletar_catalogo(session, adaptadores, permissoes, marketplace)


def criar_agendador(
    settings: Settings,
    session_factory: Callable[[], Session],
    adaptadores: Mapping[str, AdaptadorMarketplace],
    permissoes: RegistroPermissoes,
) -> BackgroundScheduler:
    """Um job por marketplace que tenha adaptador. Nao inicia: quem chama decide."""
    agendador = BackgroundScheduler(timezone="UTC")
    for marketplace in MARKETPLACES:
        if marketplace not in adaptadores:
            continue
        agendador.add_job(
            executar_coleta,
            IntervalTrigger(minutes=settings.intervalo_coleta_min(marketplace)),
            args=[marketplace, session_factory, adaptadores, permissoes],
            id=f"coleta:{marketplace}",
            max_instances=1,
            coalesce=True,
        )
    return agendador
