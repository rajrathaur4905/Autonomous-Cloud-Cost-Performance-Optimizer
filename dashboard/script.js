const $ = (s, p=document) => p.querySelector(s);
const $$ = (s, p=document) => [...p.querySelectorAll(s)];

const state = {
  activeSection: "overview",
  period: "24H",
  resources: [
    {name:"prod-api-01",type:"EC2",status:"Healthy",cpu:42,memory:61,cost:840,opt:"Optimized"},
    {name:"prod-db",type:"RDS",status:"Healthy",cpu:61,memory:72,cost:1240,opt:"Monitoring"},
    {name:"prod-cluster",type:"EKS",status:"Warning",cpu:76,memory:81,cost:3860,opt:"Optimization Available"},
    {name:"storage-main",type:"S3",status:"Healthy",cpu:null,memory:null,cost:840,opt:"Optimized"},
    {name:"batch-workers",type:"EC2",status:"Healthy",cpu:19,memory:28,cost:920,opt:"Optimization Available"},
    {name:"analytics-db",type:"RDS",status:"Warning",cpu:7,memory:31,cost:540,opt:"Optimization Available"},
    {name:"edge-cache",type:"EC2",status:"Healthy",cpu:54,memory:49,cost:620,opt:"Optimized"},
    {name:"dev-worker-03",type:"EC2",status:"Healthy",cpu:4,memory:13,cost:180,opt:"Optimization Available"},
    {name:"logs-archive",type:"S3",status:"Healthy",cpu:null,memory:null,cost:310,opt:"Optimized"},
    {name:"payments-cluster",type:"EKS",status:"Healthy",cpu:68,memory:74,cost:2280,opt:"Monitoring"}
  ],
  actions: [
    {time:"12:42 PM",title:"Scaled EC2 fleet",resource:"prod-api-server",saving:"Saved $120/month",status:"Successful"},
    {time:"11:35 AM",title:"Stopped idle instance",resource:"dev-worker-03",saving:"Saved $85/month",status:"Successful"},
    {time:"10:18 AM",title:"RDS optimization pending approval",resource:"analytics-db",saving:"Awaiting approval",status:"Pending"},
    {time:"09:42 AM",title:"Changed instance type",resource:"analytics-server",saving:"Saved $210/month",status:"Successful"}
  ],
  alerts: [
    {type:"critical",title:"EKS latency exceeded SLA",desc:"prod-cluster · p95 latency is above the configured threshold.",time:"8 minutes ago"},
    {type:"warning",title:"EC2 utilization below 20%",desc:"batch-workers · AI recommends a smaller instance class.",time:"21 minutes ago"},
    {type:"info",title:"New cost optimization opportunity detected",desc:"18 opportunities are currently ranked by impact and safety.",time:"42 minutes ago"},
    {type:"resolved",title:"High-cost workload optimized",desc:"prod-api-server-04 · estimated $420/month savings.",time:"1 hour ago"}
  ]
};

const chartDefaults = {
  color: "#7d8da3",
  grid: "rgba(125,141,163,.09)"
};
Chart.defaults.color = chartDefaults.color;
Chart.defaults.font.family = "Inter, system-ui, sans-serif";
Chart.defaults.font.size = 9;

const periodData = {
  "24H": {labels:["00","03","06","09","12","15","18","21"], cost:[420,510,470,620,590,710,680,760], optimized:[390,455,420,530,505,585,560,610], perf:[96,97,97,98,98,99,98,99]},
  "7D": {labels:["Mon","Tue","Wed","Thu","Fri","Sat","Sun"], cost:[1620,1740,1690,1880,1810,1950,2020], optimized:[1490,1560,1505,1660,1580,1690,1740], perf:[97,97,98,98,99,99,99]},
  "30D": {labels:["W1","W2","W3","W4"], cost:[3860,4010,4260,4450], optimized:[3380,3420,3510,3640], perf:[96,98,98,99]},
  "90D": {labels:["Jun","Jul","Aug"], cost:[10680,11420,12480], optimized:[9580,10120,10920], perf:[96,98,99]}
};

let costPerformanceChart, donutChart, forecastChart, costTrendChart, donutChart2, performanceChart;

