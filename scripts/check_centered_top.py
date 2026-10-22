"""離線驗證前後台實際回頂控制項的響應式排版。"""
import json
from playwright.sync_api import sync_playwright
from check_tech_story import serve_local,FAKE_API
url,server=serve_local()
try:
 with sync_playwright() as pw:
  b=pw.chromium.launch()
  for w in (1440,768,390,360):
   for mode in ('front','tech','admin'):
    p=b.new_page(viewport={'width':w,'height':850},reduced_motion='reduce')
    def fake(route):
     method=route.request.post_data_json.get('method','')
     result={'ok':True,'data':{'today':'2026/10/15','closedToday':False,'hasData':False,'postedDates':[],'holidays':[]}} if method=='apiAdminLogin' else []
     route.fulfill(content_type='application/json',body=json.dumps({'ok':True,'result':result}))
    p.route(FAKE_API,fake)
    p.goto(url.split('?')[0]+('admin.html' if mode=='admin' else '?tab=tech' if mode=='tech' else ''),wait_until='domcontentloaded')
    if mode=='admin':
     p.locator('#key').fill('offline');p.locator('#loginBtn').click()
    p.evaluate('window.scrollTo(0,document.documentElement.scrollHeight)')
    p.locator('#toTop').wait_for(state='visible')
    box=p.locator('#toTop').bounding_box();assert abs(box['x']+box['width']/2-w/2)<2,(w,mode,box)
    assert 0<=850-box['y']-box['height']<=24,(w,mode,box)
    assert box['height']>=44
    p.locator('#toTop').click();p.wait_for_function('window.scrollY<2')
    print(w,mode,'centered, bottom, scroll passed');p.close()
  b.close()
finally:server.shutdown()
