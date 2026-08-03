"""Tests for backlog B15.1's in-app HTTPS certificate management
(app.services.tls_service and app.routers.tls), ported from the sibling
Fiduciary project's own tls.py at the author's direction (2026-08-01,
after the barcode scanner and Dining Out's geolocation both failed on an
iPad with a "needs HTTPS" error).

Every test here monkeypatches TLS_DIR/CERT_PATH/etc. to a pytest
tmp_path (module-level constants computed once at import time from an
env var, so tests patch the module's own attributes directly rather
than the environment) and monkeypatches restart_to_apply() to a no-op
-- the real implementation spawns a background thread that calls
os.execv() to replace the current process, which would kill the test
run itself if left real. Self-signed and manual-CSR/import are pure
`cryptography` operations with no network calls, exactly like
Fiduciary's own tls.py, so this suite covers them for real rather than
mocking around them."""

from __future__ import annotations

import datetime as dt
import os
import pathlib
import plistlib

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.x509.oid import NameOID

from app.routers import tls as tls_router
from app.schemas.tls import CsrRequest, ImportCertRequest, SelfSignedRequest
from app.services import tls_service as ts


@pytest.fixture(autouse=True)
def _tls_sandbox(tmp_path, monkeypatch):
    """Redirects every TLS path constant to a throwaway temp dir, and
    neuters the real self-restart so tests can call the mutating
    functions safely."""
    tls_dir = str(tmp_path / "tls")
    monkeypatch.setattr(ts, "TLS_DIR", tls_dir)
    monkeypatch.setattr(ts, "CERT_PATH", os.path.join(tls_dir, "cert.pem"))
    monkeypatch.setattr(ts, "KEY_PATH", os.path.join(tls_dir, "key.pem"))
    monkeypatch.setattr(ts, "PENDING_KEY_PATH", os.path.join(tls_dir, "pending_csr_key.pem"))
    monkeypatch.setattr(ts, "PENDING_CSR_PATH", os.path.join(tls_dir, "pending.csr"))
    monkeypatch.setattr(ts, "TLS_META_FILE", os.path.join(tls_dir, "meta.json"))
    monkeypatch.setattr(ts, "restart_to_apply", lambda *a, **k: None)
    yield


def _sign_csr_as_ca(csr_pem: str, days: int = 30) -> bytes:
    """Test helper -- acts as a minimal CA, signing a CSR with a
    throwaway CA key so import_signed_certificate() has something
    genuinely matching the CSR's own public key to import. Mirrors what
    a real external CA (or an internal one) would hand back."""
    from cryptography.hazmat.primitives.asymmetric import rsa

    csr = x509.load_pem_x509_csr(csr_pem.encode())
    ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Test CA")])
    now = dt.datetime.now(dt.timezone.utc)
    builder = (
        x509.CertificateBuilder()
        .subject_name(csr.subject)
        .issuer_name(ca_name)
        .public_key(csr.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(minutes=5))
        .not_valid_after(now + dt.timedelta(days=days))
    )
    san_ext = csr.extensions.get_extension_for_class(x509.SubjectAlternativeName)
    builder = builder.add_extension(san_ext.value, critical=False)
    cert = builder.sign(ca_key, hashes.SHA256())
    return cert.public_bytes(serialization.Encoding.PEM)


# --- has_active_cert / status (no cert yet) -----------------------------


def test_has_active_cert_false_initially():
    assert ts.has_active_cert() is False


def test_status_reports_inactive_with_no_extra_fields():
    st = ts.status()
    assert st["active"] is False
    assert st["method"] is None
    assert "common_name" not in st  # only added once a cert is actually active


# --- generate_self_signed ------------------------------------------------


def test_generate_self_signed_creates_valid_cert_and_key():
    result = ts.generate_self_signed(["192.168.1.50", "chef.lan"])
    assert result["active"] is True
    assert ts.has_active_cert() is True
    assert os.path.isfile(ts.CERT_PATH)
    assert os.path.isfile(ts.KEY_PATH)
    # Key file should not be world/group readable.
    assert oct(os.stat(ts.KEY_PATH).st_mode)[-3:] == "600"


