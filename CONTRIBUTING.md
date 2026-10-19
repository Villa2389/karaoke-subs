# Contributing

Thanks for helping. Bug reports with the command you ran and its output are the most useful
thing; for a wrong gloss, include the Japanese line, what was shown and what you expected.

## Setup

```bash
uv sync --all-extras
uv run pytest -q
```

Tests need no GPU, model, dictionary or media files. A real run needs FFmpeg (`uv run karaoke doctor`).

## How glossing changes are made

[docs/glossing-rules.md](docs/glossing-rules.md) is the specification. A change in what the viewer
sees starts there:

1. Describe the situation as a **general rule** (a part of speech, a grammar pattern, a kind of
   word), never as a fix for one episode or one word.
2. Implement it in code (`gloss.py`, `reading.py`, ...) or in the model prompt, and add a test with
   a short made-up or quoted line.
3. Measure: `karaoke time ... --gloss NAME` writes `*.labels.json`; `karaoke compare` scores it
   against a reference karaoke script by grammar category. Word-choice differences are mostly
   synonyms; look at categories, not single words.

## Style

- Match the surrounding code: short functions, docstrings that say *why*, type hints.
- Keep the core dependency-free where it can be (the web UI is stdlib only).
- Platform-specific code goes behind a check (`os.name`, `sys.platform`) with a fallback, and file
  locations go through `karaoke/paths.py`.
- Do not commit subtitles, videos, model files or the dictionary.
