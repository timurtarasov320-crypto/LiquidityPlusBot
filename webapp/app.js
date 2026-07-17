const tg = window.Telegram?.WebApp;
if (tg) {
  tg.ready();
  tg.expand();
  tg.setHeaderColor("#070707");
  tg.setBackgroundColor("#050505");
}

const initData = tg?.initData || "";
const fallbackUser = tg?.initDataUnsafe?.user || {id: 0, first_name: "Trader"};

const $ = id => document.getElementById(id);
const fmt = n => Number(n || 0).toLocaleString(undefined,{maximumFractionDigits:2});

function setText(id, value){ const el=$(id); if(el) el.textContent=value; }

function renderSignals(items){
  const html = items?.length ? items.map(s => {
    const dir = String(s.direction || "").toUpperCase();
    const cls = dir === "LONG" ? "long" : "short";
    const result = s.result_percent == null ? "ACTIVE" : `${Number(s.result_percent)>=0?"+":""}${Number(s.result_percent).toFixed(2)}%`;
    return `<div class="signal">
      <h4>#${s.signal_id} · ${s.symbol}</h4>
      <span class="direction ${cls}">${dir}</span>
      <p>Entry ${s.entry} · SL ${s.stop_loss} · TP1 ${s.take_profit_1}</p>
      <span></span><b class="result">${result}</b>
    </div>`;
  }).join("") : '<div class="empty">Сигналов пока нет</div>';
  $("recent-signals").innerHTML = html;
  $("all-signals").innerHTML = html;
}

async function load(){
  try{
    const res = await fetch("/api/dashboard", {headers: {"X-Telegram-Init-Data": initData}});
    if(!res.ok) throw new Error(await res.text());
    const d = await res.json();

    setText("user-name", d.user.first_name || fallbackUser.first_name || "Trader");
    setText("account-level", d.user.vip ? "PREMIUM" : "STANDARD");
    setText("vip-status", d.user.vip ? "VIP ACTIVE" : "VIP OFF");
    setText("winrate", `${Number(d.stats.winrate).toFixed(1)}%`);
    $("winrate-ring").style.background = `conic-gradient(var(--accent) ${Math.min(100,d.stats.winrate)*3.6}deg,#222 0deg)`;
    setText("active-signals", d.stats.active);
    setText("total-signals", d.stats.total);
    setText("total-result", `${Number(d.stats.total_result)>=0?"+":""}${Number(d.stats.total_result).toFixed(2)}%`);
    setText("referrals", d.user.referrals);
    setText("profile-id", d.user.id);
    setText("profile-vip", d.user.vip ? "ACTIVE" : "INACTIVE");
    setText("profile-discount", `${d.user.discount}%`);
    setText("profile-free", `${d.user.free_remaining}/${d.user.free_limit}`);

    for(const t of d.market || []){
      const key=t.symbol.toLowerCase();
      setText(`${key}-price`, `$${fmt(t.price)}`);
      setText(`${key}-change`, `${t.change>=0?"+":""}${Number(t.change).toFixed(2)}%`);
      $(`${key}-change`).style.color = t.change>=0 ? "var(--green)" : "var(--red)";
    }
    renderSignals(d.history);
  }catch(e){
    console.error(e);
    setText("welcome","WebApp запущен в демо-режиме");
    setText("user-name",fallbackUser.first_name || "Trader");
    renderSignals([]);
  }
}

document.querySelectorAll(".tab").forEach(btn=>{
  btn.onclick=()=>{
    document.querySelectorAll(".tab").forEach(x=>x.classList.remove("active"));
    document.querySelectorAll(".page").forEach(x=>x.classList.remove("active"));
    btn.classList.add("active");
    $(`page-${btn.dataset.page}`).classList.add("active");
  };
});

$("scanner-button").onclick = async ()=>{
  const b=$("scanner-button"), r=$("scanner-result");
  b.disabled=true; b.textContent="Анализ рынка…";
  const steps=["Funding & OI","Volume & CVD","Liquidity","FVG & Order Blocks","AI Score"];
  for(let i=0;i<steps.length;i++){
    r.textContent=`${steps[i]} · ${20*(i+1)}%`;
    await new Promise(x=>setTimeout(x,350));
  }
  r.innerHTML="<b style='color:var(--green)'>Сканирование завершено.</b><br>Сильные идеи отправляются администратору в Telegram.";
  b.disabled=false;b.textContent="Запустить анализ";
};

$("close-app").onclick=()=>tg?.close();
load();

function compactMoney(n){
  n=Number(n||0); if(Math.abs(n)>=1e12)return `$${(n/1e12).toFixed(2)}T`;
  if(Math.abs(n)>=1e9)return `$${(n/1e9).toFixed(2)}B`;
  if(Math.abs(n)>=1e6)return `$${(n/1e6).toFixed(2)}M`; return `$${fmt(n)}`;
}
function impactClass(text){ return text.includes("Быч")?"var(--green)":text.includes("Медв")?"var(--red)":"var(--muted)"; }
async function loadMarketPro(){
  try{
    const res=await fetch('/api/market-pro',{headers:{"X-Telegram-Init-Data":initData}});
    if(!res.ok) throw new Error(await res.text()); const d=await res.json();
    setText('market-score',d.overview.score); setText('market-score-label',d.overview.score_label.toUpperCase());
    setText('fear-greed',`${d.overview.fear_greed}/100`); setText('btc-dominance',`${Number(d.overview.btc_dominance).toFixed(2)}%`);
    setText('market-change',`${Number(d.overview.market_change)>=0?'+':''}${Number(d.overview.market_change).toFixed(2)}%`);
    $('market-change').style.color=Number(d.overview.market_change)>=0?'var(--green)':'var(--red)';
    $('derivatives-list').innerHTML=(d.derivatives||[]).map(x=>`<div class="derivative"><div class="derivative-head"><b>${x.symbol}</b><span style="color:${Math.abs(Number(x.funding))>=.05?'var(--red)':'var(--green)'}">${Math.abs(Number(x.funding))>=.05?'OVERHEATED':'NORMAL'}</span></div><div class="derivative-grid"><div><small>Funding</small><b>${Number(x.funding)>=0?'+':''}${Number(x.funding).toFixed(4)}%</b></div><div><small>Long/Short</small><b>${Number(x.long_short).toFixed(2)}</b></div><div><small>Open Interest</small><b>${compactMoney(x.oi).replace('$','')}</b></div></div></div>`).join('')||'<div class="empty">Нет данных</div>';
    const movers=(id,items)=>$(id).innerHTML=(items||[]).map(x=>`<div class="mover"><b>${x.symbol}</b><span style="color:${Number(x.change)>=0?'var(--green)':'var(--red)'}">${Number(x.change)>=0?'+':''}${Number(x.change).toFixed(2)}%</span></div>`).join('');
    movers('gainers-list',d.gainers); movers('losers-list',d.losers);
    $('news-list').innerHTML=(d.news||[]).map(n=>`<a class="news-item" href="${n.url}" target="_blank" rel="noopener"><h4>${n.title}</h4><div class="news-meta"><span>${n.source}</span><b style="color:${impactClass(n.impact)}">${n.impact}</b></div><div class="news-explanation">${n.explanation}</div></a>`).join('')||'<div class="empty">Новостей пока нет</div>';
  }catch(e){ console.error(e); $('derivatives-list').innerHTML='<div class="empty">Рыночные данные временно недоступны</div>'; }
}
loadMarketPro();
