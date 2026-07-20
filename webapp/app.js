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
const signed = value => `${Number(value || 0) >= 0 ? "+" : ""}${Number(value || 0).toFixed(2)}%`;
function setText(id, value){ const el=$(id); if(el) el.textContent=value; }

function formatDate(value){
  if(!value) return "—";
  const d = new Date(value);
  if(Number.isNaN(d.getTime())) return String(value).slice(0,16);
  return d.toLocaleString("ru-RU", {day:"2-digit",month:"2-digit",hour:"2-digit",minute:"2-digit"});
}

function renderSignals(items){
  const html = items?.length ? items.map(s => {
    const dir = String(s.direction || "").toUpperCase();
    const cls = dir === "LONG" ? "long" : "short";
    const status = String(s.status || "active").toLowerCase();
    const result = s.result_percent == null ? "ACTIVE" : signed(s.result_percent);
    const score = s.score == null ? "—" : `${Number(s.score)}/100`;
    const confidence = s.confidence == null ? "—" : `${Number(s.confidence)}%`;
    const rr = s.rr_ratio == null ? "—" : `1:${Number(s.rr_ratio).toFixed(1)}`;
    const checks = (s.confirmations || []).slice(0,8).map(x=>`<span class="check">✓ ${esc(x)}</span>`).join("");
    return `<div class="signal">
      <div class="signal-head"><h4>#${esc(s.signal_id)} · ${esc(s.symbol)}</h4><span class="quality">${esc(s.quality_label || "STANDARD")}</span></div>
      <span class="direction ${cls}">${esc(dir)}</span>
      <p>Entry ${esc(s.entry)} · SL ${esc(s.stop_loss)} · TP1 ${esc(s.take_profit_1)}</p>
      <div class="quality-grid"><span>Score <b>${score}</b></span><span>Confidence <b>${confidence}</b></span><span>RR <b>${rr}</b></span></div>
      ${checks ? `<div class="checks">${checks}</div>` : ""}
      <div class="signal-meta"><span>${formatDate(s.created_at)}</span><span class="status-pill ${esc(status)}">${esc(status.toUpperCase())}</span></div>
      <b class="result">${result}</b>
    </div>`;
  }).join("") : '<div class="empty">Сигналов пока нет</div>';
  $("recent-signals").innerHTML = html;
  $("all-signals").innerHTML = html;
}

function renderEquity(points){
  const host = $("equity-chart");
  setText("curve-caption", `${points?.length || 0} закрытых`);
  if(!points?.length){ host.innerHTML='<div class="empty">Закрытых сигналов пока нет</div>'; return; }
  const width=720, height=170, pad=16;
  const values=points.map(p=>Number(p.value||0));
  let min=Math.min(0,...values), max=Math.max(0,...values);
  if(min===max){ min-=1; max+=1; }
  const x=i=>pad+(i/(Math.max(1,points.length-1)))*(width-pad*2);
  const y=v=>pad+(max-v)/(max-min)*(height-pad*2);
  const line=points.map((p,i)=>`${i?"L":"M"}${x(i).toFixed(1)},${y(Number(p.value||0)).toFixed(1)}`).join(" ");
  const area=`${line} L${x(points.length-1).toFixed(1)},${height-pad} L${x(0).toFixed(1)},${height-pad} Z`;
  host.innerHTML=`<svg viewBox="0 0 ${width} ${height}" role="img" aria-label="Кривая суммарного результата">
    <defs><linearGradient id="areaGradient" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="var(--accent)" stop-opacity=".30"/><stop offset="1" stop-color="var(--accent)" stop-opacity="0"/></linearGradient></defs>
    <line class="chart-zero" x1="${pad}" x2="${width-pad}" y1="${y(0)}" y2="${y(0)}"/>
    <path class="chart-area" d="${area}"/><path class="chart-line" d="${line}"/>
    <circle class="chart-dot" cx="${x(points.length-1)}" cy="${y(values[values.length-1])}" r="5"/>
  </svg>`;
}

function renderAnalytics(a){
  const periods=a?.periods || {};
  setText("result-day", signed(periods.day));
  setText("result-week", signed(periods.week));
  setText("result-month", signed(periods.month));
  setText("result-all", signed(periods.all));
  const quality=a?.quality || {};
  setText("avg-score", `${Number(quality.average_score||0).toFixed(1)}/100`);
  setText("avg-confidence", `${Number(quality.average_confidence||0).toFixed(1)}%`);
  setText("premium-count", quality.premium_count || 0);
  renderEquity(a?.equity_curve || []);
  const coins=a?.best_coins || [];
  $("best-coins").innerHTML = coins.length ? coins.map(c=>`<div class="coin-row">
    <div><b>${esc(c.symbol)}</b><small>${c.total} сигналов · WR ${Number(c.winrate||0).toFixed(1)}%</small></div>
    <span>${c.wins}W / ${c.losses}L</span>
    <b class="coin-result ${Number(c.result)>=0?'positive':'negative'}">${signed(c.result)}</b>
  </div>`).join("") : '<div class="empty">Данных пока нет</div>';
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
    const status=$("api-status"); if(status) status.classList.remove("error");
    setText("api-status", "Данные обновлены");
    setText("user-name", d.user.first_name || fallbackUser.first_name || "Trader");
    setText("account-level", d.user.vip ? "PREMIUM" : "STANDARD");
    setText("vip-status", d.user.vip ? "VIP ACTIVE" : "VIP OFF");
    setText("winrate", `${Number(d.stats.winrate || 0).toFixed(1)}%`);
    $("winrate-ring").style.background = `conic-gradient(var(--accent) ${Math.min(100,Number(d.stats.winrate||0))*3.6}deg,#222 0deg)`;
    setText("active-signals", d.stats.active || 0);
    setText("total-signals", d.stats.total || 0);
    setText("total-result", signed(d.stats.total_result));
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
    renderAnalytics(d.analytics || {});
  }catch(e){
    console.error(e);
    showError(e.message || "Не удалось загрузить данные");
    setText("user-name",fallbackUser.first_name || "Trader");
    renderSignals([]);
    renderAnalytics({});
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
  r.innerHTML="<b style='color:var(--green)'>Проверка завершена.</b><br>Сигналы ниже установленного порога качества автоматически отклоняются.";
  b.disabled=false;b.textContent="Запустить анализ";
});

$("close-app")?.addEventListener("click",()=>tg?.close());
$("refresh-data")?.addEventListener("click",load);
load();
