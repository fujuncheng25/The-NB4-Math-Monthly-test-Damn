#!/usr/bin/env python3
"""Join monthly borrow counts with Douban ratings and export CSV + plots."""

from __future__ import annotations

import argparse
import os
import re
from typing import Any

import pandas as pd


def normalize_isbn(value: Any) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    text = str(value).strip()
    text = text.replace("-", "").replace(" ", "")
    if text.endswith(".0"):
        text = text[:-2]
    text = re.sub(r"[^0-9Xx]", "", text)
    return text.upper()


def load_borrow_data(path: str, count_by: str) -> pd.DataFrame:
    df = pd.read_csv(path, dtype=str, low_memory=False)

    if "Beginning of borrow" not in df.columns:
        raise SystemExit("Missing 'Beginning of borrow' column in borrow CSV.")

    df["begin_borrow"] = pd.to_datetime(df["Beginning of borrow"], errors="coerce")
    df = df[df["begin_borrow"].notna()].copy()

    df["borrow_month"] = df["begin_borrow"].dt.to_period("M").astype(str)
    df["isbn_norm"] = df.get("ISBN", "").map(normalize_isbn)

    if count_by == "number_of_borrow":
        if "Number of borrow" not in df.columns:
            raise SystemExit("Missing 'Number of borrow' column in borrow CSV.")
        df["borrow_units"] = pd.to_numeric(df["Number of borrow"], errors="coerce").fillna(0)
    else:
        df["borrow_units"] = 1

    return df


def load_ratings(path: str) -> pd.DataFrame:
    ratings = pd.read_csv(path, dtype=str, low_memory=False)
    if "ISBN" not in ratings.columns:
        raise SystemExit("Missing 'ISBN' column in ratings CSV.")

    ratings["isbn_norm"] = ratings["ISBN"].map(normalize_isbn)
    ratings["douban_average"] = pd.to_numeric(ratings.get("douban_average"), errors="coerce")
    ratings["douban_votes"] = pd.to_numeric(ratings.get("douban_votes"), errors="coerce")

    # Keep the most-voted record if duplicates exist.
    ratings = ratings.sort_values("douban_votes", ascending=False)
    ratings = ratings.drop_duplicates(subset=["isbn_norm"], keep="first")

    keep_cols = [
        "isbn_norm",
        "douban_average",
        "douban_votes",
        "douban_url",
        "douban_title",
        "douban_source",
    ]
    return ratings[[c for c in keep_cols if c in ratings.columns]]


def build_monthly_table(borrow_df: pd.DataFrame, ratings_df: pd.DataFrame) -> pd.DataFrame:
    group_cols = ["borrow_month", "isbn_norm", "Title"]
    monthly = (
        borrow_df.groupby(group_cols, dropna=False)
        .agg(
            borrow_count=("borrow_units", "sum"),
            borrow_records=("Collection Barcode", "count"),
            isbn_raw=("ISBN", "first"),
            first_borrow=("begin_borrow", "min"),
            last_borrow=("begin_borrow", "max"),
        )
        .reset_index()
    )

    merged = monthly.merge(ratings_df, on="isbn_norm", how="left")

    merged = merged.rename(
        columns={
            "Title": "title",
            "borrow_month": "month",
        }
    )

    column_order = [
        "month",
        "title",
        "isbn_raw",
        "isbn_norm",
        "borrow_count",
        "borrow_records",
        "douban_average",
        "douban_votes",
        "douban_url",
        "douban_title",
        "douban_source",
        "first_borrow",
        "last_borrow",
    ]
    existing_cols = [c for c in column_order if c in merged.columns]
    extra_cols = [c for c in merged.columns if c not in existing_cols]
    merged = merged[existing_cols + extra_cols]

    return merged.sort_values(["month", "borrow_count"], ascending=[True, False])


def build_rating_bins(bin_size: float) -> list[float]:
    start = 1.0
    end = 10.0
    if bin_size <= 0:
        raise ValueError("bin_size must be positive")
    bins = []
    value = start
    while value < end:
        bins.append(round(value, 2))
        value += bin_size
    if not bins or bins[-1] != end:
        bins.append(end)
    return bins


def first_non_empty(series: pd.Series) -> str:
    for value in series:
        if value is None or (isinstance(value, float) and pd.isna(value)):
            continue
        text = str(value).strip()
        if text:
            return text
    return ""


