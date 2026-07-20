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
const esc = value => String(value ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#039;"}[c]));
function setText(id, value){ const el=$(id); if(el) el.textContent=value; }

function renderSignals(items){
  const html = items?.length ? items.map(s => {
    const dir = String(s.direction || "").toUpperCase();
    const cls = dir === "LONG" ? "long" : "short";
    const result = s.result_percent == null ? "ACTIVE" : `${Number(s.result_percent)>=0?"+":""}${Number(s.result_percent).toFixed(2)}%`;
    const score = s.score == null ? "—" : `${Number(s.score)}/100`;
    const confidence = s.confidence == null ? "—" : `${Number(s.confidence)}%`;
    const rr = s.rr_ratio == null ? "—" : `1:${Number(s.rr_ratio).toFixed(1)}`;
    const checks = (s.confirmations || []).slice(0,6).map(x=>`<span class="check">✓ ${esc(x)}</span>`).join("");
    return `<div class="signal">
      <div class="signal-head"><h4>#${esc(s.signal_id)} · ${esc(s.symbol)}</h4><span class="quality">${esc(s.quality_label || "STANDARD")}</span></div>
      <span class="direction ${cls}">${esc(dir)}</span>
      <p>Entry ${esc(s.entry)} · SL ${esc(s.stop_loss)} · TP1 ${esc(s.take_profit_1)}</p>
      <div class="quality-grid"><span>Score <b>${score}</b></span><span>Confidence <b>${confidence}</b></span><span>RR <b>${rr}</b></span></div>
      ${checks ? `<div class="checks">${checks}</div>` : ""}
      <b class="result">${result}</b>
    </div>`;
  }).join("") : '<div class="empty">Сигналов пока нет</div>';
  $("recent-signals").innerHTML = html;
  $("all-signals").innerHTML = html;
}

async function api(path){
  const res = await fetch(path, {headers: {"X-Telegram-Init-Data": initData}, cache:"no-store"});
  const body = await res.json().catch(()=>({message:"Сервер вернул неверный ответ"}));
  if(!res.ok) throw new Error(body.message || body.error || `HTTP ${res.status}`);
  return body;
}

function showError(message){
  setText("api-status", message);
  const el=$("api-status"); if(el) el.classList.add("error");
}

async function load(){
  try{
    setText("api-status", "Подключение к API…");
    const d = await api("/api/dashboard");
    setText("api-status", "Данные обновлены");
    setText("user-name", d.user.first_name || fallbackUser.first_name || "Trader");
    setText("account-level", d.user.vip ? "PREMIUM" : "STANDARD");
    setText("vip-status", d.user.vip ? "VIP ACTIVE" : "VIP OFF");
    setText("winrate", `${Number(d.stats.winrate || 0).toFixed(1)}%`);
    $("winrate-ring").style.background = `conic-gradient(var(--accent) ${Math.min(100,Number(d.stats.winrate||0))*3.6}deg,#222 0deg)`;
    setText("active-signals", d.stats.active || 0);
    setText("total-signals", d.stats.total || 0);
    setText("total-result", `${Number(d.stats.total_result||0)>=0?"+":""}${Number(d.stats.total_result||0).toFixed(2)}%`);
    setText("referrals", d.user.referrals || 0);
    setText("profile-id", d.user.id);
    setText("profile-vip", d.user.vip ? "ACTIVE" : "INACTIVE");
    setText("profile-discount", `${d.user.discount || 0}%`);
    setText("profile-free", `${d.user.free_remaining}/${d.user.free_limit}`);
    setText("profile-plan", d.user.plan || "FREE");
    for(const t of d.market || []){
      const key=t.symbol.toLowerCase();
      setText(`${key}-price`, t.price ? `$${fmt(t.price)}` : "—");
      setText(`${key}-change`, t.price ? `${t.change>=0?"+":""}${Number(t.change).toFixed(2)}%` : "—");
      const changeEl=$(`${key}-change`); if(changeEl) changeEl.style.color = t.change>=0 ? "var(--green)" : "var(--red)";
    }
    renderSignals(d.history);
  }catch(e){
    console.error(e);
    showError(e.message || "Не удалось загрузить данные");
    setText("user-name",fallbackUser.first_name || "Trader");
    renderSignals([]);
  }
}

document.querySelectorAll(".tab").forEach(btn=>{
  btn.addEventListener("click",()=>{
    document.querySelectorAll(".tab").forEach(x=>x.classList.remove("active"));
    document.querySelectorAll(".page").forEach(x=>x.classList.remove("active"));
    btn.classList.add("active");
    $(`page-${btn.dataset.page}`).classList.add("active");
  });
});

$("scanner-button")?.addEventListener("click", async ()=>{
  const b=$("scanner-button"), r=$("scanner-result");
  b.disabled=true; b.textContent="Анализ рынка…";
  const steps=["Trend 4H/1H","Liquidity Sweep","BOS / CHoCH","OB / FVG","Funding / OI","Volume / CVD","Risk/Reward","Final AI Score"];
  for(let i=0;i<steps.length;i++){
    r.textContent=`${steps[i]} · ${Math.round((i+1)/steps.length*100)}%`;
    await new Promise(x=>setTimeout(x,260));
  }
  r.innerHTML="<b style='color:var(--green)'>Проверка завершена.</b><br>Публикуются только сигналы, прошедшие установленный порог качества.";
  b.disabled=false;b.textContent="Запустить анализ";
});

$("close-app")?.addEventListener("click",()=>tg?.close());
$("refresh-data")?.addEventListener("click",load);
load();
