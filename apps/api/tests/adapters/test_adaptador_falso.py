from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from pulso.adapters import nucleo
from pulso.adapters.base import AdaptadorMarketplace
from pulso.adapters.dto import Falha, Leitura, TipoFalha
from pulso.adapters.fake import AdaptadorFalso
from pulso.adapters.permissoes import (
    AVISO_AFILIADO,
    PERMISSOES_PADRAO,
    Permissoes,
    RegistroPermissoes,
)

FIXTURES = Path(__file__).parent / "fixtures"
AGORA = datetime(2026, 9, 26, 12, 0, tzinfo=UTC)


class Explosivo:
    marketplace = "amazon"
    nome = "explosivo"

    def consultar(self, item_id: str) -> Leitura:
        raise RuntimeError("boom")


def falso(marketplace: str = "shopee") -> AdaptadorFalso:
    return AdaptadorFalso(marketplace, FIXTURES, agora=AGORA)


def test_implementa_interface_e_devolve_dto() -> None:
    adaptador = falso()
    assert isinstance(adaptador, AdaptadorMarketplace)
    r = adaptador.consultar("ok1")
    assert isinstance(r, Leitura)
    assert r.preco_vista == Decimal("199.90")
    assert r.parcelas == 10 and r.cupom_aplicavel == "PROMO10"
    assert r.em_estoque is True


def test_origem_e_instante_da_coleta() -> None:
    r = falso().consultar("ok1")
    assert isinstance(r, Leitura)
    assert r.origem == "fake-shopee:ok1.json"
    assert r.coletado_em == AGORA


def test_dinheiro_e_decimal_nunca_float() -> None:
    r = falso().consultar("ok1")
    assert isinstance(r, Leitura)
    for valor in (r.preco_vista, r.preco_parcelado_total, r.frete):
        assert isinstance(valor, Decimal)
    with pytest.raises(ValidationError):
        Leitura(
            preco_vista=19.9,  # type: ignore[arg-type]
            em_estoque=True,
            vendedor={},
            origem="x",
            coletado_em=AGORA,
        )


def test_vendedor_e_payload_cru() -> None:
    r = falso().consultar("ok1")
    assert isinstance(r, Leitura)
    assert r.vendedor == {"campo_qualquer": {"a": 1}, "nivel": "x"}


def test_nucleo_le_permissoes_sem_conhecer_marketplace() -> None:
    registro = RegistroPermissoes()
    registro.registrar(
        "shopee", Permissoes(alerta_preco=True, canais_permitidos=frozenset({"telegram"}))
    )
    p = nucleo.permissoes_de(falso("shopee"), registro)
    assert p.alerta_preco and p.canais_permitidos == {"telegram"}
    assert AVISO_AFILIADO in p.avisos_exigidos
    # sem entrada registrada: padrao restritivo
    assert nucleo.permissoes_de(falso("amazon"), registro) == PERMISSOES_PADRAO
    assert not PERMISSOES_PADRAO.guarda_historico_precos and not PERMISSOES_PADRAO.alerta_preco


@pytest.mark.parametrize("item", ["erro", "inexistente", "corrompido"])
def test_falha_vira_resultado_nao_excecao(item: str) -> None:
    r = falso().consultar(item)
    assert isinstance(r, Falha) and r.tipo == TipoFalha.ERRO
    assert r.origem == f"fake-shopee:{item}.json"


def test_dado_desatualizado_e_marcado() -> None:
    r = falso().consultar("velho")
    assert isinstance(r, Falha) and r.tipo == TipoFalha.DESATUALIZADO
    assert r.ultima_leitura is not None and r.ultima_leitura.preco_vista == Decimal("150.00")


def test_excecao_inesperada_nao_derruba_a_coleta() -> None:
    resultados = nucleo.coletar([(Explosivo(), "x"), (falso(), "ok1")])
    assert isinstance(resultados[0], Falha) and "boom" in resultados[0].mensagem
    assert isinstance(resultados[1], Leitura)


def test_segundo_marketplace_nao_exige_mudanca_no_nucleo() -> None:
    """RNF16: novo adaptador + suas permissoes; nucleo, DTO e interface intactos."""
    registro = RegistroPermissoes()
    registro.registrar("shopee", Permissoes(alerta_preco=True))
    registro.registrar("aliexpress", Permissoes(guarda_historico_precos=True))
    a, b = falso("shopee"), AdaptadorFalso("aliexpress", FIXTURES, nome="fake-ali", agora=AGORA)

    resultados = nucleo.coletar([(a, "ok1"), (b, "outro")])

    assert all(isinstance(r, Leitura) for r in resultados)
    assert [r.origem for r in resultados if isinstance(r, Leitura)] == [
        "fake-shopee:ok1.json",
        "fake-ali:outro.json",
    ]
    assert nucleo.permissoes_de(a, registro).alerta_preco
    assert nucleo.permissoes_de(b, registro).guarda_historico_precos
    assert not nucleo.permissoes_de(b, registro).alerta_preco
