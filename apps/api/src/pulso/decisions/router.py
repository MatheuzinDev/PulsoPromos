from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from pulso.db import get_session
from pulso.decisions import service
from pulso.decisions.schemas import (
    AprovarIn,
    AprovarOut,
    CandidateOut,
    DescartarIn,
    PublicationOut,
)

router = APIRouter(prefix="/candidates", tags=["candidatos"])

SessionDep = Annotated[Session, Depends(get_session)]


@router.get("/{candidate_id}", response_model=CandidateOut)
def obter_candidato(candidate_id: int, session: SessionDep) -> CandidateOut:
    return CandidateOut.model_validate(service.get_candidate(session, candidate_id))


@router.post("/{candidate_id}/aprovar", response_model=AprovarOut)
def aprovar_candidato(candidate_id: int, data: AprovarIn, session: SessionDep) -> AprovarOut:
    candidate, publication = service.aprovar(session, candidate_id, data)
    return AprovarOut(
        candidate=CandidateOut.model_validate(candidate),
        publication=PublicationOut.model_validate(publication),
    )


@router.post("/{candidate_id}/descartar", response_model=CandidateOut)
def descartar_candidato(candidate_id: int, data: DescartarIn, session: SessionDep) -> CandidateOut:
    return CandidateOut.model_validate(service.descartar(session, candidate_id, data))
