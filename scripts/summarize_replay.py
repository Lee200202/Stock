"""GitHub 執行摘要：分開來源判斷、通用關卡與流程輸出，網站不作答案。"""
import json
import os
import sys
from pathlib import Path


def summarize(report):
    lines = ['## 原稿重播診斷', '', '**網站現況不作答案；通用檢查通過不等於分類或說明全面正確。**', '']
    if not report:
        return '\n'.join(lines + ['未取得重播結果；分類品質未評估。']) + '\n'
    lines += [f"- 日期：{report.get('date', '未知')}",
              f"- 原稿 SHA256：`{report.get('source_sha256', '未取得')}`",
              f"- 結果狀態：{report.get('status', '未知')}；結束代碼：{report.get('exit_code')}",
              f"- 通用檢查錯誤：{len(report.get('errors') or [])}（只涵蓋已實作關卡）"]
    review = report.get('independent_review_comparison')
    if review:
        lines += ['', '### 獨立來源審核草稿與流程的差異', '',
                  '代理草稿不是核定人工金標；只在推論後比對，沒有送進模型。', '',
                  f"- 草稿指紋：`{review.get('review_sha256')}`",
                  f"- 草稿曝光紀錄：{review.get('target_result_exposure')}",
                  f"- 草稿部位／通知核對範圍：{review.get('as_of_positions')}",
                  f"- 分類相符：{review.get('matched_candidates')}/{review.get('decided_candidates')} 個已判讀候選",
                  f"- 待核候選：{len(review.get('unresolved') or [])}（不計正確排除）",
                  f"- 沒有獨立判斷的輸出：{len(review.get('unreviewed_output_codes') or [])}（待核）", '',
                  '| 個股 | 來源審核草稿 | 流程輸出 | 獨立判斷依據 | 流程痕跡（待核） |', '|---|---|---|---|---|']
        labels = {'buy': '買入', 'sell': '賣出', 'holdings': '會員持股',
                  'watch_watch': '觀望注意', 'watch_avoid': '觀望不碰'}
        def cell(value):
            return str(value).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;').replace('|', '&#124;').replace('\n', ' ')
        def label(values):
            return '／'.join(labels.get(v, v) for v in values) or '不收錄'
        for row in review.get('differences') or []:
            lines.append('| ' + ' | '.join(map(cell, (f"{row['name']} {row['code']}",
                label(row['expected']), label(row['actual']), row['basis'],
                (row.get('workflow_diagnostic') or {}).get('status', '') + '：' +
                (row.get('workflow_diagnostic') or {}).get('basis', '未保存')))) + ' |')
        if not review.get('differences'):
            lines.append('| 已判讀候選 | 與草稿相符 | 仍須檢查說明及部位事實 | 不作準確率宣稱 | — |')
    elif report.get('golden_comparison'):
        lines += ['', '分類有既有答案檔比對；審核來源與適用範圍以檔內紀錄為準，不由golden路徑推定人工核定。']
    else:
        lines += ['', '**分類品質未評估：未提供核定答案或獨立來源審核草稿。**']
    lines += ['', '漏收須回查：候選建立 → 可引用範圍 → 事實題／無效票 → 票型與定案 → 收尾刪列。',
              '分類相符不證明部位、價位、時間及說明歸屬正確；待核差異不是程式故障，也不是網站答案不符。']
    return '\n'.join(lines) + '\n'


if __name__ == '__main__':
    path = Path(sys.argv[1])
    report = json.loads(path.read_text(encoding='utf-8')) if path.exists() else None
    text = summarize(report)
    destination = os.environ.get('GITHUB_STEP_SUMMARY')
    if destination:
        with open(destination, 'a', encoding='utf-8') as output:
            output.write(text)
    else:
        print(text)
