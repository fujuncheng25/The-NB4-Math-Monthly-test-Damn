import pandas as pd
import os

# 读取原始CSV文件
csv_file = "katrina_processed (3).csv"
df = pd.read_csv(csv_file)

print("✓ 数据加载完成")

# 数据清洗
df['Beginning of borrow'] = pd.to_datetime(df['Beginning of borrow'], errors='coerce')
df['Return time'] = pd.to_datetime(df['Return time'], errors='coerce')
df['Number of borrow'] = pd.to_numeric(df['Number of borrow'], errors='coerce').fillna(0).astype(int)

# 创建日期列
df['借阅日期'] = df['Beginning of borrow'].dt.date
df['书名'] = df['Title']

print("✓ 数据清洗完成")

# ==================== 1. 每日统计 CSV ====================
daily_stats = df.groupby('借阅日期').agg({
    'Number of borrow': ['sum', 'count'],
    'Collection Barcode': 'count'
}).reset_index()

daily_stats.columns = ['借阅日期', '总借阅次数', '借阅记录数', '涉及书籍数']
daily_stats = daily_stats.sort_values('借阅日期').reset_index(drop=True)

daily_stats.to_csv('00_daily_stats.csv', index=False, encoding='utf-8-sig')
print(f"✓ 生成: 00_daily_stats.csv ({len(daily_stats)} 行)")

# ==================== 2. 书籍总体统计 CSV ====================
book_stats = df.groupby('书名').agg({
    'Number of borrow': 'sum',
    'Collection Barcode': 'first',
    '借阅日期': ['min', 'max', 'count']
}).reset_index()

book_stats.columns = ['书名', '总借阅次数', '条形码', '首次借阅日期', '最后借阅日期', '借阅记录数']
book_stats = book_stats[book_stats['总借阅次数'] > 0]
book_stats = book_stats.sort_values('总借阅次数', ascending=False).reset_index(drop=True)

book_stats.to_csv('01_books_summary.csv', index=False, encoding='utf-8-sig')
print(f"✓ 生成: 01_books_summary.csv ({len(book_stats)} 行)")

# ==================== 3. 创建 books_details 目录并生成各书籍CSV ====================
output_dir = "books_details"
os.makedirs(output_dir, exist_ok=True)

for idx, (book_name, total_borrows) in enumerate(zip(book_stats['书名'], book_stats['总借阅次数']), 1):
    book_data = df[df['书名'] == book_name].copy()
    
    book_daily = book_data.groupby('借阅日期').agg({
        'Number of borrow': 'sum',
        'Beginning of borrow': 'min',
        'Return time': 'first'
    }).reset_index()
    
    book_daily.columns = ['借阅日期', '借阅次数', '借阅时间', '归还时间']
    book_daily = book_daily.sort_values('借阅日期').reset_index(drop=True)
    
    # 清理文件名中的非法字符
    clean_name = book_name.replace('/', '_').replace('\\', '_').replace(':', '_').replace('*', '_').replace('?', '_').replace('"', '_').replace('<', '_').replace('>', '_').replace('|', '_')
    clean_name = clean_name[:50]  # 限制文件名长度
    
    filename = os.path.join(output_dir, f"{idx:03d}_{clean_name}.csv")
    book_daily.to_csv(filename, index=False, encoding='utf-8-sig')
    
    if idx % 50 == 0:
        print(f"✓ 已生成 {idx} 本书的CSV...")

print(f"✓ 生成完毕: {len(book_stats)} 本书的详细CSV")

# ==================== 4. 综合详细表（所有数据）====================
all_details = []
for idx, book_name in enumerate(book_stats['书名'], 1):
    book_data = df[df['书名'] == book_name].copy()
    book_data['书籍序号'] = idx
    all_details.append(book_data)

combined_df = pd.concat(all_details, ignore_index=True)
# 只保留关键列
combined_df_out = combined_df[['书籍序号', 'Title', 'Collection Barcode', 'Beginning of borrow', 'Return time', 'Number of borrow', 'Total time']].copy()
combined_df_out.columns = ['书籍序号', '书名', '条形码', '借阅时间', '归还时间', '借阅次数', '总用时']
combined_df_out = combined_df_out.sort_values(['书籍序号', '借阅时间']).reset_index(drop=True)

combined_df_out.to_csv('02_all_details.csv', index=False, encoding='utf-8-sig')
print(f"✓ 生成: 02_all_details.csv ({len(combined_df_out)} 行)")

print("\n" + "=" * 60)
print("✅ CSV 生成完成！")
print("=" * 60)
print("\n生成的文件：")
print("  00_daily_stats.csv ............ 每日统计汇总")
print("  01_books_summary.csv ......... 书籍总体统计（按借阅次数排序）")
print("  02_all_details.csv ........... 所有借阅详细信息")
print(f"  books_details/ ............... （目录下 {len(book_stats)} 个CSV文件）")
print("                           每本书的每日借阅统计")
print("=" * 60)
