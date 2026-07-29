from fastapi import Header, HTTPException

from . import config


def require_principal(authorization: str | None = Header(default=None)) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="missing bearer token")
    token = authorization[len("Bearer "):].strip()
    if not token or token not in config.VALID_TOKENS:
        raise HTTPException(status_code=401, detail="invalid bearer token")
    return token  # the token itself is the principal id
