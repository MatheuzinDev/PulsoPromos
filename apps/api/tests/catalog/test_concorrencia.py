"""Corrida real entre a API e quem grava historico (RF30, RNF09, RNF12).

O resto da suite compartilha uma sessao so entre cliente e assercoes e por isso nao enxerga
corrida. Aqui a API roda com a get_session de producao (uma sessao e uma conexao por request) e o
concorrente -- o papel do futuro coletor ou publicador -- tem conexao e transacao proprias. Tudo e
commitado de verdade; a fixture `api` limpa o que os testes criaram.
"""

import threading
from collections.abc import Callable, Generator
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from helpers import payload
from sqlalchemy import Engine, delete, func, or_, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from pulso.catalog import service
from pulso.catalog.errors import CatalogError
from pulso.catalog.schemas import WatchUpdate
from pulso.db import SessionLocal
from pulso.main import app
from pulso.models import Listing, PriceReading, Publication, Watch

EAN = "20000017"
NOVO_EAN = "20000024"
ESPERA = 0.5  # quanto tempo a janela da corrida fica aberta


def _limpar(engine: Engine) -> None:
    # limpeza do banco de teste; o produto nunca apaga historico
    with Session(engine) as s:
        s.execute(text("SET lock_timeout = '10s'"))  # falha em vez de pendurar a suite
        watches = select(Watch.id).where(Watch.ean.in_((EAN, NOVO_EAN)))
        listings = select(Listing.id).where(Listing.watch_id.in_(watches))
        s.execute(
            delete(Publication).where(
                or_(Publication.watch_id.in_(watches), Publication.listing_id.in_(listings))
            )
        )
        s.execute(delete(PriceReading).where(PriceReading.listing_id.in_(listings)))
        s.execute(delete(Listing).where(Listing.watch_id.in_(watches)))
        s.execute(delete(Watch).where(Watch.ean.in_((EAN, NOVO_EAN))))
        s.commit()


@pytest.fixture
def api(engine: Engine) -> Generator[TestClient, None, None]:
    assert not app.dependency_overrides, "estes testes precisam da get_session real"
    _limpar(engine)
    try:
        yield TestClient(app, raise_server_exceptions=False)
    finally:
        _limpar(engine)


def _preparar(api: TestClient) -> tuple[int, int]:
    watch = api.post("/watches", json=payload(EAN))
    assert watch.status_code == 201, watch.text
    watch_id = int(watch.json()["id"])
    listing = api.post(
        f"/watches/{watch_id}/listings",
        json={"marketplace": "shopee", "marketplace_item_id": "CORRIDA1", "url": "http://x/1"},
    )
    assert listing.status_code == 201, listing.text
    return watch_id, int(listing.json()["id"])


def _leitura(listing_id: int) -> PriceReading:
    return PriceReading(
        listing_id=listing_id,
        coletado_em=datetime.now(UTC),
        preco_vista=Decimal("100.00"),
        em_estoque=True,
        vendedor_raw={},
        origem="teste-concorrencia",
    )


def _publicacao(watch_id: int, listing_id: int) -> Publication:
    return Publication(
        watch_id=watch_id,
        listing_id=listing_id,
        publicado_em=datetime.now(UTC),
        preco_vista_publicado=Decimal("100.00"),
        preco_efetivo=Decimal("90.00"),
        loja="Loja",
        link_publicado="http://x/1",
    )


def _escritor(engine: Engine) -> Session:
    """Sessao independente da API; o lock_timeout so evita um teste pendurado para sempre."""
    session = Session(engine)
    session.execute(text("SET lock_timeout = '10s'"))
    return session


def _gravar(engine: Engine, registro: object) -> None:
    with _escritor(engine) as s:
        s.add(registro)
        s.commit()


def _estado(engine: Engine, watch_id: int, listing_id: int) -> tuple[str | None, bool, int, int]:
    """(EAN do relogio, listing existe, leituras do listing, publicacoes do relogio)."""
    with Session(engine) as s:
        ean = s.scalar(select(Watch.ean).where(Watch.id == watch_id))
        listing = s.get(Listing, listing_id) is not None
        leituras = s.scalar(
            select(func.count())
            .select_from(PriceReading)
            .where(PriceReading.listing_id == listing_id)
        )
        publicacoes = s.scalar(
            select(func.count()).select_from(Publication).where(Publication.watch_id == watch_id)
        )
    return ean, listing, leituras or 0, publicacoes or 0


