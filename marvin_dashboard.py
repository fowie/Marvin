"""Dependency-free browser dashboard assets for the local operator service."""

HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Marvin operator</title>
  <link rel="stylesheet" href="/style.css">
</head>
<body>
<header>
  <div><h1>Marvin operator</h1><p>Local, evidence-bounded controls</p></div>
  <button id="stop" class="danger">STOP <kbd>Space</kbd></button>
</header>
<div id="alert" role="alert" aria-live="assertive"></div>
<main>
  <section aria-labelledby="global-title">
    <h2 id="global-title">System</h2>
    <dl id="global"></dl>
    <p class="boundary">Ground driving is not established. Live motion is for a
      secured on-blocks setup with an operator at the external cutoff.</p>
  </section>

  <section aria-labelledby="sensors-title" class="wide">
    <h2 id="sensors-title">Sensors</h2>
    <p id="freshness" aria-live="polite">Waiting for a snapshot.</p>
    <div class="toolbar">
      <button id="record-start">Start JSONL recording</button>
      <button id="record-stop">Stop recording</button>
      <span id="record-status"></span>
    </div>
    <h3>Proximity</h3><div id="proximity" class="cards"></div>
    <h3>Cliff channels <small>(raw; physical positions unresolved)</small></h3>
    <div id="cliff" class="cards"></div>
    <h3>Motors / encoders</h3><div id="motors" class="cards"></div>
    <h3>Servo words</h3><div id="servos" class="cards"></div>
  </section>

  <section aria-labelledby="drive-title">
    <h2 id="drive-title">Drive</h2>
    <label>Mode
      <select id="drive-mode">
        <option value="deadman">Press-and-hold dead-man</option>
        <option value="fixed">Fixed: four proved 250 ms pulses</option>
      </select>
    </label>
    <div class="drive-pad">
      <button data-drive="forward" aria-label="Forward">W<br>Forward</button>
      <button data-drive="rotate-left" aria-label="Rotate left">A<br>Left</button>
      <button data-drive="backward" aria-label="Backward">S<br>Back</button>
      <button data-drive="rotate-right" aria-label="Rotate right">D<br>Right</button>
    </div>
    <p id="drive-status" aria-live="polite"></p>
  </section>

  <section aria-labelledby="led-title">
    <h2 id="led-title">LEDs</h2>
    <p>Individual binary states only; combined effects are not evidence-supported.</p>
    <div id="leds" class="toggles"></div>
    <button id="led-reset">Reset to captured baseline</button>
    <p id="led-status" aria-live="polite"></p>
  </section>

  <section aria-labelledby="media-title" class="wide">
    <h2 id="media-title">Private media</h2>
    <p>No automatic preview, playback, upload, or transcription. Five-minute cap.</p>
    <div class="media-grid">
      <fieldset><legend>Microphone</legend>
        <label><input id="audio-consent" type="checkbox"> I confirm recording privacy</label>
        <button id="audio-start">Start recording</button>
        <button id="audio-stop">Stop recording</button>
        <p id="audio-status" aria-live="polite"></p>
      </fieldset>
      <fieldset><legend>LifeCam</legend>
        <p class="warning">Direct-host route is planned/unverified until Jetson acceptance.</p>
        <label><input id="video-consent" type="checkbox"> I confirm capture privacy</label>
        <button id="frame-capture">Capture frame</button>
        <button id="video-start">Start video</button>
        <button id="video-stop">Stop video</button>
        <p id="video-status" aria-live="polite"></p>
      </fieldset>
    </div>
  </section>
