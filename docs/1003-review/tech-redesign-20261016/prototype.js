/* 本機設計原型，不抓取資料、不呼叫模型、不寫入資料或通知。 */
(() => {
  'use strict';
  const pages = [...document.querySelectorAll('.chapter')];
  const svg = document.querySelector('#cache-svg');
  const stage = document.querySelector('#cache-stage');
  const dot = document.querySelector('#traveller');
  const pauseButton = document.querySelector('#pause');
  const reduce = matchMedia('(prefers-reduced-motion: reduce)');
  const narrow = matchMedia('(max-width:700px)');
  const cases = {
    hit: {title:'有效快取',result:'直接回覆已整理的答案。',detail:'手機 → 快取 → 手機，不讀試算表。',note:'命中快取：查詢已有存好的答案，而且仍在有效時間內。',paths:['request','return']},
    backup:{title:'背景備份',result:'有人沒來，背景也可先準備。',detail:'可用備份先回覆；背景更新是另一項工作。',note:'是否可使用由有效時間決定，不以半小時沒訪客推論。',paths:['request','return']},
    history:{title:'歷史日期',result:'這個日期沒有有效答案，才回源整理。',detail:'手機 → 快取 → 試算表 → 快取 → 手機。',note:'歷史查詢也可能命中；此例特別指定尚無有效答案。',paths:['request','source','store','return']},
    expired:{title:'備份過期',result:'舊備份不能冒充最新資料。',detail:'回源成功後，更新答案再回覆手機。',note:'此例示範回源成功；正式頁還須呈現回源也失敗的結果。',paths:['request','source','store','return']}
  };
  let active = 'cache', caseKey = 'hit', elapsed = 0, previous = null, frame = 0, cycles=0;
  let inView = false, manualPause = false, modalOpen = false;
  const duration = 5200;
  // 窄螢幕重新排列同一張圖，不縮小標籤。
  const nodes=[...svg.children].filter(el=>el.tagName.toLowerCase()==='g'&&!el.classList.contains('mobile-names'));
  const widePaths={request:'M260 170 C350 170 350 110 480 110',return:'M480 160 C350 160 370 270 260 270',source:'M650 190 C760 220 760 280 650 290',store:'M520 290 C425 260 425 225 520 190'};
  const smallPaths={request:'M158 130 C180 130 178 75 200 75',return:'M200 110 C175 110 180 208 158 208',source:'M327 133 C415 155 415 222 327 240',store:'M230 240 C175 218 180 157 230 133'};
  function layoutDiagram(){
    svg.setAttribute('viewBox',narrow.matches?'0 0 430 420':'0 0 780 480');
    ['translate(-24,20) scale(.7)','translate(-160,-10) scale(.75)','translate(-160,22) scale(.75)'].forEach((value,i)=>{
      if(narrow.matches)nodes[i].setAttribute('transform',value);else nodes[i].removeAttribute('transform');
    });
    Object.entries(narrow.matches?smallPaths:widePaths).forEach(([id,d])=>svg.querySelector('#'+id).setAttribute('d',d));
    elapsed=0; previous=null;
  }
  function eligible() {return active === 'cache' && inView && !document.hidden && !manualPause && !modalOpen && !reduce.matches;}
  function tick(now) {
    if (!eligible()) {frame=0; previous=null; return;}
    if (previous !== null) {
      const next=elapsed+Math.min(now-previous,80);
      if(next>=duration){cycles++;stage.dataset.cycles=String(cycles);}
      elapsed=next%duration;
    }
    previous=now;
    const ids=cases[caseKey].paths, t=elapsed/duration*ids.length;
    const path=svg.querySelector('#'+ids[Math.min(ids.length-1,Math.floor(t))]);
    const p=path.getPointAtLength(path.getTotalLength()*(t%1));
    dot.setAttribute('transform',`translate(${p.x} ${p.y})`);
    frame=requestAnimationFrame(tick);
  }
  function sync() {
    const running=eligible(); stage.classList.toggle('is-playing',running);
    stage.dataset.playing=String(running);
    pauseButton.disabled=reduce.matches;
    pauseButton.textContent=reduce.matches?'已減少動態':manualPause?'繼續播放':'暫停圖解';
    if(running&&!frame)frame=requestAnimationFrame(tick);
    if(!running&&frame){cancelAnimationFrame(frame);frame=0;previous=null;}
  }
  function selectCase(key) {
    const c=cases[key]; if(!c)return;
    caseKey=key; elapsed=0; previous=null; cycles=0;stage.dataset.cycles='0';svg.dataset.case=key;
    ['heading','result','detail'].forEach((suffix,i)=>document.querySelector('#case-'+suffix).textContent=c[['title','result','detail'][i]]);
    document.querySelector('#flow-note').textContent=c.note;
    document.querySelectorAll('[data-case]').forEach(b=>{if(b.tagName==='BUTTON')b.setAttribute('aria-pressed',String(b.dataset.case===key));});
    svg.querySelectorAll('.route').forEach(p=>p.classList.toggle('is-route',c.paths.includes(p.id)));
    sync();
  }
  function route(focus=false) {
    const id=location.hash.slice(1); active=pages.some(p=>p.id===id)?id:'cache';
    pages.forEach(p=>p.hidden=p.id!==active);
    document.querySelectorAll('.chapter-links a').forEach(a=>{if(a.hash==='#'+active)a.setAttribute('aria-current','page');else a.removeAttribute('aria-current');});
    sync();
    if(focus){document.querySelector('#'+active+' h2').focus({preventScroll:true}); document.querySelector('#'+active).scrollIntoView({behavior:'instant',block:'start'});}
  }
  document.querySelectorAll('.chapter-links a,.chapter-pager a').forEach(a=>a.addEventListener('click',e=>{
    e.preventDefault(); history.pushState(null,'',a.hash); route(true);
  }));
  addEventListener('popstate',()=>route(true)); addEventListener('hashchange',()=>route(true));
  document.querySelectorAll('button[data-case]').forEach(b=>b.addEventListener('click',()=>{manualPause=true;selectCase(b.dataset.case);}));
  pauseButton.addEventListener('click',()=>{manualPause=!manualPause;sync();});
  document.querySelector('#replay').addEventListener('click',()=>{manualPause=false;elapsed=0;previous=null;cycles=0;stage.dataset.cycles='0';sync();});
  document.querySelector('#theme').addEventListener('click',()=>{document.documentElement.dataset.theme=document.documentElement.dataset.theme==='dark'?'light':'dark';});
  document.querySelector('#to-top').addEventListener('click',()=>scrollTo({top:0,behavior:reduce.matches?'instant':'smooth'}));
  // 保持要求的中央按鈕不遮住互動內容。
  let collisionFrame=0;
  function avoidOverlap(){
    collisionFrame=0; const top=document.querySelector('#to-top'),r=top.getBoundingClientRect();
    const blocked=[...document.querySelectorAll('main button, main a')].some(el=>{
      if(!el.getClientRects().length)return false;const b=el.getBoundingClientRect();
      return b.left<r.right+8&&b.right>r.left-8&&b.top<r.bottom+8&&b.bottom>r.top-8;
    });
    top.style.visibility=blocked||modalOpen?'hidden':'visible';
  }
  function scheduleOverlap(){if(!collisionFrame)collisionFrame=requestAnimationFrame(avoidOverlap);}
  addEventListener('scroll',scheduleOverlap,{passive:true});addEventListener('resize',scheduleOverlap);
  document.querySelectorAll('[data-dialog]').forEach(b=>b.addEventListener('click',()=>{
    const dialog=document.getElementById(b.dataset.dialog);if(!dialog)return;
    dialog._opener=b;modalOpen=true;sync();dialog.showModal();scheduleOverlap();
  }));
  document.querySelectorAll('dialog').forEach(d=>{
    d.querySelector('.dialog-close').addEventListener('click',()=>d.close());
    d.addEventListener('close',()=>{modalOpen=false;sync();d._opener?.focus({preventScroll:true});scheduleOverlap();});
  });
  new IntersectionObserver(entries=>{inView=entries[0].isIntersecting;sync();},{threshold:0.2}).observe(svg);
  document.addEventListener('visibilitychange',sync);reduce.addEventListener('change',sync);
  narrow.addEventListener('change',layoutDiagram);
  // 必要元件與控制器都初始化完成後，再啟用互動增強。
  layoutDiagram();selectCase('hit'); document.documentElement.classList.add('enhanced'); route();scheduleOverlap();
})();
