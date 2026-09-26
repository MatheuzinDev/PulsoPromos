import logging
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from pulso.adapters.dto import Falha, Leitura, Resultado, TipoFalha
from pulso.adapters.fake import AdaptadorFalso
from pulso.adapters.permissoes import PERMISSOES_POR_MARKETPLACE, Permissoes, RegistroPermissoes
from pulso.collect.agendador import criar_agendador, executar_coleta
from pulso.collect.service import coletar_catalogo
from pulso.config import Settings
from pulso.models import Listing, PriceReading, Watch

T0 = datetime(2026, 9, 26, 12, 0, tzinfo=UTC)


def leitura(agora: datetime, **campos: Any) -> Leitura:
    base: dict[str, Any] = {
        "preco_vista": Decimal("199.90"),
        "preco_parcelado_total": Decimal("219.90"),
        "parcelas": 10,
        "frete": Decimal("0.00"),
        "em_estoque": True,
        "vendedor": {"nivel": "x", "nota": 4.8, "extra": {"a": 1}},
        "origem": "stub:item",
        "coletado_em": agora,
    }
    return Leitura(**(base | campos))


class Stub:
    """Adaptador programavel: `respostas[item_id]` e o que ele devolve (ou levanta)."""

    def __init__(self, marketplace: str = "shopee") -> None:
        self.marketplace = marketplace
        self.nome = f"stub-{marketplace}"
        self.respostas: dict[str, Resultado | Exception] = {}
        self.chamadas: list[str] = []

    def consultar(self, item_id: str) -> Resultado:
        self.chamadas.append(item_id)
        resposta = self.respostas[item_id]
        if isinstance(resposta, Exception):
            raise resposta
        return resposta


def novo_relogio(session: Session, ean: str, ativa: bool = True) -> Watch:
    watch = Watch(
        marca="Seiko",
        referencia_fabricante="SNK809",
        ean=ean,
        tipo_movimento="automatico",
        tamanho_caixa_mm=Decimal("37.0"),
        vigilancia_ativa=ativa,
    )
    session.add(watch)
    session.flush()
    return watch


def novo_anuncio(
    session: Session, watch: Watch, item_id: str, marketplace: str = "shopee", ativo: bool = True
) -> Listing:
    listing = Listing(
        watch_id=watch.id,
        marketplace=marketplace,
        marketplace_item_id=item_id,
        url=f"https://exemplo.test/{item_id}",
        ativo=ativo,
    )
    session.add(listing)
    session.flush()
    return listing


def liberado(marketplace: str = "shopee") -> RegistroPermissoes:
    registro = RegistroPermissoes(por_marketplace={})
    registro.registrar(marketplace, Permissoes(guarda_historico_precos=True))
    return registro


def total(session: Session) -> int:
    return session.scalar(select(func.count()).select_from(PriceReading)) or 0


@pytest.fixture
def cenario(db_session: Session) -> tuple[Session, Stub, Listing]:
    listing = novo_anuncio(db_session, novo_relogio(db_session, "7891234567895"), "A1")
    stub = Stub()
    stub.respostas["A1"] = leitura(T0)
    return db_session, stub, listing


def test_primeira_coleta_grava_leitura(cenario: tuple[Session, Stub, Listing]) -> None:
    session, stub, listing = cenario
    resumo = coletar_catalogo(session, {"shopee": stub}, liberado())
    assert resumo.gravados == 1
    linha = session.scalars(select(PriceReading)).one()
    assert linha.listing_id == listing.id
    assert linha.preco_vista == Decimal("199.90")


def test_preco_mudou_grava_linha_nova(cenario: tuple[Session, Stub, Listing]) -> None:
    session, stub, _ = cenario
    coletar_catalogo(session, {"shopee": stub}, liberado())
    stub.respostas["A1"] = leitura(T0 + timedelta(hours=1), preco_vista=Decimal("179.90"))
    coletar_catalogo(session, {"shopee": stub}, liberado())
    assert total(session) == 2


def test_idempotente_duas_coletas_sem_mudanca(cenario: tuple[Session, Stub, Listing]) -> None:
    session, stub, _ = cenario
    coletar_catalogo(session, {"shopee": stub}, liberado())
    stub.respostas["A1"] = leitura(T0 + timedelta(hours=1))  # mesmos valores, outro instante
    resumo = coletar_catalogo(session, {"shopee": stub}, liberado())
    assert total(session) == 1
    assert resumo.gravados == 0
    assert resumo.inalterados == 1


