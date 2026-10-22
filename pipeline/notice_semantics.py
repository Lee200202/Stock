"""以原文綁定通知語意；一般建議不等於已完成交易。"""
import re

VERSION = 'notice-v182-20261015'

def context(body, name, code=''):
    stem = re.sub(r'(?:[-＊*]|KY)+$', '', str(name)).strip()
    forms = [x for x in (str(name), stem, str(code)) if len(x) >= 2]
    return '。'.join(s for s in re.split(r'[。！？!?；;\n]', str(body or ''))
                    if any(x in s for x in forms))

def classify(body, name, code, action):
    """回傳允許操作、語句類型與原文證據；沒有操作時回傳空值。
    
    明確會員指令保留既有操作含義；選擇性建議不建立已成交部位。持有狀態與目前進場立場分開處理。"""
    own = context(body, name, code)
    if not own:
        return '', 'missing', ''
    verb = r'(?:賣出|賣掉|減碼|出清|站賣方)' if action == '賣出' else r'(?:買進|買入|買回|加碼|站買方)'
    current_order = bool(re.search(r'(?:請|應|務必|建議)[^，,。]{0,25}' + verb, own))
    if action != '賣出':
        current_order = current_order or bool(re.search(r'(?:資金|轉為).{0,12}買進', own))
    current_order = current_order or bool(re.search(r'(?:市價|[0-9.]+元(?:以上|以下))[^，,。]{0,8}'+verb,own))
    blocked = bool(re.search(r'(?:不急(?:於|著)?|不要急(?:於|著)?|勿|不必|不能|不可|不宜|不要).{0,6}(?:買回|買進|買入|追買|追高)|(?:條件|買點).{0,8}(?:尚未|還未|未到|未出現|未成熟)', own))
    optional = bool(re.search(r'可以買|可買|可考慮買', own)) and not current_order
    history = bool(re.search(r'(?:高點|高檔|先前|之前|昨天|昨日|早上|已經|已).{0,12}(?:賣出|賣掉|出清)(?:者|過|後|了|的)', own))
    if blocked and action in ('買入', '賣出', '觀望注意', '觀望不碰'):
        # 明確的新賣出指令，可以與暫不買回的禁令並存。
        if action == '賣出' and current_order:
            return action, 'order', own
        return '觀望不碰', 'not_yet', own
    if optional and action in ('買入', '觀望注意'):
        return '觀望注意', 'optional', own
    if action in ('買入', '賣出'):
        if not re.search(r'買|加碼|站買方' if action=='買入' else r'賣|出清|減碼|了結|停利|停損|站賣方',own):
            return '', 'missing_action', own
        if history and not current_order:
            return '', 'history', own
        if re.search(r'(?:不能|不可|不要|不准|勿).{0,4}(?:買|賣)', own) and not current_order:
            return '', 'prohibited_trade', own
        return action, 'order' if current_order else 'trade_candidate', own
    return action, 'holding' if action == '會員持股' else 'stance', own
