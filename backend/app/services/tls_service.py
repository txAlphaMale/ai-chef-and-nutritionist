"""Backlog B15.1: in-app HTTPS certificate
management, so Chef can serve itself over a secure context. Ported from
the sibling Fiduciary project's `portfolio-api/tls.py` (read directly,
not assumed, before writing this) at the author's explicit direction --
Fiduciary already solved exactly this problem, and re-deriving it from
scratch would be pointless when a validated implementation exists one
folder over.

Why this exists at all: the author tried the barcode scanner (B4.1) and
the Dining Out "use my location" button (B10.1) on an iPad and got
"Camera access needs HTTPS" / "this needs HTTPS in most browsers" --
both `navigator.mediaDevices.getUserMedia` and `navigator.geolocation`
are gated by the browser's Secure Context check (HTTPS, or `localhost`
specifically), and Chef has served plain HTTP over a LAN IP since Phase
1. Nothing about that was a bug; it just never needed fixing until a
feature that needs a secure context showed up.

Two real deviations from Fiduciary's version, both explained here so a
future session doesn't wonder if they were accidental:

1. NO ACME (Let's Encrypt) support. Fiduciary's tls.py has three methods
   (self-signed / ACME via vendored acme.sh / manual CSR); this module
   only ports two. ACME needs a real public DNS domain and (for the
   http-01 challenge) port 80 reachable from the internet -- and the
   author has said directly, in this same project, that this deployment
   is LAN-only with no internet access expected (see the B12.1 Google
   Calendar redirect-URI notes elsewhere in PROJECT-PLAN.md). Vendoring
   acme.sh into the Docker image for a scenario that doesn't apply to
   this household's actual deployment isn't worth the image-size and
   maintenance cost. Self-signed is the right default anyway --
   Fiduciary's own docstring calls it "the right quick-start default"
   even with ACME available. If a future household clones this repo and
   DOES have a public domain, ACME is reasonable future work (would port
   the same way, using this file as the template) -- tracked as B15.2 in
   PROJECT-PLAN.md rather than silently left out.

2. NO store.py-backed encrypted metadata store. Fiduciary's `store.py`
   is a SQLite-backed, encrypted key-value store used for its entire
   general-purpose persisted-JSON layer (financial data, settings, etc);
   porting that whole subsystem just for a few non-secret operational
   fields (which method produced the active cert, when it was created)
   would be a lot of new machinery for very little. This module instead
   writes a small plain JSON file directly (atomic tmp+os.replace, the
   same pattern already used elsewhere in this app -- see
   recipe_image_service.save_image / backup_service.py) under TLS_DIR,
   right next to the cert/key files it describes. Nothing in it is a
   secret (a Fernet-encrypted settings value, like the Google Calendar
   OAuth secret, still goes through settings_service.py as always) --
   it's just "which of the two methods is this."

Both self-signed generation and manual-CSR generation/import are pure
`cryptography` operations (already a dependency here, via
secrets_crypto.py's Fernet key -- same underlying package, no new pip
install) with no network calls, so both are fully unit-testable, unlike
the ACME path Fiduciary itself documents as only testable against a
stubbed acme.sh.

One certificate covers everything, because one container serves
everything. A browser's Secure Context check is about the origin the
PAGE was loaded from, not the origin its API calls target -- and since
the app and the API share an origin, a single cert on a single listener
satisfies both. The cert lives on the `chef-tls` volume so it survives
a rebuild.

`run_server.py` (the container's entrypoint) picks up
CERT_PATH/KEY_PATH at launch and decides plain-HTTP vs. HTTPS from their
presence -- nothing in THIS module talks to uvicorn directly.
`restart_to_apply()` re-execs run_server.py in place (same PID, so
Docker's restart policy never sees an exit) so a newly generated/
imported cert takes effect within a couple seconds, no separate manual
restart step.
"""

from __future__ import annotations

import contextlib
import datetime as _dt
import ipaddress
import json
import os
import plistlib
import sys
import threading
import time
import uuid

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from app.config import settings

