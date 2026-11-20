"""Real BigQuery ingest from GitHub Archive. Falls back to mock if no credentials."""
import os, json
from pathlib import Path

GCP_PROJECT = os.environ.get("GCP_PROJECT_ID")
GCP_KEY = os.environ.get("GCP_SA_KEY")

def has_credentials():
    return bool(GCP_PROJECT and GCP_KEY)

def ingest():
    if not has_credentials():
        print("No GCP credentials — using mock data (run.py generates mock)")
        return False
    try:
        from google.cloud import bigquery
        import json as _json
        # Write key to temp file
        key_path = "/tmp/gcp_sa.json"
        Path(key_path).write_text(GCP_KEY)
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = key_path
        client = bigquery.Client(project=GCP_PROJECT)
        sql = Path("scripts/bigquery/daily_ingest.sql").read_text()
        # Remove DECLARE line for client library (it handles params)
        sql = sql.replace("DECLARE target_date DATE DEFAULT DATE_SUB(CURRENT_DATE(), INTERVAL 1 DAY);\n", "")
        sql = sql.replace("WHERE _TABLE_SUFFIX = FORMAT_DATE('%Y%m%d', target_date)",
                          "WHERE _TABLE_SUFFIX = FORMAT_DATE('%Y%m%d', DATE_SUB(CURRENT_DATE(), INTERVAL 1 DAY))")
        job = client.query(sql)
        df = job.to_dataframe()
        # Save as parquet
        import pyarrow as pa, pyarrow.parquet as pq
        table = pa.Table.from_pandas(df)
        pq.write_table(table, "data/raw.parquet")
        print(f"Ingested {len(df)} users from BigQuery")
        return True
    except Exception as e:
        print(f"BigQuery ingest failed: {e}")
        return False

if __name__ == "__main__":
    ingest()