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
const escapeHtml = value => String(value ?? "").replace(/[&<>'"]/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[ch]));
let dashboardData = null;
let activeFilter = "all";

function setText(id, value){ const el=$(id); if(el) el.textContent=value; }
function api(path){ return fetch(path,{headers:{"X-Telegram-Init-Data":initData}}); }
function statusText(status, tpStage=0){
  if(status === "active" && tpStage > 0) return `TP${tpStage} HIT`;
  return ({active:"ACTIVE",win:"WIN",loss:"STOP",breakeven:"BREAKEVEN"}[status] || String(status || "—").toUpperCase());
}
function timeAgo(value){
  if(!value) return "—";
  const date = new Date(value);
  if(Number.isNaN(date.getTime())) return "—";
  const seconds = Math.max(0, Math.floor((Date.now()-date.getTime())/1000));
  if(seconds < 60) return `${seconds} сек назад`;
  const minutes=Math.floor(seconds/60); if(minutes<60) return `${minutes} мин назад`;
  const hours=Math.floor(minutes/60); if(hours<24) return `${hours} ч назад`;
  return `${Math.floor(hours/24)} дн назад`;
}
function statusClass(status){ return ({active:"active-status",win:"win-status",loss:"loss-status",breakeven:"be-status"}[status] || ""); }

function signalCard(s){
  const dir = String(s.direction || "").toUpperCase();
  const cls = dir === "LONG" ? "long" : "short";
  const result = s.result_percent == null ? statusText(s.status, s.tp_stage) : `${Number(s.result_percent)>=0?"+":""}${Number(s.result_percent).toFixed(2)}%`;
  const confirmations = (s.confirmations || []).slice(0,8);
  const warnings = (s.warnings || []).slice(0,4);
  const risk = s.risk_percent == null ? "—" : `${Number(s.risk_percent).toFixed(2)}%`;
  const rr = s.planned_rr == null ? "—" : `1:${Number(s.planned_rr).toFixed(2)}`;
  const tpStage = Number(s.tp_stage || 0);
  const progress = s.status === "win" ? 100 : s.status === "loss" ? 0 : tpStage === 3 ? 90 : tpStage === 2 ? 65 : tpStage === 1 ? 35 : 8;
  const details = confirmations.length || warnings.length ? `
    <details>
      <summary>Подтверждения: ${confirmations.length}${warnings.length ? ` · риски: ${warnings.length}` : ""}</summary>
      ${confirmations.length ? `<div class="confirm-list">${confirmations.map(x=>`<div>✓ ${escapeHtml(x)}</div>`).join("")}</div>` : ""}
      ${warnings.length ? `<div class="warning-list">${warnings.map(x=>`<div>! ${escapeHtml(x)}</div>`).join("")}</div>` : ""}
    </details>` : "";
  return `<div class="signal">
    <h4>#${escapeHtml(s.signal_id)} · ${escapeHtml(s.symbol)}</h4>
    <span class="direction ${cls}">${escapeHtml(dir)}</span>
    <div class="signal-meta"><span class="status-pill ${statusClass(s.status)}">${escapeHtml(statusText(s.status,s.tp_stage))}</span><span>${escapeHtml(timeAgo(s.created_at))}</span></div>
    <p>Entry ${escapeHtml(s.entry)} · SL ${escapeHtml(s.stop_loss)} · TP1 ${escapeHtml(s.take_profit_1)}</p>
    <p>Риск ${risk} · RR ${rr} · Score ${s.score == null ? "—" : escapeHtml(s.score)+"/100"}</p>
    <div class="signal-progress"><i style="width:${progress}%"></i></div>
    <span></span><b class="result">${escapeHtml(result)}</b>
    ${details}
  </div>`;
}

function renderSignals(items){
  const source = items || [];
  const filtered = activeFilter === "all" ? source : source.filter(x => x.status === activeFilter);
  const html = filtered.length ? filtered.map(signalCard).join("") : '<div class="empty">Сигналов по этому фильтру нет</div>';
  $("recent-signals").innerHTML = source.slice(0,5).map(signalCard).join("") || '<div class="empty">Сигналов пока нет</div>';
  $("all-signals").innerHTML = html;
}

function renderScanner(items){
  const el=$("scanner-setups");
  if(!items?.length){ el.innerHTML='<div class="empty">Подходящих сетапов пока нет</div>'; return; }
  el.innerHTML=items.map(s=>{
    const confirmations=(s.confirmations||[]).slice(0,8);
    const warnings=(s.warnings||[]).slice(0,4);
    return `<div class="signal setup-card">
      <h4>${escapeHtml(s.symbol)}</h4><span class="direction ${s.direction==='LONG'?'long':'short'}">${escapeHtml(s.direction)}</span>
      <p>Score ${escapeHtml(s.score)}/100 · RR ${s.risk_reward == null?'—':Number(s.risk_reward).toFixed(2)} · OF ${escapeHtml(s.order_flow_score || 0)}</p>
      <p>Entry ${fmt(s.entry_low)}–${fmt(s.entry_high)} · SL ${fmt(s.stop_loss)} · TP1 ${fmt(s.take_profit_1)}</p>
      <details><summary>Подтверждения: ${confirmations.length}</summary>
        <div class="confirm-list">${confirmations.map(x=>`<div>✓ ${escapeHtml(x)}</div>`).join("") || '<div>Нет данных</div>'}</div>
        ${warnings.length?`<div class="warning-list">${warnings.map(x=>`<div>! ${escapeHtml(x)}</div>`).join("")}</div>`:""}
      </details>
    </div>`;
  }).join("");
}

async function load(){
  try{
    const res = await api("/api/dashboard");
    if(!res.ok) throw new Error(await res.text());
    const d = await res.json();
    dashboardData=d;
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
    setText("profile-tps", `${d.stats.tp1 || 0} / ${d.stats.tp2 || 0} / ${d.stats.tp3 || 0}`);
    setText("profile-average", `${Number(d.stats.average_result || 0)>=0?"+":""}${Number(d.stats.average_result || 0).toFixed(2)}%`);
    setText("profile-rr", Number(d.stats.average_rr || 0).toFixed(2));
    for(const t of d.market || []){
      const key=t.symbol.toLowerCase();
      setText(`${key}-price`, `$${fmt(t.price)}`);
      setText(`${key}-change`, `${t.change>=0?"+":""}${Number(t.change).toFixed(2)}%`);
      $(`${key}-change`).style.color = t.change>=0 ? "var(--green)" : "var(--red)";
    }
    renderSignals(d.history);
  }catch(e){
    console.error(e);
    setText("welcome","Ошибка подключения к API");
    setText("user-name",fallbackUser.first_name || "Trader");
    $("recent-signals").innerHTML=`<div class="empty">${escapeHtml(e.message)}</div>`;
  }
}

async function loadScanner(){
  const b=$("scanner-button"), r=$("scanner-result"), p=$("scanner-progress");
  b.disabled=true; b.textContent="Загрузка…"; p.style.width="35%";
  try{
    const res=await api("/api/scanner");
    if(!res.ok) throw new Error(await res.text());
    const d=await res.json();
    p.style.width="100%";
    r.textContent=d.message;
    renderScanner(d.setups);
  }catch(e){
    r.textContent=`Ошибка: ${e.message}`; renderScanner([]); p.style.width="0%";
  }finally{
    b.disabled=false; b.textContent="Обновить реальные сетапы";
  }
}

document.querySelectorAll(".tab").forEach(btn=>{
  btn.addEventListener("click",()=>{
    document.querySelectorAll(".tab").forEach(x=>x.classList.remove("active"));
    document.querySelectorAll(".page").forEach(x=>x.classList.remove("active"));
    btn.classList.add("active"); $(`page-${btn.dataset.page}`).classList.add("active");
    if(btn.dataset.page==="scanner") loadScanner();
    if(btn.dataset.page==="analytics") loadAnalytics();
  });
});

document.querySelectorAll(".filter").forEach(btn=>btn.addEventListener("click",()=>{
  document.querySelectorAll(".filter").forEach(x=>x.classList.remove("active"));
  btn.classList.add("active"); activeFilter=btn.dataset.filter; renderSignals(dashboardData?.history || []);
}));

$("scanner-button").addEventListener("click",loadScanner);
$("refresh-data").addEventListener("click",load);
$("close-app").addEventListener("click",()=>tg?.close());
load();


let analyticsPeriod = "all";
function signedPct(value){ const n=Number(value||0); return `${n>=0?"+":""}${n.toFixed(2)}%`; }
function drawEquityCurve(points){
  const canvas=$("equity-chart"), empty=$("chart-empty"); if(!canvas) return;
  const ctx=canvas.getContext("2d"); const ratio=window.devicePixelRatio||1; const w=canvas.clientWidth||700,h=260;
  canvas.width=w*ratio; canvas.height=h*ratio; ctx.scale(ratio,ratio); ctx.clearRect(0,0,w,h);
  if(!points?.length){ empty.style.display="block"; return; } empty.style.display="none";
  const vals=points.map(x=>Number(x.value||0)); const min=Math.min(0,...vals), max=Math.max(0,...vals); const range=(max-min)||1; const pad=24;
  ctx.strokeStyle="rgba(255,255,255,.12)"; ctx.lineWidth=1; ctx.beginPath(); ctx.moveTo(pad,h-pad); ctx.lineTo(w-pad,h-pad); ctx.stroke();
  ctx.strokeStyle="#d7ff38"; ctx.lineWidth=3; ctx.beginPath();
  points.forEach((p,i)=>{ const x=pad+(w-2*pad)*(points.length===1?.5:i/(points.length-1)); const y=h-pad-(Number(p.value)-min)/range*(h-2*pad); i?ctx.lineTo(x,y):ctx.moveTo(x,y); }); ctx.stroke();
  ctx.fillStyle="#d7ff38"; const last=points[points.length-1]; const lx=points.length===1?w/2:w-pad; const ly=h-pad-(Number(last.value)-min)/range*(h-2*pad); ctx.beginPath(); ctx.arc(lx,ly,4,0,Math.PI*2); ctx.fill();
}
function compactSignal(s,label){ if(!s) return `${label}<br><b>Нет данных</b>`; return `${label}<br><b>${escapeHtml(s.symbol||"—")} ${escapeHtml(s.direction||"")}</b><br><span>${signedPct(s.result_percent)}</span>`; }
async function loadAnalytics(){
  try{
    const res=await api(`/api/analytics?period=${analyticsPeriod}`); if(!res.ok) throw new Error(await res.text()); const d=await res.json();
    setText("analytics-winrate",`${Number(d.winrate).toFixed(1)}%`); setText("analytics-pnl",signedPct(d.total_result)); setText("analytics-average",signedPct(d.average_result)); setText("analytics-closed",d.closed);
    $("best-signal").innerHTML=compactSignal(d.best,"Лучший"); $("worst-signal").innerHTML=compactSignal(d.worst,"Худший");
    $("long-stats").innerHTML=`<b>LONG</b><br>${d.long.count} сделок · WR ${Number(d.long.winrate).toFixed(1)}% · ${signedPct(d.long.result)}`;
    $("short-stats").innerHTML=`<b>SHORT</b><br>${d.short.count} сделок · WR ${Number(d.short.winrate).toFixed(1)}% · ${signedPct(d.short.result)}`;
    $("closed-signals").innerHTML=(d.recent_closed||[]).map(signalCard).join("")||'<div class="empty">Закрытых сигналов нет</div>'; drawEquityCurve(d.curve);
  }catch(e){ console.error(e); $("closed-signals").innerHTML=`<div class="empty">${escapeHtml(e.message)}</div>`; }
}
document.querySelectorAll(".period-filter").forEach(btn=>btn.addEventListener("click",()=>{document.querySelectorAll(".period-filter").forEach(x=>x.classList.remove("active"));btn.classList.add("active");analyticsPeriod=btn.dataset.period;loadAnalytics();}));
$("analytics-refresh")?.addEventListener("click",loadAnalytics);
window.addEventListener("resize",()=>{ if(document.querySelector('[data-page="analytics"]')?.classList.contains("active")) loadAnalytics(); });
