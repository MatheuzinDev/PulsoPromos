from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from fastapi.testclient import TestClient
from helpers import criar
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from pulso.models import Listing, PriceReading, Publication, Watch


def _listing(client: TestClient, watch_id: int, item: str = "MLB1") -> int:
    response = client.post(
        f"/watches/{watch_id}/listings",
        json={"marketplace": "mercado_livre", "marketplace_item_id": item, "url": "http://x/1"},
    )
    assert response.status_code == 201, response.text
    return int(response.json()["id"])


def _leitura(session: Session, listing_id: int) -> None:
    session.add(
        PriceReading(
            listing_id=listing_id,
            coletado_em=datetime.now(UTC),
            preco_vista=Decimal("100.00"),
            em_estoque=True,
            vendedor_raw={},
            origem="teste",
        )
    )
    session.flush()


def _publicacao(session: Session, watch_id: int, listing_id: int) -> None:
    session.add(
        Publication(
            watch_id=watch_id,
            listing_id=listing_id,
            publicado_em=datetime.now(UTC),
            preco_vista_publicado=Decimal("100.00"),
            preco_efetivo=Decimal("90.00"),
            loja="Loja",
            link_publicado="http://x/1",
        )
    )
    session.flush()


def _contagens(session: Session) -> tuple[int, int, int, int]:
    def n(model: Any) -> int:
        return session.scalar(select(func.count()).select_from(model)) or 0

    return n(Watch), n(Listing), n(PriceReading), n(Publication)


def test_patch_ean_em_relogio_sem_historico(client: TestClient) -> None:
    watch = criar(client)
    response = client.patch(f"/watches/{watch['id']}", json={"ean": "7890000000017"})
    assert response.status_code == 200
    assert response.json()["ean"] == "7890000000017"
    assert client.get(f"/watches/{watch['id']}").json()["ean"] == "7890000000017"


def test_patch_ean_para_ean_de_outro_relogio_retorna_409(client: TestClient) -> None:
    a = criar(client, "7890000000024")
    criar(client, "7890000000031")
    response = client.patch(f"/watches/{a['id']}", json={"ean": "7890000000031"})
    assert response.status_code == 409
    assert "7890000000031" in response.json()["detail"]
    assert client.get(f"/watches/{a['id']}").json()["ean"] == "7890000000024"


def test_patch_ean_nulo_ou_invalido_retorna_422(client: TestClient) -> None:
    watch = criar(client)
    assert client.patch(f"/watches/{watch['id']}", json={"ean": None}).status_code == 422
    assert client.patch(f"/watches/{watch['id']}", json={"ean": "abc"}).status_code == 422


def test_patch_ean_bloqueado_com_price_reading(client: TestClient, db_session: Session) -> None:
    watch = criar(client)
    _leitura(db_session, _listing(client, watch["id"]))
    antes = _contagens(db_session)
    response = client.patch(f"/watches/{watch['id']}", json={"ean": "7890000000048"})
    assert response.status_code == 409
    detail = response.json()["detail"]
    assert "Leituras de preco: 1" in detail
    assert "publicacoes: 0" in detail
    assert _contagens(db_session) == antes
    assert client.get(f"/watches/{watch['id']}").json()["ean"] == watch["ean"]


def test_patch_ean_bloqueado_com_publication(client: TestClient, db_session: Session) -> None:
    watch = criar(client)
    _publicacao(db_session, watch["id"], _listing(client, watch["id"]))
    antes = _contagens(db_session)
    response = client.patch(f"/watches/{watch['id']}", json={"ean": "7890000000048"})
    assert response.status_code == 409
    detail = response.json()["detail"]
    assert "Leituras de preco: 0" in detail
    assert "publicacoes: 1" in detail
    assert _contagens(db_session) == antes
    assert client.get(f"/watches/{watch['id']}").json()["ean"] == watch["ean"]


def test_patch_de_outros_campos_segue_livre_com_historico(
    client: TestClient, db_session: Session
) -> None:
    watch = criar(client)
    _leitura(db_session, _listing(client, watch["id"]))
    response = client.patch(f"/watches/{watch['id']}", json={"marca": "Orient"})
    assert response.status_code == 200
    assert response.json()["marca"] == "Orient"


def test_delete_sem_historico_remove_relogio_e_listings(
    client: TestClient, db_session: Session
) -> None:
    watch = criar(client)
    outro = criar(client, "7890000000055")
    _listing(client, watch["id"], "MLB1")
    _listing(client, watch["id"], "MLB2")
    _listing(client, outro["id"], "MLB3")
    assert _contagens(db_session) == (2, 3, 0, 0)
    response = client.delete(f"/watches/{watch['id']}")
    assert response.status_code == 204
    assert client.get(f"/watches/{watch['id']}").status_code == 404
    assert _contagens(db_session) == (1, 1, 0, 0)
    assert client.get(f"/watches/{outro['id']}").status_code == 200


def test_delete_relogio_inexistente_retorna_404(client: TestClient) -> None:
    assert client.delete("/watches/999999").status_code == 404


def test_delete_bloqueado_com_price_reading(client: TestClient, db_session: Session) -> None:
    watch = criar(client)
    _leitura(db_session, _listing(client, watch["id"]))
    antes = _contagens(db_session)
    response = client.delete(f"/watches/{watch['id']}")
    assert response.status_code == 409
    assert "Leituras de preco: 1" in response.json()["detail"]
    assert _contagens(db_session) == antes == (1, 1, 1, 0)
    assert client.get(f"/watches/{watch['id']}").status_code == 200


def test_delete_bloqueado_com_publication(client: TestClient, db_session: Session) -> None:
    watch = criar(client)
    _publicacao(db_session, watch["id"], _listing(client, watch["id"]))
    antes = _contagens(db_session)
    response = client.delete(f"/watches/{watch['id']}")
    assert response.status_code == 409
    assert "publicacoes: 1" in response.json()["detail"]
    assert _contagens(db_session) == antes == (1, 1, 0, 1)
    assert client.get(f"/watches/{watch['id']}").status_code == 200