def test_mudanca_so_de_estoque_grava(cenario: tuple[Session, Stub, Listing]) -> None:
    session, stub, _ = cenario
    coletar_catalogo(session, {"shopee": stub}, liberado())
    stub.respostas["A1"] = leitura(T0 + timedelta(hours=1), em_estoque=False)
    coletar_catalogo(session, {"shopee": stub}, liberado())
    linhas = session.scalars(select(PriceReading).order_by(PriceReading.coletado_em)).all()
    assert [linha.em_estoque for linha in linhas] == [True, False]


@pytest.mark.parametrize(
    "campos",
    [
        {"frete": Decimal("12.00")},
        {"preco_parcelado_total": Decimal("229.90")},
        {"parcelas": 12},
    ],
)
def test_mudanca_de_frete_ou_parcelado_grava(
    cenario: tuple[Session, Stub, Listing], campos: dict[str, Any]
) -> None:
    session, stub, _ = cenario
    coletar_catalogo(session, {"shopee": stub}, liberado())
    stub.respostas["A1"] = leitura(T0 + timedelta(hours=1), **campos)
    coletar_catalogo(session, {"shopee": stub}, liberado())
    assert total(session) == 2


def test_mudanca_so_do_vendedor_nao_grava(cenario: tuple[Session, Stub, Listing]) -> None:
    session, stub, _ = cenario
    coletar_catalogo(session, {"shopee": stub}, liberado())
    stub.respostas["A1"] = leitura(T0 + timedelta(hours=1), vendedor={"nota": 1.0, "novo": True})
    coletar_catalogo(session, {"shopee": stub}, liberado())
    assert total(session) == 1


def test_mudanca_so_do_cupom_nao_grava(cenario: tuple[Session, Stub, Listing]) -> None:
    session, stub, _ = cenario
    coletar_catalogo(session, {"shopee": stub}, liberado())
    stub.respostas["A1"] = leitura(T0 + timedelta(hours=1), cupom_aplicavel="PROMO10")
    coletar_catalogo(session, {"shopee": stub}, liberado())
    assert total(session) == 1


def test_permissao_padrao_nega_e_nada_e_gravado(cenario: tuple[Session, Stub, Listing]) -> None:
    """Configuracao real de hoje: padrao nega e o mapa por marketplace esta vazio."""
    session, stub, _ = cenario
    assert PERMISSOES_POR_MARKETPLACE == {}
    resumo = coletar_catalogo(session, {"shopee": stub}, RegistroPermissoes())
    assert total(session) == 0
    assert resumo.sem_permissao == 1
    assert stub.chamadas == []  # nem consulta o marketplace


def test_permissao_explicitamente_falsa_nao_grava(cenario: tuple[Session, Stub, Listing]) -> None:
    session, stub, _ = cenario
    registro = RegistroPermissoes(por_marketplace={})
    registro.registrar("shopee", Permissoes(guarda_historico_precos=False))
    coletar_catalogo(session, {"shopee": stub}, registro)
    assert total(session) == 0


def test_vigilancia_inativa_e_anuncio_inativo_nao_sao_coletados(db_session: Session) -> None:
    ativo = novo_relogio(db_session, "7891234567895")
    pausado = novo_relogio(db_session, "7891234567896", ativa=False)
    novo_anuncio(db_session, ativo, "OK")
    novo_anuncio(db_session, ativo, "INATIVO", ativo=False)
    novo_anuncio(db_session, pausado, "PAUSADO")
    stub = Stub()
    for item in ("OK", "INATIVO", "PAUSADO"):
        stub.respostas[item] = leitura(T0)
    coletar_catalogo(db_session, {"shopee": stub}, liberado())
    assert stub.chamadas == ["OK"]
    assert total(db_session) == 1


def test_falha_de_um_anuncio_nao_impede_os_outros(
    db_session: Session, caplog: pytest.LogCaptureFixture
) -> None:
    watch = novo_relogio(db_session, "7891234567895")
    for item in ("A", "B", "C", "D"):
        novo_anuncio(db_session, watch, item)
    stub = Stub()
    stub.respostas["A"] = leitura(T0)
    stub.respostas["B"] = RuntimeError("explodiu no meio")
    stub.respostas["C"] = Falha(
        tipo=TipoFalha.ERRO, mensagem="fora do ar", origem="stub", ocorrida_em=T0
    )
    stub.respostas["D"] = leitura(T0)
    with caplog.at_level(logging.ERROR, logger="pulso.collect"):
        resumo = coletar_catalogo(db_session, {"shopee": stub}, liberado())
    assert stub.chamadas == ["A", "B", "C", "D"]
    assert resumo.gravados == 2
    assert resumo.falhas == 2
    assert total(db_session) == 2
    assert [r.getMessage() for r in caplog.records].count("coleta_falha") == 2


