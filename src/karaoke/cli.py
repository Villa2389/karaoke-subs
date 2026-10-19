"""Command line: karaoke time | preview | subs | learn | compare | models | doctor | ui."""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from collections import Counter
from pathlib import Path
from statistics import median

from .align import align_all
from .ass_out import write_ass
from .audio import extract_wav, load_wav
from .evaluate import evaluate, gloss_agreement
from .gloss import NAME, display_tokens, is_katakana, not_a_name, outvote_names, english_titles, harmonize, mark_potential, merge_compounds, merge_kana_names
from .reading import tokenize
from .subs import load_lines
from .sync import apply_offset, estimate_offset

# Median CTC confidence: Japanese speech ~0.3 (Dr. Stone); an English dub of the same film 0.10.
LOW_CTC_SCORE = 0.15


def resolve_glossers(args: argparse.Namespace) -> list[tuple[str, object]]:
    """--gloss entries as (output name, model Profile or None for dict). `llm` is the --model
    profile (default: the config's `default`), `claude` is Claude Code with the --claude-*
    options, any other name is a profile from the config. Fails before the long timing work
    when a profile is unknown or its API key is not set."""
    from dataclasses import replace

    from .providers import BUILT_IN, DEFAULT_PROFILE, get_profile

    methods = [g for g in (args.gloss or "").split(",") if g]
    if args.review and all(m == "dict" for m in methods):
        methods.append("llm")
    config = Path(args.config) if args.config else None
    glossers = []
    for m in dict.fromkeys(methods):
        if m == "dict":
            glossers.append(("dict", None))
            continue
        if m == "claude":
            p = replace(BUILT_IN["haiku"], name="claude", model=args.claude_model, effort=args.claude_effort,
                        batch=args.claude_batch, jobs=args.claude_jobs)
            name = "claude"
        else:
            try:
                p = get_profile(args.model if m == "llm" else m, config)
            except ValueError as e:
                sys.exit(f"--gloss {m}: {e}")
            # The local Qwen keeps its old file name (gloss-llm); other models are named by profile.
            name = "llm" if m == "llm" and p.name == DEFAULT_PROFILE else p.name
            p = replace(p, batch=args.batch or p.batch, jobs=args.jobs or p.jobs)
        if args.llm_model and p.kind == "llama-server":
            p = replace(p, gguf=args.llm_model)
        if key := p.missing_key():
            sys.exit(f"--gloss {m}: profile {p.name!r} needs the environment variable {key} (your API key)")
        if p.kind == "llama-server" and not p.model_path().is_file():
            sys.exit(f"--gloss {m}: profile {p.name!r}: model file not found: {p.model_path()} "
                     "(download it, set gguf in the config or --llm-model, or use another profile: karaoke models)")
        glossers.append((name, p))
    return glossers


def resolve_subtitles(args: argparse.Namespace, media: Path, out: Path) -> None:
    """Fill in args.subs (and --names-from given as `auto`) from the online catalog, else from a
    subtitle track inside the video (subfind.py). Exits with what to do when nothing is found."""
    from .subfind import CatalogError, extract_embedded, find_online, series_online

    if args.subs in (None, "auto"):
        found, problem = None, ""
        try:
            found = find_online(media)
            if found is None:
                problem = f"the online catalog has no subtitles for {media.name}"
        except CatalogError as e:
            problem = str(e)
        if found is None:
            found = extract_embedded(media, out)
        if found is None:
            sys.exit(f"no Japanese subtitles found: {problem}, and none inside the video. Run `karaoke subs VIDEO` "
                     "to see the series and episode read from the name, or give the file: karaoke time VIDEO SUBS")
        if problem:
            print(f"online: {problem}")
        print(f"subs: {found}")
        # Outputs are named after the subtitles: <video>.ja.ctc.gloss-*.ass, not the download's name.
        local = out / f"{media.stem}.ja{found.path.suffix}"
        out.mkdir(parents=True, exist_ok=True)
        if found.path.resolve() != local.resolve():
            shutil.copyfile(found.path, local)
        args.subs = str(local)
    if args.names_from == ["auto"]:
        try:
            args.names_from = [str(p) for p in series_online(media)]
        except CatalogError as e:
            print(f"names from: {e}")
            args.names_from = []
        print(f"names from: {len(args.names_from)} other episodes' subtitles")