</main>
<script src="/app.js" defer></script>
</body>
</html>
"""

CSS = """
:root{color-scheme:dark;--bg:#101418;--panel:#1a222a;--line:#52606d;--text:#f4f7f9;--muted:#bac5ce;--accent:#65c7ff;--bad:#ff5d68;--warn:#ffd166}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:16px/1.45 system-ui,sans-serif}
header{position:sticky;top:0;z-index:2;display:flex;justify-content:space-between;align-items:center;padding:.8rem 1.2rem;background:#0b0e11;border-bottom:2px solid var(--line)}
h1,h2,h3,p{margin:.25rem 0 .7rem}header p,small{color:var(--muted)}main{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:1rem;padding:1rem}
section,fieldset{min-width:0;padding:1rem;background:var(--panel);border:1px solid var(--line);border-radius:.35rem}.wide{grid-column:1/-1}
button,select{min-height:44px;padding:.55rem .8rem;border:2px solid var(--line);border-radius:.25rem;background:#26333e;color:var(--text);font:inherit;font-weight:650}
button:hover:not(:disabled),button:focus-visible,select:focus-visible{border-color:var(--accent);outline:2px solid var(--accent);outline-offset:2px}button:disabled{opacity:.45;cursor:not-allowed}
.danger{background:#8d1420;border-color:var(--bad);font-size:1.15rem}.boundary,.warning{padding:.55rem;border-left:5px solid var(--warn);background:#312d1f}
#alert:not(:empty){padding:.7rem 1rem;background:#63101a;border-bottom:2px solid var(--bad)}dl{display:grid;grid-template-columns:max-content 1fr;gap:.25rem .8rem}dd{margin:0}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:.5rem}.card{padding:.55rem;background:#11171c;border:1px solid var(--line)}.card strong,.card span{display:block}.card span{color:var(--muted);font-size:.85rem}
.drive-pad{display:grid;grid-template-columns:repeat(3,1fr);gap:.5rem;margin:1rem 0}.drive-pad button:nth-child(1){grid-column:2}.drive-pad button:nth-child(2){grid-column:1}.drive-pad button:nth-child(3){grid-column:2}.drive-pad button:nth-child(4){grid-column:3}
.toggles{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:.35rem;margin:.6rem 0}.toggles label{padding:.4rem;background:#11171c}.toolbar{display:flex;gap:.5rem;align-items:center;flex-wrap:wrap}.media-grid{display:grid;grid-template-columns:1fr 1fr;gap:1rem}
.stale{color:var(--bad);font-weight:700}.ok{color:#7ee787}kbd{font:inherit;font-size:.8em}@media(max-width:760px){main,.media-grid{grid-template-columns:1fr}.wide{grid-column:auto}header{position:static}}
"""

DEADMAN_CORE = r"""
async function acquireHeld(direction,token,acquireLease,releaseLease,sendPulse){
  const candidate=await acquireLease();
  if(token.cancelled){await releaseLease(candidate);return null}
  await sendPulse(direction,candidate);
  if(token.cancelled){await releaseLease(candidate);return null}
  return candidate
}
async function runHeartbeatLoop(token,isActive,wait,sendPulse){
  while(isActive(token)){
    await wait();
    if(!isActive(token))return;
    await sendPulse();
  }
}
function sseRequiresRelease(event){
  if(!event.data)return true;
  const failed=JSON.parse(event.data);
  return Boolean(failed.error||failed.managers?.drive?.error)
}
"""

JS = DEADMAN_CORE + r"""
"use strict";
const $=id=>document.getElementById(id), directions={w:"forward",s:"backward",a:"rotate-left",d:"rotate-right"};
let config={}, status={}, lease=null, held=null, holdToken=null, heartbeat=null, busy=false, sensorStamp=null,driveResult="none";
const controls=[...document.querySelectorAll("[data-drive]")];
function text(value){return value===null||value===undefined?"unknown":String(value)}
function scalar(item){const value=item&&item.value!==undefined?item.value:item;return value&&typeof value==="object"?(value.signed??value.unsigned??value.raw_hex??"raw"):value}
function card(name,item,detail){const node=document.createElement("div");node.className="card";const strong=document.createElement("strong"),value=document.createElement("b"),small=document.createElement("span");strong.textContent=name;value.textContent=text(scalar(item));small.textContent=detail||"";node.append(strong,value,small);return node}
function cards(id,items,details){const root=$(id);root.replaceChildren();Object.entries(items||{}).forEach(([name,value])=>root.append(card(name,value,details?details(name,value):"")))}
function error(message){$("alert").textContent=message||""}
async function api(path,body={}){
  const response=await fetch(path,{method:"POST",keepalive:true,headers:{"Content-Type":"application/json","X-Marvin-Operator":"1"},body:JSON.stringify(body)});
  const value=await response.json();if(!response.ok)throw new Error(value.error||`HTTP ${response.status}`);return value
}
async function safe(path,body){try{error("");return await api(path,body)}catch(e){error(e.message);await release("API error");throw e}}
function setDisabled(){
  const managers=status.managers||{}, drive=status.state==="running"&&!!managers.drive&&["ready","leased"].includes(managers.drive.state);
  controls.forEach(button=>button.disabled=!drive||busy);$("stop").disabled=!drive;
  $("record-start").disabled=status.connection?.status!=="connected"||status.recording?.active;$("record-stop").disabled=!status.recording?.active;
  const led=managers.leds, active=led?.active_channel;document.querySelectorAll("[data-led]").forEach(input=>{input.disabled=!led||led.state!=="ready"||(active&&active!==input.dataset.led)});
  $("led-reset").disabled=!led||led.state!=="ready";
  const audio=managers.microphone,video=managers.camera;
  $("audio-start").disabled=!audio||!["ready","failed"].includes(audio.state)||!$("audio-consent").checked;$("audio-stop").disabled=!audio||audio.state!=="recording";
  ["video-start","frame-capture"].forEach(id=>$(id).disabled=!video||!["ready","failed"].includes(video.state)||!$("video-consent").checked);$("video-stop").disabled=!video||video.state!=="recording";
}
function renderStatus(next){
  status=next;config=next.configuration||config;const budget=next.connection?.budget;const rows={Server:next.state,Controller:next.connection?.status,Fresh:next.freshness?.fresh,"Normal requests remaining":budget?.normal_requests_remaining??"unavailable","Cleanup requests remaining":budget?.total_requests_remaining===undefined?"unavailable":budget.total_requests_remaining-budget.normal_requests_remaining,Error:next.error||"none",Cleanup:next.state==="stopped"?"complete":"owned by runtime"};
  const root=$("global");root.replaceChildren();Object.entries(rows).forEach(([key,value])=>{const dt=document.createElement("dt"),dd=document.createElement("dd");dt.textContent=key;dd.textContent=text(value);root.append(dt,dd)});
  const drive=next.managers?.drive;$("drive-status").textContent=drive?`Direction: ${text(drive.direction)} | lease: ${drive.lease_remaining_seconds===null?"none":drive.lease_remaining_seconds.toFixed(2)+"s"} | pulses: ${drive.pulses_completed} | last: ${drive.error||driveResult}`:"Drive disabled: no verified controller callbacks.";
  const rec=next.recording||{};$("record-status").textContent=rec.active?`${rec.file||"opening"} | chunk ${rec.chunk||0} | ${Math.floor(rec.chunk_elapsed_seconds||0)}s | ${rec.rows||0} rows`:rec.error||"not recording";
  for(const kind of ["audio","video"]){const manager=next.managers?.[kind==="audio"?"microphone":"camera"];$(`${kind}-status`).textContent=manager?`${manager.state}; ${manager.elapsed_seconds===null?"0":Math.floor(manager.elapsed_seconds)}s / ${manager.maximum_seconds}s; ${manager.output||"no output"}; ${manager.error||"no error"}`:`${kind} disabled: route not configured.`}
  renderLeds(next.managers?.leds);setDisabled()
}
function renderSensor(message){
  sensorStamp=message.observed_at;const snapshot=message.snapshot||{};
  cards("proximity",snapshot.proximity,(n,v)=>`${v.physical_sensor||""} ${v.documented_location||""} | confidence: ${v.mapping_confidence||"unknown"}`);
  cards("cliff",snapshot.cliff,()=> "raw; physical mapping unresolved");cards("motors",snapshot.motors);cards("servos",snapshot.servos,(n,v)=>typeof v==="object"?`${v.documented_mapping||""} | ${v.mapping_confidence||""}`:"");
  refreshFreshness()
}
function refreshFreshness(){const fresh=status.freshness?.fresh&&sensorStamp;$("freshness").className=fresh?"ok":"stale";$("freshness").textContent=fresh?`Connected; latest ${sensorStamp}`:`STALE / unavailable; ${status.error||"no current sensor snapshot"}`}
function renderLeds(manager){const root=$("leds");if(root.children.length===0){Object.keys(manager?.channels||{}).forEach(name=>{const label=document.createElement("label"),input=document.createElement("input");input.type="checkbox";input.dataset.led=name;input.addEventListener("change",()=>led(name,input.checked));label.append(input,document.createTextNode(" "+name));root.append(label)})}document.querySelectorAll("[data-led]").forEach(input=>{const value=manager?.channels?.[input.dataset.led]?.value;input.checked=value===255;input.title=value===undefined||value===null?"unknown":`current ${value}`});$("led-status").textContent=manager?`Active: ${manager.active_channel||"none"}; baseline ${manager.baseline_hex?"captured":"unknown"}; restore uses captured baseline.`:"LEDs disabled: baseline getter/setter not configured."}
async function pulse(direction,owned=lease){await safe(`/api/drive/heartbeat/${direction}`,{lease:owned});driveResult=`${direction} pulse accepted`}
async function releaseLease(owned){await api("/api/drive/release",{lease:owned})}
async function hold(direction){if(held||busy)return;held=direction;const token={cancelled:false};holdToken=token;busy=true;setDisabled();try{const owned=await acquireHeld(direction,token,async()=>(await safe("/api/drive/acquire")).drive.lease,releaseLease,pulse);if(!owned)return;lease=owned;heartbeat=runHeartbeatLoop(token,current=>held===direction&&holdToken===current&&!current.cancelled,()=>new Promise(resolve=>setTimeout(resolve,650)),()=>pulse(direction));heartbeat.catch(()=>{})}finally{busy=false;setDisabled()}}
async function release(reason){held=null;if(holdToken)holdToken.cancelled=true;holdToken=null;heartbeat=null;if(!lease)return;const owned=lease;lease=null;try{await releaseLease(owned)}catch(e){error(`${reason}: ${e.message}`)}}
async function activate(direction){if($("drive-mode").value==="fixed"){if(busy)return;busy=true;setDisabled();try{await safe(`/api/drive/fixed/${direction}`);driveResult=`${direction}: four pulses completed`}finally{busy=false;setDisabled()}}else await hold(direction)}
async function priorityStop(){held=null;if(holdToken)holdToken.cancelled=true;holdToken=null;heartbeat=null;lease=null;try{await safe("/api/drive/stop");driveResult="explicit stop requested"}catch(_){}}
controls.forEach(button=>{const direction=button.dataset.drive;button.addEventListener("pointerdown",event=>{event.preventDefault();button.setPointerCapture(event.pointerId);activate(direction)});for(const event of ["pointerup","pointercancel","lostpointercapture"])button.addEventListener(event,()=>release(event.type));button.addEventListener("click",event=>{if(event.detail===0)activate(direction)})});
addEventListener("keydown",event=>{if(event.code==="Space"){event.preventDefault();priorityStop();return}const direction=directions[event.key.toLowerCase()];if(direction&&!event.repeat){event.preventDefault();activate(direction)}});
addEventListener("keyup",event=>{if(directions[event.key.toLowerCase()])release("keyup")});addEventListener("blur",()=>release("window blur"));addEventListener("pagehide",()=>release("pagehide"));document.addEventListener("visibilitychange",()=>{if(document.hidden)release("visibility hidden")});
$("stop").addEventListener("click",priorityStop);$("drive-mode").addEventListener("change",()=>release("mode change"));
async function led(name,on){try{await safe(`/api/leds/${name}/${on?"on":"off"}`)}catch(_){renderStatus(status)}}
$("led-reset").addEventListener("click",()=>safe("/api/leds/reset"));
$("record-start").addEventListener("click",()=>safe("/api/recording/start",{directory:config.evidence_root}));$("record-stop").addEventListener("click",()=>safe("/api/recording/stop"));
function media(kind,action,suffix){const consent=$(`${kind}-consent`).checked;if(!consent){error("Explicit privacy confirmation is required.");return}const key=kind==="audio"?"microphone":"camera";safe(`/api/media/${kind}/${action}`,{output:`${config.media_directory}/marvin-${kind}-${Date.now()}.${suffix}`,usb_path:config[`${key}_usb_path`],privacy_authorized:true})}
$("audio-start").addEventListener("click",()=>media("audio","start","wav"));$("audio-stop").addEventListener("click",()=>safe("/api/media/audio/stop"));
$("video-start").addEventListener("click",()=>media("video","start","mkv"));$("video-stop").addEventListener("click",()=>safe("/api/media/video/stop"));$("frame-capture").addEventListener("click",()=>media("video","capture","jpg"));
for(const id of ["audio-consent","video-consent"])$(id).addEventListener("change",setDisabled);
const events=new EventSource("/api/events");events.addEventListener("status",event=>renderStatus(JSON.parse(event.data)));events.addEventListener("sensor",event=>renderSensor(JSON.parse(event.data)));events.addEventListener("error",event=>{if(event.data){const failed=JSON.parse(event.data);renderStatus(failed);error(failed.error||failed.managers?.drive?.error||"Operator runtime error.")}else error("Operator event stream failed; motion release requested.");if(sseRequiresRelease(event))release("SSE error");refreshFreshness()});
fetch("/api/status").then(r=>r.json()).then(renderStatus).catch(e=>error(e.message));setInterval(refreshFreshness,1000);
"""