def first_non_null(series: pd.Series) -> Any:
    for value in series:
        if value is None or (isinstance(value, float) and pd.isna(value)):
            continue
        return value
    return pd.NA


def build_book_key(df: pd.DataFrame, title_col: str) -> pd.Series:
    isbn_series = df["isbn_norm"].fillna("").astype(str)
    title_series = df[title_col].fillna("").astype(str)
    return isbn_series.where(isbn_series.str.strip() != "", title_series)


def parse_price(value: Any) -> float:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return float("nan")
    text = str(value).replace(",", "")
    match = re.search(r"\d+(?:\.\d+)?", text)
    if not match:
        return float("nan")
    try:
        return float(match.group(0))
    except ValueError:
        return float("nan")


def filter_outliers_iqr(df: pd.DataFrame, columns: list[str], factor: float = 1.5) -> pd.DataFrame:
    filtered = df.copy()
    for column in columns:
        series = filtered[column].dropna()
        if series.empty:
            continue
        q1 = series.quantile(0.25)
        q3 = series.quantile(0.75)
        iqr = q3 - q1
        if iqr == 0:
            continue
        low = q1 - factor * iqr
        high = q3 + factor * iqr
        filtered = filtered[(filtered[column] >= low) & (filtered[column] <= high)]
    return filtered


def add_rating_bin_stats(book_stats: pd.DataFrame, bin_size: float) -> pd.DataFrame:
    df = book_stats.dropna(subset=["douban_average", "total_borrow"]).copy()
    df = df[(df["douban_average"] >= 1.0) & (df["douban_average"] <= 10.0)]
    if df.empty:
        return df

    bins = build_rating_bins(bin_size)
    labels = [f"{bins[i]:.1f}-{bins[i + 1]:.1f}" for i in range(len(bins) - 1)]
    df["rating_bin"] = pd.cut(
        df["douban_average"],
        bins=bins,
        include_lowest=True,
        right=True,
        labels=labels,
    )

    bin_stats = df.groupby("rating_bin", observed=True).agg(
        bin_total_borrow=("total_borrow", "sum"),
        bin_book_count=("book_key", "count"),
    )
    bin_stats["bin_borrow_rate"] = bin_stats.apply(
        lambda row: row["bin_total_borrow"] / row["bin_book_count"] if row["bin_book_count"] else 0,
        axis=1,
    )
    bin_stats = bin_stats.reset_index()
    df = df.merge(bin_stats[["rating_bin", "bin_borrow_rate", "bin_book_count"]], on="rating_bin", how="left")
    return df


def build_book_level_table(
    merged: pd.DataFrame,
    borrow_df: pd.DataFrame,
) -> pd.DataFrame:
    book_df = merged.copy()
    book_df["book_key"] = build_book_key(book_df, "title")

    book_stats = (
        book_df.groupby("book_key", dropna=False)
        .agg(
            title=("title", first_non_empty),
            isbn_norm=("isbn_norm", first_non_empty),
            isbn_raw=("isbn_raw", first_non_empty),
            total_borrow=("borrow_count", "sum"),
            borrow_records=("borrow_records", "sum"),
            douban_average=("douban_average", first_non_null),
            douban_votes=("douban_votes", first_non_null),
        )
        .reset_index()
    )

    if "Price" in borrow_df.columns:
        price_df = borrow_df.copy()
        price_df["price_value"] = price_df["Price"].apply(parse_price)
        price_df["book_key"] = build_book_key(price_df, "Title")
        price_stats = (
            price_df.groupby("book_key", dropna=False)["price_value"]
            .median()
            .reset_index()
        )
        book_stats = book_stats.merge(price_stats, on="book_key", how="left")

    return book_stats


