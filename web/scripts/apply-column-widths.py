"""把用户填好的列宽表回写到 src/sheet2/columnWidths.ts。
用法：python3 scripts/apply-column-widths.py docs/sheet-column-widths.xlsx（或 .csv）
只改「新宽度（像素）」一列填了正整数的行，按「配置 key」匹配。"""
import csv, re, sys, pathlib
src = pathlib.Path(__file__).resolve().parent.parent / 'src/sheet2/columnWidths.ts'
path = sys.argv[1]
if path.endswith('.xlsx'):
    import openpyxl
    rows = [[c for c in r] for r in openpyxl.load_workbook(path).active.iter_rows(min_row=2, values_only=True)]
else:
    rows = list(csv.reader(open(path, encoding='utf-8-sig')))[1:]
text = src.read_text(encoding='utf-8')
n = 0
for r in rows:
    key, new = r[3], r[5]
    if key and new not in (None, '') and str(new).strip().isdigit():
        text, k = re.subn(r"('%s': )\d+" % re.escape(str(key)), r'\g<1>%d' % int(str(new).strip()), text)
        n += k
src.write_text(text, encoding='utf-8')
print(f'已回写 {n} 项')
