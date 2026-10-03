"""Admin commands (run where the app runs: inside the container, or on AWS with devtools/aws_admin.sh).

    python -m gwashub_api.admin users
    python -m gwashub_api.admin invite EMAIL "Full Name" [--institution X] [--admin]     # Cognito mode
    python -m gwashub_api.admin add-user EMAIL "Full Name" [--institution X] [--admin]   # password mode
    python -m gwashub_api.admin set-password EMAIL                                       # password mode
    python -m gwashub_api.admin add-member EMAIL PROJECT_ID uploader|reviewer
    python -m gwashub_api.admin remove-member EMAIL PROJECT_ID
    python -m gwashub_api.admin add-project ID "Name" SHORT --phenotype "All-cause HF" [--phenotype ...]
    python -m gwashub_api.admin db-readonly-user        # Postgres: (re)create gwashub_readonly for browsing

`invite` creates the GWASHub user and, in Cognito mode, the Cognito account: Cognito emails a
temporary password, and the person sets their own password and authenticator app at first sign-in.
Passwords (password mode) are read from a prompt, or GWASHUB_NEW_PASSWORD for scripts.
"""
import argparse
import getpass
import os
import sys

from sqlalchemy import select

from gwashub_api import services
from gwashub_api.accounts import cognito_invite
from gwashub_api.auth import hash_password
from gwashub_api.config import settings
from gwashub_api.db import SessionLocal, create_all
from gwashub_api.models import Membership, Project, User


def read_password() -> str:
    pw = os.environ.get("GWASHUB_NEW_PASSWORD") or getpass.getpass("New password: ")
    if len(pw) < 12:
        sys.exit("Password must be at least 12 characters")
    if "GWASHUB_NEW_PASSWORD" not in os.environ and getpass.getpass("Repeat password: ") != pw:
        sys.exit("Passwords don't match")
    return pw


def find_user(db, email: str) -> User:
    user = db.scalar(select(User).where(User.email == email.strip().lower()))
    if not user:
        sys.exit(f"No user {email}")
    return user


