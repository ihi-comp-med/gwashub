"""Creating people: a GWASHub user plus, with Cognito sign-in, their Cognito account and invitation email.
Used by reviewers inviting members from the website and by the admin command."""
import re

from sqlalchemy import select

from gwashub_api.config import settings
from gwashub_api.models import User

EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class InviteError(Exception):
    def __init__(self, message: str, status: int = 422):
        super().__init__(message)
        self.status = status


def cognito_invite(email: str, name: str, resend: bool = False) -> str:
    """Create the Cognito account; Cognito emails a temporary password. Returns what happened."""
    import boto3
    s = settings()
    client = boto3.client("cognito-idp", region_name=s.aws_region)
    kwargs = {"UserPoolId": s.cognito_user_pool_id, "Username": email, "DesiredDeliveryMediums": ["EMAIL"],
              "UserAttributes": [{"Name": "email", "Value": email}, {"Name": "email_verified", "Value": "true"},
                                 {"Name": "name", "Value": name}]}
    if resend:
        kwargs["MessageAction"] = "RESEND"
    try:
        client.admin_create_user(**kwargs)
        return "invitation email sent"
    except client.exceptions.UsernameExistsException:
        return "already has a Cognito account"


def find_or_invite(db, email: str, name: str, institution: str = "") -> tuple[User, str]:
    """The user with this email, creating them (and their Cognito account) if needed.
    Returns (user, "existing" | "invited" | "created"). Commits nothing; the caller does."""
    email = email.strip().lower()
    if not EMAIL.match(email) or len(email) > 200:
        raise InviteError("That doesn't look like an email address")
    user = db.scalar(select(User).where(User.email == email))
    if user:
        return user, "existing"
    name = name.strip()
    if not name or len(name) > 200 or len(institution) > 200:
        raise InviteError("Give the person's name (and institution, if known), up to 200 characters each")
    user = User(email=email, name=name, institution=institution.strip())
    db.add(user)
    db.flush()
    if settings().auth_mode == "cognito":
        try:
            cognito_invite(email, name)
        except Exception as e:
            raise InviteError(f"Couldn't send the invitation: {e}", status=502)
        return user, "invited"
    return user, "created"
