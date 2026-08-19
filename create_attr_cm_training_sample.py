"""
create_attr_cm_training_sample.py
--------------------------------
Create a balanced training dataset for the ATTR-CM risk model from the feature
cohort table.

This script creates a patient-level training set using a 1:5 positive-to-negative
ratio and retains only model-ready variables. It is appropriate for a baseline
logistic regression and similar interpretable models.

Usage:
    py -3 create_attr_cm_training_sample.py
"""

import os
import sys

from create_undiagnosed_output_table import build_session

FEATURE_TABLE = "VAW_AMER_DESIGN.US_VYNDA_DT_2.US_VYNDA_DT_2_ATTR_CM_MODEL_FEATURES_INF"
TRAIN_TABLE = "VAW_AMER_DESIGN.US_VYNDA_DT_2.US_VYNDA_DT_2_ATTR_CM_TRAINING_SET_INF"


def build_balanced_training_sql():
    return """
WITH positives AS (
    SELECT
        *
    FROM VAW_AMER_DESIGN.US_VYNDA_DT_2.US_VYNDA_DT_2_ATTR_CM_MODEL_FEATURES_INF
    WHERE LABEL_ATTR_CM = 1
),
negatives AS (
    SELECT
        *
    FROM VAW_AMER_DESIGN.US_VYNDA_DT_2.US_VYNDA_DT_2_ATTR_CM_MODEL_FEATURES_INF
    WHERE LABEL_ATTR_CM = 0
    QUALIFY ROW_NUMBER() OVER (ORDER BY RANDOM()) <= (
        SELECT GREATEST(1, COUNT(*) * 5)
        FROM positives
    )
),
training AS (
    SELECT * FROM positives
    UNION ALL
    SELECT * FROM negatives
)
SELECT *
FROM training
""".strip()


def main():
    session = build_session()
    try:
        create_sql = f"CREATE OR REPLACE TABLE {TRAIN_TABLE} AS\n{build_balanced_training_sql()}"
        print(f"Creating balanced training set: {TRAIN_TABLE}")
        session.sql(create_sql).collect()
        count = session.table(TRAIN_TABLE).count()
        print(f"Training set row count: {count:,}")
        label_counts = session.sql(f"SELECT LABEL_ATTR_CM, COUNT(*) AS n FROM {TRAIN_TABLE} GROUP BY LABEL_ATTR_CM ORDER BY LABEL_ATTR_CM").collect()
        for row in label_counts:
            print(f"LABEL_ATTR_CM={row[0]} -> {row[1]:,}")
        print("Done.")
    finally:
        session.close()


if __name__ == "__main__":
    sys.exit(main())