def test_falha_nao_marca_nem_grava_leitura_desatualizada(
    cenario: tuple[Session, Stub, Listing],
) -> None:
    session, stub, _ = cenario
    coletar_catalogo(session, {"shopee": stub}, liberado())
    stub.respostas["A1"] = Falha(
        tipo=TipoFalha.DESATUALIZADO,
        mensagem="dado velho",
        origem="stub",
        ocorrida_em=T0,
        ultima_leitura=leitura(T0),
    )
    coletar_catalogo(session, {"shopee": stub}, liberado())
    linha = session.scalars(select(PriceReading)).one()
    assert linha.desatualizado is False


def test_adaptador_de_um_marketplace_falhando_nao_barra_outro(db_session: Session) -> None:
    watch = novo_relogio(db_session, "7891234567895")
    novo_anuncio(db_session, watch, "S1", "shopee")
    novo_anuncio(db_session, watch, "M1", "mercado_livre")
    quebrado = Stub("shopee")
    quebrado.respostas["S1"] = RuntimeError("x")
    bom = Stub("mercado_livre")
    bom.respostas["M1"] = leitura(T0)
    registro = liberado("shopee")
    registro.registrar("mercado_livre", Permissoes(guarda_historico_precos=True))
    resumo = coletar_catalogo(db_session, {"shopee": quebrado, "mercado_livre": bom}, registro)
    assert (resumo.gravados, resumo.falhas) == (1, 1)


def test_anuncio_excluido_durante_a_coleta_nao_derruba_a_rodada(db_session: Session) -> None:
    watch = novo_relogio(db_session, "7891234567895")
    apagado = novo_anuncio(db_session, watch, "A")
    novo_anuncio(db_session, watch, "B")
    db_session.commit()  # o catalogo ja esta persistido quando a coleta roda

    class ApagaAoConsultar(Stub):
        def consultar(self, item_id: str) -> Resultado:
            if item_id == "A":
                db_session.execute(delete(Listing).where(Listing.id == apagado.id))
            return super().consultar(item_id)

    stub = ApagaAoConsultar()
    stub.respostas["A"] = leitura(T0)
    stub.respostas["B"] = leitura(T0)
    resumo = coletar_catalogo(db_session, {"shopee": stub}, liberado())
    assert resumo.falhas == 1
    assert resumo.gravados == 1


def test_leitura_gravada_tem_origem_e_coletado_em_e_vendedor_cru(
    cenario: tuple[Session, Stub, Listing],
) -> None:
    session, stub, _ = cenario
    esperado = stub.respostas["A1"]
    assert isinstance(esperado, Leitura)
    coletar_catalogo(session, {"shopee": stub}, liberado())
    linha = session.scalars(select(PriceReading)).one()
    assert linha.origem == "stub:item"
    assert linha.coletado_em == T0
    assert linha.vendedor_raw == esperado.vendedor  # identico, sem normalizar


def test_com_adaptador_falso_e_fixture(db_session: Session, tmp_path: Path) -> None:
    fixtures = Path(__file__).parent.parent / "adapters" / "fixtures"
    adaptador = AdaptadorFalso("shopee", fixtures, agora=T0)
    novo_anuncio(db_session, novo_relogio(db_session, "7891234567895"), "ok1")
    coletar_catalogo(db_session, {"shopee": adaptador}, liberado())
    coletar_catalogo(db_session, {"shopee": adaptador}, liberado())
    linha = db_session.scalars(select(PriceReading)).one()
    assert linha.origem == "fake-shopee:ok1.json"
    assert linha.vendedor_raw == {"campo_qualquer": {"a": 1}, "nivel": "x"}


def test_agendador_chamado_direto_e_por_marketplace(
    db_session: Session, cenario: tuple[Session, Stub, Listing]
) -> None:
    session, stub, _ = cenario
    resumo = executar_coleta("shopee", lambda: session, {"shopee": stub}, liberado())
    assert resumo.gravados == 1
    # coleta de outro marketplace nao toca o anuncio da shopee
    assert executar_coleta("amazon", lambda: session, {"amazon": stub}, liberado()).consultados == 0


def test_agendador_cria_um_job_por_marketplace_com_intervalo_da_config() -> None:
    settings = Settings(coleta_intervalo_shopee_min=40)
    agendador = criar_agendador(
        settings,
        lambda: None,  # type: ignore[arg-type,return-value]
        {"shopee": Stub(), "amazon": Stub("amazon")},
        RegistroPermissoes(),
    )
    jobs = {job.id: job for job in agendador.get_jobs()}
    assert set(jobs) == {"coleta:shopee", "coleta:amazon"}
    assert jobs["coleta:shopee"].trigger.interval == timedelta(minutes=40)
    assert jobs["coleta:amazon"].trigger.interval == timedelta(minutes=60)
