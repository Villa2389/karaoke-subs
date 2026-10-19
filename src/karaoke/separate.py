"""Optional vocal isolation before alignment (removes BGM / sound effects).

Needs the `separation` extra: uv sync --extra separation
"""
from __future__ import annotations

from pathlib import Path

from .paths import models_dir

DEFAULT_MODEL = "vocals_mel_band_roformer.ckpt"  # MelBand RoFormer (Kim), vocals SDR ~12.6


def isolate_vocals(media: Path, out_dir: Path, model: str = DEFAULT_MODEL) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / f"vocals.{Path(model).stem}.wav"
    if target.exists() and target.stat().st_mtime >= media.stat().st_mtime:
        return target
    try:
        from audio_separator.separator import Separator
    except ImportError:
        raise SystemExit("--vocals needs the separation extra: uv sync --extra separation") from None

    sep = Separator(output_dir=str(out_dir), model_file_dir=str(models_dir()),
                    output_single_stem="Vocals", output_format="WAV")
    sep.load_model(model_filename=model)
    produced = sep.separate(str(media), {"Vocals": target.stem})
    produced_path = out_dir / Path(produced[0]).name
    if produced_path != target:
        produced_path.replace(target)
    return target
