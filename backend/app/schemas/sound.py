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
    # True when the row survived but its file did not (a wiped volume, a
    # half-restored backup). Built-ins repair themselves on the next boot;
    # an upload cannot, and the UI says so rather than offering a silent
    # option.
    missing_file: bool = False
