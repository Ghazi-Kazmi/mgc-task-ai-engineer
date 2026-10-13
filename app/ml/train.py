"""Train the lead-scoring baseline with a time-based split.

    python -m app.ml.train

Writes models/lead_model.joblib (pipeline + metadata used by the API) and
reports/ml_report.json (the numbers quoted in the README).

Split: train on leads created before CUTOFF, test on leads created on/after it.
A random split would let the model learn from leads that arrived *after* the
ones it is tested on (seasonality, campaign mix, pricing changes), so its score
would overstate how it does on next month's leads, which is the only thing
the sales team cares about.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import joblib
import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, roc_auc_score
from sklearn.model_selection import train_test_split
from sklearn.pipeline import Pipeline, make_pipeline
from sklearn.preprocessing import FunctionTransformer, OneHotEncoder, StandardScaler
from sklearn.impute import SimpleImputer

from ..config import ROOT, settings
from . import features as F

CUTOFF = "2025-04-01"   # ~6 months of test data (Apr-Oct 2025), ~15 months of training data
TOP_N = 100


def build_pipeline(numeric: list[str], categorical: list[str], binary: list[str]) -> Pipeline:
    numeric_pipe = make_pipeline(
        SimpleImputer(strategy="median", add_indicator=True),
        FunctionTransformer(np.log1p, feature_names_out="one-to-one"),  # budgets / durations are heavily right-skewed
        StandardScaler(),
    )
    pre = ColumnTransformer([
        ("num", numeric_pipe, numeric),
        ("cat", OneHotEncoder(handle_unknown="ignore", min_frequency=20), categorical),
        ("bin", "passthrough", binary),
    ], sparse_threshold=0)   # ~40 columns: dense is simpler and lets the scorer explain each feature
    return Pipeline([("pre", pre), ("clf", LogisticRegression(max_iter=2000))])


def top_n_hits(y_true: np.ndarray, scores: np.ndarray, n: int = TOP_N) -> int:
    order = np.argsort(-scores, kind="stable")[:n]
    return int(y_true[order].sum())


def evaluate(pipe: Pipeline, X: pd.DataFrame, y: np.ndarray) -> dict:
    s = pipe.predict_proba(X)[:, 1]
    return {
        "average_precision": round(float(average_precision_score(y, s)), 4),
        "roc_auc": round(float(roc_auc_score(y, s)), 4),
        f"top_{TOP_N}_converted": top_n_hits(y, s),
    }


def main() -> dict:
    raw = pd.read_csv(settings.leads_csv)
    df = F.clean(F.dedupe(raw))
    df = df.sort_values("created_at").reset_index(drop=True)

    train = df[df.created_at < CUTOFF]
    test = df[df.created_at >= CUTOFF]
    y_tr, y_te = train[F.TARGET].to_numpy(), test[F.TARGET].to_numpy()
    prevalence = float(y_te.mean())

    # --- production model: intake-time features only -----------------------------
    model = build_pipeline(F.NUMERIC, F.CATEGORICAL, F.BINARY).fit(train[F.INTAKE_FEATURES], y_tr)
    test_scores = model.predict_proba(test[F.INTAKE_FEATURES])[:, 1]
    intake = evaluate(model, test[F.INTAKE_FEATURES], y_te)

    # --- no-skill baselines --------------------------------------------------------
    rng = np.random.default_rng(0)
    random_top = [top_n_hits(y_te, rng.random(len(y_te))) for _ in range(1000)]
    # "Common-sense" baseline: rank by each source's historical conversion rate.
    src_rate = train.groupby("source")[F.TARGET].mean()
    src_scores = test["source"].map(src_rate).fillna(y_tr.mean()).to_numpy() + rng.random(len(test)) * 1e-9

    # --- comparison only: + engagement features (not usable for brand-new leads) ----
    eng_model = build_pipeline(F.NUMERIC + F.ENGAGEMENT, F.CATEGORICAL, F.BINARY)
    eng_model.fit(train[F.INTAKE_FEATURES + F.ENGAGEMENT], y_tr)
    engaged = evaluate(eng_model, test[F.INTAKE_FEATURES + F.ENGAGEMENT], y_te)

    # --- comparison only: what a random split would have reported ---------------------
    r_tr, r_te = train_test_split(df, test_size=len(test), random_state=0, stratify=df[F.TARGET])
    r_model = build_pipeline(F.NUMERIC, F.CATEGORICAL, F.BINARY).fit(r_tr[F.INTAKE_FEATURES], r_tr[F.TARGET])
    random_split = evaluate(r_model, r_te[F.INTAKE_FEATURES], r_te[F.TARGET].to_numpy())

    report = {
        "trained_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "rows_raw": int(len(raw)),
        "rows_after_dedupe": int(len(df)),
        "split": {
            "cutoff": CUTOFF,
            "train": {"rows": int(len(train)), "from": str(train.created_at.min()), "to": str(train.created_at.max()),
                      "conversion_rate": round(float(y_tr.mean()), 4)},
            "test": {"rows": int(len(test)), "from": str(test.created_at.min()), "to": str(test.created_at.max()),
                     "conversion_rate": round(prevalence, 4)},
        },
        "metric": "average_precision (PR-AUC); no-skill baseline = test conversion rate",
        "model_intake_features": intake,
        "no_skill": {
            "average_precision": round(prevalence, 4),
            "roc_auc": 0.5,
            f"top_{TOP_N}_converted_random_mean": round(float(np.mean(random_top)), 1),
            f"top_{TOP_N}_converted_random_p95": int(np.percentile(random_top, 95)),
        },
        "source_rate_heuristic": {
            "average_precision": round(float(average_precision_score(y_te, src_scores)), 4),
            f"top_{TOP_N}_converted": top_n_hits(y_te, src_scores),
        },
        "comparison_with_engagement_features": engaged,
        "comparison_random_split_intake_features": random_split,
    }

    settings.model_path.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump({
        "pipeline": model,
        "features": F.INTAKE_FEATURES,
        "options": {c: sorted(df[c].dropna().unique().tolist()) for c in F.CATEGORICAL},
        "test_scores": np.sort(test_scores),           # for "percentile vs recent leads"
        # average encoded training lead; a factor's effect = coef * (value - average)
        "train_mean": model.named_steps["pre"].transform(train[F.INTAKE_FEATURES]).mean(axis=0),
        "top_n_threshold": float(np.sort(test_scores)[-TOP_N]),
        "base_rate": prevalence,
        "report": report,
    }, settings.model_path)

    (ROOT / "reports").mkdir(exist_ok=True)
    (ROOT / "reports" / "ml_report.json").write_text(json.dumps(report, indent=2))
    return report


if __name__ == "__main__":
    print(json.dumps(main(), indent=2))
