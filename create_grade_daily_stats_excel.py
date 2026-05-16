import pandas as pd

INPUT_CSV = "katrina (3).csv"
OUTPUT_XLSX = "00_daily_stats_by_grade.xlsx"


def build_daily_stats(dataframe: pd.DataFrame) -> pd.DataFrame:
    """Build daily stats using the same rules as 00_daily_stats.csv."""
    daily_stats = dataframe.groupby("借阅日期").agg(
        {
            "是否借出": "sum",
            "Circulation Type": "count",
            "Collection Barcode": "nunique",
        }
    ).reset_index()

    daily_stats.columns = ["借阅日期", "借阅次数", "总操作数", "涉及书籍数"]
    daily_stats = daily_stats.sort_values("借阅日期").reset_index(drop=True)
    return daily_stats


def main() -> None:
    df = pd.read_csv(INPUT_CSV)

    # Match existing counting rule: borrow + renew count as borrow, return does not.
    df["是否借出"] = df["Circulation Type"].apply(
        lambda x: 1 if "外借" in str(x) or "续借" in str(x) else 0
    )
    df["借阅日期"] = pd.to_datetime(df["Circulation Time"], errors="coerce").dt.date

    grades = sorted(df["Grade"].dropna().astype(str).unique())

    with pd.ExcelWriter(OUTPUT_XLSX, engine="openpyxl") as writer:
        for grade in grades:
            grade_df = df[df["Grade"].astype(str) == grade].copy()
            grade_daily_stats = build_daily_stats(grade_df)
            grade_daily_stats.to_excel(writer, sheet_name=grade, index=False)

    print(f"✅ 已生成: {OUTPUT_XLSX}")
    print(f"   包含 {len(grades)} 个年级 sheet: {', '.join(grades)}")


if __name__ == "__main__":
    main()
