"""
Same pattern as BookingRepository: the service layer depends on this
abstraction, not on SQLAlchemy directly.
"""
from abc import ABC, abstractmethod
from typing import Optional

from sqlalchemy import or_
from sqlalchemy.orm import Session

from .models import User


class UserRepository(ABC):
    @abstractmethod
    def get_by_username(self, username: str) -> Optional[User]:
        raise NotImplementedError

    @abstractmethod
    def get_by_email(self, email: str) -> Optional[User]:
        raise NotImplementedError

    @abstractmethod
    def get_by_username_or_email(self, identifier: str) -> Optional[User]:
        """Used at login, where the person may type either one into the same field."""
        raise NotImplementedError

    @abstractmethod
    def create(self, username: str, email: str, hashed_password: str) -> User:
        raise NotImplementedError


class SqlAlchemyUserRepository(UserRepository):
    def __init__(self, db: Session):
        self._db = db

    def get_by_username(self, username: str) -> Optional[User]:
        return self._db.query(User).filter(User.username == username).first()

    def get_by_email(self, email: str) -> Optional[User]:
        return self._db.query(User).filter(User.email == email).first()

    def get_by_username_or_email(self, identifier: str) -> Optional[User]:
        return (
            self._db.query(User)
            .filter(or_(User.username == identifier, User.email == identifier))
            .first()
        )

    def create(self, username: str, email: str, hashed_password: str) -> User:
        user = User(username=username, email=email, hashed_password=hashed_password)
        self._db.add(user)
        self._db.commit()
        self._db.refresh(user)
        return user
