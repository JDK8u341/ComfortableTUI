"use strict";
const query=new URLSearchParams(location.search), canvas=document.querySelector("canvas"), ctx=canvas.getContext("2d");
const processMode=query.get("mode")==="process", pid=Number(query.get("pid")), created=Number(query.get("created"));
let payload=null, range=90;
document.querySelectorAll("[data-range]").forEach(button=>button.onclick=()=>{range=Number(button.dataset.range);draw();});
function draw(){
  if(!payload)return;
  const watched=(payload.watched||[]).find(p=>p.pid===pid&&p.created===created);
  const rows=processMode?(watched?.history||[]).slice(-range).map(v=>[0,v,null]):payload.history.slice(-range);
  ctx.clearRect(0,0,canvas.width,canvas.height);
  ctx.font="11px monospace";
  [0,50,100].forEach(v=>{const y=140-v*1.3;ctx.strokeStyle="#29354b";ctx.beginPath();ctx.moveTo(28,y);ctx.lineTo(680,y);ctx.stroke();ctx.fillStyle="#8292ac";ctx.fillText(String(v),0,y);});
  [1,2].forEach((index)=>{
    ctx.strokeStyle=index===1?"#77e0c2":"#ff79bc";ctx.lineWidth=2;ctx.beginPath();let pen=false;
    rows.forEach((r,i)=>{if(r[index]===null){pen=false;return;}const x=28+i*646/Math.max(1,rows.length-1),y=140-Math.min(100,Math.max(0,r[index]))*1.3;
      if(pen)ctx.lineTo(x,y);else ctx.moveTo(x,y);pen=true;});ctx.stroke();});
  document.querySelector("#badge").textContent=new Date(payload.timestamp*1000).toLocaleTimeString()+" · "+rows.length+" samples";
  const p=processMode?watched?.process:payload.selected;
  document.querySelector("#title").textContent=processMode?(p?`${p.name} · PID ${p.pid}`:`PID ${pid} · exited`):"Resource history";
  document.querySelector("#detail").textContent=processMode?"Process CPU: 100% = one core; graph clips above 100%. Values remain exact in TUI.":"Click a process to focus its TUI row. Browser range is independent for each page.";
  const holder=document.querySelector("#processes");holder.replaceChildren();
  (processMode?(p?[p]:[]):payload.processes.slice(0,12)).forEach(p=>{
    const button=document.createElement("button");button.textContent=`${p.pid} ${p.name} · ${p.cpu===null?"warmup":p.cpu.toFixed(1)+"%"}`;
    button.onclick=()=>comfortable.publish("monitor.select",{pid:p.pid,created:p.created});holder.append(button);});
}
document.addEventListener("DOMContentLoaded",()=>{
  comfortable.subscribe("monitor.sample",event=>{payload=event.payload;draw();});
  comfortable.subscribe("monitor.probe",event=>{
    const p=event.payload;
    comfortable.publish("monitor.proof",{token:p.token,received:payload!==null&&payload.processes.length>0,mode:processMode?"process":"overview"});
    if(!processMode&&payload?.processes.some(row=>row.pid===p.pid&&row.created===p.created))
      comfortable.publish("monitor.select",{pid:p.pid,created:p.created});
  });
});
canvas.addEventListener("mousemove",e=>{
  if(!payload)return;const rows=payload.history.slice(-range),r=rows[Math.round((e.offsetX/canvas.clientWidth)*(rows.length-1))];
  canvas.title=r?new Date(r[0]*1000).toLocaleTimeString()+` CPU ${r[1]??"warmup"}% · RAM ${r[2]}%`:"No sample";
});
