"""Backlog B15.1 (author-reported 2026-08-01): in-app HTTPS certificate
management -- Settings > Security > Certificate. All mutating endpoints
trigger tls_service.restart_to_apply() (an in-place self-restart a
couple seconds later) so a newly generated/imported certificate takes
effect without a separate manual step. Gated by the same optional
session-auth middleware as every other /api/* route (main.py's
auth_gate) -- nothing here is exempted.

Mirrors the shape of the sibling Fiduciary project's own
`/api/tls/*` endpoints (read directly before writing this), minus the
ACME routes -- see tls_service.py's module docstring for why ACME
wasn't ported this pass."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from app.schemas.tls import CsrRequest, ImportCertRequest, SelfSignedRequest
from app.services import tls_service

router = APIRouter(prefix="/api/tls", tags=["tls"])


@router.get("/status")
def tls_status():
    return tls_service.status()


@router.get("/mobileconfig")
def tls_mobileconfig():
    """Author-reported 2026-08-03: downloads the active certificate as an
    Apple Configuration Profile, so iOS/iPadOS can install it as a
    trusted root instead of relying only on the per-origin browser
    click-through. See tls_service.build_mobileconfig()'s own docstring
    for why this is additive, not a replacement for that existing flow."""
    try:
        content = tls_service.build_mobileconfig()
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return Response(
        content=content,
        media_type="application/x-apple-aspen-config",
        headers={"Content-Disposition": 'attachment; filename="chef-ca.mobileconfig"'},
    )


@router.post("/self-signed")
def tls_self_signed(payload: SelfSignedRequest):
    hosts = [h.strip() for h in payload.hostnames if h.strip()]
    if not hosts:
        raise HTTPException(
            status_code=400,
            detail="at least one hostname or IP is required (e.g. this box's LAN IP, a hostname "
            "you use to reach it, or 'localhost')",
        )
    try:
        return {"ok": True, "status": tls_service.generate_self_signed(hosts)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"self-signed generation failed: {e}") from e


@router.post("/csr")
def tls_csr(payload: CsrRequest):
    """Generates a NEW private key (stays on this server) + a CSR you
    can take to any Certificate Authority (or an internal/self-signed CA
    for a LAN-only deployment). Submit the returned PEM; once you have
    the signed certificate, POST it to /api/tls/import-cert."""
    cn = payload.common_name.strip()
    if not cn:
        raise HTTPException(status_code=400, detail="common_name is required")
    sans = [s.strip() for s in payload.sans if s.strip()]
    try:
        csr_pem = tls_service.generate_csr(cn, sans)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"CSR generation failed: {e}") from e
    return {
        "ok": True,
        "csr_pem": csr_pem,
        "note": "Submit this CSR to any Certificate Authority (or an internal/self-signed CA for "
        "a LAN-only deployment). Once you have the signed certificate back, POST it to "
        "/api/tls/import-cert to install it -- the private key never leaves this server.",
    }


@router.post("/import-cert")
def tls_import_cert(payload: ImportCertRequest):
    """Pairs the signed certificate with the pending CSR's private key
    -- rejected with a clear error if they don't actually match, so a
    wrong/unrelated certificate can never get installed silently."""
    try:
        return {"ok": True, "status": tls_service.import_signed_certificate(payload.cert_pem, payload.chain_pem)}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"certificate import failed: {e}") from e


@router.post("/clear")
def tls_clear():
    """Removes the active certificate -- reverts to plain HTTP on the
    next (self-triggered) restart."""
    return {"ok": True, "status": tls_service.clear_active_cert()}


@router.post("/restart")
def tls_restart():
    """Manually re-applies whatever certificate state is currently on
    disk -- the mutating endpoints above already do this automatically;
    this is only for recovering from an edge case (e.g. a cert file
    dropped in by hand outside the app)."""
    tls_service.restart_to_apply()
    return {
        "ok": True,
        "note": "restarting now to apply the certificate configuration -- this page will briefly disconnect",
    }