function makeLineChart(canvas, data, includePerf=true){
  const ctx = canvas.getContext("2d");
  const gradient = ctx.createLinearGradient(0,0,0,260);
  gradient.addColorStop(0,"rgba(56,189,248,.18)");
  gradient.addColorStop(1,"rgba(56,189,248,0)");
  return new Chart(ctx,{
    type:"line",
    data:{labels:data.labels,datasets:[
      {label:"Actual Cost",data:data.cost,borderColor:"#38bdf8",backgroundColor:gradient,fill:true,tension:.42,pointRadius:2,pointHoverRadius:5,borderWidth:2},
      {label:"Optimized Cost",data:data.optimized,borderColor:"#9b7cff",backgroundColor:"transparent",tension:.42,pointRadius:2,pointHoverRadius:5,borderWidth:2},
      ...(includePerf ? [{label:"Performance Score",data:data.perf,borderColor:"#35d49a",backgroundColor:"transparent",tension:.42,yAxisID:"y1",pointRadius:2,borderWidth:2}] : [])
    ]},
    options:{responsive:true,maintainAspectRatio:false,interaction:{mode:"index",intersect:false},plugins:{legend:{labels:{boxWidth:8,usePointStyle:true,padding:14}}},scales:{
      x:{grid:{display:false},ticks:{color:"#617189"}},
      y:{grid:{color:chartDefaults.grid},ticks:{callback:v=>"$"+v}},
      ...(includePerf?{y1:{position:"right",min:90,max:100,grid:{drawOnChartArea:false},ticks:{callback:v=>v+"%"}}}:{})
    }}
  });
}
function makeDonut(canvas){
  return new Chart(canvas.getContext("2d"),{type:"doughnut",data:{labels:["EC2","EKS","RDS","S3","Other"],datasets:[{data:[39,31,19,7,4],backgroundColor:["#38bdf8","#7c6cff","#a98bff","#35d49a","#f6b94b"],borderWidth:0,hoverOffset:7}]},options:{responsive:true,maintainAspectRatio:false,cutout:"76%",plugins:{legend:{display:false}}}});
}
function makeForecast(canvas){
  return new Chart(canvas.getContext("2d"),{type:"line",data:{labels:["W-4","W-3","W-2","W-1","Current","Forecast +1","Forecast +2"],datasets:[
    {label:"Historical",data:[10400,10920,11240,11980,12480,null,null],borderColor:"#38bdf8",tension:.4,pointRadius:2,borderWidth:2},
    {label:"AI Forecast",data:[null,null,null,null,12480,12980,13840],borderColor:"#9b7cff",borderDash:[6,5],tension:.4,pointRadius:2,borderWidth:2}
  ]},options:{responsive:true,maintainAspectRatio:false,plugins:{legend:{display:false}},scales:{x:{grid:{display:false}},y:{grid:{color:chartDefaults.grid},ticks:{callback:v=>"$"+(v/1000).toFixed(0)+"k"}}}}});
}

