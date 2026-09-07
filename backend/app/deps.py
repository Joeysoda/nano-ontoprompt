from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session
from jose import JWTError
from app.database import SessionLocal
from app.services.auth_service import decode_token, get_user_by_id
from app.models.user import User
from app.config import settings

bearer = HTTPBearer(auto_error=False)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: Session = Depends(get_db),
) -> User:
    if settings.auth_mode == "local_single_user":
        # The local workbench is deliberately single-user, but still passes a
        # real User object through every existing permission dependency so the
        # rest of the application does not need an insecure bypass branch.
        user = db.query(User).filter(User.role == "admin", User.is_active == True).order_by(User.created_at).first()
        if user:
            return user
        raise HTTPException(status_code=503, detail="本地管理员尚未初始化")
    if not credentials:
        raise HTTPException(status_code=403, detail="Not authenticated")
    try:
        payload = decode_token(credentials.credentials)
        user = get_user_by_id(db, payload["sub"])
        if not user or not user.is_active:
            raise HTTPException(status_code=401, detail="Invalid credentials")
        return user
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid token")

def require_admin(current_user: User = Depends(get_current_user)) -> User:
    if current_user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin required")
    return current_user

def require_editor(current_user: User = Depends(get_current_user)) -> User:
    """编辑权限：admin 或 editor 角色。"""
    if current_user.role not in ("admin", "editor"):
        raise HTTPException(status_code=403, detail="Editor role required")
    return current_user