def save_monthly_plots(
    merged: pd.DataFrame,
    out_dir: str,
    top_labels: int,
    bin_size: float,
) -> int:
    try:
        import matplotlib.pyplot as plt
    except ImportError:
        print("matplotlib is not installed. Skip plot output.")
        return 0

    os.makedirs(out_dir, exist_ok=True)
    plot_count = 0

    bins = build_rating_bins(bin_size)
    labels = [f"{bins[i]:.1f}-{bins[i + 1]:.1f}" for i in range(len(bins) - 1)]

    for month, month_df in merged.groupby("month"):
        plot_df = month_df.dropna(subset=["douban_average"]).copy()
        if plot_df.empty:
            continue

        plot_df = plot_df[(plot_df["douban_average"] >= 1.0) & (plot_df["douban_average"] <= 10.0)]
        if plot_df.empty:
            continue

        plot_df["rating_bin"] = pd.cut(
            plot_df["douban_average"],
            bins=bins,
            include_lowest=True,
            right=True,
            labels=labels,
        )

        bin_stats = (
            plot_df.groupby("rating_bin", observed=True)["borrow_count"]
            .sum()
            .reindex(labels, fill_value=0)
        )

        fig, ax = plt.subplots(figsize=(10, 5.5))
        ax.bar(labels, bin_stats.values, color="#4C78A8")
        ax.set_title(f"Borrow count by Douban rating bins ({month})")
        ax.set_xlabel("Douban rating range")
        ax.set_ylabel("Total borrow count")
        ax.grid(True, axis="y", linestyle="--", alpha=0.3)
        plt.setp(ax.get_xticklabels(), rotation=45, ha="right")

        if top_labels > 0:
            top_bins = bin_stats.nlargest(top_labels)
            for label, value in top_bins.items():
                if value <= 0:
                    continue
                ax.annotate(
                    f"{int(value)}",
                    (label, value),
                    fontsize=8,
                    xytext=(0, 3),
                    textcoords="offset points",
                    ha="center",
                )

        fig.tight_layout()
        filename = os.path.join(out_dir, f"borrow_rating_bins_{month}.png")
        fig.savefig(filename, dpi=150)
        plt.close(fig)
        plot_count += 1

    return plot_count


def save_overall_distribution(
    merged: pd.DataFrame,
    out_path: str,
    bin_size: float,
) -> bool:
    try:
        import matplotlib.pyplot as plt
    except ImportError:
        print("matplotlib is not installed. Skip overall distribution plot.")
        return False

    df = merged.dropna(subset=["douban_average"]).copy()
    df = df[(df["douban_average"] >= 1.0) & (df["douban_average"] <= 10.0)]
    if df.empty:
        print("No rated books available for overall distribution plot.")
        return False

    isbn_series = df["isbn_norm"].fillna("").astype(str)
    title_series = df["title"].fillna("").astype(str)
    df["book_key"] = isbn_series.where(isbn_series.str.strip() != "", title_series)
    df = df.drop_duplicates(subset=["book_key"], keep="first")

    bins = build_rating_bins(bin_size)
    labels = [f"{bins[i]:.1f}-{bins[i + 1]:.1f}" for i in range(len(bins) - 1)]

    df["rating_bin"] = pd.cut(
        df["douban_average"],
        bins=bins,
        include_lowest=True,
        right=True,
        labels=labels,
    )

    bin_counts = (
        df.groupby("rating_bin", observed=True)
        .size()
        .reindex(labels, fill_value=0)
    )

    fig, ax = plt.subplots(figsize=(10, 5.5))
    ax.bar(labels, bin_counts.values, color="#72B7B2")
    ax.set_title("Douban rating distribution (all books)")
    ax.set_xlabel("Douban rating range")
    ax.set_ylabel("Book count")
    ax.grid(True, axis="y", linestyle="--", alpha=0.3)
    plt.setp(ax.get_xticklabels(), rotation=45, ha="right")
    fig.tight_layout()
    fig.savefig(out_path, dpi=150)
    plt.close(fig)

    return True


