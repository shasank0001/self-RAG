from uuid import UUID

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.core.pipeline_config import get_pipeline_config
from app.db.session import get_db_session
from app.models.bin import Bin
from app.models.user import User
from app.services.authorization import require_bin_owner

router = APIRouter(prefix="/bins", tags=["bins"])


class BinCreateRequest(BaseModel):
    title: str
    description: str | None = None
    vector_namespace: str
    user_id: UUID | None = None


class BinResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    user_id: UUID
    title: str
    description: str | None
    vector_namespace: str


@router.get(
    "",
    response_model=list[BinResponse],
    summary="List bins",
    description="Lists bins owned by the authenticated user.",
)
async def list_bins(
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
) -> list[BinResponse]:
    result = await session.execute(select(Bin).where(Bin.user_id == current_user.id).order_by(Bin.created_at.desc()))
    bins = result.scalars().all()
    return [BinResponse.model_validate(item) for item in bins]


@router.post(
    "",
    response_model=BinResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create bin",
    description="Creates a new bin owned by the authenticated user.",
)
async def create_bin(
    payload: BinCreateRequest,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
) -> BinResponse:
    pipeline_config = get_pipeline_config()
    bin_record = Bin(
        user_id=current_user.id,
        title=payload.title,
        description=payload.description,
        vector_namespace=payload.vector_namespace,
        embedding_provider=pipeline_config.embedding.active_provider,
        embedding_model=pipeline_config.embedding.active_model,
        embedding_dimensions=pipeline_config.embedding.dimensions,
    )
    session.add(bin_record)
    await session.commit()
    await session.refresh(bin_record)
    return BinResponse.model_validate(bin_record)


@router.get(
    "/{bin_id}",
    response_model=BinResponse,
    summary="Get bin",
    description="Returns a bin owned by the authenticated user.",
)
async def get_bin(
    bin_id: UUID,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
) -> BinResponse:
    bin_record = await require_bin_owner(session, bin_id=bin_id, owner_user_id=current_user.id)
    return BinResponse.model_validate(bin_record)


@router.delete(
    "/{bin_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete bin",
    description="Deletes a bin owned by the authenticated user.",
)
async def delete_bin(
    bin_id: UUID,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
) -> None:
    bin_record = await require_bin_owner(session, bin_id=bin_id, owner_user_id=current_user.id)
    await session.delete(bin_record)
    await session.commit()