def test_generate_self_signed_covers_requested_sans():
    ts.generate_self_signed(["192.168.1.50", "chef.lan"])
    st = ts.status()
    assert st["common_name"] == "192.168.1.50"
    assert set(st["sans"]) == {"192.168.1.50", "chef.lan"}
    assert st["self_issued"] is True
    assert st["expired"] is False
    assert st["method"] == "self_signed"
    assert st["domain"] == "192.168.1.50"


def test_generate_self_signed_requires_at_least_one_hostname():
    with pytest.raises(ValueError):
        ts.generate_self_signed([])


def test_generate_self_signed_replaces_a_previous_cert():
    ts.generate_self_signed(["10.0.0.1"])
    first_cert = pathlib.Path(ts.CERT_PATH).read_bytes()
    ts.generate_self_signed(["10.0.0.2"])
    second_cert = pathlib.Path(ts.CERT_PATH).read_bytes()
    assert first_cert != second_cert
    assert ts.status()["common_name"] == "10.0.0.2"


# --- generate_csr / import_signed_certificate -----------------------------


def test_generate_csr_produces_a_real_csr_pem():
    csr_pem = ts.generate_csr("chef.example.com", ["chef.example.com", "192.168.1.50"])
    assert "BEGIN CERTIFICATE REQUEST" in csr_pem
    assert os.path.isfile(ts.PENDING_CSR_PATH)
    assert os.path.isfile(ts.PENDING_KEY_PATH)
    csr = x509.load_pem_x509_csr(csr_pem.encode())
    cn = csr.subject.get_attributes_for_oid(NameOID.COMMON_NAME)[0].value
    assert cn == "chef.example.com"


def test_generate_csr_requires_common_name():
    with pytest.raises(ValueError):
        ts.generate_csr("")


def test_import_signed_certificate_round_trip():
    csr_pem = ts.generate_csr("chef.example.com", ["chef.example.com"])
    signed_cert_pem = _sign_csr_as_ca(csr_pem)
    result = ts.import_signed_certificate(signed_cert_pem)
    assert result["active"] is True
    assert result["method"] == "manual"
    assert result["self_issued"] is False  # issued by the test "CA", not self-signed
    assert not os.path.isfile(ts.PENDING_CSR_PATH)  # pending files cleared on success
    assert not os.path.isfile(ts.PENDING_KEY_PATH)


def test_import_signed_certificate_rejects_mismatched_key():
    ts.generate_csr("chef.example.com", ["chef.example.com"])
    # A completely unrelated self-signed cert -- not derived from the pending CSR at all.
    ts.generate_csr("someone-else.example.com")  # overwrites the pending CSR...
    # ...so sign a cert against a THIRD, never-pending key instead, guaranteeing a mismatch.
    from cryptography.hazmat.primitives.asymmetric import rsa

    unrelated_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "unrelated")])
    now = dt.datetime.now(dt.timezone.utc)
    unrelated_cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(unrelated_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + dt.timedelta(days=1))
        .sign(unrelated_key, hashes.SHA256())
    )
    unrelated_cert_pem = unrelated_cert.public_bytes(serialization.Encoding.PEM)
    with pytest.raises(ValueError, match="does not match"):
        ts.import_signed_certificate(unrelated_cert_pem)


def test_import_signed_certificate_requires_a_pending_csr():
    with pytest.raises(ValueError, match="no pending CSR"):
        ts.import_signed_certificate(b"not a real cert")


# --- clear_active_cert / mark_applied --------------------------------------


def test_clear_active_cert_removes_files_and_reverts_status():
    ts.generate_self_signed(["10.0.0.1"])
    assert ts.has_active_cert() is True
    result = ts.clear_active_cert()
    assert result["active"] is False
    assert ts.has_active_cert() is False


def test_mark_applied_clears_stale_restart_flag():
    ts.generate_self_signed(["10.0.0.1"])
    assert ts.status()["restart_required"] is True
    ts.mark_applied()
    assert ts.status()["restart_required"] is False


