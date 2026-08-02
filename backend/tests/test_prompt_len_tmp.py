from app.routers.inventory import RECEIPT_IMPORT_PROMPT

def test_prompt_renders_and_length():
    rendered = RECEIPT_IMPORT_PROMPT.format(content="test content", today="2026-08-02")
    print("LENGTH:", len(rendered))
    assert rendered
