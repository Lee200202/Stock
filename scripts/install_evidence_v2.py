"""Apply the bounded evidence-v2 upgrade to the current standalone pipeline."""
import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
path = ROOT / 'pipeline/pipeline.py'
source = path.read_text(encoding='utf-8-sig')
if 'def finish_transcript_refresh(' in source:
    raise SystemExit('evidence-v2 已安裝；請修改 pipeline/pipeline.py，使用 sync_quality.py 同步，勿重套遷移。')
runtime = (ROOT / 'scripts/evidence_v2.py').read_text(encoding='utf-8')
replacements = {}
additions = []
old = ast.parse(source)
old_names = {n.name for n in old.body if isinstance(n, ast.FunctionDef)}
old_names |= {t.id for n in old.body if isinstance(n, ast.Assign) for t in n.targets if isinstance(t, ast.Name)}
for n in ast.parse(runtime).body:
    name = n.name if isinstance(n, ast.FunctionDef) else n.targets[0].id if isinstance(n, ast.Assign) else None
    if name:
        if name in old_names:
            replacements[name] = ast.get_source_segment(runtime, n)
        else:
            additions.append(ast.get_source_segment(runtime, n))
lines = source.splitlines(keepends=True)
for n in reversed(old.body):
    name = n.name if isinstance(n, ast.FunctionDef) else n.targets[0].id if isinstance(n, ast.Assign) and isinstance(n.targets[0], ast.Name) else None
    if name in replacements:
        lines[n.lineno-1:n.end_lineno] = [replacements[name] + '\n']
source = ''.join(lines)
at = source.index('def _ev_norm(')
source = source[:at] + '\n\n'.join(additions) + '\n\n' + source[at:]

# Validation must use the exact evidence that survived source verification.
source = source.replace('evidence = _ev_norm(chr(10).join(str(q) for q in quotes))',
                        'evidence = _ev_norm(chr(10).join(str(q) for q in good))')
source = source.replace('quotes = good\n', "quotes = good\n            row['evidence'] = good\n")
source = source.replace('row["_疑點"] = why\n', 'row["_疑點"] = why\n        row["_原分類"] = row.get("_原分類") or cat\n', 1)
source = source.replace('if published == 0 and not signals["history"]:',
                        'if published == 0 and not signals["history"] and not signals.get("market") and not signals.get("ignored") and not signals.get("uncertain"):')
needle = '    published = sum(len(signals.get(c) or []) for c in SIGNAL_CATEGORIES)'
source = source.replace(needle, '''    # Market facts obey the same evidence rule as stocks, including every number.
    market_keep = []
    for item in signals.get('market', []):
        quotes = item.get('evidence') or []
        evidence = _ev_norm('\\n'.join(quotes))
        numbers = re.findall(r'\\d+(?:[.,]\\d+)*(?:[xX]+)?', str(item.get('text') or ''))
        valid = bool(quotes) and all(_quote_is_real(q, hay) for q in quotes)
        valid = valid and all(_ev_norm(n) in evidence for n in numbers)
        if valid:
            item['_evidence_verified'] = True
            market_keep.append(item)
        else:
            signals.setdefault('_repair_gaps', []).append('大盤摘要有未驗證的引用或數字：' + str(item.get('text','')))
    signals['market'] = market_keep
''' + needle)
source = source.replace('    # 這一輪做了哪些判定，一起記進「判定歷程」給後台看。', '''    if signals.get('_quality_requires_review'):
        flush_decisions(ss, date_str)
        raise ValueError('證據修復後仍有待確認項目；完整候選已保存於逐字稿判讀稽核，未以部分結果覆蓋整日資料。請核對來源/判定歷程。')
    # 這一輪做了哪些判定，一起記進「判定歷程」給後台看。''')
# Do not claim the model is switching sources: it always reads the raw transcript.
source = source.replace('f"擷取改讀原始逐字稿（潤飾稿可能整段消失）"', 'f"原始逐字稿仍是唯一判讀來源"')
# All temporary article failures fall back to verified structured content.
a = source.index('    except RuntimeError as e:\n', source.index('def build_article('))
b = source.index('\n\ndef ', a)
source = source[:a] + '''    except (RuntimeError, RateLimited) as e:
        print('撰稿服務未完成，改用已驗證資料的固定格式：' + str(e)[:160])
        return canonical_article(signals, date_str)
''' + source[b:]
# A failed dependent refresh must not be reported as fully complete.
a = source.index("        if not result or not result.get('ok'):", source.index("result = maybe_refresh_site(only=['codes', 'tracker', 'perfhist', 'perf']", source.index('def run_admin_job(')))
b = source.index('    except Exception as e:', a)
source = source[:a] + "        if not result or not result.get('ok'):\n            raise RuntimeError('持股追蹤／績效未完成；已保存資料，下次只需續跑刷新')\n" + source[b:]
source = source.replace('    mark_status(ss, vid, date_str, video["title"], "完成")\n    _report',
                        '    mark_status(ss, vid, date_str, video["title"], "處理中")\n    _report')
source = source.replace('    job_progress(job, step="完成", done=len(ADMIN_STEP_NAMES),',
                        '    mark_status(ss, vid, date_str, video["title"], "完成")\n    job_progress(job, step="完成", done=len(ADMIN_STEP_NAMES),')
source = source.replace('"下游尚未部署 evidence-v1，請先更新 Apps Script 並部署新版本；未執行刷新"',
                        '"下游尚未部署 evidence-v2，請先更新 Apps Script 並部署新版本；未執行刷新"')
source = source.replace('if "evidence-v1" not in feats:', 'if "evidence-v2" not in feats:')
source = source.replace('audit-reads-raw,polish-length-floor,extract-prompt-v8,evidence-v1,',
                        'audit-reads-raw,polish-length-floor,extract-prompt-v9,evidence-v1,evidence-v2,')
ast.parse(source)
path.write_text(source, encoding='utf-8')
(ROOT / 'pipeline.py').write_text(source, encoding='utf-8')
print('Embedded evidence-v2; root and nested pipeline synchronized')
