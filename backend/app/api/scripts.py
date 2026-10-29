"""CRUD endpoints for pitch scripts (outlines, target WPM, keywords)."""

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, require_email_verified
from app.config.settings import get_settings
from app.db.session import get_db
from app.models.script import Script
from app.models.user import User
from app.schemas.script import ScriptCreate, ScriptPublic, ScriptUpdate
from app.services.billing import assert_script_quota
from app.services.knowledge import schedule_upsert_script_card

router = APIRouter(prefix="/scripts", tags=["scripts"])


async def _get_owned_script(
    script_id: uuid.UUID, user: User, db: AsyncSession
) -> Script:
    script = await db.scalar(
        select(Script).where(Script.id == script_id, Script.user_id == user.id)
    )
    if script is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Script not found")
    return script


@router.get("", response_model=list[ScriptPublic])
async def list_scripts(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[Script]:
    result = await db.scalars(
        select(Script)
        .where(Script.user_id == current_user.id)
        .order_by(Script.title.asc())
    )
    return list(result)


@router.post("", response_model=ScriptPublic, status_code=status.HTTP_201_CREATED)
async def create_script(
    payload: ScriptCreate,
    current_user: User = Depends(require_email_verified),
    db: AsyncSession = Depends(get_db),
) -> Script:
    # Plus-only quota: free members create 3 scripts per month (402 → paywall)
    await assert_script_quota(db, current_user, get_settings())

    script = Script(
        user_id=current_user.id,
        title=payload.title.strip(),
        content_outline=payload.content_outline,
        target_wpm=payload.target_wpm,
        keywords=[k.strip() for k in payload.keywords if k.strip()],
    )
    db.add(script)
    await db.commit()
    await db.refresh(script)
    schedule_upsert_script_card(  # F3: script outline becomes a RAG card
        user_id=current_user.id,
        script_id=script.id,
        title=script.title,
        outline=script.content_outline,
        keywords=script.keywords,
    )
    return script


@router.get("/{script_id}", response_model=ScriptPublic)
async def get_script(
    script_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Script:
    return await _get_owned_script(script_id, current_user, db)


@router.put("/{script_id}", response_model=ScriptPublic)
async def update_script(
    script_id: uuid.UUID,
    payload: ScriptUpdate,
    current_user: User = Depends(require_email_verified),
    db: AsyncSession = Depends(get_db),
) -> Script:
    script = await _get_owned_script(script_id, current_user, db)
    if payload.title is not None:
        script.title = payload.title.strip()
    if payload.content_outline is not None:
        script.content_outline = payload.content_outline
    if payload.target_wpm is not None:
        script.target_wpm = payload.target_wpm
    if payload.keywords is not None:
        script.keywords = [k.strip() for k in payload.keywords if k.strip()]
    await db.commit()
    await db.refresh(script)
    schedule_upsert_script_card(  # F3: keep the RAG card in sync
        user_id=current_user.id,
        script_id=script.id,
        title=script.title,
        outline=script.content_outline,
        keywords=script.keywords,
    )
    return script


@router.delete("/{script_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_script(
    script_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    script = await _get_owned_script(script_id, current_user, db)
    await db.delete(script)
    await db.commit()
