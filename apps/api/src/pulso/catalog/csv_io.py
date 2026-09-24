import csv
import io
from collections.abc import Iterator
from typing import Any

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from pulso.catalog.errors import ConflictError, CsvInvalidoError, LinhaCsv
from pulso.catalog.schemas import ImportResult, ListingRow, WatchCreate
from pulso.models import Listing, Watch

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


def _abrir(
    conteudo: bytes, permitidas: tuple[str, ...], obrigatorias: tuple[str, ...]
) -> tuple[csv.DictReader[str], set[str]]:
    """Le e valida o cabecalho; CsvInvalidoError se faltar, sobrar ou repetir coluna."""
    reader = csv.DictReader(io.StringIO(_decodificar(conteudo), newline=""), restkey="__extra__")
    cabecalho = [c.strip() for c in reader.fieldnames or []]
    problemas = [
        *(f"coluna obrigatoria ausente: {c}" for c in obrigatorias if c not in cabecalho),
        *(f"coluna desconhecida: {c}" for c in cabecalho if c not in permitidas),
        *(f"coluna repetida: {c}" for c in set(cabecalho) if cabecalho.count(c) > 1),
    ]
    if problemas:
        raise CsvInvalidoError([LinhaCsv(1, p) for p in sorted(problemas)])
    reader.fieldnames = cabecalho
    return reader, set(cabecalho)


def _linhas(
    reader: csv.DictReader[str],
) -> Iterator[tuple[int, dict[str, str] | None, str | None]]:
    """(linha, celulas, None) se bem formada; (linha, None, motivo) se malformada."""
    for row in reader:
        linha = reader.line_num
        if "__extra__" in row:
            yield linha, None, "mais celulas que colunas no cabecalho"
        elif any(v is None for v in row.values()):
            yield linha, None, "menos celulas que colunas no cabecalho"
        else:
            yield linha, row, None


def import_csv(session: Session, conteudo: bytes) -> ImportResult:
    """Upsert por EAN, tudo ou nada: qualquer linha invalida aborta sem gravar."""
    reader, colunas = _abrir(conteudo, COLUNAS, OBRIGATORIAS)

    erros: list[LinhaCsv] = []
    validas: list[WatchCreate] = []
    eans_vistos: dict[str, int] = {}
    for linha, row, motivo in _linhas(reader):
        if row is None:
            erros.append(LinhaCsv(linha, motivo or ""))
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


COLUNAS_ANUNCIO = ("ean", "marketplace", "marketplace_item_id", "url", "ativo")
OBRIGATORIAS_ANUNCIO = ("ean", "marketplace", "marketplace_item_id", "url")
_CHAVE = ("ean", "marketplace", "marketplace_item_id")


def export_listings_csv(session: Session) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(COLUNAS_ANUNCIO)
    linhas = session.execute(
        select(Watch.ean, Listing)
        .join(Watch, Watch.id == Listing.watch_id)
        .order_by(Listing.marketplace, Listing.marketplace_item_id)
    )
    for ean, x in linhas:
        writer.writerow([ean, x.marketplace, x.marketplace_item_id, x.url, _bool(x.ativo)])
    return buffer.getvalue()


def _parse_listing_row(row: dict[str, str]) -> ListingRow:
    dados: dict[str, Any] = {}
    for coluna, valor in row.items():
        celula = valor.strip()
        if coluna == "ativo":
            if celula.lower() in _VERDADEIRO:
                dados[coluna] = True
            elif celula.lower() in _FALSO:
                dados[coluna] = False
            else:
                raise ValueError(f"{coluna}: use true/false (recebido {celula!r})")
        else:
            dados[coluna] = celula
    return ListingRow.model_validate(dados)


def import_listings_csv(session: Session, conteudo: bytes) -> ImportResult:
    """Upsert por (marketplace, marketplace_item_id), tudo ou nada.

    O relogio dono vem do EAN da linha e precisa existir: nunca cria relogio.
    """
    reader, colunas = _abrir(conteudo, COLUNAS_ANUNCIO, OBRIGATORIAS_ANUNCIO)

    erros: list[LinhaCsv] = []
    validas: list[tuple[int, ListingRow]] = []
    chaves_vistas: dict[tuple[str, str], int] = {}
    for linha, row, motivo in _linhas(reader):
        if row is None:
            erros.append(LinhaCsv(linha, motivo or ""))
            continue
        try:
            item = _parse_listing_row(row)
        except (ValidationError, ValueError) as exc:
            erros.append(LinhaCsv(linha, _motivo(exc)))
            continue
        chave = (item.marketplace, item.marketplace_item_id)
        if chave in chaves_vistas:
            erros.append(
                LinhaCsv(
                    linha,
                    f"anuncio {chave[1]} em {chave[0]} repetido "
                    f"(ja na linha {chaves_vistas[chave]})",
                )
            )
            continue
        chaves_vistas[chave] = linha
        validas.append((linha, item))
    if erros:
        raise CsvInvalidoError(erros)
    if not validas:
        raise CsvInvalidoError([LinhaCsv(1, "o arquivo nao tem nenhuma linha de dados")])

    # a validacao que depende do banco tambem roda antes de qualquer escrita
    watches = {
        w.ean: w
        for w in session.scalars(select(Watch).where(Watch.ean.in_({i.ean for _, i in validas})))
    }
    existentes = {
        (x.marketplace, x.marketplace_item_id): x
        for x in session.scalars(
            select(Listing).where(Listing.marketplace_item_id.in_([c[1] for c in chaves_vistas]))
        )
    }
    for linha, item in validas:
        watch = watches.get(item.ean)
        if watch is None:
            erros.append(LinhaCsv(linha, f"EAN {item.ean} nao existe no catalogo"))
            continue
        atual = existentes.get((item.marketplace, item.marketplace_item_id))
        if atual is not None and atual.watch_id != watch.id:
            erros.append(
                LinhaCsv(
                    linha,
                    f"anuncio {item.marketplace_item_id} em {item.marketplace} ja esta vinculado "
                    "a outro relogio; o import nao move anuncios entre relogios",
                )
            )
    if erros:
        raise CsvInvalidoError(erros)

    criados = atualizados = inalterados = 0
    try:
        for _, item in validas:
            atual = existentes.get((item.marketplace, item.marketplace_item_id))
            if atual is None:
                session.add(
                    Listing(watch_id=watches[item.ean].id, **item.model_dump(exclude={"ean"}))
                )
                criados += 1
                continue
            # so toca colunas presentes no arquivo e so grava se algo mudou
            mudou = False
            for campo in colunas - set(_CHAVE):
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
            "Conflito ao gravar (anuncio criado ou relogio excluido por outra requisicao); "
            "nada foi gravado, reenvie o arquivo"
        ) from None
    return ImportResult(criados=criados, atualizados=atualizados, inalterados=inalterados)
