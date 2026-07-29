from fastapi import Header, HTTPException


def require_principal(authorization: str | None = Header(default=None)) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="missing bearer token")
    token = authorization[len("Bearer "):].strip()
    if not token:
        raise HTTPException(status_code=401, detail="invalid bearer token")
    return token  # any nonempty token is accepted and becomes its own isolated principal
