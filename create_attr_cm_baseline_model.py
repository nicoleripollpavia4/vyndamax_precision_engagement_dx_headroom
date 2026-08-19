"""
create_attr_cm_baseline_model.py
--------------------------------
Train a baseline ATTR-CM risk model from the balanced training sample and save
patient-level scores to a Snowflake table.

This is a first-pass model intended to produce calibrated risk estimates with an
interpretable logistic regression. The model uses the clinically relevant
journey features created in the ATTR-CM feature table.

Usage:
    py -3 create_attr_cm_baseline_model.py
"""

import os
import sys

import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, precision_recall_fscore_support, roc_auc_score
from sklearn.model_selection import train_test_split

from create_undiagnosed_output_table import build_session

TRAIN_TABLE = "VAW_AMER_DESIGN.US_VYNDA_DT_2.US_VYNDA_DT_2_ATTR_CM_TRAINING_SET_INF"
SCORE_TABLE = "VAW_AMER_DESIGN.US_VYNDA_DT_2.US_VYNDA_DT_2_ATTR_CM_MODEL_SCORES_INF"

FEATURE_COLUMNS = [
    "AFIB_PREINDEX_FLAG",
    "AFIB_PREINDEX_COUNT",
    "HF_PREINDEX_FLAG",
    "HF_PREINDEX_COUNT",
    "HFPEF_PREINDEX_FLAG",
    "HFPEF_PREINDEX_COUNT",
    "RED_FLAG_PREINDEX_FLAG",
    "RED_FLAG_PREINDEX_COUNT",
    "ATTR_PROCEDURE_PREINDEX_FLAG",
    "ATTR_PROCEDURE_PREINDEX_COUNT",
    "TOTAL_CONDITIONS",
    "FLAG_2PLUS_CONDITIONS",
    "FLAG_2PLUS_AND_HF",
    "FLAG_2PLUS_AND_HFPEF",
    "HAS_RELEVANT_RED_FLAG",
    "HAS_HF_OR_HFPEF",
    "HAS_AORTIC_STENOSIS",
    "HAS_ATTR_RELEVANT_PROCEDURE",
    "HAS_2PLUS_RED_FLAGS",
    "DAYS_FROM_FIRST_CLAIM_TO_INDEX",
]


def load_training_data(session):
    df = session.table(TRAIN_TABLE).to_pandas()
    df = df.copy()
    for col in FEATURE_COLUMNS:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)
    if "LABEL_ATTR_CM" not in df.columns:
        raise ValueError(f"LABEL_ATTR_CM column not found in {TRAIN_TABLE}")
    if "PATIENT_ID" not in df.columns:
        raise ValueError(f"PATIENT_ID column not found in {TRAIN_TABLE}")
    df["LABEL_ATTR_CM"] = pd.to_numeric(df["LABEL_ATTR_CM"], errors="coerce").fillna(0).astype(int)
    return df


def train_and_score(df):
    X = df[FEATURE_COLUMNS].fillna(0).astype(float)
    y = df["LABEL_ATTR_CM"].astype(int)

    X_train, X_test, y_train, y_test = train_test_split(
        X,
        y,
        test_size=0.2,
        random_state=42,
        stratify=y,
    )

    model = LogisticRegression(
        max_iter=2000,
        class_weight="balanced",
        random_state=42,
    )
    model.fit(X_train, y_train)

    prob = model.predict_proba(X_test)[:, 1]
    pred = model.predict(X_test)

    roc_auc = roc_auc_score(y_test, prob)
    avg_precision = average_precision_score(y_test, prob)
    precision, recall, _, _ = precision_recall_fscore_support(
        y_test,
        pred,
        average="binary",
        zero_division=0,
    )

    print(f"Test ROC AUC: {roc_auc:.4f}")
    print(f"Average precision: {avg_precision:.4f}")
    print(f"Precision: {precision:.4f}")
    print(f"Recall: {recall:.4f}")

    scored = df.copy()
    scored["MODEL_SCORE"] = model.predict_proba(X)[:, 1]
    scored["MODEL_PREDICTION"] = model.predict(X)
    scored = scored[["PATIENT_ID", "LABEL_ATTR_CM", "MODEL_SCORE", "MODEL_PREDICTION"]]
    return scored


def publish_scores(session, scored_df):
    scored_df = scored_df.copy()
    scored_df["PATIENT_ID"] = pd.to_numeric(scored_df["PATIENT_ID"], errors="coerce").astype("Int64")
    scored_df["LABEL_ATTR_CM"] = pd.to_numeric(scored_df["LABEL_ATTR_CM"], errors="coerce").astype(int)
    scored_df["MODEL_SCORE"] = pd.to_numeric(scored_df["MODEL_SCORE"], errors="coerce").astype(float)
    scored_df["MODEL_PREDICTION"] = pd.to_numeric(scored_df["MODEL_PREDICTION"], errors="coerce").astype(int)
    session.create_dataframe(scored_df).write.mode("overwrite").save_as_table(SCORE_TABLE)
    count = session.table(SCORE_TABLE).count()
    print(f"Saved scored patients: {count:,}")


def main():
    session = build_session()
    try:
        df = load_training_data(session)
        print(f"Loaded training rows: {len(df):,}")
        scored = train_and_score(df)
        publish_scores(session, scored)
        print(f"Results published to: {SCORE_TABLE}")
    finally:
        session.close()


if __name__ == "__main__":
    sys.exit(main())