# --- router functions (called directly, same pattern as test_barcode_lookup.py) --


def test_router_self_signed_endpoint_rejects_empty_hostnames():
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc_info:
        tls_router.tls_self_signed(SelfSignedRequest(hostnames=[]))
    assert exc_info.value.status_code == 400


def test_router_self_signed_endpoint_installs_a_cert():
    result = tls_router.tls_self_signed(SelfSignedRequest(hostnames=["10.0.0.5"]))
    assert result["ok"] is True
    assert result["status"]["active"] is True


def test_router_csr_and_import_round_trip():
    csr_result = tls_router.tls_csr(CsrRequest(common_name="chef.example.com", sans=[]))
    assert csr_result["ok"] is True
    signed_pem = _sign_csr_as_ca(csr_result["csr_pem"]).decode()
    import_result = tls_router.tls_import_cert(ImportCertRequest(cert_pem=signed_pem))
    assert import_result["ok"] is True
    assert import_result["status"]["method"] == "manual"


def test_router_clear_endpoint():
    tls_router.tls_self_signed(SelfSignedRequest(hostnames=["10.0.0.5"]))
    result = tls_router.tls_clear()
    assert result["status"]["active"] is False


def test_router_status_endpoint_matches_service_status():
    tls_router.tls_self_signed(SelfSignedRequest(hostnames=["10.0.0.5"]))
    assert tls_router.tls_status() == ts.status()


# --- build_mobileconfig (author-reported 2026-08-03, iOS Certificate Trust Settings) --


def test_build_mobileconfig_requires_an_active_cert():
    with pytest.raises(ValueError, match="no active certificate"):
        ts.build_mobileconfig()


def test_build_mobileconfig_is_a_valid_plist_with_the_root_cert_payload():
    ts.generate_self_signed(["10.0.0.9", "chef.lan"])
    raw = ts.build_mobileconfig()
    profile = plistlib.loads(raw)
    assert profile["PayloadType"] == "Configuration"
    assert profile["PayloadRemovalDisallowed"] is False
    assert len(profile["PayloadContent"]) == 1
    cert_payload = profile["PayloadContent"][0]
    assert cert_payload["PayloadType"] == "com.apple.security.root"
    assert "chef.lan" in cert_payload["PayloadDisplayName"] or "10.0.0.9" in cert_payload["PayloadDisplayName"]


def test_build_mobileconfig_embeds_the_real_active_cert_der_bytes():
    ts.generate_self_signed(["10.0.0.9"])
    with open(ts.CERT_PATH, "rb") as f:
        expected_der = x509.load_pem_x509_certificate(f.read()).public_bytes(serialization.Encoding.DER)
    profile = plistlib.loads(ts.build_mobileconfig())
    assert profile["PayloadContent"][0]["PayloadContent"] == expected_der


def test_build_mobileconfig_uuids_are_unique_per_call():
    ts.generate_self_signed(["10.0.0.9"])
    first = plistlib.loads(ts.build_mobileconfig())
    second = plistlib.loads(ts.build_mobileconfig())
    assert first["PayloadUUID"] != second["PayloadUUID"]
    assert first["PayloadContent"][0]["PayloadUUID"] != second["PayloadContent"][0]["PayloadUUID"]


def test_router_mobileconfig_endpoint_returns_installable_profile():
    tls_router.tls_self_signed(SelfSignedRequest(hostnames=["10.0.0.9"]))
    response = tls_router.tls_mobileconfig()
    assert response.media_type == "application/x-apple-aspen-config"
    assert 'filename="chef-ca.mobileconfig"' in response.headers["content-disposition"]
    profile = plistlib.loads(response.body)
    assert profile["PayloadContent"][0]["PayloadType"] == "com.apple.security.root"


def test_router_mobileconfig_endpoint_400s_with_no_active_cert():
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc_info:
        tls_router.tls_mobileconfig()
    assert exc_info.value.status_code == 400
