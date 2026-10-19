"""Word timing inside each subtitle line.

Methods
- "ctc": MMS forced aligner (torchaudio MMS_FA) on romaji, one audio window
  per line. A <star> token at both ends soaks up neighbouring speech/noise.
- "proportional": no audio; split the line duration by mora count.

The CTC result falls back to proportional when the alignment is not
trustworthy (unknown readings, too little audio, low confidence).
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .audio import SAMPLE_RATE
from .reading import Token, hira_to_kata, mora_count
from .subs import Line

PAD = 0.4  # seconds of audio searched before/after each subtitle line


@dataclass
class AlignedLine:
    line: Line
    tokens: list[Token]
    method: str
    score: float  # mean per-char probability for ctc, nan otherwise


def proportional(line: Line, tokens: list[Token]) -> AlignedLine:
    weights = [max(1, mora_count(hira_to_kata(t.pron)) if t.pron else len(t.surface)) for t in tokens]
    total = sum(weights) or 1
    t = line.start
    dur = line.end - line.start
    for tok, w in zip(tokens, weights):
        tok.t_start = t
        t += dur * w / total
        tok.t_end = t
    return AlignedLine(line, tokens, "proportional", float("nan"))


class CTCAligner:
    def __init__(self, device: str | None = None):
        import warnings

        import torch
        from torchaudio.pipelines import MMS_FA

        # torchaudio 2.8 warns that forced_align leaves in 2.9; the version is pinned to 2.8.
        warnings.filterwarnings("ignore", message=".*forced_align has been deprecated", category=UserWarning)
        self.torch = torch
        self.device = device or ("cuda" if torch.cuda.is_available() else "cpu")
        self.model = MMS_FA.get_model(with_star=True).to(self.device).eval()
        self.dictionary = MMS_FA.get_dict(star="*")
        self.min_score = 0.15

    def emission(self, audio: np.ndarray):
        torch = self.torch
        with torch.inference_mode():
            wav = torch.from_numpy(audio).unsqueeze(0).to(self.device)
            em, _ = self.model(wav)
            return torch.log_softmax(em, dim=-1)[0].cpu()

    def align(self, audio: np.ndarray, line: Line, tokens: list[Token],
              lo: float, hi: float) -> AlignedLine:
        """lo/hi: hard limits for the search window (neighbouring lines)."""
        import torchaudio.functional as F
        torch = self.torch

        speakable = [i for i, t in enumerate(tokens) if t.romaji]
        if not speakable or len(speakable) < len(tokens) * 0.5:
            return proportional(line, tokens)

        w0 = max(lo, line.start - PAD, 0.0)
        w1 = min(hi, line.end + PAD, len(audio) / SAMPLE_RATE)
        chunk = audio[int(w0 * SAMPLE_RATE):int(w1 * SAMPLE_RATE)]
        if len(chunk) < SAMPLE_RATE * 0.2:
            return proportional(line, tokens)
        em = self.emission(chunk)
        frame = (w1 - w0) / em.shape[0]

        star = self.dictionary["*"]
        target: list[int] = [star]
        owner: list[int] = [-1]  # which token each target char belongs to
        for i in speakable:
            for ch in tokens[i].romaji:
                target.append(self.dictionary[ch])
                owner.append(i)
        target.append(star)
        owner.append(-1)
        if len(target) * 1.0 > em.shape[0]:  # needs >= 1 frame per char
            return proportional(line, tokens)

        try:
            path, scores = F.forced_align(em.unsqueeze(0), torch.tensor([target], dtype=torch.int32),
                                          blank=0)
        except RuntimeError:
            return proportional(line, tokens)
        path, probs = path[0].tolist(), scores[0].exp().tolist()

        # Walk the CTC path: each non-blank run that advances the target index
        # marks where a character is emitted.
        char_frames: list[list[int]] = [[] for _ in target]
        ti = -1
        prev = 0
        for f, lab in enumerate(path):
            if lab != 0 and (lab != prev or ti < 0):
                ti += 1
            if lab != 0:
                char_frames[ti].append(f)
            prev = lab
        char_probs = [float(np.mean([probs[f] for f in fr])) for fr in char_frames[1:-1] if fr]
        score = float(np.mean(char_probs)) if char_probs else 0.0
        if ti != len(target) - 1 or score < self.min_score:
            res = proportional(line, tokens)
            res.score = score
            res.method = "proportional(lowconf)"
            return res

        firsts: dict[int, float] = {}
        lasts: dict[int, float] = {}
        for k in range(1, len(target) - 1):
            fr = char_frames[k]
            if not fr:
                continue
            i = owner[k]
            firsts.setdefault(i, w0 + fr[0] * frame)
            lasts[i] = w0 + (fr[-1] + 1) * frame
            tokens[i].char_times.append(w0 + fr[0] * frame)
        for i in speakable:
            tokens[i].t_start = firsts[i]
            tokens[i].t_end = lasts[i]
        # Unspeakable tokens (no reading) share the gap around them.
        for i, t in enumerate(tokens):
            if not t.romaji:
                before = tokens[i - 1].t_end if i else firsts[speakable[0]]
                t.t_start = t.t_end = before
        return AlignedLine(line, tokens, "ctc", score)


def align_all(audio: np.ndarray, lines: list[Line], token_lists: list[list[Token]],
              method: str = "ctc", min_score: float = 0.0, lead: float = 0.0) -> list[AlignedLine]:
    """`lead`: start each CTC syllable this many seconds early. CTC tends to fire
    slightly after a sound begins, and karaoke reads better when the highlight
    anticipates the voice a little."""
    if method == "proportional":
        return [proportional(ln, toks) for ln, toks in zip(lines, token_lists)]
    aligner = CTCAligner()
    aligner.min_score = min_score
    out = []
    for k, (ln, toks) in enumerate(zip(lines, token_lists)):
        # Never let the window run deep into the neighbouring lines' speech.
        lo = lines[k - 1].end - 0.1 if k else 0.0
        hi = lines[k + 1].start + 0.1 if k + 1 < len(lines) else 1e9
        al = aligner.align(audio, ln, toks, min(lo, ln.start), max(hi, ln.end))
        if al.method == "ctc" and lead:
            prev = -1e9
            for t in al.tokens:
                t.t_start = max(prev, t.t_start - lead)
                t.char_times = [max(t.t_start, c - lead) for c in t.char_times]
                prev = t.t_start
        out.append(al)
    # Free the GPU for the LLM server that may run next (8 GB cards can't hold both).
    del aligner
    import torch
    if torch.cuda.is_available():
        torch.cuda.empty_cache()
    return out