def save_borrow_rate_by_rating(
    book_stats: pd.DataFrame,
    out_path: str,
    bin_size: float,
) -> bool:
    try:
        import matplotlib.pyplot as plt
    except ImportError:
        print("matplotlib is not installed. Skip borrow-rate plot.")
        return False

    df = book_stats.dropna(subset=["douban_average"]).copy()
    df = df[(df["douban_average"] >= 1.0) & (df["douban_average"] <= 10.0)]
    if df.empty:
        print("No rated books available for borrow-rate plot.")
        return False

    bins = build_rating_bins(bin_size)
    labels = [f"{bins[i]:.1f}-{bins[i + 1]:.1f}" for i in range(len(bins) - 1)]
    df["rating_bin"] = pd.cut(
        df["douban_average"],
        bins=bins,
        include_lowest=True,
        right=True,
        labels=labels,
    )

    grouped = df.groupby("rating_bin", observed=True).agg(
        total_borrow=("total_borrow", "sum"),
        book_count=("book_key", "count"),
    )
    grouped = grouped.reindex(labels, fill_value=0)
    grouped["borrow_rate"] = grouped.apply(
        lambda row: row["total_borrow"] / row["book_count"] if row["book_count"] else 0,
        axis=1,
    )

    fig, ax = plt.subplots(figsize=(10, 5.5))
    ax.bar(labels, grouped["borrow_rate"].values, color="#F28E2B")
    ax.set_title("Average borrow count per book by rating bin")
    ax.set_xlabel("Douban rating range")
    ax.set_ylabel("Average borrow count per book")
    ax.grid(True, axis="y", linestyle="--", alpha=0.3)
    plt.setp(ax.get_xticklabels(), rotation=45, ha="right")
    fig.tight_layout()
    fig.savefig(out_path, dpi=150)
    plt.close(fig)

    return True


def save_price_vs_borrow_rate(
    book_stats: pd.DataFrame,
    out_path: str,
    normalized: bool = False,
    bin_size: float = 0.2,
) -> bool:
    try:
        import matplotlib.pyplot as plt
    except ImportError:
        print("matplotlib is not installed. Skip price plot.")
        return False

    if "price_value" not in book_stats.columns:
        print("Price column not found. Skip price plot.")
        return False

    if normalized:
        df = add_rating_bin_stats(book_stats, bin_size)
        if df.empty:
            print("No rated books available for normalized price plot.")
            return False
        df = df.dropna(subset=["price_value", "bin_borrow_rate"]).copy()
        df = df[df["price_value"] > 0]
        borrow_col = "bin_borrow_rate"
        title = "Price vs borrow rate by rating bin"
        y_label = "Borrow rate per rating bin"
    else:
        df = book_stats.dropna(subset=["price_value", "total_borrow"]).copy()
        df = df[df["price_value"] > 0]
        borrow_col = "total_borrow"
        title = "Price vs total borrow count per book"
        y_label = "Total borrow count per book"

    before = len(df)
    df = filter_outliers_iqr(df, ["price_value", borrow_col])
    if before != len(df):
        print(f"Removed {before - len(df)} outlier(s) from price plot.")
    if df.empty:
        print("No price data available for price vs borrow plot.")
        return False

    fig, ax = plt.subplots(figsize=(9, 5.5))
    ax.scatter(df["price_value"], df[borrow_col], alpha=0.6, color="#59A14F")
    ax.set_title(title)
    ax.set_xlabel("Price")
    ax.set_ylabel(y_label)
    ax.grid(True, linestyle="--", alpha=0.3)
    fig.tight_layout()
    fig.savefig(out_path, dpi=150)
    plt.close(fig)

    return True


