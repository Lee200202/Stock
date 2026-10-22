"""立場必須綁定來源，不以價位推測語意。"""
import re


def source_direction_consensus(entry, normalize):
    """只恢復來源已確認、方向與中性收錄票分歧的個股。
    
    只使用已驗證回覆，不採原始或無效答案。公司用途異議、反向立場、交易與部位判斷都不能當成棄權。"""
    if (entry.get('tier') != '未定' or entry.get('current') not in ('ignored', 'history', 'uncertain', '')
            or entry.get('identity_prior') == 'ordinary' or entry.get('trade_check')):
        return None
    names = {normalize(n) for n in entry.get('source_names', []) if isinstance(n, str) and len(normalize(n)) >= 2}
    if not names:
        return None
    attempts = [a for a in entry.get('attempts', []) if a.get('class')]
    if len(attempts) < 3:
        return None
    directions = {a['class'] for a in attempts if a['class'] != 'ignored'}
    if len(directions) != 1:
        return None
    target = next(iter(directions))
    if target not in ('watch_watch', 'watch_avoid'):
        return None
    supporters = [a for a in attempts if a['class'] == target]
    if len(supporters) < 2 or len(supporters) * 3 < len(attempts) * 2:
        return None
    for attempt in attempts:
        answer = attempt.get('answer') or {}
        semantic = answer.get('semantic') or {}
        if semantic.get('entity') != 'company' or answer.get('about_itself') is not True:
            return None
        if attempt['class'] == 'ignored':
            if semantic.get('role') not in ('general_example', 'neutral_description'):
                return None
        elif (semantic.get('role') not in ('current_view', 'current_risk')
              or not any(n in normalize(semantic.get('quote', '')) for n in names)):
            return None
    return target, f'{len(supporters)}／{len(attempts)}份有效本股具名方向一致；其餘僅為收錄中性，無反向、身分或成交反對'


def units(zones, size=80, step=55):
    """以固定且重疊的原文片段切分，不猜測句子或話題邊界。"""
    result, seen = [], set()
    for zone in zones:
        for at in range(0, len(zone), step):
            text = zone[at:at + size]
            if len(text) < 8 or text in seen:
                continue
            seen.add(text)
            result.append({'id': 'u' + str(len(result)), 'text': text})
    return result


def resolve_units(answer, source_units):
    """只解析本候選的來源編號，未知編號不能產生證據。"""
    if not isinstance(answer, dict):
        return answer
    answer = dict(answer)
    index = {unit['id']: unit['text'] for unit in source_units}
    semantic = answer.get('semantic')
    if isinstance(semantic, dict) and ('anchor_id' in semantic or 'quote_id' in semantic):
        semantic = dict(semantic)
        semantic['anchor'] = index.get(semantic['anchor_id'], '') if isinstance(semantic.get('anchor_id'), str) else ''
        semantic['quote'] = index.get(semantic['quote_id'], '') if isinstance(semantic.get('quote_id'), str) else ''
        answer['semantic'] = semantic
    if isinstance(answer.get('quote_ids'), dict):
        answer['quotes'] = {field: [index[i] for i in ids if isinstance(i, str) and i in index]
                            for field, ids in answer['quote_ids'].items()
                            if isinstance(ids, list)}
    return answer


def source_excerpt(quote, source, normalize):
    """只將標點差異還原成逐字來源，不新增字詞。"""
    q = normalize(quote)
    if len(q) < 8:
        return ''
    at = normalize(source).find(q)
    if at < 0:
        return ''
    offsets = [i for i, ch in enumerate(source) for _ in normalize(ch)]
    if at+len(q) > len(offsets):
        return ''
    return source[offsets[at]:offsets[at+len(q)-1]+1]


def windows(names, transcript, normalize):
    """較寬上下文供判斷，不代表其中所有內容都屬於本股。"""
    text = re.sub(r'\s+', '', str(transcript or ''))
    spans = []
    for name in set(names):
        if len(normalize(name)) < 2:
            continue
        for match in re.finditer(re.escape(name), text):
            a, b = max(0, match.start()-300), min(len(text), match.end()+350)
            spans.append((a, b))
    merged = []
    for a, b in sorted(set(spans)):
        if merged and a <= merged[-1][1]:
            merged[-1][1] = max(b, merged[-1][1])
        else:
            merged.append([a, b])
    return [text[a:b] for a, b in merged][:12]