TLS_DIR = os.environ.get("TLS_DIR", "/app/tls")
CERT_PATH = os.path.join(TLS_DIR, "cert.pem")
KEY_PATH = os.path.join(TLS_DIR, "key.pem")
PENDING_KEY_PATH = os.path.join(TLS_DIR, "pending_csr_key.pem")
PENDING_CSR_PATH = os.path.join(TLS_DIR, "pending.csr")
TLS_META_FILE = os.path.join(TLS_DIR, "meta.json")


# ---------------------------------------------------------------------
# metadata + shared file plumbing
# ---------------------------------------------------------------------
def _ensure_dir() -> None:
    os.makedirs(TLS_DIR, exist_ok=True)


def _atomic_write_bytes(path: str, data: bytes, mode: int | None = None) -> None:
    _ensure_dir()
    tmp = path + ".tmp"
    with open(tmp, "wb") as f:
        f.write(data)
    if mode is not None:
        os.chmod(tmp, mode)
    os.replace(tmp, path)


def _read_meta() -> dict:
    try:
        with open(TLS_META_FILE, encoding="utf-8") as f:
            return json.load(f) or {}
    except (FileNotFoundError, ValueError):
        return {}


def _write_meta(patch: dict) -> dict:
    """Merges `patch` into the existing metadata (doesn't clobber
    unrelated keys)."""
    meta = _read_meta()
    meta.update(patch)
    _atomic_write_bytes(TLS_META_FILE, json.dumps(meta).encode("utf-8"))
    return meta


def has_active_cert() -> bool:
    return os.path.isfile(CERT_PATH) and os.path.isfile(KEY_PATH)


def _write_active(key_pem: bytes, cert_pem: bytes, chain_pem: bytes | None = None) -> None:
    """Atomic-ish install: write to .tmp then os.replace, so a mid-write
    crash can never leave uvicorn (or the frontend's `serve`) pointed at
    a half-written cert or key."""
    _ensure_dir()
    full_cert = cert_pem + (b"\n" + chain_pem if chain_pem else b"")
    _atomic_write_bytes(KEY_PATH, key_pem, mode=0o600)
    _atomic_write_bytes(CERT_PATH, full_cert)


def _clear_pending() -> None:
    for p in (PENDING_KEY_PATH, PENDING_CSR_PATH):
        with contextlib.suppress(OSError):
            os.remove(p)


def mark_applied() -> None:
    """Called once at app startup (see app.main's startup hook). Whatever
    cert exists on disk right NOW is, by definition, what run_server.py
    just chose to serve for this process -- so any earlier
    'restart_required' flag is stale the moment we're actually running."""
    meta = _read_meta()
    if meta.get("restart_required"):
        _write_meta({"restart_required": False})


# ---------------------------------------------------------------------
# status
# ---------------------------------------------------------------------
def _load_cert() -> x509.Certificate:
    with open(CERT_PATH, "rb") as f:
        return x509.load_pem_x509_certificate(f.read())


def _not_before_after(cert: x509.Certificate) -> tuple[_dt.datetime, _dt.datetime]:
    # cryptography >=42 exposes *_utc properties; older versions only have the naive ones.
    nb = getattr(cert, "not_valid_before_utc", None) or cert.not_valid_before.replace(tzinfo=_dt.timezone.utc)
    na = getattr(cert, "not_valid_after_utc", None) or cert.not_valid_after.replace(tzinfo=_dt.timezone.utc)
    return nb, na


def _san_list(cert: x509.Certificate) -> list[str]:
    try:
        ext = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName)
    except x509.ExtensionNotFound:
        return []
    out = []
    for gn in ext.value:
        if isinstance(gn, x509.DNSName):
            out.append(gn.value)
        elif isinstance(gn, x509.IPAddress):
            out.append(str(gn.value))
    return out


