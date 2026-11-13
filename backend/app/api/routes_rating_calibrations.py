from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.security import require_admin_access
from app.db import models
from app.db.session import get_db
from app.services.ratings import calibrations

router = APIRouter(prefix="/admin/rating", tags=["rating settings"])


class SeasonAction(BaseModel):
    season: int = Field(ge=1992, le=2100)
    frozen: bool = True


@router.get("/calibrations")
def list_calibrations(request: Request, season: int, db: Session = Depends(get_db)):
    require_admin_access(request, "Rating calibrations")
    rows = db.query(models.RatingCalibration).filter_by(season=season).order_by(models.RatingCalibration.version.desc()).all()
    bundle = calibrations.active_bundle(db, season)
    return {"season": season, "bundle_version": bundle.bundle_version if bundle else 0,
            "active_id": bundle.calibration_id if bundle else None, "frozen": bundle.frozen if bundle else False,
            "versions": [{"id": r.id, "recipe": r.recipe, "version": r.version, "status": r.status,
                          "fitted_through": r.fitted_through, "matches": r.matches, "metrics": r.fit_metrics,
                          "code_sha": r.code_sha, "params": r.params} for r in rows]}


@router.post("/calibrations/{calibration_id}/activate")
def activate(request: Request, calibration_id: int, db: Session = Depends(get_db)):
    require_admin_access(request, "Rating activation")
    try:
        result = calibrations.activate(db, calibration_id)
        db.commit()
        return result
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/freeze")
def freeze(request: Request, action: SeasonAction, db: Session = Depends(get_db)):
    require_admin_access(request, "Rating freeze")
    bundle = calibrations.ensure_bundle(db, action.season)
    bundle.frozen, bundle.updated_at = action.frozen, datetime.now(timezone.utc)
    db.commit()
    return {"season": action.season, "frozen": action.frozen}


@router.post("/rollback")
def rollback(request: Request, action: SeasonAction, db: Session = Depends(get_db)):
    require_admin_access(request, "Rating rollback")
    result = calibrations.rollback(db, action.season)
    db.commit()
    return result
