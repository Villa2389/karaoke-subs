# karaoke-subs

Turn any anime episode and its Japanese subtitles into **word-by-word karaoke subtitles**:
the Japanese word order is kept, every word lights up the moment it is spoken, and content
words are replaced by a short literal English gloss, so you hear the Japanese and read what each
word means. The style follows
[gokenshadow/LearnJapaneseKaraokeSubtitles](https://github.com/gokenshadow/LearnJapaneseKaraokeSubtitles).

```
Japanese:  もう三十分も待ってるよ！
gloss:     ALREADY 30 MINUTES mo WAIT-tteru yo!
```

(Both rows are karaoke: each word is highlighted while it is spoken; a romaji row can be added.)

- **Accurate timing**: forced alignment on the audio itself (median word-boundary error ~20–25 ms
  against hand-timed karaoke), subtitles re-synced to your copy of the episode automatically.
- **Readable glosses**: the code decides the structure (which words, romaji endings, particles,
  names, numbers); an LLM only chooses the English word. Rules are written down in
  [docs/glossing-rules.md](docs/glossing-rules.md).
- **Downloads the subtitles for you**, by the series and episode in the video's file or folder
  name (no account needed).
- **Any model**: Claude through your Claude Code login, any OpenAI-compatible API (OpenAI,
  OpenRouter, Gemini, DeepSeek, Ollama, LM Studio...), the Claude API, or a local GGUF.
- **Learns with you**: words you know stop being translated (`karaoke learn`, Anki import).
- Runs locally on Windows, macOS and Linux; a GPU helps but is not required.

## Requirements

| What | Why | Install |
|---|---|---|
| [uv](https://docs.astral.sh/uv/) | installs Python and the dependencies | Windows: `winget install astral-sh.uv` · macOS/Linux: `curl -LsSf https://astral.sh/uv/install.sh \| sh` |
| [FFmpeg](https://ffmpeg.org/) | reads the audio, renders previews | Windows: `winget install Gyan.FFmpeg` · macOS: `brew install ffmpeg` · Debian/Ubuntu: `sudo apt install ffmpeg` |
| A model for the English glosses | optional (timing-only works without) | see [Choosing the model](#choosing-the-model) |

An NVIDIA GPU makes alignment faster; without one everything runs on the CPU. The first run
downloads the alignment model (~1.2 GB, torchaudio MMS) and, for glossing, the JMdict dictionary
(~10 MB).

## Install

```bash
git clone https://github.com/Villa2389/karaoke-subs.git
cd karaoke-subs
uv sync
uv run karaoke doctor
```

`doctor` checks FFmpeg, PyTorch / GPU, the dictionary and your default model. Optional extras:

```bash
uv sync --extra separation   # vocal isolation before alignment (--vocals), for music-heavy scenes
uv sync --extra anthropic    # the Claude API with an API key
uv sync --all-extras         # both (each `uv sync` removes the extras it is not given)
```

## Quick start

The easiest way is the local web interface:

```bash
uv run karaoke ui
```

It opens `http://127.0.0.1:8765`: pick the episode, the subtitles are downloaded for you (or pick
your own file), choose the glossers, press Run, and watch the log. When it finishes, render a preview video
and play it in the page. The *Words* section asks about words you have heard often.

From the command line:

```bash
uv run karaoke time "Show - 01.mkv" --gloss claude                 # subtitles downloaded automatically
uv run karaoke time "Show - 01.mkv" "Show - 01.ja.srt" --gloss claude   # or give them
uv run karaoke preview "Show - 01.mkv" "work/Show - 01/Show - 01.ja.ctc.gloss-claude.ass" --romaji
```

Outputs go to `work/<video name>/`. Load the `.ass` files in any player that supports ASS
subtitles (mpv, VLC, MPC-HC) or burn them in with `preview`.

## Getting Japanese subtitles

The program needs Japanese subtitles with the dialogue (line timing does not have to match your
video: it is re-synced). Usually they are downloaded for you (below); you can also pass your own
file, e.g. from [jimaku.cc](https://jimaku.cc). English subtitles are optional and improve word
senses and name spelling (`--en-subs`).

### Automatic download

When `time` is given no subtitle file (or `auto`), it downloads them, **no account or key
needed**, from the [Ajatt-Tools Japanese subtitle catalog](https://github.com/Ajatt-Tools/kitsunekko-mirror)
(thousands of anime series and films: volunteer uploads plus a mirror of kitsunekko.net,
updated every few hours):

- the **series is read from the video's file name**, or from its **folder name** when the file
  name only has the episode (`Dr. Stone [BD]/Episode 07.mkv`);
- the **episode** comes from the number in the file name (`[03]`, ` - 03`, `S01E03`, `第3話`, ...);
- the closest series name wins (`Dr Stone` takes *Dr. STONE*, not *Dr. STONE. STONE WARS*); a season
  `.zip` is unpacked when needed, and the file must really be Japanese (from its name or its text,
  so Chinese subtitles are never taken).

Downloads are kept in `~/.karaoke-subs/subtitles/`, so a re-run works offline. When nothing is
found online, a Japanese text subtitle track inside the video (`.mkv`) is used.

See what would be downloaded for a video:

```bash
uv run karaoke subs "D:/Anime/[SubsPlease] Sousou no Frieren - 05 (1080p).mkv"
```

Later seasons often have their own name in the catalog (*Dr. STONE. STONE WARS*): if the video is
named only "Dr. Stone S02E03", rename it after the season, or pass the subtitle file yourself.
`--names-from auto` also downloads the series' other episodes, so a name whose reading
(furigana) is only given once in the series is read correctly in every episode.

## Choosing the model

Glossing works with many models; each is a **profile** with a name. `uv run karaoke models` lists
them, `uv run karaoke models --test NAME` sends one tiny request to check it works.

| Profile | What it is | Needs |
|---|---|---|
| `haiku`, `sonnet`, `opus` (built-in) | Claude through [Claude Code](https://claude.com/claude-code) on your Claude login | `claude` on PATH, logged in; counts against your plan |
| `local` (built-in) | a GGUF model on your GPU through llama.cpp, started and stopped by the program | `llama-server`, a model file (see below) |
| `kind = "openai"` | any OpenAI-compatible API: OpenAI, OpenRouter, Gemini, DeepSeek, Groq, Mistral, Together, or a local Ollama / LM Studio / vLLM | `base_url`, `model`, usually an API key |
| `kind = "anthropic"` | the Claude API, billed per token | `uv sync --extra anthropic`, `ANTHROPIC_API_KEY` |

`--gloss claude` is Claude Code with Haiku (`--claude-model`, `--claude-effort` to change it);
`--gloss NAME` uses any profile; several at once compare them: `--gloss dict,claude,openrouter`.
`dict` is a dictionary-only baseline that needs no model.

Add your own profiles in `~/.karaoke-subs/config.toml`: copy [config.example.toml](config.example.toml),
which documents every setting. API keys never go in that file: `api_key_env` names the environment
variable that holds the key.

```toml
default = "openrouter"          # used by --gloss llm

[profiles.openrouter]
kind = "openai"
base_url = "https://openrouter.ai/api/v1"
api_key_env = "OPENROUTER_API_KEY"
model = "deepseek/deepseek-v4.1-flash"
```

Per-profile settings: `batch` (lines per request), `jobs` (requests at once; lower it on rate
limits), `context` (previous lines shown), `verify` (second look at suspicious glosses),
`json_mode` (`schema`, or `object` / `none` for servers without JSON-schema support). Answers are
cached per model in `work/<video>/gloss_cache.<model>.json`, so re-runs cost nothing.

**Local model (`local` profile).** Install [llama.cpp](https://github.com/ggml-org/llama.cpp)
(`llama-server` on PATH, or unpack a release into `~/.karaoke-subs/tools/llama-cuda/`; on NVIDIA use a
CUDA build, the Vulkan build is far slower at prompt processing) and put a model in
`~/.karaoke-subs/models/`, e.g. `Qwen3.5-9B-Q4_K_M.gguf` from `unsloth/Qwen3.5-9B-GGUF`
(5.7 GB, ~6.4 GB VRAM). Another file: `--llm-model path.gguf` or `gguf = "..."` in the profile.

## Reviewing the glosses

`--review` writes `<subs>.ctc.review.tsv` (opens in Excel / LibreOffice): only lines with a
suspicious gloss, worst first, with the timestamp, the dictionary's meanings and an empty `fix`
column.

| Flag | Meaning | Points | Example |
|---|---|---|---|
| `number` | a numeral glossed with the wrong value | 3 | 億 → BILLION (it is a hundred million) |
| `nonword` | a word that never appears in JMdict's ~100k English words | 2 | RIPT, BOOTHER |
| `unusual` | matches none of the dictionary's meanings | 1 | ひでえ → AWESOME (it means terrible) |

Write a correction in `fix` (`=` keeps the word in Japanese) and run again with `--fixes <sheet>`;
model answers are cached, so this takes seconds.

## Learning words

Every translated word is counted per episode. `uv run karaoke learn` asks about the words you
heard often (after 5 hearings, then 10, 20, ...) and the most frequent words of each series; the
ones you know are shown in romaji from then on instead of English. Your list lives in
`~/.karaoke-subs/known_words.txt` (`japanese|romaji` per line, easy to edit). Studied Anki cards can
be imported: `uv run karaoke learn --import-anki "Deck name" --anki-field Word`.

## Commands

| Command | What it does |
|---|---|
| `karaoke ui` | local web interface (127.0.0.1 only) |
| `karaoke time VIDEO [SUBS]` | timing, glossing, all outputs |
| `karaoke preview VIDEO GLOSS.ass` | MP4 with Japanese / romaji / English karaoke burned in |
| `karaoke subs VIDEO...` | download and show the subtitles `time` would use |
| `karaoke models [--test NAME]` | list or test model profiles |
| `karaoke learn` | known-word review, Anki import |
| `karaoke compare LABELS GOLD...` | score a run against a reference karaoke script, by grammar category |
| `karaoke doctor` | check the installation |

Useful `time` options (`uv run karaoke time --help` lists them all):

| Option | |
|---|---|
| `--gloss dict,claude,NAME` | glossers to run (none: timing only) |
| `--en-subs FILE\|auto` | English subtitles as context |
| `--romaji` | also write the English karaoke with romaji karaoke above it |
| `--stream "0:a:m:language:jpn"` | pick the Japanese audio track of a multi-audio release |
| `--vocals` | isolate vocals before aligning (needs the `separation` extra) |
| `--reference FILE.ass` | compare timing with a hand-timed karaoke file |
| `--out DIR`, `--tag TEXT` | output folder, suffix for output names |
| `--no-known` | translate every word, don't count words for `learn` |

Outputs in `work/<video>/`: `*.romaji.ass`, `*.japanese.ass`, `*.gloss-<model>.ass`
(`+romaji.ass` with `--romaji`), `*.report.json` (per-line alignment confidence), the review sheet
and the model caches.

## How it works

```
video ──ffmpeg──▶ 16 kHz mono WAV ─┬─▶ Silero VAD ─▶ sync the subtitles to this audio (global + per 2-min block)
                                   │
Japanese subs ─▶ clean ─▶ fugashi/UniDic tokens + spoken readings ─▶ romaji
                                   │
                                   └─▶ MMS CTC forced alignment, one window per line ─▶ word times
                                                                                          │
JMdict + LLM ─▶ one English gloss per content word ─▶ display rules ─▶ .ass with {\k} karaoke
```

| Step | Module | Notes |
|---|---|---|
| Subtitle download | `subfind.py` | Series and episode from the names, online catalog, season zips, embedded tracks; language by tag or text. |
| Audio | `audio.py` | Any media → 16 kHz mono PCM through FFmpeg. |
| Subtitle cleanup | `subs.py` | Drops speaker labels, sound tags, song lines; keeps furigana as readings and reuses them for names. |
| Sync | `sync.py` | Cross-correlates Silero VAD speech with the subtitle on/off pattern (release offsets, ads). |
| Readings | `reading.py`, `jpnum.py` | UniDic pronunciation (は→wa, 今日→kyoo), colloquial readings, numbers. |
| Alignment | `align.py` | torchaudio `MMS_FA`, `<star>` tokens at both ends of each line window. |
| Glossing | `gloss.py`, `dictionary.py` | Which tokens get English, display rules, `dict` and LLM glossers. |
| Models | `providers.py`, `llm.py`, `claude_code.py` | Model profiles, JSON-schema-constrained replies where supported. |
| Review | `review.py`, `verify.py` | Flags suspicious glosses; a second model look at flagged words only. |
| English context | `english.py` | `--en-subs`: synced and paired by time; capitalised words mark names. |
| Output | `ass_out.py`, `preview.py` | `{\k}` karaoke per token; preview rendering. |
| Vocabulary | `vocab.py` | Known words, word counts, Anki import. |
| Files | `paths.py` | User folder, dictionary download, model and tool lookup. |

### Accuracy

Timing, against gokenshadow's hand-timed *Dr. Stone* Ep 1 (1,713 word boundaries):

| Method | Median error | ≤ 50 ms | ≤ 100 ms |
|---|---|---|---|
| Splitting each line by mora count (no audio) | 88 ms | 30 % | 54 % |
| **CTC forced alignment, 30 ms lead (default)** | **24 ms** | **76 %** | **88 %** |

A second test on a film with songs (*Whisper of the Heart*) gave a 20 ms median (30–45 ms in the
songs). Vocal isolation did not improve either.

## Your files

Everything the program keeps for you is in `~/.karaoke-subs` (`KARAOKE_USER_DIR` to move it):
`config.toml`, `known_words.txt`, `words.json`, `dict/` (JMdict), `models/`, `tools/`, `subtitles/`
(downloaded subtitles) and `logs/`.

## Troubleshooting

- **`ffmpeg not found`**: install FFmpeg (see Requirements) and open a new terminal.
- **"median alignment confidence 0.10"**: the audio is probably a dub. Pick the Japanese track
  with `--stream "0:a:m:language:jpn"` (or `0:a:1`).
- **No subtitles found**: run `uv run karaoke subs VIDEO` to see the series and episode read from
  the name. Rename the file or folder to the series' romaji name (as on
  [subtitles.ajatt.top](https://subtitles.ajatt.top/)), or get the file from
  [jimaku.cc](https://jimaku.cc) and pass it: `karaoke time VIDEO SUBS`.
- **"GitHub's hourly limit ... is used up"**: the catalog allows 60 lookups an hour without an
  account; wait, or set a `GITHUB_TOKEN` environment variable.
- **`claude` not on PATH**: install Claude Code and log in once (`claude`, then `/login`), or use
  another profile.
- **PowerShell**: quote paths with spaces and brackets: `"[Group] Show - 01.mkv"`.

## Development

```bash
uv sync
uv run pytest -q
```

How each situation is glossed is specified in [docs/glossing-rules.md](docs/glossing-rules.md):
change the rule there first, then the code, and keep rules general (never tuned to one episode).
See [CONTRIBUTING.md](CONTRIBUTING.md).

## Credits and licenses

This project is released under the [MIT License](LICENSE). It downloads or uses:

- [JMdict](https://www.edrdg.org/jmdict/j_jmdict.html) (EDRDG, CC BY-SA 4.0), downloaded on first use.
- Japanese subtitles from the [Ajatt-Tools catalog](https://github.com/Ajatt-Tools/kitsunekko-mirror),
  downloaded on demand for your own episodes.
- [MMS forced aligner](https://pytorch.org/audio/stable/tutorials/forced_alignment_for_multilingual_data_tutorial.html)
  weights through torchaudio (Meta, CC BY-NC 4.0: non-commercial use).
- [Silero VAD](https://github.com/snakers4/silero-vad) (MIT), [fugashi](https://github.com/polm/fugashi)
  and [unidic-lite](https://github.com/polm/unidic-lite).

Subtitles and videos are not included: use your own copies.