def status() -> dict:
    """Current certificate status for Settings > Security > Certificate.
    Everything X.509 already encodes (SANs, issuer, validity) is read
    straight off the live PEM on disk, never from our own metadata -- so
    this can never report a state that isn't what's actually installed,
    even if the metadata write for some reason didn't happen/got out of
    sync."""
    meta = _read_meta()
    out = {
        "active": has_active_cert(),
        "method": meta.get("method"),
        "domain": meta.get("domain"),
        "pending_csr": os.path.isfile(PENDING_CSR_PATH),
        "restart_required": bool(meta.get("restart_required")),
        # So the UI can tell the household the EXACT right address instead
        # of guessing or hardcoding a port. These come from app/config.py,
        # which is also where run_server.py reads the ports it binds --
        # one declaration, so the reported address cannot disagree with
        # the listening one.
        "http_port": str(settings.app_port),
        "https_port": str(settings.app_https_port),
        "note": (
            "HTTPS is served once a certificate exists AND the app has (re)started to pick "
            "it up. That restart is automatic -- the app restarts itself in place a couple "
            "seconds after installing a new/imported certificate, so no separate manual step "
            "is needed; only in-flight requests at that instant are interrupted."
        ),
    }
    if not out["active"]:
        return out
    try:
        cert = _load_cert()
        not_before, not_after = _not_before_after(cert)
        now = _dt.datetime.now(_dt.timezone.utc)
        days_remaining = (not_after - now).days
        cn_attrs = cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)
        out.update(
            {
                "common_name": cn_attrs[0].value if cn_attrs else None,
                "issuer": cert.issuer.rfc4514_string(),
                "sans": _san_list(cert),
                "issued_at": int(not_before.timestamp()),
                "expires_at": int(not_after.timestamp()),
                "days_remaining": days_remaining,
                "expired": days_remaining < 0,
                "self_issued": (cert.issuer == cert.subject),
            }
        )
    except Exception as e:
        out["error"] = f"could not read the installed certificate: {e}"
    return out


# ---------------------------------------------------------------------
# iOS/iPadOS trusted-profile export
# ---------------------------------------------------------------------
def build_mobileconfig() -> bytes:
    """Packages the currently active certificate as an Apple Configuration
    Profile (.mobileconfig) so iOS/iPadOS can install it as a trusted
    root, instead of relying solely on the per-origin "click through the
    warning in Safari" flow this feature originally shipped with.

    Why this exists: clicking through Safari's own self-signed warning
    (Settings > Security's documented flow, and the WIKI's https-setup
    entry) is sufficient in ordinary Safari tabs, per-origin, and needs
    no profile at all. But the author hit two real problems on a real
    iPad that per-origin trust doesn't solve: (1) Safari's certificate
    warning can be easy to miss or dismiss without realizing a
    "continue anyway" option was even there, leaving the household stuck
    looking at Settings > General > About > Certificate Trust Settings
    for a toggle that will never appear there unless a certificate was
    actually installed as a system profile -- that screen is empty by
    design until one is; and (2) a PWA installed to the home screen (see
    B7.3) runs in standalone mode, which cannot show a per-origin
    "visit this website anyway" prompt at all -- for the installed app
    to work, the certificate has to already be trusted at the OS level
    before the PWA is ever opened. A system-trusted profile is the only
    thing that solves both.

    This does NOT replace the existing per-origin flow -- it's an
    additional, more thorough option for households that want the
    warning gone everywhere (Safari, the PWA, and any other app on the
    device) rather than once per browser tab. Format follows Apple's own
    published Configuration Profile Reference for a certificate payload:
    a `com.apple.security.root` PayloadType is what makes Settings >
    General > About > Certificate Trust Settings actually show a toggle
    for this cert after the profile is installed -- installing the
    profile alone only trusts it for chain validation, NOT for the
    "this is a trusted root" purpose Safari/WebKit needs; the household
    still has to flip that toggle by hand, which Apple deliberately
    requires (root-trust cannot be granted silently by a profile) and
    which the WIKI's iOS steps spell out.

    Built with the stdlib `plistlib` module -- no new dependency, and
    already implicitly available since the project ships on Python 3.10+
    everywhere else. Not independently verified against a real iOS
    device from this sandbox (no route to apple.com/icloud.com domains,
    and no physical device reaches here either) -- the plist structure
    and PayloadType are per Apple's own published reference, and the
    round-trip (`plistlib.loads` on the output) is unit-tested, but the
    actual "does iOS accept and offer to install this file" step is the
    author's own real-device verification, same standing limitation as
    every other iOS-facing change in this project's history.
    """
    if not has_active_cert():
        raise ValueError("no active certificate to export -- generate or import one first")
    cert = _load_cert()
    der_bytes = cert.public_bytes(serialization.Encoding.DER)
    cn_attrs = cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)
    common_name = cn_attrs[0].value if cn_attrs else "chef"
    cert_uuid = str(uuid.uuid4())
    profile_uuid = str(uuid.uuid4())
    profile = {
        "PayloadContent": [
            {
                "PayloadCertificateFileName": "chef-ca.cer",
                "PayloadContent": der_bytes,
                "PayloadDescription": "Trusts the self-signed certificate this Chef instance is currently serving.",
                "PayloadDisplayName": f"Chef certificate ({common_name})",
                "PayloadIdentifier": f"local.chef.tls.cert.{cert_uuid}",
                "PayloadType": "com.apple.security.root",
                "PayloadUUID": cert_uuid,
                "PayloadVersion": 1,
            }
        ],
        "PayloadDescription": (
            "Trusts the self-signed certificate this Chef instance generated for itself, so Safari "
            "(and the Chef PWA, if installed to the home screen) stop warning about it. After "
            "installing, go to Settings > General > About > Certificate Trust Settings and enable "
            "full trust for this certificate -- iOS requires that as a separate, deliberate step and "
            "will not do it automatically."
        ),
        "PayloadDisplayName": f"Chef self-signed certificate ({common_name})",
        "PayloadIdentifier": f"local.chef.tls.profile.{profile_uuid}",
        "PayloadOrganization": "Chef (self-hosted)",
        "PayloadRemovalDisallowed": False,
        "PayloadType": "Configuration",
        "PayloadUUID": profile_uuid,
        "PayloadVersion": 1,
    }
    return plistlib.dumps(profile, fmt=plistlib.FMT_XML)


