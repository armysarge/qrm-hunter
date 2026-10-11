"""Print measured features and the top rule scores for synthetic profiles.

    uv run python tools/feature_table.py               # every profile and noise
    uv run python tools/feature_table.py smps vsd      # selected profiles
"""

from __future__ import annotations

import sys

from qrm_hunter.analysis.features import AnalysisContext, extract_features
from qrm_hunter.analysis.rules import apply_rules, load_signatures, ranked
from qrm_hunter.sdr.synthetic import CLIP_CENTER_HZ, CLIP_RATE, PROFILE_IDS, render_profile_clip

STRENGTHS = (6.0, 15.0, 25.0)


def main(argv: list[str]) -> None:
    names = argv or [*PROFILE_IDS, "noise"]
    signatures, _ = load_signatures(None)
    for name in names:
        for strength in STRENGTHS:
            iq = render_profile_clip(None if name == "noise" else name, strength)
            f = extract_features(iq, CLIP_RATE, CLIP_CENTER_HZ, AnalysisContext())
            top = ranked(apply_rules(signatures, f))[:3]
            print(f"{name:>15} {strength:>4.0f} dB  " + "  ".join(f"{r.id}={r.score:.2f}" for r in top))
            print("      " + "  ".join(f"{k}={v:.4g}" for k, v in f.as_dict().items() if v is not None))


if __name__ == "__main__":
    main(sys.argv[1:])
