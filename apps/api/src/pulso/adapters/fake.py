"""Adaptador falso: le respostas gravadas de fixtures, sem rede (RNF10).

Fixture `<dir>/<item_id>.json` no formato do dominio:
  {"status": "ok", ...campos de Leitura sem origem/coletado_em...}
  {"status": "erro" | "desatualizado", "mensagem": "...", "ultima_leitura": {...}}
"""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from pulso.adapters.dto import Falha, Leitura, Resultado, TipoFalha


class AdaptadorFalso:
    def __init__(
        self,
        marketplace: str,
        diretorio: Path,
        nome: str | None = None,
        agora: datetime | None = None,
    ) -> None:
        self.marketplace = marketplace
        self.nome = nome or f"fake-{marketplace}"
        self._diretorio = diretorio
        self._agora = agora

    def _instante(self) -> datetime:
        return self._agora or datetime.now(UTC)

    def _origem(self, item_id: str) -> str:
        return f"{self.nome}:{item_id}.json"

    def _leitura(self, dados: dict[str, Any], item_id: str) -> Leitura:
        return Leitura(**dados, origem=self._origem(item_id), coletado_em=self._instante())

    def consultar(self, item_id: str) -> Resultado:
        origem = self._origem(item_id)
        try:
            # o nome do arquivo nao pode escapar do diretorio de fixtures
            arquivo = self._diretorio / f"{Path(item_id).name}.json"
            dados = json.loads(arquivo.read_text(encoding="utf-8"))
            status = dados.pop("status", "ok")
            if status == "ok":
                return self._leitura(dados, item_id)
            ultima = dados.get("ultima_leitura")
            return Falha(
                tipo=TipoFalha(status),
                mensagem=dados.get("mensagem", status),
                origem=origem,
                ocorrida_em=self._instante(),
                ultima_leitura=self._leitura(ultima, item_id) if ultima else None,
            )
        except (OSError, ValueError, ValidationError, TypeError) as exc:
            return Falha(
                tipo=TipoFalha.ERRO,
                mensagem=f"fixture invalida ou ausente: {exc}",
                origem=origem,
                ocorrida_em=self._instante(),
            )
