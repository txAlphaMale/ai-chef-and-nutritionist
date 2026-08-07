from pydantic import BaseModel, ConfigDict


class SoundRead(BaseModel):
    """One library entry. The audio is fetched separately from
    /api/sounds/{id}/audio -- a base64 blob in a list response would be
    megabytes the dropdown never plays."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    slug: str | None = None
    is_builtin: bool = False
    # "warning" | "finish" | None -- what a fresh install picks for each
    # slot. Stated by the library rather than inferred from list position,
    # which is how a 1318Hz bell became the default finish sound and an
    # urgent alarm became the default warning.
    default_for: str | None = None
    # True when the row survived but its file did not (a wiped volume, a
    # half-restored backup). Built-ins repair themselves on the next boot;
    # an upload cannot, and the UI says so rather than offering a silent
    # option.
    missing_file: bool = False