# ---------------------------------------------------------------------
# 1. self-signed
# ---------------------------------------------------------------------
def _san_entries(hosts: list[str]) -> list[x509.GeneralName]:
    entries = []
    for h in hosts:
        try:
            entries.append(x509.IPAddress(ipaddress.ip_address(h)))
        except ValueError:
            entries.append(x509.DNSName(h))
    return entries


def generate_self_signed(hostnames: list[str], days: int = 825) -> dict:
    """Generates a fresh private key + self-signed X.509 leaf covering
    every name/IP in `hostnames` (first entry doubles as the subject CN)
    and installs it as the active cert, replacing whatever was there
    before. 825 days (~2.25y) is a reasonable default validity for a
    self-signed cert -- nothing enforces the CA/Browser Forum's public-CA
    lifetime limits on a self-signed leaf, but keeping it bounded avoids
    it silently living forever unnoticed."""
    if not hostnames:
        raise ValueError("at least one hostname or IP is required")
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, hostnames[0])])
    now = _dt.datetime.now(_dt.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - _dt.timedelta(minutes=5))
        .not_valid_after(now + _dt.timedelta(days=days))
        .add_extension(x509.SubjectAlternativeName(_san_entries(hostnames)), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    key_pem = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )
    cert_pem = cert.public_bytes(serialization.Encoding.PEM)
    _write_active(key_pem=key_pem, cert_pem=cert_pem)
    _clear_pending()
    _write_meta(
        {
            "method": "self_signed",
            "domain": hostnames[0],
            "hostnames": hostnames,
            "created_at": int(time.time()),
            "restart_required": True,
        }
    )
    restart_to_apply()
    return status()


