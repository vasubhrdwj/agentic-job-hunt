"""Transaction-owning adapter for the in-app daily digest."""

from __future__ import annotations

from datetime import date
from typing import Protocol

from .daily_digest_repository import build_daily_digest
from .daily_digest_schemas import DailyDigestResponse
from .database import Database
from .security import DataKeyring


class DailyDigestWorkspaceStore(Protocol):
    def get_daily_digest(
        self,
        *,
        owner_id: str,
        owner_timezone: str,
        owner_local_date: date,
    ) -> DailyDigestResponse: ...


class SqlAlchemyDailyDigestWorkspaceStore:
    def __init__(self, database: Database, keyring: DataKeyring) -> None:
        self.database = database
        self.keyring = keyring

    def get_daily_digest(
        self,
        *,
        owner_id: str,
        owner_timezone: str,
        owner_local_date: date,
    ) -> DailyDigestResponse:
        with self.database.session() as session:
            return build_daily_digest(
                session,
                owner_id=owner_id,
                owner_timezone=owner_timezone,
                owner_local_date=owner_local_date,
                keyring=self.keyring,
            )


__all__ = [
    "DailyDigestWorkspaceStore",
    "SqlAlchemyDailyDigestWorkspaceStore",
]
