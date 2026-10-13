"""Load the trained model artifact and score one lead from the web form."""
from __future__ import annotations

from pathlib import Path

import joblib
import numpy as np

from . import features as F


class LeadScorer:
    def __init__(self, model_path: Path) -> None:
        art = joblib.load(model_path)
        self.pipe = art["pipeline"]
        self.options = art["options"]
        self.test_scores = art["test_scores"]
        self.threshold = art["top_n_threshold"]
        self.base_rate = art["base_rate"]
        self.train_mean = art["train_mean"]
        self.report = art["report"]
        pre = self.pipe.named_steps["pre"]
        self.coef = self.pipe.named_steps["clf"].coef_.ravel()
        self.names = [_pretty(n) for n in pre.get_feature_names_out()]

    def score(self, lead: dict) -> dict:
        X = F.to_frame(lead)
        p = float(self.pipe.predict_proba(X)[0, 1])
        pct = float(np.searchsorted(self.test_scores, p) / len(self.test_scores) * 100)

        # Logistic regression is additive in log-odds, so each feature's push is
        # coef * (this lead's value - the average training lead's value).
        x = np.asarray(self.pipe.named_steps["pre"].transform(X)).ravel()
        effect = self.coef * (x - self.train_mean)
        order = np.argsort(-np.abs(effect))
        factors = [{"feature": self._label(i, x[i]), "effect": round(float(effect[i]), 3),
                    "direction": "raises" if effect[i] > 0 else "lowers"}
                   for i in order[:5] if abs(effect[i]) > 0.02]

        return {
            "probability": round(p, 4),
            "lift_vs_average": round(p / self.base_rate, 2),
            "percentile_vs_recent_leads": round(pct, 1),
            "in_top_100_of_recent_leads": p >= self.threshold,
            "priority": "call first" if pct >= 90 else "call soon" if pct >= 60 else "normal queue",
            "top_factors": factors,
            "base_rate": round(self.base_rate, 4),
        }

    def _label(self, i: int, value: float) -> str:
        """An absent one-hot / yes-no feature can still move the score (e.g. *not*
        being a referral lowers it), so say "not ..." instead of naming it as if present."""
        name = self.names[i]
        is_flag = " = " in name or name in F.BINARY or name.startswith("missing ")
        return f"not {name}" if is_flag and value == 0 else name


def _pretty(name: str) -> str:
    # "cat__source_Referral" -> "source = Referral", "num__budget_pkr_lac" -> "budget_pkr_lac"
    name = name.split("__", 1)[-1]
    for c in F.CATEGORICAL:
        if name.startswith(c + "_"):
            return f"{c} = {name[len(c) + 1:]}"
    return name.replace("missingindicator_", "missing ")
