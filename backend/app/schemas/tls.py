"""Pydantic request models for backlog B15.1's in-app HTTPS certificate
management (Settings > Security > Certificate). Responses are plain
dicts from tls_service.status() -- see that module's own docstring for
why a formal Read schema wasn't added (the shape already varies by
whether a cert is active, and X.509-derived fields are read fresh off
disk every call rather than persisted, so a rigid schema would fight
that more than help)."""
from __future__ import annotations

from pydantic import BaseModel, Field


class SelfSignedRequest(BaseModel):
    # Every LAN IP/hostname you actually use to reach this box, so the
    # browser doesn't warn about a SAN mismatch on top of the expected
    # self-signed trust warning. At least one entry required.
    hostnames: list[str] = Field(default_factory=list)


class CsrRequest(BaseModel):
    common_name: str
    sans: list[str] = Field(default_factory=list)


class ImportCertRequest(BaseModel):
    cert_pem: str
    chain_pem: str | None = None
