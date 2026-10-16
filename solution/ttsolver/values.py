"""Small value domain. Concrete Python scalars are Known values (no boxing loss)."""
from dataclasses import dataclass, field
from decimal import Decimal
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class Unknown:
    reason: str
    def __bool__(self):
        raise TypeError('Unknown must be resolved by the path engine: ' + self.reason)


@dataclass(frozen=True)
class NonNull:
    type: str


@dataclass(frozen=True)
class Interval:
    lower: Decimal
    upper: Decimal


@dataclass(frozen=True)
class StringPattern:
    prefix: str


@dataclass(frozen=True)
class FiniteSet:
    values: tuple


@dataclass
class Collection:
    elements: list | None = None
    minimum: int = 0
    maximum: int | None = None


@dataclass
class Obj:
    type: str
    fields: dict = field(default_factory=dict)
    identity: str | None = None
    origin: tuple | None = None


@dataclass
class OptionalValue:
    value: Any


@dataclass
class TypeRef:
    name: str


@dataclass
class Deferred:
    node: Any
    env: dict


def unknown(*values):
    return next((x for x in values if isinstance(x, Unknown)), None)


def truth(value):
    if isinstance(value, Unknown):
        return value
    if isinstance(value, FiniteSet):
        states = {truth(v) for v in value.values}
        return states.pop() if len(states) == 1 else Unknown('finite boolean alternatives')
    if isinstance(value, bool):
        return value
    return Unknown('not a boolean: ' + repr(value)[:100])


def describe(value):
    if isinstance(value, Obj):
        return {'type': value.type, 'identity': value.identity,
                'fields': {k: describe(v) for k, v in value.fields.items()}}
    if isinstance(value, dict):
        return {str(k): describe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [describe(v) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return repr(value)
