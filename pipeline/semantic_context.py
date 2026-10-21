"""Source-anchored stance contracts; semantic claims are never inferred from prices."""
import re


def source_excerpt(quote, source, normalize):
    """Resolve punctuation-only quote differences to literal source, never new words."""
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
    """Wider context is evidence to judge, not inherited ownership of its contents."""
    text = re.sub(r'\s+', '', str(transcript or ''))
    spans = []
    for name in set(names):
        if len(normalize(name)) < 2:
            continue
        for match in re.finditer(re.escape(name), text):
            a, b = max(0, match.start()-450), min(len(text), match.end()+550)
            spans.append((a, b))
    merged = []
    for a, b in sorted(set(spans)):
        if merged and a <= merged[-1][1]:
            merged[-1][1] = max(b, merged[-1][1])
        else:
            merged.append([a, b])
    return [text[a:b] for a, b in merged][:12]


def stance(answer, names, zones, normalize):
    """Return None for an incomplete/unbound semantic answer, not a negative vote.

    Both a literal target-name anchor and the semantic conclusion must occur in
    the same bounded source window, within 600 normalized characters. Shared
    conclusions require an explicit target member. No generated prose is evidence.
    """
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
    # A literal named anchor already identifies the target. An omitted redundant
    # members list must not invalidate it; an explicit conflicting list still does.
    if named_members and not own & named_members:
        return None
    bound = own                        # official and heard spellings are one identity
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
        if any(abs(x-y) <= 600 for x in anchors for y in quotes):
            supported = True
            break
    if not supported and not (anchor_found and named_quote_found):
        return None
    # Entity identity, event role, then stance. None of these creates a trade.
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
    """Conservative identity suggestions only; no category or price is inferred."""
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
