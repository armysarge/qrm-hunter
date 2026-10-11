"""Train the beta QRM ranker on synthetic profiles and export it as ONNX (development only).

    uv run --group train python tools/train_ranker.py --clips-per-class 300

Writes backend/qrm_hunter/analysis/models/ranker.onnx and ranker.json. Retrain whenever
FEATURE_VERSION, FEATURE_IDS, CLASS_IDS or the synthetic profiles change.
"""

from __future__ import annotations

import argparse
import json
import os
from concurrent.futures import ProcessPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

from qrm_hunter.analysis.features import FEATURE_IDS, FEATURE_VERSION, AnalysisContext, extract_features
from qrm_hunter.analysis.rules import CLASS_IDS, UNKNOWN_ID
from qrm_hunter.sdr.synthetic import CLIP_RATE, PROFILE_IDS, profile_sources, render_clip

MODELS_DIR = Path(__file__).resolve().parents[1] / "backend" / "qrm_hunter" / "analysis" / "models"
TRAIN_SECONDS = 4.0
# HF, VHF and UHF centres: the model must not learn that QRM only exists on HF
CENTRES_HZ = (1.9e6, 3.6e6, 7.1e6, 10.1e6, 14.2e6, 18.1e6, 21.2e6, 24.9e6, 28.5e6,
              50.5e6, 70.2e6, 145.0e6, 435.0e6)


def make_example(args: tuple[int, int]) -> tuple[np.ndarray, int]:
    """One labelled feature vector: (class index, seed) -> (features, class index)."""
    label_index, seed = args
    rng = np.random.default_rng(seed)
    label = CLASS_IDS[label_index]
    mains = int(rng.choice([50, 60]))
    noise_db = float(rng.uniform(-60, -35))
    centre = float(rng.choice(CENTRES_HZ)) + float(rng.uniform(-50_000, 50_000))
    sources = []
    if label != UNKNOWN_ID:
        strength = float(rng.uniform(-10, 30))
        sources += profile_sources(label, centre, strength_db=strength, noise_db=noise_db,
                                   mains_hz=mains, rng=rng)
        if rng.random() < 0.25:  # sometimes a weaker second source shares the clip
            other = str(rng.choice([p for p in PROFILE_IDS if p != label]))
            sources += profile_sources(other, centre, strength_db=strength - float(rng.uniform(10, 20)),
                                       noise_db=noise_db, mains_hz=mains, rng=rng)
    iq = render_clip(sources, seconds=TRAIN_SECONDS, rate=CLIP_RATE, center_hz=centre,
                     noise_db=noise_db, seed=seed % (2 ** 31))
    ctx = AnalysisContext(mains_hz="auto" if rng.random() < 0.5 else mains)
    return extract_features(iq, CLIP_RATE, centre, ctx).to_vector(), label_index


def main(argv: list[str] | None = None) -> None:
    import lightgbm
    import onnxmltools
    from onnxmltools.convert.common.data_types import FloatTensorType

    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--clips-per-class", type=int, default=300)
    p.add_argument("--jobs", type=int, default=os.cpu_count() or 1)
    p.add_argument("--seed", type=int, default=20261010)
    p.add_argument("--out", type=Path, default=MODELS_DIR)
    args = p.parse_args(argv)
    if args.clips_per_class < 300:
        print("note: fewer than 300 clips per class; the committed model must use at least 300")

    tasks = [(ci, args.seed * 1_000_003 + ci * 100_000 + j)
             for ci in range(len(CLASS_IDS)) for j in range(args.clips_per_class)]
    with ProcessPoolExecutor(args.jobs) as ex:
        rows = list(ex.map(make_example, tasks, chunksize=8))
    x = np.stack([r[0] for r in rows])
    y = np.array([r[1] for r in rows])

    def model():
        return lightgbm.LGBMClassifier(n_estimators=60, num_leaves=15, learning_rate=0.1, min_child_samples=10,
                                       subsample=0.8, subsample_freq=1, colsample_bytree=0.8,
                                       random_state=args.seed, verbose=-1)

    order = np.random.default_rng(args.seed).permutation(len(y))
    cut = int(0.8 * len(y))
    held = model().fit(x[order[:cut]], y[order[:cut]])
    accuracy = float((held.predict(x[order[cut:]]) == y[order[cut:]]).mean())
    print(f"hold-out accuracy {accuracy:.3f} on {len(y) - cut} clips")

    final = model().fit(x, y)
    onx = onnxmltools.convert_lightgbm(final, initial_types=[("features", FloatTensorType([None, len(FEATURE_IDS)]))],
                                       zipmap=False, target_opset=15)
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "ranker.onnx").write_bytes(onx.SerializeToString())
    meta = {
        "classes": list(CLASS_IDS), "features": list(FEATURE_IDS), "feature_version": FEATURE_VERSION,
        "trained": datetime.now(UTC).date().isoformat(), "clips_per_class": args.clips_per_class,
        "holdout_accuracy": round(accuracy, 4), "seed": args.seed, "model": "lightgbm",
    }
    (args.out / "ranker.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    size = (args.out / "ranker.onnx").stat().st_size
    print(f"wrote {args.out / 'ranker.onnx'} ({size / 1024:.0f} KiB)")


if __name__ == "__main__":
    main()
