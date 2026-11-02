# Long Movie — Recap Style Prompts

Captured from the user's message on 2026-10-30.

| File | Style | Output |
|---|---|---|
| `style_1_recap.txt` | Recap with narration + selected direct dialogue | `[N]` + `[D]` lines |
| `style_2_dub.txt` | Full dialogue translation for a dub | `[D]` lines only |

## Notes

- `style_1_recap.txt` is saved verbatim except for one Telegram timestamp header
  (`[10/30/2026 10:20 PM] Omega:`) that was removed from the bullet list.
- The numbers in style 1 (`05:00`, `03:00–03:30`, `03:15`, `1008`, `1085`,
  10 parts of about 30 seconds) are **example values for a 5-minute sample**.
  At runtime the app must replace them with the real source duration, target
  duration, syllable budget and part count.
- Style 1 requires about one third direct `[D]` lines and about one direct line
  every 20–40 seconds. Keep that behaviour.
- Style 2 is a full dub: every spoken dialogue line, no narration.
- Style 2's "Written for the ear" section mentions `[N]` and `[D]`, but its own
  output format only produces `[D]` lines. This is preserved from the source.
