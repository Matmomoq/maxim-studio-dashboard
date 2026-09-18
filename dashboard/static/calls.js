const callState = { team: "cc", granularity: "day", data: null, requestId: 0 };
const callNumber = new Intl.NumberFormat("ru-RU");

const callIso = (date) => `${date.getFullYear()}-${String(date.getMonth()+1).padStart(2,"0")}-${String(date.getDate()).padStart(2,"0")}`;
const callEscape = (value) => String(value ?? "").replaceAll("&","&amp;").replaceAll("<","&lt;").replaceAll(">","&gt;").replaceAll('"',"&quot;");
function callDuration(seconds) {
  const value = Math.max(0, Number(seconds) || 0);
  const hours = Math.floor(value / 3600); const minutes = Math.floor((value % 3600) / 60); const secs = value % 60;
  if (hours) return `${hours} ч ${minutes} мин`;
  if (minutes) return `${minutes} мин ${secs} сек`;
  return `${secs} сек`;
}
function callSetDates(preset="month") {
  const today = new Date(`${document.body.dataset.today}T12:00:00`); const start = new Date(today);
  if (preset === "month") start.setDate(1); else if (preset === "week") start.setDate(today.getDate()-6);
  document.querySelector("#callDateFrom").value = callIso(start) < document.body.dataset.minDate ? document.body.dataset.minDate : callIso(start);
  document.querySelector("#callDateTo").value = callIso(today);
  document.querySelectorAll("[data-call-preset]").forEach((item)=>item.classList.toggle("active",item.dataset.callPreset===preset));
}
function callError(message="") { const box=document.querySelector("#callsError"); box.hidden=!message; box.textContent=message; }
function callLoading(active) { document.querySelector("#callsLoading").classList.toggle("active",active); }
async function callRequest() {
  const params=new URLSearchParams({date_from:document.querySelector("#callDateFrom").value,date_to:document.querySelector("#callDateTo").value,team:callState.team,city:document.querySelector("#callCity").value,user:document.querySelector("#callUser").value});
  const response=await fetch(`/api/calls?${params}`); if(response.status===401){window.location.assign("/login?next=/calls");return null;} const data=await response.json(); if(!response.ok) throw new Error(data.error||"Не удалось получить аналитику звонков"); return data;
}
function callFillUsers(values) { const select=document.querySelector("#callUser"); const current=select.value; select.innerHTML='<option value="Все">Все сотрудники</option>'+values.map((x)=>`<option value="${callEscape(x)}">${callEscape(x)}</option>`).join(""); if([...select.options].some((x)=>x.value===current))select.value=current; }
function callKpi(kpi) {
  document.querySelector("#callsTotal").textContent=callNumber.format(kpi.calls); document.querySelector("#callsSplit").textContent=`${callNumber.format(kpi.incoming)} входящих · ${callNumber.format(kpi.outgoing)} исходящих`;
  document.querySelector("#callsAccepted").textContent=callNumber.format(kpi.accepted); document.querySelector("#callsAcceptedShare").textContent=`${kpi.calls?Math.round(kpi.accepted/kpi.calls*100):0}% от всех звонков`;
  document.querySelector("#callsMissed").textContent=callNumber.format(kpi.missed); document.querySelector("#callsTalkTime").textContent=callDuration(kpi.talk_seconds); document.querySelector("#callsAverage").textContent=`Среднее: ${callDuration(kpi.average_talk_seconds)}`;
  document.querySelector("#callsBookings").textContent=callNumber.format(kpi.bookings); document.querySelector("#callsConversion").textContent=`${kpi.bookings_per_100_calls}`;
  const ids={callsFootTotal:"calls",callsFootIncoming:"incoming",callsFootOutgoing:"outgoing",callsFootAccepted:"accepted",callsFootMissed:"missed",callsFootBookings:"bookings"}; Object.entries(ids).forEach(([id,key])=>document.querySelector(`#${id}`).textContent=callNumber.format(kpi[key]));
  document.querySelector("#callsFootTime").textContent=callDuration(kpi.talk_seconds); document.querySelector("#callsFootAverage").textContent=callDuration(kpi.average_talk_seconds); document.querySelector("#callsFootConversion").textContent=`${kpi.bookings_per_100_calls}`;
}
function callEmployees(rows) { const body=document.querySelector("#callsEmployeesBody"); body.innerHTML=rows.length?rows.map((r)=>`<tr><td><strong>${callEscape(r.employee)}</strong><small>${callEscape(r.city)}</small></td><td>${callNumber.format(r.calls)}</td><td>${callNumber.format(r.incoming)}</td><td>${callNumber.format(r.outgoing)}</td><td>${callNumber.format(r.accepted)}</td><td>${callNumber.format(r.missed)}</td><td>${callDuration(r.talk_seconds)}</td><td>${callDuration(r.average_talk_seconds)}</td><td class="booking-cell">${callNumber.format(r.bookings)}</td><td>${r.bookings_per_100_calls}</td></tr>`).join(""):'<tr><td colspan="10" class="registry-empty">Для выбранного среза нет сотрудников</td></tr>'; }
function callBucket(points) {
  const groups=new Map(); for(const point of points){const d=new Date(`${point.date}T12:00:00`); let key=point.date,label=`${String(d.getDate()).padStart(2,"0")}.${String(d.getMonth()+1).padStart(2,"0")}`;
    if(callState.granularity==="week"){const monday=new Date(d); monday.setDate(d.getDate()-((d.getDay()+6)%7)); key=callIso(monday); label=`с ${String(monday.getDate()).padStart(2,"0")}.${String(monday.getMonth()+1).padStart(2,"0")}`;} else if(callState.granularity==="month"){key=point.date.slice(0,7); label=d.toLocaleDateString("ru-RU",{month:"short",year:"2-digit"});}
    if(!groups.has(key))groups.set(key,{label,accepted:0,missed:0,bookings:0}); const g=groups.get(key); g.accepted+=point.accepted;g.missed+=point.missed;g.bookings+=point.bookings;
  } return [...groups.values()];
}
function callCharts() { const points=callBucket(callState.data.daily); const maxCalls=Math.max(1,...points.map((x)=>Math.max(x.accepted,x.missed))); const maxBookings=Math.max(1,...points.map((x)=>x.bookings));
  document.querySelector("#callsVolumeChart").innerHTML=points.map((x)=>`<div class="calls-bar-group" title="${x.label}\nПринято: ${x.accepted}\nПропущено: ${x.missed}"><div class="calls-bar-pair"><i class="accepted" style="height:${x.accepted/maxCalls*100}%"></i><i class="missed" style="height:${x.missed/maxCalls*100}%"></i></div><span>${x.label}</span></div>`).join("");
  document.querySelector("#callsBookingsChart").innerHTML=points.map((x)=>`<div class="calls-bar-group" title="${x.label}\nЗаписей: ${x.bookings}"><div class="calls-bar-pair single"><i class="bookings" style="height:${x.bookings/maxBookings*100}%"></i></div><span>${x.label}</span></div>`).join("");
}
function callRender(data){callState.data=data;callFillUsers(data.options.users);callKpi(data.kpi);callEmployees(data.employees);document.querySelector("#callsBookingLabel").textContent=data.method.booking_label;document.querySelector("#callsBookingsChartTitle").textContent=data.method.booking_label;document.querySelector("#callsPeriodLabel").textContent=`${data.period.from.split("-").reverse().join(".")} — ${data.period.to.split("-").reverse().join(".")}`;document.querySelector("#syncStatus span:last-child").textContent=data.last_sync?`Данные в БД: ${data.last_sync}`:"Подключено к базе amoCRM";callCharts();}
async function loadCalls(){const requestId=++callState.requestId;callLoading(true);callError();try{const data=await callRequest();if(data&&requestId===callState.requestId)callRender(data);}catch(error){if(requestId===callState.requestId)callError(error.message);}finally{if(requestId===callState.requestId)callLoading(false);}}
function initCalls(){callSetDates();document.querySelectorAll("[data-call-team]").forEach((b)=>b.addEventListener("click",()=>{callState.team=b.dataset.callTeam;document.querySelectorAll("[data-call-team]").forEach((x)=>x.classList.toggle("active",x===b));document.querySelector("#callUser").value="Все";loadCalls();}));document.querySelectorAll("#callDateFrom,#callDateTo,#callCity,#callUser").forEach((x)=>x.addEventListener("change",loadCalls));document.querySelectorAll("[data-call-preset]").forEach((b)=>b.addEventListener("click",()=>{callSetDates(b.dataset.callPreset);loadCalls();}));document.querySelectorAll("[data-call-granularity]").forEach((b)=>b.addEventListener("click",()=>{callState.granularity=b.dataset.callGranularity;document.querySelectorAll("[data-call-granularity]").forEach((x)=>x.classList.toggle("active",x===b));callCharts();}));document.querySelector("#resetCallFilters").addEventListener("click",()=>{callSetDates();document.querySelector("#callCity").value="Все";document.querySelector("#callUser").value="Все";loadCalls();});loadCalls();}
document.addEventListener("DOMContentLoaded",initCalls);
