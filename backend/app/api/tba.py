from fastapi import APIRouter

from app.services.clients.tba import get_event

router = APIRouter(prefix="/tba", tags=["tba"])


@router.get("/event/{event_key}")
async def event(event_key: str):
    return await get_event(event_key)
