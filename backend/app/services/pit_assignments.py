from __future__ import annotations

from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.db import models


def release_pit_assignments(db: Session, workspace_id: int, member_id: int) -> None:
    # A removed member's teams go back to unassigned, so no scout is left "on the way" to a pit.
    db.execute(
        delete(models.PitAssignment).where(
            models.PitAssignment.workspace_id == workspace_id,
            models.PitAssignment.member_id == member_id,
        )
    )
