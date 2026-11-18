from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, PlainSerializer


def _iso_z(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _opt_iso_z(value: datetime | None) -> str | None:
    return None if value is None else _iso_z(value)


# ISO 8601 with a "Z" suffix (PRD 6.1).
UTCDatetime = Annotated[datetime, PlainSerializer(_iso_z, return_type=str)]
OptUTCDatetime = Annotated[datetime | None, PlainSerializer(_opt_iso_z, return_type=str | None)]


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class ListResponse[T](BaseModel):
    items: list[T]
    total: int