function initCharts(){
  costPerformanceChart = makeLineChart($("#costPerformanceChart"), periodData["24H"]);
  donutChart = makeDonut($("#costBreakdownChart"));
  forecastChart = makeForecast($("#forecastChart"));
  if($("#costTrendChart")) costTrendChart = makeLineChart($("#costTrendChart"), periodData["30D"], false);
  if($("#costBreakdownChart2")) donutChart2 = makeDonut($("#costBreakdownChart2"));
  if($("#performanceChart")) {
    const data={labels:["00","03","06","09","12","15","18","21","Now"],cost:[96,97,96.8,98,98.3,98,99,98.6,98.7],optimized:[96.5,97.3,97.5,98.2,98.5,98.8,99,99.1,99.2]};
    performanceChart=makeLineChart($("#performanceChart"),data,false);
  }
}
function updateCharts(){
  costPerformanceChart.data=makeLineChartData(periodData[state.period],true);
  costPerformanceChart.update();
  if(costTrendChart){costTrendChart.data=makeLineChartData(periodData["30D"],false);costTrendChart.update();}
  if(performanceChart){const data={labels:["00","03","06","09","12","15","18","21","Now"],cost:[96,97,96.8,98,98.3,98,99,98.6,98.7],optimized:[96.5,97.3}
}
function updatePeriod(period){
  state.period=period;
  $$("#periods button").forEach(b=>b.classList.toggle("active",b.dataset.period===period));
  costPerformanceChart.data=makeLineChartData(periodData[period],true);
  costPerformanceChart.update();
}
function makeLineChartData(data, includePerf){
  const gradient=costPerformanceChart?.ctx?.createLinearGradient(0,0,0,260);
  if(gradient){gradient.addColorStop(0,"rgba(56,189,248,.18)");gradient.addColorStop(1,"rgba(56,189,248,0)");}
  return {labels:data.labels,datasets:[
    {label:"Actual Cost",data:data.cost,borderColor:"#38bdf8",backgroundColor:gradient,fill:true,tension:.42,pointRadius:2,pointHoverRadius:5,borderWidth:2},
    {label:"Optimized Cost",data:data.optimized,borderColor:"#9b7cff",backgroundColor:"transparent",tension:.42,pointRadius:2,pointHoverRadius:5,borderWidth:2},
    ...(includePerf?[{label:"Performance Score",data:data.perf,borderColor:"#35d49a",backgroundColor:"transparent",tension:.42,yAxisID:"y1",pointRadius:2,borderWidth:2}]:[])
  ]};
}

function animateCounters(){
  $$("[data-count]").forEach(el=>{
    const target=Number(el.dataset.count), prefix=el.dataset.prefix||"", suffix=el.dataset.suffix||"";
    let start=0, duration=1000, startTime=null;
    const step=t=>{
      if(!startTime) startTime=t;
      const p=Math.min((t-startTime)/duration,1), eased=1-Math.pow(1-p,3), value=Math.round(start+(target-start)*eased);
      el.textContent=prefix+value.toLocaleString()+suffix+(el.innerHTML.includes("<small>")?"":"");
      if(p<1) requestAnimationFrame(step);
      else if(target===86) el.innerHTML="86 <small>/ 128</small>";
    };
    requestAnimationFrame(step);
  });
}

const sectionNames={overview:["Dashboard","Overview"],resources:["Cloud Resources","Resources"],cost:["Cost Analytics","Cost Analytics"],performance:["Performance","Performance"],optimizer:["AI Optimizer","AI Optimizer"],actions:["Autonomous Actions","Actions"],alerts:["Alerts","Alerts"],settings:["Settings","Settings"]};
function showSection(name){
  state.activeSection=name;
  $$(".dashboard-section").forEach(s=>s.classList.toggle("active",s.dataset.view===name));
  $$(".nav-item").forEach(n=>n.classList.toggle("active",n.dataset.section===name));
  $("#pageTitle").textContent=sectionNames[name][0];
  $("#breadcrumbText").textContent=sectionNames[name][1];
  if(window.innerWidth<=650){$("#sidebar").classList.remove("mobile-open");$("#mobileBackdrop").classList.remove("open")}
  setTimeout(()=>window.dispatchEvent(new Event("resize")),50);
}

function toast(title, message){
  const el=document.createElement("div");el.className="toast";
  el.innerHTML=`<i class="fa-solid fa-circle-check"></i><div><strong>${title}</strong><span>${message}</span></div>`;
  $("#toastContainer").appendChild(el);
  setTimeout(()=>el.remove(),3600);
}
function openModal(html){$("#modal").innerHTML=html;$("#modalBackdrop").classList.add("open")}
function closeModal(){$("#modalBackdrop").classList.remove("open")}

function recommendationModal(card){
  const title=$("h4",card).textContent, resource=$("code",card).textContent, saving=$(".saving strong",card).textContent;
  openModal(`<button class="modal-close" onclick="closeModal()"><i class="fa-solid fa-xmark"></i></button>
    <h3>${title}</h3><p>ACCPO's decision engine identified this opportunity after evaluating utilization, cost and performance signals. This is a simulated demo action.</p>
    <div class="modal-detail"><div class="detail-box"><span>Resource</span><strong>${resource}</strong></div><div class="detail-box"><span>Potential savings</span><strong class="success-text">${saving}</strong></div><div class="detail-box"><span>Confidence</span><strong>96.8%</strong></div><div class="detail-box"><span>Risk</span><strong>Low</strong></div></div>
    <div class="modal-actions"><button class="secondary-btn" onclick="closeModal()">Close</button><button class="primary-btn" id="modalApply">Apply Optimization</button></div>`);
  $("#modalApply").addEventListener("click",()=>{closeModal();applyOptimization(card)});
}
function applyOptimization(card){
  const title=$("h4",card).textContent;
  const btn=$(".apply-btn",card);
  btn.disabled=true;btn.innerHTML='<i class="fa-solid fa-spinner fa-spin"></i> Optimizing...';
  card.style.opacity=".72";
  setTimeout(()=>{
    btn.innerHTML='<i class="fa-solid fa-check"></i> Optimization Applied';btn.style.background="linear-gradient(135deg,#178c69,#2cae83)";
    card.style.opacity="1";
    state.actions.unshift({time:new Date().toLocaleTimeString([], {hour:"2-digit",minute:"2-digit"}),title:"Applied optimization",resource:title,saving:"Saved $420/month",status:"Successful"});
    renderActions(); toast("Optimization Applied","Cloud resource optimized successfully.");
  },2000);
}
function actionHtml(a){
  const cls=a.status.toLowerCase();
  const icon=cls==="successful"?"fa-check":cls==="pending"?"fa-clock":"fa-xmark";
  return `<div class="activity"><div class="activity-icon ${cls}"><i class="fa-solid ${icon}"></i></div><div class="activity-copy"><strong>${a.title}</strong><small>${a.resource} · ${a.saving}</small></div><span class="activity-time">${a.time}</span></div>`;
}
function renderActions(filter="All"){
  const list=filter==="All"?state.actions:state.actions.filter(a=>a.status===filter);
  const html=list.map(actionHtml).join("");
  if($("#activityPreview")) $("#activityPreview").innerHTML=state.actions.slice(0,4).map(actionHtml).join("");
  if($("#activityFull")) $("#activityFull").innerHTML=html;
}
function alertHtml(a){
  const icon={critical:"fa-circle-exclamation",warning:"fa-triangle-exclamation",info:"fa-circle-info",resolved:"fa-check"}[a.type];
  return `<div class="alert ${a.type}"><div class="alert-icon"><i class="fa-solid ${icon}"></i></div><div class="alert-copy"><strong>${a.title}</strong><small>${a.desc}</small></div><span class="activity-time">${a.time}</span><button class="dismiss-alert" title="Dismiss"><i class="fa-solid fa-xmark"></i></button></div>`;
}
function renderAlerts(){
  const html=state.alerts.map(alertHtml).join("");
  if($("#alertPreview")) $("#alertPreview").innerHTML=state.alerts.slice(0,4).map(alertHtml).join("");
  if($("#alertFull")) $("#alertFull").innerHTML=html;
  $$(".dismiss-alert").forEach(btn=>btn.addEventListener("click",e=>{
    const row=e.currentTarget.closest(".alert"), title=$("strong",row).textContent;
    state.alerts=state.alerts.filter(a=>a.title!==title);renderAlerts();toast("Alert dismissed",title);
  }));
}

function typeIcon(type){return type==="EC2"?"fa-server":type==="RDS"?"fa-database":type==="EKS"?"fa-cubes":"fa-hard-drive"}
function renderResources(){
  const q=($("#resourceSearch")?.value||"").toLowerCase(), status=$("#statusFilter")?.value||"all", sort=$("#resourceSort")?.value||"name";
  let rows=state.resources.filter(r=>(r.name+" "+r.type).toLowerCase().includes(q)&&(status==="all"||r.status===status));
  rows.sort((a,b)=>sort==="cost"?b.cost-a.cost:sort==="cpu"?(b.cpu||0)-(a.cpu||0):a.name.localeCompare(b.name));
  $("#resourceTable").innerHTML=rows.map(r=>`<tr class="resource-row" data-resource="${r.name}">
    <td><div class="resource-name"><span class="resource-icon"><i class="fa-solid ${typeIcon(r.type)}"></i></span><strong>${r.name}</strong></div></td>
    <td>${r.type}</td><td><span class="status ${r.status.toLowerCase()}"><i class="fa-solid fa-circle"></i>${r.status}</span></td>
    <td>${r.cpu===null?"—":r.cpu+"%"}</td><td>${r.memory===null?"—":r.memory+"%"}</td><td><strong>$${r.cost.toLocaleString()}</strong></td>
    <td><span class="optimization ${r.opt==="Optimized"?"optimized":"available"}">${r.opt}</span></td></tr>`).join("");
  $("#resourceCount").textContent=`Showing ${rows.length} resources`;
  $$(".resource-row").forEach(row=>row.addEventListener("click",()=>openResource(row.dataset.resource)));
}
function openResource(name){
  const r=state.resources.find(x=>x.name===name);
  openModal(`<button class="modal-close" onclick="closeModal()"><i class="fa-solid fa-xmark"></i></button><h3>${r.name}</h3><p>${r.type} resource in the ${$("#environmentSelect").value} environment.</p>
  <div class="modal-detail"><div class="detail-box"><span>Status</span><strong>${r.status}</strong></div><div class="detail-box"><span>Monthly cost</span><strong>$${r.cost.toLocaleString()}</strong></div><div class="detail-box"><span>CPU utilization</span><strong>${r.cpu===null?"—":r.cpu+"%"}</strong></div><div class="detail-box"><span>Memory utilization</span><strong>${r.memory===null?"—":r.memory+"%"}</strong></div><div class="detail-box"><span>Optimization</span><strong>${r.opt}</strong></div><div class="detail-box"><span>AI recommendation</span><strong>${r.opt==="Optimized"?"No action needed":"Review opportunity"}</strong></div></div>
  <div class="modal-actions"><button class="primary-btn" onclick="closeModal()">Done</button></div>`);
}

$$(".nav-item").forEach(btn=>btn.addEventListener("click",()=>showSection(btn.dataset.section)));
$$("[data-section-jump]").forEach(btn=>btn.addEventListener("click",()=>showSection(btn.dataset.sectionJump)));
$("#sidebarToggle").addEventListener("click",()=>$("#sidebar").classList.toggle("collapsed"));
$("#mobileMenu").addEventListener("click",()=>{$("#sidebar").classList.add("mobile-open");$("#mobileBackdrop").classList.add("open")});
$("#mobileBackdrop").addEventListener("click",()=>{$("#sidebar").classList.remove("mobile-open");$("#mobileBackdrop").classList.remove("open")});
$("#notificationTrigger").addEventListener("click",e=>{e.stopPropagation();$("#notificationDropdown").classList.toggle("open")});
document.addEventListener("click",e=>{if(!e.target.closest(".notification-wrap"))$("#notificationDropdown").classList.remove("open")});
$("#viewAlerts").addEventListener("click",()=>{showSection("alerts");$("#notificationDropdown").classList.remove("open")});
$("#searchTrigger").addEventListener("click",()=>$("#searchOverlay").classList.add("open"));
$("#closeSearch").addEventListener("click",()=>$("#searchOverlay").classList.remove("open"));
$("#searchOverlay").addEventListener("click",e=>{if(e.target.id==="searchOverlay")$("#searchOverlay").classList.remove("open")});
document.addEventListener("keydown",e=>{if(e.key==="Escape"){closeModal();$("#searchOverlay").classList.remove("open")}});

$("#periods").addEventListener("click",e=>{if(e.target.matches("button"))updatePeriod(e.target.dataset.period)});
$$(".review-btn").forEach(btn=>btn.addEventListener("click",()=>recommendationModal(btn.closest(".recommendation-card"))));
$$(".apply-btn").forEach(btn=>btn.addEventListener("click",()=>applyOptimization(btn.closest(".recommendation-card"))));
$("#modalBackdrop").addEventListener("click",e=>{if(e.target.id==="modalBackdrop")closeModal()});

$("#resourceSearch").addEventListener("input",renderResources);
$("#statusFilter").addEventListener("change",renderResources);
$("#resourceSort").addEventListener("change",renderResources);
$("#actionFilters").addEventListener("click",e=>{if(e.target.matches("button")){$$("#actionFilters button").forEach(b=>b.classList.remove("active"));e.target.classList.add("active");renderActions(e.target.dataset.filter)}});

$("#environmentSelect").addEventListener("change",e=>{toast("Environment switched",`Demo view is now ${e.target.value}.`)});
$("#cloudSelect").addEventListener("change",e=>{toast("Cloud provider switched",`Demo provider is now ${e.target.value}.`)});
$("#settingsEnvironment").addEventListener("change",e=>{$("#environmentSelect").value=e.target.value;toast("Settings updated",`Environment set to ${e.target.value}.`)});
$("#runScan").addEventListener("click",e=>{const btn=e.currentTarget;btn.disabled=true;btn.innerHTML='<i class="fa-solid fa-spinner fa-spin"></i> Scanning...';setTimeout(()=>{btn.disabled=false;btn.innerHTML='<i class="fa-solid fa-check"></i> Scan complete';toast("Optimization scan complete","18 opportunities ranked by impact and safety.");setTimeout(()=>btn.innerHTML='<i class="fa-solid fa-rotate"></i> Run optimization scan',2200)},1800)});

$("#globalSearch").addEventListener("input",e=>{
  const q=e.target.value.toLowerCase().trim();
  if(!q)return;
  const r=state.resources.find(x=>(x.name+" "+x.type).toLowerCase().includes(q));
  if(r){$("#searchOverlay").classList.remove("open");showSection("resources");$("#resourceSearch").value=q;renderResources();}
});

initCharts();renderResources();renderActions();renderAlerts();
setTimeout(animateCounters,250);