class Concorrente(threading.Thread):
    """Roda `trabalho` noutra thread (e noutra conexao) e guarda o resultado ou o erro."""

    def __init__(self, trabalho: Callable[[], object]) -> None:
        super().__init__(daemon=True)
        self._trabalho = trabalho
        self.resultado: object = None
        self.erro: Exception | None = None
        self.bloqueado: bool | None = None

    def run(self) -> None:
        try:
            self.resultado = self._trabalho()
        except Exception as exc:
            self.erro = exc

    def soltar(self) -> None:
        """Abre a janela: solta o concorrente e segura a operacao ate ele acabar ou travar."""
        self.start()
        self.join(ESPERA)
        self.bloqueado = self.is_alive()

    def terminar(self) -> None:
        self.join(10)
        assert not self.is_alive(), "o concorrente ficou travado"


def _depois_de(monkeypatch: pytest.MonkeyPatch, nome: str, acao: Callable[[], None]) -> None:
    """Executa `acao` uma vez, logo depois da primeira chamada de service.<nome>."""
    original = getattr(service, nome)
    pendente = [True]

    def envolvido(*args: Any, **kwargs: Any) -> Any:
        resultado = original(*args, **kwargs)
        if pendente[0]:
            pendente[0] = False
            acao()
        return resultado

    monkeypatch.setattr(service, nome, envolvido)


# --- (A) DELETE -------------------------------------------------------------------------------


def test_delete_com_leitura_em_andamento_responde_409_e_nao_apaga_nada(
    api: TestClient, engine: Engine
) -> None:
    watch_id, listing_id = _preparar(api)
    coletor = _escritor(engine)
    coletor.add(_leitura(listing_id))
    coletor.flush()  # a leitura existe mas nao foi commitada: FOR KEY SHARE no listing
    commit = threading.Timer(ESPERA, coletor.commit)
    commit.start()
    try:
        response = api.delete(f"/watches/{watch_id}")
    finally:
        commit.join()
        coletor.close()
    assert response.status_code == 409, response.text
    assert "Nao e possivel excluir o relogio" in response.json()["detail"]
    assert _estado(engine, watch_id, listing_id) == (EAN, True, 1, 0)


