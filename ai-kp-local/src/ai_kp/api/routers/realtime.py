from fastapi import APIRouter, Depends, Response, WebSocket

from ai_kp.api.dependencies import get_identity, get_repo
from ai_kp.infrastructure.database.repositories import Repository
from ai_kp.infrastructure.database.security import AuthenticatedMember
from ai_kp.infrastructure.realtime.websocket import handle_realtime_websocket


router = APIRouter()


@router.websocket("/ws")
async def realtime_websocket(websocket: WebSocket) -> None:
    settings = websocket.app.state.settings
    await handle_realtime_websocket(
        websocket,
        db_path=settings.db_path,
        allowed_origins=settings.cors_origin_list,
    )


@router.post("/realtime/tickets")
def issue_realtime_ticket(
    response: Response,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> dict:
    response.headers["Cache-Control"] = "no-store"
    return repo.issue_realtime_ticket(identity)
