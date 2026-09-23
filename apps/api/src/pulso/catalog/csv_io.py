import csv
import io
from typing import Any

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from pulso.catalog.errors import ConflictError, CsvInvalidoError, LinhaCsv
from pulso.catalog.schemas import ImportResult, WatchCreate
from pulso.models import Watch

COLUNAS = (
    "ean",
    "marca",
    "referencia_fabricante",
    "tipo_movimento",
    "tamanho_caixa_mm",
    "vigilancia_ativa",
    "preco_alvo",
    "exige_loja_oficial",
    "exige_reputacao_minima",
)
OBRIGATORIAS = ("ean", "marca", "referencia_fabricante", "tipo_movimento", "tamanho_caixa_mm")
BOOLEANAS = ("vigilancia_ativa", "exige_loja_oficial", "exige_reputacao_minima")
_VERDADEIRO = {"true", "1"}
_FALSO = {"false", "0"}


def _bool(valor: bool) -> str:
    return "true" if valor else "false"


def export_csv(session: Session) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(COLUNAS)
    for w in session.scalars(select(Watch).order_by(Watch.ean)):
        writer.writerow(
            [
                w.ean,
                w.marca,
                w.referencia_fabricante,
                w.tipo_movimento,
                f"{w.tamanho_caixa_mm:.1f}",
                _bool(w.vigilancia_ativa),
                "" if w.preco_alvo is None else f"{w.preco_alvo:.2f}",
                _bool(w.exige_loja_oficial),
                _bool(w.exige_reputacao_minima),
            ]
        )
    return buffer.getvalue()


def _parse_row(row: dict[str, str], colunas: set[str]) -> WatchCreate:
    """Converte as celulas em texto para o schema; ValueError/ValidationError se invalida."""
    dados: dict[str, Any] = {}
    for coluna in colunas:
        celula = row[coluna].strip()
        if coluna in BOOLEANAS:
            if celula.lower() in _VERDADEIRO:
                dados[coluna] = True
            elif celula.lower() in _FALSO:
                dados[coluna] = False
            else:
                raise ValueError(f"{coluna}: use true/false (recebido {celula!r})")
        elif coluna == "preco_alvo":
            dados[coluna] = celula or None
        else:
            dados[coluna] = celula
    return WatchCreate.model_validate(dados)


def _motivo(exc: ValidationError | ValueError) -> str:
    if isinstance(exc, ValidationError):
        return "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors())
    return str(exc)


def _decodificar(conteudo: bytes) -> str:
    try:
        return conteudo.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise CsvInvalidoError([LinhaCsv(1, "o arquivo precisa estar em UTF-8")]) from None


def import_csv(session: Session, conteudo: bytes) -> ImportResult:
    """Upsert por EAN, tudo ou nada: qualquer linha invalida aborta sem gravar."""
    reader = csv.DictReader(io.StringIO(_decodificar(conteudo), newline=""), restkey="__extra__")
    cabecalho = [c.strip() for c in reader.fieldnames or []]
    problemas = [
        *(f"coluna obrigatoria ausente: {c}" for c in OBRIGATORIAS if c not in cabecalho),
        *(f"coluna desconhecida: {c}" for c in cabecalho if c not in COLUNAS),
        *(f"coluna repetida: {c}" for c in set(cabecalho) if cabecalho.count(c) > 1),
    ]
    if problemas:
        raise CsvInvalidoError([LinhaCsv(1, p) for p in sorted(problemas)])
    reader.fieldnames = cabecalho
    colunas = set(cabecalho)

    erros: list[LinhaCsv] = []
    validas: list[WatchCreate] = []
    eans_vistos: dict[str, int] = {}
    for row in reader:
        linha = reader.line_num
        if "__extra__" in row:
            erros.append(LinhaCsv(linha, "mais celulas que colunas no cabecalho"))
            continue
        if any(v is None for v in row.values()):
            erros.append(LinhaCsv(linha, "menos celulas que colunas no cabecalho"))
            continue
        try:
            item = _parse_row(row, colunas)
        except (ValidationError, ValueError) as exc:
            erros.append(LinhaCsv(linha, _motivo(exc)))
            continue
        if item.ean in eans_vistos:
            primeira = eans_vistos[item.ean]
            erros.append(LinhaCsv(linha, f"EAN {item.ean} repetido (ja na linha {primeira})"))
            continue
        eans_vistos[item.ean] = linha
        validas.append(item)
    if erros:
        raise CsvInvalidoError(erros)
    if not validas:
        raise CsvInvalidoError([LinhaCsv(1, "o arquivo nao tem nenhuma linha de dados")])

    existentes = {
        w.ean: w for w in session.scalars(select(Watch).where(Watch.ean.in_(eans_vistos)))
    }
    criados = atualizados = inalterados = 0
    try:
        for item in validas:
            atual = existentes.get(item.ean)
            if atual is None:
                session.add(Watch(**item.model_dump()))
                criados += 1
                continue
            # so toca colunas presentes no arquivo e so grava se algo mudou
            mudou = False
            for campo in colunas - {"ean"}:
                novo = getattr(item, campo)
                if getattr(atual, campo) != novo:
                    setattr(atual, campo, novo)
                    mudou = True
            if mudou:
                atualizados += 1
            else:
                inalterados += 1
        session.commit()
    except IntegrityError:
        session.rollback()
        raise ConflictError(
            "Conflito ao gravar (EAN criado por outra requisicao); "
            "nada foi gravado, reenvie o arquivo"
        ) from None
    return ImportResult(criados=criados, atualizados=atualizados, inalterados=inalterados)