def test_delete_com_leitura_gravada_entre_guarda_e_exclusao_nunca_responde_500(
    api: TestClient, engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A corrida do review: a guarda passa e so depois outra conexao grava uma leitura."""
    watch_id, listing_id = _preparar(api)
    coletor = Concorrente(lambda: _gravar(engine, _leitura(listing_id)))
    _depois_de(monkeypatch, "_guard_sem_historico", coletor.soltar)

    response = api.delete(f"/watches/{watch_id}")
    coletor.terminar()

    assert response.status_code == 204, response.text
    # o coletor ficou esperando o lock do listing; quando o DELETE commitou, a FK recusou a
    # leitura. Nenhuma leitura foi apagada, porque nenhuma chegou a existir
    assert coletor.bloqueado is True
    assert isinstance(coletor.erro, IntegrityError)
    assert _estado(engine, watch_id, listing_id) == (None, False, 0, 0)


def test_delete_sem_o_lock_dos_anuncios_ainda_responde_409_e_nao_apaga_nada(
    api: TestClient, engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Defesa em profundidade: sem os locks, a FK barra o DELETE e o savepoint desfaz tudo."""
    watch_id, listing_id = _preparar(api)
    monkeypatch.setattr(service, "_travar_anuncios", lambda *_args: None)
    coletor = Concorrente(lambda: _gravar(engine, _leitura(listing_id)))
    _depois_de(monkeypatch, "_guard_sem_historico", coletor.soltar)

    response = api.delete(f"/watches/{watch_id}")
    coletor.terminar()

    assert coletor.erro is None
    assert response.status_code == 409, response.text
    assert "Nada foi apagado" in response.json()["detail"]
    assert _estado(engine, watch_id, listing_id) == (EAN, True, 1, 0)


# --- (B) PATCH do EAN -------------------------------------------------------------------------


def test_patch_ean_com_leitura_em_andamento_responde_409_e_ean_nao_muda(
    api: TestClient, engine: Engine
) -> None:
    watch_id, listing_id = _preparar(api)
    coletor = _escritor(engine)
    coletor.add(_leitura(listing_id))
    coletor.flush()
    commit = threading.Timer(ESPERA, coletor.commit)
    commit.start()
    try:
        response = api.patch(f"/watches/{watch_id}", json={"ean": NOVO_EAN})
    finally:
        commit.join()
        coletor.close()
    assert response.status_code == 409, response.text
    assert "Nao e possivel trocar o EAN do relogio" in response.json()["detail"]
    assert _estado(engine, watch_id, listing_id) == (EAN, True, 1, 0)


def test_patch_ean_nunca_troca_o_ean_de_um_relogio_que_ja_tem_historico(
    api: TestClient, engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A corrida do review: a guarda passa e so depois outra conexao grava uma leitura."""
    watch_id, listing_id = _preparar(api)

    def coletar() -> str | None:
        with _escritor(engine) as s:
            s.add(_leitura(listing_id))
            s.commit()
            # o EAN que qualquer leitor ve no instante em que o historico passa a existir
            return s.scalar(select(Watch.ean).where(Watch.id == watch_id))

    coletor = Concorrente(coletar)
    _depois_de(monkeypatch, "_guard_sem_historico", coletor.soltar)

    response = api.patch(f"/watches/{watch_id}", json={"ean": NOVO_EAN})
    coletor.terminar()

    assert coletor.erro is None
    ean_final, _, leituras, _ = _estado(engine, watch_id, listing_id)
    assert leituras == 1
    # a invariante: depois que o historico existe, o EAN nao muda mais
    assert ean_final == coletor.resultado
    # com o lock, a leitura esperou o PATCH: a troca aconteceu sem historico e a leitura depois
    assert coletor.bloqueado is True
    assert response.status_code == 200, response.text
    assert ean_final == NOVO_EAN


# --- ordem dos locks: nenhum deadlock ---------------------------------------------------------


@pytest.mark.parametrize("metodo", ["DELETE", "PATCH"])
def test_sem_deadlock_contra_escritor_que_trava_listing_antes_do_watch(
    api: TestClient, engine: Engine, monkeypatch: pytest.MonkeyPatch, metodo: str
) -> None:
    """O publicador grava uma leitura (lock no listing) e depois uma publication (quer o watch).

    E a ordem inversa da nossa (watch -> listings). Se a API esperasse o listing, haveria ciclo;
    com NOWAIT ela desiste na hora com 409 e o publicador segue.
    """
    watch_id, listing_id = _preparar(api)
    publicador = _escritor(engine)

    def publicar() -> None:
        publicador.add(_publicacao(watch_id, listing_id))
        publicador.commit()

    concorrente = Concorrente(publicar)
    try:
        _depois_de(monkeypatch, "_travar_relogio", concorrente.soltar)
        publicador.add(_leitura(listing_id))
        publicador.flush()
        corpo = {"ean": NOVO_EAN} if metodo == "PATCH" else None
        response = api.request(metodo, f"/watches/{watch_id}", json=corpo)
        concorrente.terminar()
    finally:
        publicador.close()

    assert concorrente.bloqueado is True  # o publicador esperou o lock do watch: a ordem cruzou
    assert concorrente.erro is None  # nem deadlock nem timeout
    assert response.status_code == 409, response.text
    assert _estado(engine, watch_id, listing_id) == (EAN, True, 1, 1)


def test_patch_que_espera_um_delete_do_mesmo_relogio_responde_404(
    api: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    watch_id, _ = _preparar(api)

    def trocar_ean() -> int:
        with SessionLocal() as s:
            try:
                service.update_watch(s, watch_id, WatchUpdate(ean=NOVO_EAN))
            except CatalogError as exc:
                return exc.status_code
            return 200

    patch = Concorrente(trocar_ean)
    _depois_de(monkeypatch, "_guard_sem_historico", patch.soltar)

    response = api.delete(f"/watches/{watch_id}")
    patch.terminar()

    assert response.status_code == 204, response.text
    assert patch.bloqueado is True
    assert patch.erro is None
    assert patch.resultado == 404


# --- (C) mensagem da violacao -----------------------------------------------------------------


def test_ean_duplicado_gravado_em_paralelo_responde_409_com_o_ean_certo(
    api: TestClient, engine: Engine
) -> None:
    """O indice unico decide a corrida e a mensagem fala do EAN que de fato colidiu."""
    watch_id, listing_id = _preparar(api)
    outro = _escritor(engine)
    outro.add(Watch(**(payload(NOVO_EAN) | {"tamanho_caixa_mm": Decimal("37.0")})))
    outro.flush()  # ainda nao commitado: a checagem previa do service nao o enxerga
    commit = threading.Timer(ESPERA, outro.commit)
    commit.start()
    try:
        response = api.patch(f"/watches/{watch_id}", json={"ean": NOVO_EAN})
    finally:
        commit.join()
        outro.close()
    assert response.status_code == 409, response.text
    assert response.json()["detail"] == f"Ja existe um relogio com o EAN {NOVO_EAN}"
    assert _estado(engine, watch_id, listing_id)[0] == EAN
