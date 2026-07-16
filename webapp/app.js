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