def cmd_time(args: argparse.Namespace) -> None:
    glossers = resolve_glossers(args)
    media = Path(args.media)
    if not media.is_file():
        sys.exit(f"video/audio file not found: {media}")
    out = Path(args.out or Path("work") / media.stem)
    resolve_subtitles(args, media, out)
    subs = Path(args.subs)
    if not subs.is_file():
        sys.exit(f"subtitles not found: {subs}")
    audio = load_wav(extract_wav(media, out / "audio16k.wav", args.stream))
    if args.vocals:
        from .separate import isolate_vocals
        # Sync uses the full mix (VAD is robust to music); alignment uses vocals only.
        vocals = isolate_vocals(media, out)
        align_audio = load_wav(extract_wav(vocals, out / "vocals16k.wav", "0:a:0"))
        print(f"vocals: {vocals.name}")
    else:
        align_audio = audio
    lines = load_lines(subs, [Path(p) for p in args.names_from or []])

    if args.no_sync:
        print("sync: skipped")
    else:
        sync = estimate_offset(audio, lines)
        apply_offset(lines, sync)
        print(f"sync: global offset {sync.global_offset:+.2f}s, blocks "
              + " ".join(f"{o:+.2f}" for _, o in sync.block_offsets))

    token_lists = [tokenize(ln) for ln in lines]
    keep = [i for i, toks in enumerate(token_lists) if toks]
    lines = [lines[i] for i in keep]
    token_lists = [token_lists[i] for i in keep]

    aligned = align_all(align_audio, lines, token_lists, args.method, args.min_score, args.lead)
    methods = Counter(al.method for al in aligned)
    print(f"aligned {len(aligned)} lines: " + ", ".join(f"{k}={v}" for k, v in methods.items()))
    scores = [al.score for al in aligned if al.method == "ctc"]
    if len(scores) >= 20 and median(scores) < LOW_CTC_SCORE:
        print(f"WARNING: median alignment confidence {median(scores):.2f} (Japanese audio gives ~0.3): "
              "the audio may not be the Japanese track (a dub?) or not match these subtitles")

    tag = args.method + ("_vocals" if args.vocals else "") + (args.tag or "")
    for mode in ("romaji", "japanese"):
        p = write_ass(aligned, out / f"{subs.stem}.{tag}.{mode}.ass", mode)
        print(f"wrote {p}")

    report = [{"line": al.line.index, "start": round(al.line.start, 2), "text": al.line.text,
               "method": al.method, "score": None if al.score != al.score else round(al.score, 3),
               "tokens": [[t.surface, t.romaji, round(t.t_start, 3), round(t.t_end, 3)] for t in al.tokens]}
              for al in aligned]
    (out / f"{subs.stem}.{tag}.report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")

    english: list[str] = []
    if args.en_subs:
        from .english import load_english, mark_names, pair, sync_english
        en_lines = load_english(Path(args.en_subs))
        offset = sync_english(audio, en_lines)
        english = pair(aligned, en_lines)
        print(f"english: {len(en_lines)} lines, offset {offset:+.2f}s, paired with "
              f"{sum(1 for e in english if e)}/{len(aligned)} Japanese lines, "
              f"{mark_names(aligned, english)} name tokens")

    if glossers:
        run_glossing(glossers, aligned, args, out, subs, tag, english)

    if args.reference:
        for prefix in (None, "ctc", "proportional"):
            res = evaluate(aligned, Path(args.reference), prefix)
            if res["n"]:
                print(f"eval [{prefix or 'all'}]: " + json.dumps({k: round(v, 3) for k, v in res.items()}))


