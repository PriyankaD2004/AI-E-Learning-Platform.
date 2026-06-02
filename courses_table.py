import pandas as pd
import sqlite3
import os
from db_config import COURSES_EXCEL_DB   # ✅ use shared DB config

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

EXCEL_FILE = os.path.join(BASE_DIR, "courses.xlsx")
DB_FILE = COURSES_EXCEL_DB
TABLE_NAME = "courses_excel"


def clean_columns(df):
    df.columns = (
        df.columns
        .str.strip()
        .str.replace(r"\s+", " ", regex=True)
    )
    return df


def clean_values(df):
    for col in df.columns:
        if df[col].dtype == object:
            df[col] = (
                df[col]
                .astype(str)
                .str.strip()
                .replace("nan", "")
            )
    return df


def clean_urls(df):
    for col in ["Course Links", "Course Thumbnail Image"]:
        if col in df.columns:
            df[col] = df[col].apply(
                lambda x: x if isinstance(x, str) and x.startswith("http") else ""
            )
    return df


def import_excel():
    if not os.path.exists(EXCEL_FILE):
        raise FileNotFoundError("❌ courses.xlsx not found")

    df = pd.read_excel(EXCEL_FILE)
    df = clean_columns(df)
    df = clean_values(df)
    df = clean_urls(df)

    conn = sqlite3.connect(DB_FILE)
    df.to_sql(TABLE_NAME, conn, if_exists="replace", index=False)
    conn.close()

    print("✅ Excel imported & cleaned successfully!")


if __name__ == "__main__":
    import_excel()
