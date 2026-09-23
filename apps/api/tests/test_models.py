from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from pulso.models import Listing, PriceReading, Publication, Watch


def make_watch(ean: str = "7891234567895") -> Watch:
    return Watch(
        marca="Seiko",
        referencia_fabricante="SNK809",
        ean=ean,
        tipo_movimento="automatico",
        tamanho_caixa_mm=Decimal("37.0"),
    )


def test_criar_watch_listing_price_reading_publication_e_ler_de_volta(
    db_session: Session,
) -> None:
    watch = make_watch()
    db_session.add(watch)
    db_session.flush()

    listing = Listing(
        watch_id=watch.id,
        marketplace="shopee",
        marketplace_item_id="item-full-flow",
        url="https://shopee.com.br/item-full-flow",
        ativo=True,
    )
    db_session.add(listing)
    db_session.flush()

    coletado_em = datetime.now(UTC)
    reading = PriceReading(
        listing_id=listing.id,
        coletado_em=coletado_em,
        preco_vista=Decimal("899.90"),
        em_estoque=True,
        vendedor_raw={"nome": "Loja Exemplo", "nota": 4.8},
        origem="shopee-adapter-v1",
    )
    db_session.add(reading)
    db_session.flush()

    publication = Publication(
        watch_id=watch.id,
        listing_id=listing.id,
        publicado_em=coletado_em,
        preco_vista_publicado=Decimal("899.90"),
        preco_efetivo=Decimal("849.90"),
        loja="Shopee",
        link_publicado="https://shopee.com.br/item-full-flow?aff=1",
    )
    db_session.add(publication)
    db_session.commit()

    db_session.expire_all()

    loaded_watch = db_session.get(Watch, watch.id)
    assert loaded_watch is not None
    assert loaded_watch.ean == "7891234567895"

    loaded_listing = db_session.get(Listing, listing.id)
    assert loaded_listing is not None
    assert loaded_listing.watch_id == watch.id

    loaded_reading = db_session.get(PriceReading, reading.id)
    assert loaded_reading is not None
    assert loaded_reading.vendedor_raw == {"nome": "Loja Exemplo", "nota": 4.8}

    loaded_publication = db_session.get(Publication, publication.id)
    assert loaded_publication is not None
    assert loaded_publication.preco_efetivo == Decimal("849.90")


def test_ean_duplicado_e_rejeitado_pelo_banco(db_session: Session) -> None:
    db_session.add(make_watch(ean="7899999999991"))
    db_session.flush()

    db_session.add(make_watch(ean="7899999999991"))
    with pytest.raises(IntegrityError):
        db_session.flush()
    db_session.rollback()


def test_preco_gravado_volta_como_decimal_com_duas_casas(db_session: Session) -> None:
    watch = make_watch(ean="7891111111116")
    db_session.add(watch)
    db_session.flush()

    listing = Listing(
        watch_id=watch.id,
        marketplace="mercado_livre",
        marketplace_item_id="item-decimal",
        url="https://mercadolivre.com.br/item-decimal",
        ativo=True,
    )
    db_session.add(listing)
    db_session.flush()

    reading = PriceReading(
        listing_id=listing.id,
        coletado_em=datetime.now(UTC),
        preco_vista=Decimal("199.9"),
        em_estoque=True,
        vendedor_raw={},
        origem="ml-adapter-v1",
    )
    db_session.add(reading)
    db_session.commit()
    db_session.expire_all()

    loaded = db_session.get(PriceReading, reading.id)
    assert loaded is not None
    assert isinstance(loaded.preco_vista, Decimal)
    assert loaded.preco_vista == Decimal("199.90")


def test_unique_listing_coletado_em_rejeita_leitura_duplicada(db_session: Session) -> None:
    watch = make_watch(ean="7892222222220")
    db_session.add(watch)
    db_session.flush()

    listing = Listing(
        watch_id=watch.id,
        marketplace="aliexpress",
        marketplace_item_id="item-unique",
        url="https://aliexpress.com/item-unique",
        ativo=True,
    )
    db_session.add(listing)
    db_session.flush()

    coletado_em = datetime.now(UTC)
    db_session.add(
        PriceReading(
            listing_id=listing.id,
            coletado_em=coletado_em,
            preco_vista=Decimal("10.00"),
            em_estoque=True,
            vendedor_raw={},
            origem="aliexpress-adapter-v1",
        )
    )
    db_session.flush()

    db_session.add(
        PriceReading(
            listing_id=listing.id,
            coletado_em=coletado_em,
            preco_vista=Decimal("10.00"),
            em_estoque=True,
            vendedor_raw={},
            origem="aliexpress-adapter-v1",
        )
    )
    with pytest.raises(IntegrityError):
        db_session.flush()
    db_session.rollback()


def test_server_defaults_aplicados_no_nivel_do_banco(db_session: Session) -> None:
    watch = make_watch(ean="7893333333337")
    db_session.add(watch)
    db_session.commit()

    db_session.execute(
        text(
            """
            INSERT INTO listing (watch_id, marketplace, marketplace_item_id, url)
            VALUES (:watch_id, :marketplace, :marketplace_item_id, :url)
            """
        ),
        {
            "watch_id": watch.id,
            "marketplace": "shopee",
            "marketplace_item_id": "item-sql-insert",
            "url": "https://shopee.com.br/item-sql",
        },
    )
    db_session.commit()

    listing = db_session.query(Listing).filter_by(marketplace_item_id="item-sql-insert").one()
    assert listing.ativo is True

    coletado_em = datetime.now(UTC)
    db_session.execute(
        text(
            """
            INSERT INTO price_reading
            (listing_id, coletado_em, preco_vista, em_estoque, vendedor_raw, origem)
            VALUES (:listing_id, :coletado_em, :preco_vista, :em_estoque, :vendedor_raw, :origem)
            """
        ),
        {
            "listing_id": listing.id,
            "coletado_em": coletado_em,
            "preco_vista": Decimal("100.00"),
            "em_estoque": True,
            "vendedor_raw": '{"loja": "Teste"}',
            "origem": "test-adapter",
        },
    )
    db_session.commit()

    reading = (
        db_session.query(PriceReading)
        .filter_by(listing_id=listing.id)
        .one()
    )
    assert reading.moeda == "BRL"
    assert reading.desatualizado is False

    db_session.execute(
        text(
            """
            INSERT INTO watch (marca, referencia_fabricante, ean, tipo_movimento, tamanho_caixa_mm)
            VALUES (:marca, :referencia_fabricante, :ean, :tipo_movimento, :tamanho_caixa_mm)
            """
        ),
        {
            "marca": "Citizen",
            "referencia_fabricante": "NH8350",
            "ean": "7894444444444",
            "tipo_movimento": "quartzo",
            "tamanho_caixa_mm": Decimal("42.0"),
        },
    )
    db_session.commit()

    watch2 = db_session.query(Watch).filter_by(ean="7894444444444").one()
    assert watch2.vigilancia_ativa is True
    assert watch2.exige_loja_oficial is False
    assert watch2.exige_reputacao_minima is False