def run_glossing(glossers: list[tuple[str, object]], aligned, args, out: Path, subs: Path, tag: str,
                 english: list[str]) -> None:
    from .dictionary import Dictionary
    from .review import apply_fixes, build_rows, load_fixes, write_review

    d = Dictionary()
    known = stats = None
    if not args.no_known:
        from .vocab import open_user
        known, stats = open_user(Path(args.user_dir) if args.user_dir else None)
    for al, tokens in zip(aligned, merge_kana_names([al.tokens for al in aligned])):
        al.tokens = merge_compounds(tokens, d.is_compound)
    for al in aligned:
        mark_potential(al.tokens, d.is_word, d.is_common)
        for t in al.tokens:  # subtitle furigana over a known word (洗濯物, 私達): a word, not a name
            if t.ruby and t.name and not t.spelling and d.has_reading(t.surface, t.lemma_kana):
                t.name = False
            # UniDic calls some katakana words names (カゲキ, ヤダ): a dictionary word the English
            # subs don't spell as a name goes to the glosser, which can still answer "@" (rule 1.5b).
            elif t.name and not t.spelling and is_katakana(t.surface) and d.is_word(t.surface):
                t.name = False
            elif t.name and not t.spelling and not_a_name(t):  # フンッ, 甘っ
                t.name = False
    for method, profile in glossers:
        rows = None
        if profile is None:
            from .gloss import dict_glosses
            glosses = [dict_glosses(al.tokens, d) for al in aligned]
        else:
            from .providers import make_client
            client = make_client(profile)
            try:
                glosses = run_llm(method, profile, client, aligned, args, out, d, english)
                # Rows describe the FIRST pass; the verifier's decisions are attached to them.
                rows = build_rows(aligned, glosses, d)
                if profile.verify and not args.no_verify:
                    from .verify import Verifier
                    verifier = Verifier(client, d, out / f"verify_cache.{profile.cache_id}.json")
                    verifier.english = dict(enumerate(english))
                    decisions = verifier.run(aligned, glosses, rows)
                    by_key = {(x.line, x.token): x for x in decisions}
                    for r in rows:
                        if (x := by_key.get((r.line, r.token))) is not None:
                            r.verified, r.reason = x.after, x.analysis
                    changed = sum(x.after != x.before for x in decisions)
                    print(f"  verifier: checked {len(decisions)} words, changed {changed}")
            finally:
                client.close()
        if profile is not None:
            n = english_titles([al.tokens for al in aligned], glosses, english, d)
            if n:
                print(f"  titles translated (English writes them as names): {n}")
            for al, g in zip(aligned, glosses):  # shouts, single kana, verbs: never names (rule 1.5b)
                for i, t in enumerate(al.tokens):
                    if g.get(i) == NAME and (t.pos not in ("名詞", "代名詞") or not_a_name(t)):
                        g[i] = ""
            n = outvote_names([al.tokens for al in aligned], glosses)
            if n:
                print(f"  names outvoted by the word's translation elsewhere: {n}")
            # Names are consistent within an episode: marked once means a name everywhere, when
            # code found it (subtitles, UniDic, English subs) or the word is not in the dictionary.
            # The glosser calling a dictionary word a name (花 Hana) holds for that line only:
            # elsewhere 花 may be a flower.
            names = {t.surface for al, g in zip(aligned, glosses) for i, t in enumerate(al.tokens)
                     if t.name or (g.get(i) == NAME and not d.is_word(t.surface))}
            for al in aligned:
                for t in al.tokens:
                    if t.surface in names and t.pos in ("名詞", "代名詞"):
                        t.name = True
        n = harmonize([al.tokens for al in aligned], glosses)
        print(f"  one gloss per word: changed {n}")
        if profile is not None and args.fixes:
            n = apply_fixes(aligned, glosses, load_fixes(Path(args.fixes)))
            print(f"  fixes: applied {n} from {args.fixes}")
        if known is not None:
            from .vocab import blank_known
            lines = [al.tokens for al in aligned]
            n = stats.record(Path(args.media).stem, lines, [al.line.text for al in aligned], glosses, known)
            print(f"  vocabulary: {n} words counted, {blank_known(lines, glosses, known)} known words kept in romaji")
        labels = [display_tokens(al.tokens, g) for al, g in zip(aligned, glosses)]
        p = write_ass(aligned, out / f"{subs.stem}.{tag}.gloss-{method}.ass", "gloss", labels)
        print(f"wrote {p}")
        # What each token shows, for comparing with a reference script (`karaoke compare`).
        p.with_suffix(".labels.json").write_text(json.dumps(
            [{"japanese": al.line.text, "show": lab,
              "tokens": [[t.surface, t.pos, t.pos2, t.base or t.lemma] for t in al.tokens]}
             for al, lab in zip(aligned, labels)], ensure_ascii=False), encoding="utf-8")
        if args.romaji:
            from .preview import combine
            both = combine(p, None, p.with_name(p.stem + "+romaji.ass"), out / f"{subs.stem}.{tag}.romaji.ass")
            print(f"wrote {both}")
        if args.reference:
            res = gloss_agreement(aligned, labels, Path(args.reference))
            print(f"gloss [{method}] vs reference: " + json.dumps({k: round(v, 3) for k, v in res.items()}))
        if args.review and rows is not None:
            path = out / f"{subs.stem}.{tag}.review{'' if method == 'llm' else '-' + method}.tsv"
            n_lines, n_words, path = write_review(rows, path, args.review_min)
            counts = Counter(r.verdict for r in rows)
            print(f"review: {dict(counts)}; {n_lines} lines / {n_words} words -> {path}")


