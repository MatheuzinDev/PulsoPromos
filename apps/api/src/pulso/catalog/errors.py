from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse


class CatalogError(Exception):
    status_code = 400

    def __init__(self, mensagem: str) -> None:
        super().__init__(mensagem)
        self.mensagem = mensagem


class NotFoundError(CatalogError):
    status_code = 404


class ConflictError(CatalogError):
    status_code = 409


class LinhaCsv:
    """Erro de uma linha do CSV (a linha 1 e o cabecalho)."""

    def __init__(self, linha: int, motivo: str) -> None:
        self.linha = linha
        self.motivo = motivo

    def as_dict(self) -> dict[str, object]:
        return {"linha": self.linha, "motivo": self.motivo}


class CsvInvalidoError(CatalogError):
    status_code = 422

    def __init__(self, erros: list[LinhaCsv]) -> None:
        super().__init__("CSV rejeitado: nenhum registro foi gravado")
        self.erros = erros


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(CatalogError)
    async def _catalog_error(_: Request, exc: CatalogError) -> JSONResponse:
        body: dict[str, object] = {"detail": exc.mensagem}
        if isinstance(exc, CsvInvalidoError):
            body["erros"] = [e.as_dict() for e in exc.erros]
        return JSONResponse(status_code=exc.status_code, content=body)
