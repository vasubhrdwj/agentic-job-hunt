"""User-editable company registry backed by YAML packs."""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator, Mapping
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError
from yaml.constructor import ConstructorError
from yaml.nodes import MappingNode
from yaml.resolver import BaseResolver

from job_hunt_agent.schemas import Company, CompanySource


DEFAULT_PACK_DIR = Path(__file__).resolve().parents[2] / "config" / "company_packs"
_PACK_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
_SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_DOMAIN = re.compile(
    r"^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+"
    r"[a-z]{2,63}$",
    re.IGNORECASE,
)
_COMPANY_FIELDS = frozenset(Company.model_fields)
_PACK_FIELDS = frozenset({"name", "description", "companies"})
_TOKEN_REQUIRED = frozenset(
    {
        CompanySource.greenhouse,
        CompanySource.lever,
        CompanySource.ashby,
        CompanySource.workday,
        CompanySource.smartrecruiters,
        CompanySource.workable,
        CompanySource.bespoke,
    },
)


class UniqueKeySafeLoader(yaml.SafeLoader):
    """Safe YAML loader that rejects duplicate mapping keys."""


def _construct_unique_mapping(
    loader: UniqueKeySafeLoader,
    node: MappingNode,
    deep: bool = False,
) -> dict[Any, Any]:
    loader.flatten_mapping(node)
    mapping: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        try:
            duplicate = key in mapping
        except TypeError as exc:
            raise ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                "found an unhashable YAML key",
                key_node.start_mark,
            ) from exc
        if duplicate:
            raise ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                f"found duplicate YAML key {key!r}",
                key_node.start_mark,
            )
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