def readonly_user():
    """A Postgres login that can only read (SELECT) GWASHub's tables, now and in future migrations."""
    import json

    import boto3
    from sqlalchemy import text

    from gwashub_api.db import engine
    s = settings()
    if s.is_sqlite or not os.environ.get("GWASHUB_DB_READONLY_SECRET"):
        sys.exit("Only for Postgres on AWS (needs GWASHUB_DB_READONLY_SECRET)")
    raw = boto3.client("secretsmanager", region_name=s.aws_region).get_secret_value(
        SecretId=os.environ["GWASHUB_DB_READONLY_SECRET"])["SecretString"]
    password = json.loads(raw)["password"] if raw.startswith("{") else raw
    with engine().begin() as conn:
        owner = conn.execute(text("select current_user")).scalar()
        exists = conn.execute(text("select 1 from pg_roles where rolname = 'gwashub_readonly'")).scalar()
        verb = "ALTER" if exists else "CREATE"
        if not password.isalnum():  # generated without punctuation; DDL can't take bound parameters
            sys.exit("Unexpected characters in the read-only password secret")
        conn.execute(text(f"{verb} ROLE gwashub_readonly LOGIN PASSWORD '{password}'"))
        conn.execute(text("ALTER ROLE gwashub_readonly SET default_transaction_read_only = on"))
        conn.execute(text("GRANT CONNECT ON DATABASE gwashub TO gwashub_readonly"))
        conn.execute(text("GRANT USAGE ON SCHEMA public TO gwashub_readonly"))
        conn.execute(text("GRANT SELECT ON ALL TABLES IN SCHEMA public TO gwashub_readonly"))
        conn.execute(text(f'ALTER DEFAULT PRIVILEGES FOR ROLE "{owner}" IN SCHEMA public GRANT SELECT ON TABLES TO gwashub_readonly'))
    print(f"Read-only user gwashub_readonly {'updated' if exists else 'created'}; password in {os.environ['GWASHUB_DB_READONLY_SECRET']}")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m gwashub_api.admin")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("users", help="list users, their roles and whether a password is set")
    i = sub.add_parser("invite", help="create a user and (Cognito mode) email them an invitation")
    i.add_argument("email")
    i.add_argument("name")
    i.add_argument("--institution", default="")
    i.add_argument("--admin", action="store_true")
    i.add_argument("--resend", action="store_true", help="send the invitation email again")
    rm = sub.add_parser("remove-member", help="take a user out of a project")
    rm.add_argument("email")
    rm.add_argument("project")
    pr = sub.add_parser("add-project", help="create a project with the default QC pipeline")
    pr.add_argument("id", help="short slug used in URLs, e.g. hermes3")
    pr.add_argument("name")
    pr.add_argument("short", help="short display name, e.g. HERMES 3")
    pr.add_argument("--phenotype", action="append", required=True)
    pr.add_argument("--description", default="")
    sub.add_parser("db-readonly-user", help="Postgres: create or update the read-only user (password from Secrets Manager)")
    a = sub.add_parser("add-user", help="create a user and set their password")
    a.add_argument("email")
    a.add_argument("name")
    a.add_argument("--institution", default="")
    a.add_argument("--admin", action="store_true")
    p = sub.add_parser("set-password", help="set or reset a user's password")
    p.add_argument("email")
    m = sub.add_parser("add-member", help="give a user a role in a project")
    m.add_argument("email")
    m.add_argument("project")
    m.add_argument("role", choices=["uploader", "reviewer"])
    args = ap.parse_args(argv)

    create_all()
    if args.cmd == "db-readonly-user":
        return readonly_user()
    db = SessionLocal()
    try:
        if args.cmd == "users":
            for u in db.scalars(select(User).order_by(User.id)):
                roles = ", ".join(f"{x.project_id}:{x.role}" for x in db.scalars(select(Membership).where(Membership.user_id == u.id)))
                print(f"{u.email:35} {'password set' if u.password_hash else 'no password':13} {roles}")
        elif args.cmd == "invite":
            email = args.email.strip().lower()
            user = db.scalar(select(User).where(User.email == email))
            if not user:
                user = User(email=email, name=args.name, institution=args.institution, is_admin=args.admin)
                db.add(user)
                db.commit()
                print(f"Created GWASHub user {email}")
            if settings().auth_mode == "cognito":
                print(f"Cognito: {cognito_invite(email, args.name, args.resend)}")
            print(f"Next: python -m gwashub_api.admin add-member {email} PROJECT uploader|reviewer")
        elif args.cmd == "remove-member":
            user = find_user(db, args.email)
            m = db.scalar(select(Membership).where(Membership.user_id == user.id, Membership.project_id == args.project))
            if not m:
                sys.exit(f"{user.email} isn't in {args.project}")
            db.delete(m)
            db.commit()
            print(f"Removed {user.email} from {args.project}")
        elif args.cmd == "add-project":
            if db.get(Project, args.id):
                sys.exit(f"Project {args.id} already exists")
            p = Project(id=args.id, name=args.name, short=args.short, description=args.description, phenotypes=args.phenotype)
            db.add(p)
            db.flush()
            services.ensure_project_pipeline(db, p)
            db.commit()
            print(f"Created project {args.id}; add people with add-member")
        elif args.cmd == "add-user":
            email = args.email.strip().lower()
            if db.scalar(select(User).where(User.email == email)):
                sys.exit(f"User {email} already exists (use set-password)")
            db.add(User(email=email, name=args.name, institution=args.institution, is_admin=args.admin,
                        password_hash=hash_password(read_password())))
            db.commit()
            print(f"Created {email}")
        elif args.cmd == "set-password":
            user = find_user(db, args.email)
            user.password_hash = hash_password(read_password())
            db.commit()
            print(f"Password set for {user.email}")
        elif args.cmd == "add-member":
            user = find_user(db, args.email)
            if not db.get(Project, args.project):
                sys.exit(f"No project {args.project}")
            m = db.scalar(select(Membership).where(Membership.user_id == user.id, Membership.project_id == args.project))
            if m:
                m.role = args.role
            else:
                db.add(Membership(user_id=user.id, project_id=args.project, role=args.role))
            db.commit()
            print(f"{user.email} is now {args.role} in {args.project}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
