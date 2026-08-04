# Previously shipped extraction prompts

Exact copies of extraction-prompt defaults this project shipped while
`app/seed.py` was still writing a copy of each default into the
`system_prompts` table. An install first booted in that window holds one
of these texts in a row, and that row is not a household edit -- see
`app/prompt_defaults.py` for why that distinction decides which prompt
the model actually runs.

`app/prompt_defaults._HISTORICAL_SHIPPED_SHA256` records the SHA-256 of
each of these so such a row can be recognised and dropped on the next
boot. These files exist so those digests are checkable rather than
asserted: `tests/test_seed_system_prompts.py` hashes each file and
requires the result to be in that set. A wrong digest fails a test
instead of silently never matching.

Named `<prompt_key>__<commit>.txt`, where the commit is the first one
that shipped that text. Byte-exact, no trailing newline added -- the
comparison is exact, so any reformatting of these files breaks the thing
they are here to prove.

| File | Prompt key | Chars | Shipped from | Superseded by |
|---|---|---|---|---|
| `recipe_import__1660aa3.txt` | `recipe_import` | 4728 | `1660aa3` | `abc621d` (reverted, measured worse than what it replaced) |
| `receipt_import__1fd5b77.txt` | `receipt_import` | 2676 | `1fd5b77` | `759bb06` |
| `vision_intake__1fd5b77.txt` | `vision_intake` | 763 | `1fd5b77` | `759bb06` |

`recipe_modify` has no entry: its text never changed inside the seeding
window, so the current default is the only value a seeded row can hold.

The set is closed. Seeding is gone, so no future default can end up in a
row without a household saving it, and nothing new belongs in this
directory.
