from collections.abc import Iterator
from typing import cast

from fastapi import Depends, Header, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from ai_kp.bootstrap.settings import Settings
from ai_kp.infrastructure.database.repositories import Repository
from ai_kp.infrastructure.database.schema import connect
from ai_kp.platform.sessions.models import AuthenticatedMember, AuthenticatedPlayer

bearer = HTTPBearer(auto_error=False)


def get_app_settings(request: Request) -> Settings:
    """Return the settings bound to this concrete application instance."""

    return cast(Settings, request.app.state.settings)


def get_repo(settings: Settings = Depends(get_app_settings)) -> Iterator[Repository]:
    connection = connect(settings.db_path)
    try:
        yield Repository(connection)
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def get_optional_identity(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    repo: Repository = Depends(get_repo),
) -> AuthenticatedMember | None:
    if credentials is None:
        return None
    identity = repo.authenticate_access_token(credentials.credentials)
    if identity is None:
        raise HTTPException(status_code=401, detail="Invalid or expired session token")
    return identity


def get_identity(
    identity: AuthenticatedMember | None = Depends(get_optional_identity),
) -> AuthenticatedMember:
    if identity is None:
        raise HTTPException(status_code=401, detail="Session token required")
    return identity


def get_optional_player_identity(
    x_ai_kp_player_token: str | None = Header(default=None),
    repo: Repository = Depends(get_repo),
) -> AuthenticatedPlayer | None:
    if not x_ai_kp_player_token:
        return None
    identity = repo.authenticate_player_token(x_ai_kp_player_token)
    if identity is None:
        raise HTTPException(status_code=401, detail="Invalid player profile token")
    return identity


def get_player_identity(
    identity: AuthenticatedPlayer | None = Depends(get_optional_player_identity),
) -> AuthenticatedPlayer:
    if identity is None:
        raise HTTPException(status_code=401, detail="Player profile token required")
    return identity
