"""把用户填好的列宽表回写到 src/sheet2/columnWidths.ts。
用法：python3 scripts/apply-column-widths.py docs/sheet-column-widths.xlsx（或 .csv）
- 「新宽度（像素）」填了正整数的行，按「配置 key」改 COLUMN_WIDTHS。
- 「对齐方式」填了的行，改 COLUMN_ALIGN。写法：居左 / 居中 / 居右；几列共用一个 key 时写
  「主水居右、客水居左」这种「小标题 + 居X」，用顿号或逗号分隔。
- 留空的格子不改；无法识别的值不改，并在最后列出来。"""
import csv, re, sys, pathlib
src = pathlib.Path(__file__).resolve().parent.parent / 'src/sheet2/columnWidths.ts'
path = sys.argv[1]
if path.endswith('.xlsx'):
    import openpyxl
    ws = openpyxl.load_workbook(path).active
    allrows = [list(r) for r in ws.iter_rows(values_only=True)]
else:
    allrows = list(csv.reader(open(path, encoding='utf-8-sig')))
hdr = [str(h or '').strip() for h in allrows[0]]
def col(name):
    for i, h in enumerate(hdr):
        if h.startswith(name):
            return i
    return None
ik, iw = col('配置 key'), col('新宽度')
# 模板里是「新对齐方式」；用户早先自己加的一列叫「对齐方式」，两种都认
ia = col('新对齐方式') if col('新对齐方式') is not None else col('对齐方式')
AL = {'居左': 'left', '左': 'left', '左对齐': 'left', '居中': 'center', '中': 'center', '居中对齐': 'center', '居右': 'right', '右': 'right', '右对齐': 'right'}
text = src.read_text(encoding='utf-8')
head, sep, tail = text.partition('export const COLUMN_ALIGN')
changed, problems = [], []
for r in allrows[1:]:
    key = str(r[ik] or '').strip() if ik is not None else ''
    if not key:
        continue
    new = r[iw] if iw is not None and iw < len(r) else None
    if new not in (None, ''):
        sv = str(new).strip()
        if re.fullmatch(r'\d+(\.0)?', sv):
            head, k = re.subn(r"('%s': )\d+" % re.escape(key), r'\g<1>%d' % int(float(sv)), head)
            changed.append(f'{key} 宽度 → {int(float(sv))}') if k else problems.append(f'{key}：配置里没有这个 key')
        else:
            problems.append(f'{key}：新宽度「{sv}」不是正整数')
    al = r[ia] if ia is not None and ia < len(r) else None
    if al not in (None, ''):
        sv = str(al).strip()
        val = None
        if sv in AL:
            val = f"'{AL[sv]}'"
        else:
            parts = [p for p in re.split(r'[、，,；;]\s*', sv) if p]
            obj = []
            for p in parts:
                m = re.fullmatch(r'(.+?)(居左|居中|居右|左对齐|右对齐|居中对齐)', p)
                if not m:
                    obj = None
                    break
                obj.append(f"{m.group(1)}: '{AL[m.group(2)]}'")
            if obj:
                val = '{ ' + ', '.join(obj) + ' }'
        if val is None:
            problems.append(f'{key}：对齐方式「{sv}」无法识别')
        else:
            tail, k = re.subn(r"('%s': )[^\n]*?,\n" % re.escape(key), lambda m: m.group(1) + val + ',\n', tail, count=1)
            changed.append(f'{key} 对齐 → {sv}') if k else problems.append(f'{key}：对齐配置里没有这个 key')
src.write_text(head + sep + tail, encoding='utf-8')
print(f'已回写 {len(changed)} 项')
for c in changed:
    print('  ' + c)
if problems:
    print('未回写：')
    for p in problems:
        print('  ' + p)
