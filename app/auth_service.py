import logging
from typing import Optional

from .models import User
from .security import hash_password, verify_password
from .user_repository import UserRepository

logger = logging.getLogger(__name__)


class AccountConflictError(ValueError):
    """Base class so the API layer can register one handler for both cases below."""


class UsernameTakenError(AccountConflictError):
    """Raised when signing up with a username that already exists."""


class EmailTakenError(AccountConflictError):
    """Raised when signing up with an email that's already registered."""


class AuthService:
    def __init__(self, user_repository: UserRepository):
        self._user_repository = user_repository

    def signup(self, username: str, email: str, password: str) -> User:
        if self._user_repository.get_by_username(username) is not None:
            raise UsernameTakenError(f"Username '{username}' is already taken")
        if self._user_repository.get_by_email(email) is not None:
            raise EmailTakenError(f"An account with email '{email}' already exists")

        user = self._user_repository.create(username, email, hash_password(password))
        logger.info("New user signed up: %s (%s)", username, email)
        return user

    def authenticate(self, identifier: str, password: str) -> Optional[User]:
        """`identifier` may be either a username or an email — login accepts either."""
        user = self._user_repository.get_by_username_or_email(identifier)
        if user is None or not verify_password(password, user.hashed_password):
            logger.warning("Failed login attempt for identifier: %s", identifier)
            return None
        return user
