"""重置 admin 用户密码。

用法:
    python scripts/reset_admin_password.py                       # 交互式输入新密码
    python scripts/reset_admin_password.py --password <new_pwd>  # 重置为指定密码
    python scripts/reset_admin_password.py --user <username>     # 指定用户名 (默认 admin)
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.database import SessionLocal
from app.models.user import User
from app.services.auth_service import hash_password


def main(username: str, password: str):
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.username == username).first()
        if not user:
            print(f"用户不存在: {username}")
            sys.exit(1)
        user.password_hash = hash_password(password)
        db.commit()
        print(f"已重置 {username} 的密码")
    finally:
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--user", default="admin")
    parser.add_argument("--password", help="仅在本机命令行传入的新密码；省略时安全地交互输入")
    args = parser.parse_args()
    password = args.password
    if not password:
        import getpass
        password = getpass.getpass("请输入新的管理员密码：")
    if len(password) < 8:
        parser.error("密码至少需要 8 个字符")
    main(args.user, password)
