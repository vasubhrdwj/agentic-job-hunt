"""Shared safe errors for owner-scoped product repositories."""

from __future__ import annotations


class ProductRepositoryError(RuntimeError):
    pass


class VersionConflict(ProductRepositoryError):
    def __init__(self, resource_type: str, resource_id: str, expected: int, actual: int) -> None:
        super().__init__(
            f"{resource_type} version conflict for {resource_id}: "
            f"expected {expected}, current {actual}"
        )
        self.resource_type = resource_type
        self.resource_id = resource_id
        self.expected = expected
        self.actual = actual


class ResourceConflict(ProductRepositoryError):
    pass


class ResourceInUse(ProductRepositoryError):
    pass


def require_version(
    resource_type: str,
    resource_id: str,
    *,
    expected: int,
    actual: int,
) -> None:
    if expected != actual:
        raise VersionConflict(resource_type, resource_id, expected, actual)


__all__ = [
    "ProductRepositoryError",
    "ResourceConflict",
    "ResourceInUse",
    "VersionConflict",
    "require_version",
]
