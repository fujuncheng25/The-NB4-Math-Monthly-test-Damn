import pandas as pd
import numpy as np
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment
from openpyxl.utils import get_column_letter
from datetime import datetime
import os

# 读取 CSV 文件
csv_file = "katrina_processed (3).csv"
df = pd.read_csv(csv_file)

print("✓ 数据加载完成")
print(f"总借阅记录数: {len(df)}")

# 数据清洗
df['Beginning of borrow'] = pd.to_datetime(df['Beginning of borrow'], errors='coerce')
df['Return time'] = pd.to_datetime(df['Return time'], errors='coerce')
df['Number of borrow'] = pd.to_numeric(df['Number of borrow'], errors='coerce').fillna(0).astype(int)

# 创建日期列
df['借阅日期'] = df['Beginning of borrow'].dt.date
df['书名'] = df['Title']

print("✓ 数据清洗完成")

# ==================== 生成 Summary Sheet ====================
# 统计每日借阅次数
daily_stats = df.groupby('借阅日期').agg({
    'Number of borrow': ['sum', 'count'],  # 总借阅次数 + 借阅记录数
    'Collection Barcode': 'count'  # 不同的书籍数量
}).reset_index()

daily_stats.columns = ['借阅日期', '总借阅次数', '借阅记录数', '涉及书籍数']
daily_stats = daily_stats.sort_values('借阅日期').reset_index(drop=True)

print(f"✓ 生成每日统计: {len(daily_stats)} 天")

# ==================== 统计每本书 ====================
# 按书籍分组，统计总借阅次数
book_stats = df.groupby('书名').agg({
    'Number of borrow': 'sum',
    'Collection Barcode': 'first',  # 条形码
    '借阅日期': ['min', 'max', 'count']  # 首次借阅日期，最后借阅日期，借阅次数
}).reset_index()

book_stats.columns = ['书名', '总借阅次数', '条形码', '首次借阅日期', '最后借阅日期', '借阅记录数']
# 过滤出有借阅记录的书籍（借阅次数 > 0）
book_stats = book_stats[book_stats['总借阅次数'] > 0]
book_stats = book_stats.sort_values('总借阅次数', ascending=False).reset_index(drop=True)

print(f"✓ 统计书籍信息: {len(book_stats)} 本书（有借阅记录）")

# ==================== 创建 Excel 文件 ====================
output_file = "book_statistics.xlsx"

# 收集所有 sheet 数据
all_sheets = {}

# Sheet 1: 每日统计
all_sheets['每日统计'] = daily_stats

# Sheet 2+: 每本书的详细数据（按总借阅次数降序）
for idx, (book_name, total_borrows) in enumerate(zip(book_stats['书名'], book_stats['总借阅次数']), 1):
    # 筛选这本书的数据
    book_data = df[df['书名'] == book_name].copy()
    
    # 统计每日该书的借阅次数
    book_daily = book_data.groupby('借阅日期').agg({
        'Number of borrow': 'sum',
        'Beginning of borrow': 'min',
        'Return time': 'first'
    }).reset_index()
    
    book_daily.columns = ['借阅日期', '借阅次数', '借阅时间', '归还时间']
    book_daily = book_daily.sort_values('借阅日期').reset_index(drop=True)
    
    # 创建 sheet 名称（限制长度避免 Excel 报错）
    # 移除 Excel 不允许的特殊字符: : \ / ? * [ ]
    clean_name = book_name.replace(':', '').replace('\\', '').replace('/', '').replace('?', '').replace('*', '').replace('[', '').replace(']', '')
    sheet_name = f"{idx:03d}_{clean_name[:15]}"[:31]  # Excel 最多 31 个字符
    
    all_sheets[sheet_name] = book_daily
    
    if idx % 50 == 0:
        print(f"✓ 已准备 {idx} 本书的数据...")

print(f"✓ 已准备所有 {len(all_sheets)} 个 sheet 的数据")

# 一次性写入所有数据到 Excel
with pd.ExcelWriter(output_file, engine='openpyxl') as writer:
    for sheet_name, data in all_sheets.items():
        data.to_excel(writer, sheet_name=sheet_name, index=False)
        if sheet_name != '每日统计':
            # 只输出会产出大量日志的书籍数据
            pass

print(f"\n✅ Excel 文件已生成: {output_file}")
print(f"   - 第 1 个 Sheet: 每日统计")
print(f"   - 第 2-{len(all_sheets)} 个 Sheet: 各书籍详情（按总借阅次数排序）")
