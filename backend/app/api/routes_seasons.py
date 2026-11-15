from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.session import close_db_before_response, get_db
from app.services.ratings.accuracy import season_accuracy

router = APIRouter(prefix="/seasons", tags=["seasons"])


@router.get("/{season}/accuracy")
@close_db_before_response
def get_season_accuracy(season: int, db: Session = Depends(get_db)):
    # The season's public accuracy record from the frozen prediction log.
    if season < 2000 or season > 2100:
        raise HTTPException(status_code=404, detail="Unknown season")
    return {"ok": True, **season_accuracy(db, season)}