def run_llm(name: str, p, client, aligned, args, out: Path, d, english: list[str]) -> list[dict[int, str]]:
    """Gloss dict (token index -> gloss, "" = keep Japanese, "@" = name) for every line, from
    one model profile: requests of p.batch lines with the p.context lines before as context,
    p.jobs at a time."""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    from .gloss import LLMGlosser
    from .providers import usage_text

    glosser = LLMGlosser(client, d, out / f"gloss_cache.{p.cache_id}.json")
    glosser.english = dict(enumerate(english))
    glosser.context_lines = p.context
    if args.glossary:
        from .gloss import build_glossary
        t1 = time.time()
        glosser.glossary = build_glossary(
            client, d, [(al.line.text, al.tokens, english[n] if n < len(english) else "") for n, al in enumerate(aligned)],
            out / f"glossary_cache.{p.cache_id}.json", jobs=p.jobs)
        print(f"  {name} glossary: {len(glosser.glossary)} words ({time.time() - t1:.0f}s)", flush=True)
    texts = [al.line.text for al in aligned]
    starts = list(range(0, len(aligned), p.batch))

    def chunk(b: int) -> list[dict[int, str]]:
        part = aligned[b:b + p.batch]
        return glosser.gloss_batch([(b + j, al.line.text, al.tokens) for j, al in enumerate(part)],
                                   texts[max(0, b - p.context):b])

    results: dict[int, list[dict[int, str]]] = {}
    t0 = time.time()
    every = max(1, len(starts) // 10)
    with ThreadPoolExecutor(max_workers=p.jobs) as pool:
        futures = {pool.submit(chunk, b): b for b in starts}
        for f in as_completed(futures):
            results[futures[f]] = f.result()
            if len(results) % every == 0 or len(results) == len(starts):
                print(f"  {name}: {len(results)}/{len(starts)} requests ({time.time() - t0:.0f}s)", flush=True)
    print(f"  {name} glosses ({p.kind} {p.model or p.cache_id}): {glosser.stats}; {usage_text(client)}")
    return [g for b in starts for g in results[b]]


def main(argv: list[str] | None = None) -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser(prog="karaoke")
    sub = p.add_subparsers(dest="cmd", required=True)
    t = sub.add_parser("time", help="word-level karaoke timing for one episode")
    t.add_argument("media", help="video or audio file")
    t.add_argument("subs", nargs="?", default="auto",
                   help="Japanese .srt/.ass (line boundaries + text); left out or `auto`: downloaded from "
                        "an online catalog by the series and episode in the video's file or folder name "
                        "(no account needed), else a subtitle track inside the video")
    t.add_argument("--method", choices=["ctc", "proportional"], default="ctc")
    t.add_argument("--min-score", type=float, default=0.0,
                   help="CTC confidence below which a line falls back to proportional timing "
                        "(0 = always trust CTC; on Dr. Stone Ep 1 fallbacks were worse)")
    t.add_argument("--lead", type=float, default=0.03,
                   help="seconds to start each highlighted syllable early")
    t.add_argument("--stream", default="0:a:0", help='ffmpeg -map spec, e.g. "0:a:m:language:jpn"')
    t.add_argument("--no-sync", action="store_true")
    t.add_argument("--vocals", action="store_true",
                   help="isolate vocals (MelBand RoFormer) before aligning; needs the separation extra")
    t.add_argument("--reference", help="hand-timed karaoke .ass to evaluate against")
    t.add_argument("--names-from", nargs="+", metavar="SUBS",
                   help="other episodes' Japanese subtitles: name readings (furigana) given there count here too; "
                        "`auto`: the other episodes' subtitles, downloaded")
    t.add_argument("--out", help="output folder (default work/<media stem>)")
    t.add_argument("--tag", help="suffix for output file names")
    t.add_argument("--gloss", help="comma list of glossers to run: dict, llm (the --model profile), "
                        "claude (Claude Code on your Claude login), or any model profile name "
                        "(`karaoke models`), e.g. llm,claude")
    t.add_argument("--model", help="model profile for --gloss llm (default: `default` in the config, else local)")
    t.add_argument("--config", help="model profiles file (default $KARAOKE_CONFIG or ~/.karaoke-subs/config.toml)")
    t.add_argument("--claude-model", default="claude-haiku-5-5", help="model for --gloss claude")
    t.add_argument("--claude-effort", default="xhigh", choices=["low", "medium", "high", "xhigh", "max"])
    t.add_argument("--claude-batch", type=int, default=20, help="subtitle lines per Claude request")
    t.add_argument("--claude-jobs", type=int, default=4, help="Claude requests running at once")
    t.add_argument("--glossary", "--claude-glossary", action="store_true", dest="glossary",
                   help="first choose one gloss per distinct word of the episode, then gloss lines with it as a hint")
    t.add_argument("--llm-model", help="GGUF for a llama-server profile (default models/Qwen3.5-9B-Q4_K_M.gguf)")
    t.add_argument("--batch", type=int, help="subtitle lines per LLM request (default: the profile's)")
    t.add_argument("--jobs", type=int, help="LLM requests running at once (default: the profile's)")
    t.add_argument("--review", action="store_true",
                   help="write <subs>.review.tsv: suspicious LLM glosses + the verifier's decisions (implies llm)")
    t.add_argument("--review-min", type=int, default=2,
                   help="line score to flag (number=3, nonword=2, unusual=1 per word)")
    t.add_argument("--en-subs", help="English subtitles for the same episode: context for the LLM + name spelling")
    t.add_argument("--no-verify", action="store_true",
                   help="skip the verifier's second look at suspicious LLM glosses")
    t.add_argument("--romaji", action="store_true",
                   help="also write *.gloss-<method>+romaji.ass: romaji karaoke above the English karaoke")
    t.add_argument("--fixes", help="review .tsv with a filled-in `fix` column to apply to the llm glosses")
    t.add_argument("--no-known", action="store_true",
                   help="translate every word and do not count words for `learn` (e.g. for `compare` runs)")
    t.add_argument("--user-dir", help="folder with known_words.txt and words.json (default ~/.karaoke-subs)")
    t.set_defaults(func=cmd_time)
    s = sub.add_parser("subs", help="download a video's Japanese subtitles (what `time` would use)")
    s.add_argument("media", nargs="+", help="video or audio file(s)")
    s.set_defaults(func=cmd_subs)
    dr = sub.add_parser("doctor", help="check that everything needed is installed (ffmpeg, GPU, dictionary, models)")
    dr.add_argument("--config", help="model profiles file (default $KARAOKE_CONFIG or ~/.karaoke-subs/config.toml)")
    dr.set_defaults(func=cmd_doctor)
    w = sub.add_parser("learn", help="ask about words heard often, and words of each series you generated; "
                                     "known ones stop being translated")
    w.add_argument("--list", action="store_true", help="only list the words due, ask nothing")
    w.add_argument("--limit", type=int, default=30,
                   help="words asked per series, most often heard first (0 = all)")
    w.add_argument("--import-anki", metavar="DECK",
                   help="add the words of the studied (not new, not suspended) cards of this Anki deck to the known words")
    w.add_argument("--anki-field", default="Jlab-Lemma", help="note field with the words, separated by spaces")
    w.add_argument("--anki-collection", help="collection.anki2 (default: first profile in %%APPDATA%%/Anki2)")
    w.add_argument("--user-dir", help="folder with known_words.txt and words.json (default ~/.karaoke-subs)")
    w.set_defaults(func=cmd_learn)
    v = sub.add_parser("preview", help="render a video with the karaoke burned in, to check it")
    v.add_argument("media", help="the same video/audio file used for `time`")
    v.add_argument("gloss", help="a *.gloss-*.ass (or any karaoke .ass) from `time`")
    v.add_argument("--japanese", help="*.japanese.ass to show above (default: found next to `gloss`)")
    v.add_argument("--no-japanese", action="store_true", help="leave out the Japanese row")
    v.add_argument("--romaji", nargs="?", const="auto",
                   help="also show romaji karaoke between the two: a *.romaji.ass, or no value to use "
                        "the one next to `gloss`")
    v.add_argument("--out", help="output .mp4 (default: next to `gloss`)")
    v.add_argument("--start", type=float, help="start at this many seconds")
    v.add_argument("--duration", type=float, help="only render this many seconds")
    v.set_defaults(func=cmd_preview)
    c = sub.add_parser("compare", help="compare a run with a reference karaoke script, by grammar category")
    c.add_argument("labels", help="*.gloss-<method>.labels.json written by `time`")
    c.add_argument("gold", nargs="+", help="reference JSON(s): [{line, show: [one string per token], note}]")
    c.add_argument("--out", help="report .md (default: next to `labels`)")
    c.set_defaults(func=cmd_compare)
    m = sub.add_parser("models", help="list the model profiles (built-in + config file), or test one")
    m.add_argument("--test", nargs="*", metavar="NAME",
                   help="send a tiny glossing request to these profiles (no name: the default one)")
    m.add_argument("--config", help="model profiles file (default $KARAOKE_CONFIG or ~/.karaoke-subs/config.toml)")
    m.set_defaults(func=cmd_models)
    u = sub.add_parser("ui", help="open the web interface (local, in your browser)")
    u.add_argument("--port", type=int, default=8765)
    u.add_argument("--no-browser", action="store_true", help="don't open the browser")
    u.add_argument("--user-dir", help="folder with known_words.txt and words.json (default ~/.karaoke-subs)")
    u.set_defaults(func=cmd_ui)
    args = p.parse_args(argv)
    args.func(args)


def cmd_subs(args: argparse.Namespace) -> None:
    from .subfind import CatalogError, embedded_tracks, episode_number, find_online, series_title

    for m in map(Path, args.media):
        print(f"\n{m.name}\n  series {series_title(m.name) or series_title(m.resolve().parent.name)!r}, "
              f"episode {episode_number(m.name)}")
        try:
            print(f"  online   {find_online(m) or 'not found'}")
        except CatalogError as e:
            print(f"  online   {e}")
        tracks = embedded_tracks(m)
        if tracks:
            print("  inside:  " + ", ".join(f"#{t['index']} {t['language'] or '?'} {t['codec']}" for t in tracks))


def cmd_doctor(args: argparse.Namespace) -> None:
    import platform

    from .paths import USER_DIR, dict_dir

    ok = True

    def check(name: str, good: bool, detail: str, required: bool = True) -> None:
        nonlocal ok
        ok &= good or not required
        state = "ok" if good else ("MISSING" if required else "optional")
        print(f"  [{state}] {name:<14} {detail}")

    print(f"karaoke-subs on Python {platform.python_version()}, {platform.system()} {platform.machine()}")
    print(f"user folder: {USER_DIR}")
    for tool in ("ffmpeg", "ffprobe"):
        path = shutil.which(tool)
        check(tool, bool(path), path or "install FFmpeg and put it on PATH")
    try:
        import torch
        gpu = torch.cuda.get_device_name(0) if torch.cuda.is_available() else None
        check("torch", True, f"{torch.__version__}, " + (f"GPU {gpu}" if gpu else "CPU only (alignment is slower)"))
    except ImportError as e:
        check("torch", False, str(e))
    jm = dict_dir() / "JMdict_e.gz"
    check("JMdict", jm.exists(), str(jm) if jm.exists() else "downloaded automatically on the first --gloss run",
          required=False)
    claude = shutil.which("claude")
    check("claude", bool(claude), claude or "Claude Code, for the haiku/sonnet/opus profiles", required=False)
    from .providers import load_config
    try:
        profiles, default = load_config(Path(args.config) if args.config else None)
        p = profiles[default]
        usable = (not p.missing_key() and (p.kind != "llama-server" or p.model_path().is_file())
                  and (p.kind != "claude-code" or bool(claude)))
        check("default model", usable, f"{default} ({p.kind}); list them with: karaoke models", required=False)
    except (ValueError, OSError) as e:
        check("config", False, str(e))
    sys.exit(0 if ok else 1)


def cmd_preview(args: argparse.Namespace) -> None:
    from .preview import combine, render

    gloss = Path(args.gloss)
    japanese = Path(args.japanese) if args.japanese else None
    if args.no_japanese:
        japanese = None
    elif japanese is None:  # netflix.ja.ctc.gloss-llm.ass -> netflix.ja.ctc.japanese.ass
        guess = gloss.with_name(gloss.name.split(".gloss-")[0] + ".japanese.ass")
        japanese = guess if guess.exists() else None
    romaji = None
    if args.romaji:
        romaji = Path(args.romaji) if args.romaji != "auto" else             gloss.with_name(gloss.name.split(".gloss-")[0] + ".romaji.ass")
        if not romaji.exists():
            sys.exit(f"romaji subtitles not found: {romaji}")
    combined = combine(gloss, japanese, gloss.with_name(gloss.stem + ".preview.ass"), romaji)
    out = Path(args.out) if args.out else gloss.with_name(gloss.stem + ".preview.mp4")
    print(f"rendering {out} ...", flush=True)
    t0 = time.time()
    render(Path(args.media), combined, out, start=args.start, duration=args.duration)
    print(f"done in {time.time() - t0:.0f}s: {out}")


def _head(word: str, e: dict, heard: str, label: str) -> None:
    from .vocab import romaji_of

    print(f"\n{label}  {word}  {romaji_of(word)}  ({e['reading']})  {heard}\n  e.g. {e['example']}")


def _show(word: str, e: dict, heard: str, label: str) -> None:
    """A word with its romaji, reading, example and gloss (learn --list)."""
    _head(word, e, heard, label)
    print(f"  = {e['gloss']}")


def _ask(word: str, e: dict, heard: str, label: str) -> bool | None:
    """Show a word, then ask for the meaning and whether the user knew it: True, False,
    or None when the user stops (q)."""
    _head(word, e, heard, label)
    if input("  Enter to see the meaning (q to stop) ").strip().lower() == "q":
        return None
    print(f"  = {e['gloss']}")
    a = ""
    while a not in ("y", "n", "q"):
        a = input("  Did you know it? [y/n/q] ").strip().lower()
    return None if a == "q" else a == "y"


def cmd_learn(args: argparse.Namespace) -> None:
    from .vocab import open_user, plan_review

    known, stats = open_user(Path(args.user_dir) if args.user_dir else None)
    if args.import_anki:
        from .vocab import anki_collection, anki_words
        words = anki_words(Path(args.anki_collection) if args.anki_collection else anki_collection(),
                           args.import_anki, args.anki_field)
        added = sum(known.add(w, save=False) for w in dict.fromkeys(words))
        known.save()
        print(f"anki: {len(words)} words in studied cards, {added} new known words")
        return
    due, plan = plan_review(stats, known, args.limit)
    print(f"{len(known.words)} known words ({known.path})")
    if due:
        print(f"\n== Heard often: {len(due)} words ==")
    for n, d in enumerate(due, 1):
        heard, label = f"heard {stats.count(d.entry)}x", f"[{n}/{len(due)}]"
        if args.list:
            _show(d.word, d.entry, heard, label)
            continue
        knew = _ask(d.word, d.entry, heard, label)
        if knew is None:
            return
        stats.answer(d, knew, known)
        print("  added to known words" if knew
              else f"  asked again after {d.entry['ask_at'] - stats.count(d.entry)} more times")
    for s in plan:
        if not s.words:
            print(f"\n== {s.series}: nothing new to ask")
            continue
        print(f"\n== {s.series}: {s.episodes} episodes, {s.unasked} words not asked yet, "
              f"the {len(s.words)} most often heard ==")
        for n, (w, count) in enumerate(s.words, 1):
            heard, label = f"heard {count}x in this series", f"[{n}/{len(s.words)}]"
            if args.list:
                _show(w, stats.words[w], heard, label)
                continue
            knew = _ask(w, stats.words[w], heard, label)
            if knew is None:
                return
            stats.answer_series(w, knew, known)
            print("  added to known words" if knew else "  not known: not asked again")


def cmd_compare(args: argparse.Namespace) -> None:
    from .compare import main as compare_main

    labels = Path(args.labels)
    out = Path(args.out) if args.out else labels.with_name(labels.stem + ".compare.md")
    print(f"wrote {compare_main(labels, [Path(g) for g in args.gold], out)}")


def cmd_models(args: argparse.Namespace) -> None:
    from .providers import config_path, load_config, try_profile

    path = Path(args.config) if args.config else config_path()
    try:
        profiles, default = load_config(path)
    except (ValueError, OSError) as e:
        sys.exit(str(e))
    if args.test is None:
        print(f"config: {path}{'' if path.is_file() else ' (not created yet; see config.example.toml)'}")
        for p in profiles.values():
            what = p.gguf if p.kind == "llama-server" else p.model + (f" ({p.effort})" if p.effort else "")
            where = f" @ {p.base_url}" if p.base_url else ""
            key = f"  [needs {p.missing_key()}]" if p.missing_key() else ""
            mark = "*" if p.name == default else " "
            print(f"{mark} {p.name:<12} {p.kind:<12} {what}{where}{key}")
        print("* = default for --gloss llm.  Check one with: karaoke models --test NAME")
        return
    failed = 0
    for name in args.test or [default]:
        if name not in profiles:
            sys.exit(f"no model profile {name!r}; known: {', '.join(profiles)}")
        print(f"{name}: ...", flush=True)
        r = try_profile(profiles[name])
        failed += not r["ok"]
        detail = json.dumps(r["reply"], ensure_ascii=False) if "reply" in r else ""
        print(f"{name}: {'OK' if r['ok'] else 'FAILED'} in {r['seconds']}s  {detail}  {r.get('error', '')}"
              + (f"\n  {r['usage']}" if r["ok"] else ""))
    if failed:
        sys.exit(1)


def cmd_ui(args: argparse.Namespace) -> None:
    from .ui import serve

    serve(args.port, not args.no_browser, Path(args.user_dir) if args.user_dir else None)


if __name__ == "__main__":
    main()
