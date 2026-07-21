#!/usr/bin/env python3
"""Seed a demo workspace through the running application's public HTTP API."""

from __future__ import annotations

import argparse
import http.cookiejar
import json
import secrets
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin
from urllib.request import HTTPCookieProcessor, Request, build_opener


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RESUME = ROOT / "fixtures" / "sample_resume.txt"


class SeedError(RuntimeError):
    """A useful, user-facing seeding failure."""


def _multipart_file(field: str, path: Path) -> tuple[bytes, str]:
    boundary = f"job-hunt-demo-{uuid.uuid4().hex}"
    content = path.read_bytes()
    body = b"".join(
        [
            f"--{boundary}\r\n".encode(),
            f'Content-Disposition: form-data; name="{field}"; filename="{path.name}"\r\n'.encode(),
            b"Content-Type: text/plain; charset=utf-8\r\n\r\n",
            content,
            b"\r\n",
            f'--{boundary}\r\nContent-Disposition: form-data; name="label"\r\n\r\n'.encode(),
            b"Build Week demo resume\r\n",
            f'--{boundary}\r\nContent-Disposition: form-data; name="set_as_base"\r\n\r\n'.encode(),
            b"true\r\n",
            f"--{boundary}--\r\n".encode(),
        ]
    )
    return body, f"multipart/form-data; boundary={boundary}"


class ApiClient:
    def __init__(self, base_url: str, origin: str) -> None:
        self.base_url = base_url.rstrip("/") + "/"
        self.origin = origin.rstrip("/")
        self.opener = build_opener(HTTPCookieProcessor(http.cookiejar.CookieJar()))

    def post(
        self,
        path: str,
        *,
        payload: dict[str, object] | None = None,
        body: bytes | None = None,
        content_type: str = "application/json",
    ) -> dict[str, object]:
        if payload is not None:
            body = json.dumps(payload).encode("utf-8")
        headers = {"Origin": self.origin, "Content-Type": content_type}
        if path != "/api/accounts":
            headers["Idempotency-Key"] = f"demo-{uuid.uuid4()}"
        request = Request(
            urljoin(self.base_url, path.lstrip("/")),
            data=body,
            headers=headers,
            method="POST",
        )
        try:
            with self.opener.open(request, timeout=30) as response:
                raw = response.read().decode("utf-8")
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            try:
                parsed = json.loads(detail)
                detail = parsed.get("detail") or parsed.get("message") or detail
            except json.JSONDecodeError:
                pass
            raise SeedError(f"POST {path} failed ({exc.code}): {detail}") from exc
        except URLError as exc:
            raise SeedError(
                f"Could not reach {self.base_url}: {exc.reason}. Is the local app running?"
            ) from exc
        try:
            result = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise SeedError(f"POST {path} returned invalid JSON") from exc
        if not isinstance(result, dict):
            raise SeedError(f"POST {path} returned an unexpected response")
        return result


def _default_email() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f")
    return f"build-week-demo+{stamp}-{secrets.token_hex(3)}@example.com"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--origin", default="http://localhost:3000")
    parser.add_argument("--email", default=None, help="Account email (default: unique example.com address)")
    parser.add_argument("--password", default=None, help="Account password (default: securely generated)")
    parser.add_argument("--resume", type=Path, default=DEFAULT_RESUME)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.resume.is_file():
        raise SeedError(f"Resume fixture not found: {args.resume}")
    email = args.email or _default_email()
    password = args.password or f"Bw!{secrets.token_urlsafe(24)}"
    if len(password) < 12:
        raise SeedError("Password must contain at least 12 characters")

    api = ApiClient(args.base_url, args.origin)
    account = api.post(
        "/api/accounts",
        payload={
            "email": email,
            "password": password,
            "display_name": "Build Week Demo",
            "timezone": "Asia/Kolkata",
        },
    )
    upload_body, upload_type = _multipart_file("file", args.resume)
    upload = api.post(
        "/api/me/resume-versions/upload", body=upload_body, content_type=upload_type
    )
    resume = upload.get("resume_version")
    if not isinstance(resume, dict) or not isinstance(resume.get("id"), str):
        raise SeedError("Resume upload response did not include resume_version.id")

    track = api.post(
        "/api/career-tracks",
        payload={
            "name": "Backend & platform engineering",
            "role_families": ["Backend Engineering", "Platform Engineering"],
            "seniority_levels": ["senior", "staff"],
            "target_locations": ["Remote-India", "Bengaluru"],
            "priorities": {"compensation": 4, "scope": 5, "learning": 5, "company_quality": 4, "flexibility": 4},
            "active": True,
        },
    )
    if not isinstance(track.get("id"), str):
        raise SeedError("Career-track response did not include id")
    saved_search = api.post(
        "/api/saved-searches",
        payload={
            "name": "Senior backend roles in India",
            "career_track_id": track["id"],
            "resume_version_id": resume["id"],
            "criteria": {
                "role_keywords": ["backend", "platform", "distributed systems"],
                "seniority": "senior",
                "location": ["Remote-India", "Bengaluru"],
                "employment_types": ["full_time"],
                "max_age_days": 30,
                "country": "in",
            },
            "schedule": {"cadence": "manual", "timezone": "Asia/Kolkata"},
            "pack": "backend_india",
            "use_self_rag": True,
            "active": True,
        },
    )
    print(json.dumps({
        "base_url": args.base_url,
        "email": email,
        "password": password,
        "owner_id": account.get("owner_id"),
        "resume_version_id": resume["id"],
        "career_track_id": track["id"],
        "saved_search_id": saved_search.get("id"),
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SeedError as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)