UniqueKeySafeLoader.add_constructor(
    BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


class RegistryError(ValueError):
    """Raised when a company pack is malformed or internally inconsistent."""


class CompanyRegistry:
    """Ordered, validated collection of companies from one registry pack."""

    def __init__(
        self,
        companies: Iterable[Company],
        *,
        name: str = "custom",
        description: str = "",
    ) -> None:
        ordered = tuple(companies)
        by_slug: dict[str, Company] = {}
        for company in ordered:
            validate_company_configuration(company)
            if company.slug in by_slug:
                raise RegistryError(f"duplicate company slug: {company.slug!r}")
            by_slug[company.slug] = company

        self.name = name
        self.description = description
        self._companies = ordered
        self._by_slug = by_slug

    def __iter__(self) -> Iterator[Company]:
        return iter(self._companies)

    def __len__(self) -> int:
        return len(self._companies)

    @property
    def companies(self) -> tuple[Company, ...]:
        """All configured companies, preserving pack order."""

        return self._companies

    @property
    def active_companies(self) -> tuple[Company, ...]:
        """Companies enabled for searches."""

        return tuple(company for company in self._companies if company.active)

    def get(self, slug: str, *, include_inactive: bool = False) -> Company | None:
        """Return a company by stable slug, or ``None`` when unavailable."""

        company = self._by_slug.get(slug)
        if company is None or (not include_inactive and not company.active):
            return None
        return company

    def select(
        self,
        *,
        tags: Iterable[str] = (),
        locations: Iterable[str] = (),
        active_only: bool = True,
    ) -> tuple[Company, ...]:
        """Select companies matching every requested tag and any location."""

        wanted_tags = {tag.casefold() for tag in tags}
        wanted_locations = {location.casefold() for location in locations}
        matches: list[Company] = []
        for company in self._companies:
            if active_only and not company.active:
                continue
            company_tags = {tag.casefold() for tag in company.tags}
            if not wanted_tags.issubset(company_tags):
                continue
            company_locations = {
                location.casefold() for location in company.hire_locations
            }
            if wanted_locations and company_locations.isdisjoint(wanted_locations):
                continue
            matches.append(company)
        return tuple(matches)


def validate_company_configuration(company: Company) -> None:
    """Apply registry-specific checks beyond the shared Pydantic contract."""

    if not _SLUG.fullmatch(company.slug):
        raise RegistryError(
            f"{company.slug!r}: slug must contain lowercase letters, digits, "
            "and single hyphens only"
        )

    # Inactive records may intentionally be incomplete while a user curates them.
    if not company.active:
        return

    token = company.source_token.strip() if company.source_token else None
    if company.source in _TOKEN_REQUIRED and not token:
        raise RegistryError(
            f"{company.slug!r}: source {company.source.value!r} requires source_token"
        )
    if company.source is CompanySource.google_jobs and token is not None:
        raise RegistryError(
            f"{company.slug!r}: google_jobs entries must use a null source_token"
        )
    if not company.careers_domains:
        raise RegistryError(
            f"{company.slug!r}: active entries require at least one careers domain"
        )

    for domain in company.careers_domains:
        if domain != domain.strip().lower() or not _DOMAIN.fullmatch(domain):
            raise RegistryError(
                f"{company.slug!r}: careers domain must be a lowercase hostname, "
                f"got {domain!r}"
            )


def load_company_pack(
    pack: str | Path,
    *,
    pack_dir: str | Path | None = None,
) -> CompanyRegistry:
    """Load and validate a named pack or an explicit YAML file path."""

    path = _resolve_pack_path(pack, pack_dir=pack_dir)
    try:
        raw = yaml.load(
            path.read_text(encoding="utf-8"),
            Loader=UniqueKeySafeLoader,
        )
    except OSError as exc:
        raise RegistryError(f"unable to read company pack {path}: {exc}") from exc
    except yaml.YAMLError as exc:
        raise RegistryError(f"invalid YAML in company pack {path}: {exc}") from exc

    if not isinstance(raw, Mapping):
        raise RegistryError(f"company pack {path} must contain a mapping")

    unknown_pack_fields = set(raw) - _PACK_FIELDS
    if unknown_pack_fields:
        raise RegistryError(
            f"company pack {path} has unknown fields: "
            f"{', '.join(sorted(map(str, unknown_pack_fields)))}"
        )

    raw_companies = raw.get("companies")
    if not isinstance(raw_companies, list):
        raise RegistryError(f"company pack {path} must contain a companies list")

    companies = [
        _parse_company(item, index=index, path=path)
        for index, item in enumerate(raw_companies)
    ]
    name = raw.get("name", path.stem)
    description = raw.get("description", "")
    if not isinstance(name, str) or not name.strip():
        raise RegistryError(f"company pack {path} requires a non-empty string name")
    if not isinstance(description, str):
        raise RegistryError(f"company pack {path} description must be a string")

    return CompanyRegistry(
        companies,
        name=name.strip(),
        description=description.strip(),
    )


def _parse_company(item: Any, *, index: int, path: Path) -> Company:
    if not isinstance(item, Mapping):
        raise RegistryError(f"{path}: companies[{index}] must be a mapping")

    unknown_fields = set(item) - _COMPANY_FIELDS
    if unknown_fields:
        raise RegistryError(
            f"{path}: companies[{index}] has unknown fields: "
            f"{', '.join(sorted(map(str, unknown_fields)))}"
        )

    try:
        return Company.model_validate(dict(item))
    except ValidationError as exc:
        raise RegistryError(f"{path}: invalid companies[{index}]: {exc}") from exc


def _resolve_pack_path(
    pack: str | Path,
    *,
    pack_dir: str | Path | None,
) -> Path:
    candidate = Path(pack)
    if candidate.suffix in {".yaml", ".yml"} or candidate.is_absolute():
        return candidate

    pack_name = str(pack)
    if not _PACK_NAME.fullmatch(pack_name):
        raise RegistryError(f"invalid company pack name: {pack_name!r}")
    root = Path(pack_dir) if pack_dir is not None else DEFAULT_PACK_DIR
    return root / f"{pack_name}.yaml"
