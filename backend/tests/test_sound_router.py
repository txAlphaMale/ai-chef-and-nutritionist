"""The sound library over HTTP, against a live app.

The one rule worth a test of its own: a built-in cannot be deleted. Every
timer's warning and finish sound is chosen from this list, so a library
the household can empty is a timer that goes off in silence.
"""

import pytest
from fastapi.testclient import TestClient

from app.database import get_db
from app.main import app
from app.seed import seed_builtin_sounds
from app.services import sound_service


@pytest.fixture
def client(db_session, tmp_path, monkeypatch):
    monkeypatch.setattr(sound_service, "SOUNDS_DIR", str(tmp_path / "sounds"))
    seed_builtin_sounds(db_session)
    app.dependency_overrides[get_db] = lambda: db_session
    yield TestClient(app)
    app.dependency_overrides.pop(get_db, None)


def test_the_library_lists_builtins_first(client):
    body = client.get("/api/sounds").json()
    assert len(body) == len(sound_service.BUILTIN_SOUNDS)
    assert all(s["is_builtin"] for s in body)
    assert all(not s["missing_file"] for s in body)


def test_the_audio_is_served_with_a_type_a_browser_can_play(client):
    sound_id = client.get("/api/sounds").json()[0]["id"]
    response = client.get(f"/api/sounds/{sound_id}/audio")
    assert response.status_code == 200
    assert response.headers["content-type"] == "audio/wav"
    assert response.content[:4] == b"RIFF"


def test_a_builtin_cannot_be_deleted(client):
    sound_id = client.get("/api/sounds").json()[0]["id"]
    response = client.delete(f"/api/sounds/{sound_id}")
    assert response.status_code == 400
    assert "Built-in" in response.json()["detail"]
    assert len(client.get("/api/sounds").json()) == len(sound_service.BUILTIN_SOUNDS)


def test_an_upload_round_trips_and_can_be_deleted(client):
    created = client.post(
        "/api/sounds",
        data={"name": "Kitchen bell"},
        files={"file": ("bell.wav", b"RIFF0000WAVEfmt fake", "audio/wav")},
    )
    assert created.status_code == 201
    body = created.json()
    assert body["name"] == "Kitchen bell" and body["is_builtin"] is False

    assert client.get(f"/api/sounds/{body['id']}/audio").status_code == 200
    assert client.delete(f"/api/sounds/{body['id']}").status_code == 204
    assert len(client.get("/api/sounds").json()) == len(sound_service.BUILTIN_SOUNDS)


def test_an_upload_with_no_name_falls_back_to_the_filename(client):
    body = client.post(
        "/api/sounds",
        data={"name": "   "},
        files={"file": ("my gong.wav", b"RIFF0000WAVEfmt fake", "audio/wav")},
    ).json()
    assert body["name"] == "my gong"


def test_an_unsupported_upload_is_a_400_not_a_500(client):
    response = client.post(
        "/api/sounds",
        data={"name": "x"},
        files={"file": ("x.exe", b"MZ", "application/x-msdownload")},
    )
    assert response.status_code == 400


def test_a_missing_row_and_a_missing_file_are_both_404(client, db_session):
    from app.models import SoundFile

    assert client.get("/api/sounds/99999/audio").status_code == 404
    assert client.delete("/api/sounds/99999").status_code == 404

    orphan = SoundFile(name="Ghost", storage_path="/gone/ghost.wav", slug=None, is_builtin=False)
    db_session.add(orphan)
    db_session.commit()
    assert client.get(f"/api/sounds/{orphan.id}/audio").status_code == 404
    # ...and the list says so rather than offering a silent option.
    listed = next(s for s in client.get("/api/sounds").json() if s["name"] == "Ghost")
    assert listed["missing_file"] is True
