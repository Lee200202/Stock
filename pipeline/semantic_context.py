"""Source-anchored stance contracts; semantic claims are never inferred from prices."""
import re


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
    bound = own & {normalize(n) for n in members if isinstance(n, str)}
    if len(a) < 4 or len(q) < 8 or not any(n in a for n in bound):
        return None
    supported = False
    for zone in zones:
        flat = normalize(zone)
        anchors = [m.start() for m in re.finditer(re.escape(a), flat)]
        quotes = [m.start() for m in re.finditer(re.escape(q), flat)]
        if any(abs(x-y) <= 600 for x in anchors for y in quotes):
            supported = True
            break
    if not supported:
        return None
    # Entity identity, event role, then stance. None of these creates a trade.
    if entity == 'ordinary':
        return 'ignored', '語意核對：原詞為一般用語｜' + quote[:60]
    if role == 'historical_only':
        return 'history', '語意核對：只有過去交易，沒有當下立場｜' + quote[:60]
    if role in ('general_example', 'neutral_description'):
        return 'ignored', '語意核對：教學例或中性行情，沒有本股當下立場｜' + quote[:60]
    if role == 'current_risk' and direction == 'negative':
        return 'watch_avoid', '語意核對：本股為當下風險例｜' + quote[:60]
    if role == 'current_view' and direction in ('positive', 'negative'):
        return ('watch_watch' if direction == 'positive' else 'watch_avoid'), '語意核對：本股當下立場｜' + quote[:60]
    return None
