from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from ..schemas import LoginRequest, TokenResponse
from ..security import authenticate, current_operator, issue_token

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/login", response_model=TokenResponse)
def login(payload: LoginRequest) -> TokenResponse:
    if not authenticate(payload.username, payload.password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password.",
        )
    token, expires = issue_token(payload.username)
    return TokenResponse(access_token=token, expires_at=expires, username=payload.username)


@router.get("/me")
def me(operator: str = Depends(current_operator)) -> dict:
    return {"username": operator}