def stance(answer, names, zones, normalize):
    """語意答案缺漏或來源無法綁定時回傳None，不當作反對票。
    
    本股具名錨點與語意結論必須出現在同一有界來源窗，正規化後距離不超過240字。共同結論須明確包含本股成員，生成文字不能作證據。"""
    if not isinstance(answer, dict) or not zones:
        return None
    semantic = answer.get('semantic')
    if not isinstance(semantic, dict):
        return None
    entity, role, direction = (semantic.get(k) for k in ('entity', 'role', 'direction'))
    roles = {'current_view', 'current_risk', 'historical_only', 'general_example', 'neutral_description'}
    if entity not in ('company', 'ordinary') or role not in roles or direction not in ('positive', 'negative', 'neutral'):
        return None
    if semantic.get('scope') not in ('self', 'shared'):
        return None
    anchor, quote = semantic.get('anchor'), semantic.get('quote')
    members = semantic.get('members')
    if not isinstance(anchor, str) or not isinstance(quote, str) or not isinstance(members, list):
        return None
    a, q = normalize(anchor), normalize(quote)
    own = {normalize(n) for n in names if len(normalize(n)) >= 2}
    named_members = {normalize(n) for n in members if isinstance(n, str)}
    # 含本股原字的錨點已能識別目標；
    # 重複的成員名單漏填不應使其無效，但明確衝突的名單仍拒收。
    if named_members and not own & named_members:
        return None
    bound = own                        # 正式名稱與已核對聽寫名稱屬於同一身分。
    if len(a) < 4 or len(q) < 8 or not any(n in a for n in bound):
        return None
    supported = False
    anchor_found = False
    named_quote_found = False
    for zone in zones:
        flat = normalize(zone)
        anchors = [m.start() for m in re.finditer(re.escape(a), flat)]
        quotes = [m.start() for m in re.finditer(re.escape(q), flat)]
        anchor_found = anchor_found or bool(anchors)
        named_quote_found = named_quote_found or bool(quotes and any(n in q for n in bound))
        if any(abs(x-y) <= 240 for x in anchors for y in quotes):
            supported = True
            break
    if not supported and not (anchor_found and named_quote_found):
        return None
    # 依序核對公司身分、事件角色與立場，任何一項都不新增交易。
    if entity == 'ordinary':
        return 'ignored', '語意核對：原詞為一般用語｜' + quote[:60]
    if role == 'historical_only':
        return 'history', '語意核對：只有過去交易，沒有當下立場｜' + quote[:60]
    if role in ('general_example', 'neutral_description'):
        return 'ignored', '語意核對：教學例或中性行情，沒有本股當下立場｜' + quote[:60]
    if role == 'current_view' and semantic.get('entry_restricted_now') is True:
        return 'watch_avoid', '語意事實：現在進場條件未到｜' + quote[:60]
    if role == 'current_view' and semantic.get('sell_discouraged') is True:
        return 'watch_watch', '語意事實：勸續抱而非禁止進場｜' + quote[:60]
    if role == 'current_risk' and direction == 'negative':
        return 'watch_avoid', '語意核對：本股為當下風險例｜' + quote[:60]
    if role == 'current_view' and direction in ('positive', 'negative'):
        return ('watch_watch' if direction == 'positive' else 'watch_avoid'), '語意核對：本股當下立場｜' + quote[:60]
    return None


def phonetic_candidates(source, directory, name_sound, char_sounds, inside_term):
    """保守提議公司身分，不推測分類或價位。"""
    index = {}
    for code, name in directory.items():
        name = re.sub(r'[*＊]+$', '', str(name))
        if not re.fullmatch(r'[1-9]\d{3}', str(code)) or not 2 <= len(name) <= 4:
            continue
        sound = name_sound(name)
        if sound:
            index.setdefault(sound, []).append((str(code), name))
    pins = char_sounds(source)
    output, seen = [], set()
    for size in (2, 3, 4):
        for at in range(len(source)-size+1):
            heard = source[at:at+size]
            matches = [(code, name) for code, name in index.get(tuple(pins[at:at+size]), [])
                       if set(heard) & set(name)]
            if len(matches) != 1 or not re.fullmatch(r'[\u4e00-\u9fff]+', heard):
                continue
            code, name = matches[0]
            if heard == name or not set(heard) & set(name) or (code, heard) in seen or inside_term(source, at, size):
                continue
            nearby = source[max(0, at-10):at+size+10]
            if not re.search(r'股|漲|跌|買|賣|均線|季[限線]|年[限線]|創高|主力|融資|外資|投信|最強', nearby):
                continue
            seen.add((code, heard))
            output.append({'code': code, 'heard': heard, 'quote': source[max(0, at-45):at+size+60],
                           'origin': 'phonetic_candidate_not_confirmed'})
    return output