def save_votes_vs_borrow(
    book_stats: pd.DataFrame,
    out_path: str,
    normalized: bool = False,
    bin_size: float = 0.2,
) -> bool:
    try:
        import matplotlib.pyplot as plt
    except ImportError:
        print("matplotlib is not installed. Skip votes plot.")
        return False

    if normalized:
        df = add_rating_bin_stats(book_stats, bin_size)
        if df.empty:
            print("No rated books available for normalized votes plot.")
            return False
        df = df.dropna(subset=["douban_votes", "bin_borrow_rate"]).copy()
        df = df[df["douban_votes"] > 0]
        borrow_col = "bin_borrow_rate"
        title = "Douban votes vs borrow rate by rating bin"
        y_label = "Borrow rate per rating bin"
    else:
        df = book_stats.dropna(subset=["douban_votes", "total_borrow"]).copy()
        df = df[df["douban_votes"] > 0]
        borrow_col = "total_borrow"
        title = "Douban votes vs total borrow count per book"
        y_label = "Total borrow count per book"

    before = len(df)
    df = filter_outliers_iqr(df, ["douban_votes", borrow_col])
    if before != len(df):
        print(f"Removed {before - len(df)} outlier(s) from votes plot.")
    if df.empty:
        print("No votes data available for votes vs borrow plot.")
        return False

    fig, ax = plt.subplots(figsize=(9, 5.5))
    ax.scatter(df["douban_votes"], df[borrow_col], alpha=0.6, color="#E15759")
    ax.set_title(title)
    ax.set_xlabel("Douban votes")
    ax.set_ylabel(y_label)
    ax.grid(True, linestyle="--", alpha=0.3)
    fig.tight_layout()
    fig.savefig(out_path, dpi=150)
    plt.close(fig)

    return True


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--borrow-csv",
        default="katrina_processed_numeric (3).csv",
        help="Borrow data CSV",
    )
    parser.add_argument(
        "--ratings-csv",
        default="douban_ratings.csv",
        help="Douban ratings CSV",
    )
    parser.add_argument(
        "--out-csv",
        default="monthly_borrow_douban.csv",
        help="Output CSV path",
    )
    parser.add_argument(
        "--plot-dir",
        default="monthly_plots",
        help="Directory for per-month plot images",
    )
    parser.add_argument(
        "--count-by",
        choices=("row", "number_of_borrow"),
        default="row",
        help="How to count borrows: row=each record counts as 1",
    )
    parser.add_argument(
        "--top-labels",
        type=int,
        default=10,
        help="Top-N bins to annotate with counts (0 disables labels)",
    )
    parser.add_argument(
        "--rating-bin-size",
        type=float,
        default=0.5,
        help="Douban rating bin size for bar charts",
    )
    parser.add_argument(
        "--overall-bin-size",
        type=float,
        default=0.2,
        help="Douban rating bin size for overall distribution chart",
    )
    parser.add_argument(
        "--no-plots",
        action="store_true",
        help="Skip plot generation",
    )
    args = parser.parse_args()

    borrow_df = load_borrow_data(args.borrow_csv, args.count_by)
    ratings_df = load_ratings(args.ratings_csv)
    merged = build_monthly_table(borrow_df, ratings_df)
    book_stats = build_book_level_table(merged, borrow_df)

    merged.to_csv(args.out_csv, index=False, encoding="utf-8-sig")
    print(f"Saved CSV: {args.out_csv}")

    if not args.no_plots:
        plot_count = save_monthly_plots(
            merged,
            args.plot_dir,
            args.top_labels,
            args.rating_bin_size,
        )
        overall_path = os.path.join(args.plot_dir, "douban_rating_distribution_all.png")
        overall_ok = save_overall_distribution(
            merged,
            overall_path,
            args.overall_bin_size,
        )
        borrow_rate_path = os.path.join(args.plot_dir, "borrow_rate_by_rating_bins.png")
        borrow_rate_ok = save_borrow_rate_by_rating(
            book_stats,
            borrow_rate_path,
            args.overall_bin_size,
        )
        price_borrow_path = os.path.join(args.plot_dir, "price_vs_borrow_rate.png")
        price_ok = save_price_vs_borrow_rate(book_stats, price_borrow_path)
        price_norm_path = os.path.join(args.plot_dir, "price_vs_borrow_rate_norm.png")
        price_norm_ok = save_price_vs_borrow_rate(
            book_stats,
            price_norm_path,
            normalized=True,
            bin_size=args.overall_bin_size,
        )
        votes_borrow_path = os.path.join(args.plot_dir, "votes_vs_borrow.png")
        votes_ok = save_votes_vs_borrow(book_stats, votes_borrow_path)
        votes_norm_path = os.path.join(args.plot_dir, "votes_vs_borrow_norm.png")
        votes_norm_ok = save_votes_vs_borrow(
            book_stats,
            votes_norm_path,
            normalized=True,
            bin_size=args.overall_bin_size,
        )
        if plot_count:
            print(f"Saved {plot_count} plot(s) to: {args.plot_dir}")
        else:
            print("No monthly plots were created.")
        if overall_ok:
            print(f"Saved overall distribution plot: {overall_path}")
        if borrow_rate_ok:
            print(f"Saved borrow-rate plot: {borrow_rate_path}")
        if price_ok:
            print(f"Saved price vs borrow plot: {price_borrow_path}")
        if price_norm_ok:
            print(f"Saved normalized price plot: {price_norm_path}")
        if votes_ok:
            print(f"Saved votes vs borrow plot: {votes_borrow_path}")
        if votes_norm_ok:
            print(f"Saved normalized votes plot: {votes_norm_path}")


if __name__ == "__main__":
    main()