# ---------------------------------------------------------------------
# 2. manual -- CSR generated here, signed certificate imported back
# ---------------------------------------------------------------------
def generate_csr(common_name: str, sans: list[str] | None = None) -> str:
    """Generates a NEW private key + CSR for `common_name` (plus any
    extra SAN entries) and stashes the key as 'pending' under TLS_DIR --
    it never leaves this server. Returns the CSR as PEM text for the
    household to submit to any CA (or an internal/self-signed CA for a
    LAN-only deployment). Call import_signed_certificate() with the CA's
    response to finish -- generate_self_signed() discards any pending
    CSR, since only one certificate request is "in flight" at a time."""
    common_name = (common_name or "").strip()
    if not common_name:
        raise ValueError("common_name is required")
    sans = [s.strip() for s in (sans or []) if s and s.strip()]
    if common_name not in sans:
        sans = [common_name, *sans]
    _ensure_dir()
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    csr = (
        x509.CertificateSigningRequestBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)]))
        .add_extension(x509.SubjectAlternativeName(_san_entries(sans)), critical=False)
        .sign(key, hashes.SHA256())
    )
    key_pem = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )
    csr_pem = csr.public_bytes(serialization.Encoding.PEM)
    _atomic_write_bytes(PENDING_KEY_PATH, key_pem, mode=0o600)
    _atomic_write_bytes(PENDING_CSR_PATH, csr_pem)
    return csr_pem.decode()


def import_signed_certificate(cert_pem: str | bytes, chain_pem: str | bytes | None = None) -> dict:
    """Pairs a CA-signed certificate with the pending CSR's private key
    -- validated by comparing public-key numbers, so a mismatched/wrong
    certificate is rejected with a clear error rather than silently
    installing a cert nothing has the matching key for -- and installs
    it as the active cert."""
    if not os.path.isfile(PENDING_KEY_PATH):
        raise ValueError("no pending CSR on file -- call generate_csr() first")
    cert_bytes = cert_pem.encode() if isinstance(cert_pem, str) else cert_pem
    cert = x509.load_pem_x509_certificate(cert_bytes)
    with open(PENDING_KEY_PATH, "rb") as f:
        key_pem = f.read()
    key = serialization.load_pem_private_key(key_pem, password=None)
    if cert.public_key().public_numbers() != key.public_key().public_numbers():
        raise ValueError(
            "this certificate's public key does not match the pending CSR's private key -- make "
            "sure you're importing the certificate that was actually issued against the CSR "
            "generate_csr() produced"
        )
    chain_bytes = None
    if chain_pem:
        chain_bytes = chain_pem.encode() if isinstance(chain_pem, str) else chain_pem
    _write_active(key_pem=key_pem, cert_pem=cert_bytes, chain_pem=chain_bytes)
    cn_attrs = cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)
    _clear_pending()
    _write_meta(
        {
            "method": "manual",
            "domain": cn_attrs[0].value if cn_attrs else None,
            "created_at": int(time.time()),
            "restart_required": True,
        }
    )
    restart_to_apply()
    return status()


def clear_active_cert() -> dict:
    """Removes the active cert/key -- the next (self-triggered) restart
    serves plain HTTP again."""
    for p in (CERT_PATH, KEY_PATH):
        with contextlib.suppress(OSError):
            os.remove(p)
    _write_meta({"method": None, "domain": None, "restart_required": True})
    restart_to_apply()
    return status()


def restart_to_apply(delay_s: int = 2) -> None:
    """Restarts THIS process in place (os.execv replaces the process
    image but keeps the same PID, so Docker never sees an exit and its
    restart policy never triggers) so uvicorn picks up a new/changed
    certificate. Runs after a short delay on a background thread so the
    HTTP response confirming the change reaches the browser first.
    Re-execs run_server.py itself (not uvicorn directly) so the
    plain-HTTP-vs-HTTPS decision gets freshly re-derived from whatever's
    on disk NOW, rather than reusing stale argv from container boot."""

    def _go():
        time.sleep(delay_s)
        # `-m app.run_server`, not a direct file path -- Dockerfile's CMD
        # invokes the launcher the same way (WORKDIR /app, `app` importable
        # as a top-level package), so re-exec here can't drift from how the
        # container actually starts.
        os.execv(sys.executable, [sys.executable, "-m", "app.run_server"])

    threading.Thread(target=_go, daemon=True).start()
