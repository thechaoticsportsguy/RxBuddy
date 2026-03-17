from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd
import plotly.express as px
import streamlit as st
from dotenv import load_dotenv
from sqlalchemy import create_engine, text


def get_database_url() -> str:
    """
    Loads DATABASE_URL from .env and converts it to use the psycopg driver.
    """
    import os

    load_dotenv()
    url = os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError(
            "DATABASE_URL is missing. Create a .env file in the project root with:\n"
            "DATABASE_URL=postgresql://postgres:YOUR_PASSWORD@localhost:6767/rxbuddy"
        )
    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+psycopg://", 1)
    return url


@st.cache_resource
def get_engine():
    return create_engine(get_database_url(), future=True, pool_pre_ping=True)


def load_search_logs() -> pd.DataFrame:
    """
    Pulls search_logs joined with questions (so we have category + question text).
    """
    sql = text(
        """
        SELECT
          sl.id,
          sl.query,
          sl.matched_question_id,
          sl.clicked,
          sl.session_id,
          sl.searched_at,
          q.question AS matched_question,
          q.category AS matched_category
        FROM search_logs sl
        LEFT JOIN questions q
          ON q.id = sl.matched_question_id
        ORDER BY sl.searched_at DESC
        """
    )
    with get_engine().connect() as conn:
        return pd.read_sql(sql, conn)


def main() -> None:
    st.set_page_config(
        page_title="RxBuddy Analytics",
        page_icon="💊",
        layout="wide",
    )

    st.title("RxBuddy Analytics Dashboard")
    st.caption("Powered by your PostgreSQL `search_logs` table.")

    try:
        df = load_search_logs()
    except Exception as e:
        st.error("Could not connect to Postgres or query the database.")
        st.code(str(e))
        st.info(
            "Make sure Postgres is running and your `.env` has a valid `DATABASE_URL`."
        )
        return

    # ---------- Empty state ----------
    if df.empty:
        st.metric("Total searches today", 0)
        st.info("No searches yet. This dashboard will populate once users start searching.")
        return

    # Convert timestamps to pandas datetime
    df["searched_at"] = pd.to_datetime(df["searched_at"], utc=True, errors="coerce")

    # ---------- Metric: searches today ----------
    # We store timestamps in UTC, so we count "today" in UTC too.
    today = datetime.now(timezone.utc).date()
    df_today = df[df["searched_at"].dt.date == today]
    st.metric("Total searches today", int(len(df_today)))

    col1, col2 = st.columns(2, gap="large")

    # ---------- Chart 1: Top 10 most searched questions ----------
    # We count matched_question_id (the question the search matched).
    # If matched_question_id is NULL, it means the search didn't map to a specific question.
    topq = (
        df.dropna(subset=["matched_question_id", "matched_question"])
        .groupby(["matched_question_id", "matched_question"], as_index=False)
        .size()
        .sort_values("size", ascending=False)
        .head(10)
    )

    with col1:
        st.subheader("Top 10 most searched questions")
        if topq.empty:
            st.info("No matched questions yet (searches are not linked to a question).")
        else:
            fig = px.bar(
                topq,
                x="size",
                y="matched_question",
                orientation="h",
                labels={"size": "Searches", "matched_question": "Question"},
                title=None,
            )
            fig.update_layout(height=420, margin=dict(l=10, r=10, t=10, b=10))
            st.plotly_chart(fig, use_container_width=True)

    # ---------- Chart 2: Search volume by category ----------
    cat = (
        df.dropna(subset=["matched_category"])
        .groupby("matched_category", as_index=False)
        .size()
        .sort_values("size", ascending=False)
    )

    with col2:
        st.subheader("Search volume by category")
        if cat.empty:
            st.info("No categories yet (searches are not linked to a question).")
        else:
            fig = px.pie(
                cat,
                values="size",
                names="matched_category",
                hole=0.45,
            )
            fig.update_layout(height=420, margin=dict(l=10, r=10, t=10, b=10))
            st.plotly_chart(fig, use_container_width=True)

    # ---------- Table: Most recent 10 searches ----------
    st.subheader("Most recent 10 searches")
    recent = df[["searched_at", "query", "matched_question_id", "matched_category"]].head(10)
    # Make timestamp easier to read
    recent = recent.copy()
    recent["searched_at"] = recent["searched_at"].dt.tz_convert(None)
    st.dataframe(recent, use_container_width=True, hide_index=True)


if __name__ == "__main__":
    main()

