"""Interfaccia web di CueStage: pagina per telefono/tablet con le liste (MAIN e PANEL), GO, STOP, pausa.

Il server HTTP gira in un thread; lo stato è un JSON preparato dal thread di Qt (state_fn, ogni 0,25 s)
e i comandi arrivano al thread di Qt tramite il segnale «command». Con un PIN impostato ogni richiesta
deve portare l'intestazione «X-CueStage-Pin» (la pagina lo chiede una volta e lo ricorda).
"""
import hmac
import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from PySide6.QtCore import QObject, QTimer, Signal

COMMANDS = {"go", "go_list", "stop_list", "xfade", "stop_all", "pause_all", "resume_all", "stop_cue", "pause_cue", "playhead",
            "proj", "proj_status", "proj_corners_read", "proj_corner"}


class ProjRelay(QObject):
    """Risultati dei proiettori dai thread dei driver al thread di Qt."""
    result = Signal(str, str, bool, object)     # id proiettore, tipo ("status"/"corners"), ok, dati


def local_addresses():
    """Indirizzi IPv4 della macchina (per mostrare l'URL nelle impostazioni)."""
    out = []
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))
        out.append(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if ip not in out and not ip.startswith("127."):
                out.append(ip)
    except OSError:
        pass
    return out or ["127.0.0.1"]


class _Handler(BaseHTTPRequestHandler):
    server_version = "CueStage"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):     # niente log su stderr a ogni richiesta
        pass

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _authorized(self):
        pin = self.server.web.pin
        if not pin:
            return True
        got = self.headers.get("X-CueStage-Pin", "")
        return hmac.compare_digest(got.encode(), pin.encode())

    def do_GET(self):
        web = self.server.web
        path = self.path.split("?")[0]
        if path in ("/", "/index.html"):
            return self._send(200, PAGE, "text/html; charset=utf-8")
        if path == "/api/state":
            if not self._authorized():
                return self._send(401, '{"error":"pin"}')
            return self._send(200, web.state_json)
        if path == "/api/lists":
            if not self._authorized():
                return self._send(401, '{"error":"pin"}')
            return self._send(200, web.lists_json)
        self._send(404, '{"error":"not found"}')

    def do_POST(self):
        web = self.server.web
        if self.path.split("?")[0] != "/api/cmd":
            return self._send(404, '{"error":"not found"}')
        if not self._authorized():
            return self._send(401, '{"error":"pin"}')
        try:
            n = int(self.headers.get("Content-Length", "0") or 0)
            data = json.loads(self.rfile.read(min(n, 65536)) or b"{}")
        except (ValueError, OSError):
            return self._send(400, '{"error":"json"}')
        if not isinstance(data, dict) or data.get("cmd") not in COMMANDS:
            return self._send(400, '{"error":"comando"}')
        web.command.emit(data)
        self._send(200, '{"ok":true}')


class WebServer(QObject):
    command = Signal(dict)       # emesso dal thread HTTP, ricevuto nel thread di Qt
    status = Signal(str)

    def __init__(self, state_fn, lists_fn):
        super().__init__()
        self.state_fn = state_fn
        self.lists_fn = lists_fn
        self.server = None
        self.thread = None
        self.port = None
        self.pin = ""
        self.state_json = b"{}"
        self.lists_json = b"{}"
        self.timer = QTimer(self)
        self.timer.setInterval(250)
        self.timer.timeout.connect(self.refresh)

    def start(self, port, pin=""):
        self.stop()
        self.pin = str(pin or "")
        try:
            self.server = ThreadingHTTPServer(("0.0.0.0", int(port)), _Handler)
        except OSError as e:
            self.server = None
            self.status.emit(f"Web: porta {port} non disponibile ({e})")
            return False
        self.server.daemon_threads = True
        self.server.web = self
        self.port = int(port)
        self.refresh()
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.timer.start()
        ips = ", ".join(f"http://{ip}:{port}" for ip in local_addresses())
        self.status.emit(f"Interfaccia web attiva: {ips}")
        return True

    def stop(self):
        self.timer.stop()
        if self.server:
            srv = self.server
            self.server = None
            srv.shutdown()          # attende la fine del ciclo del server (≤ 0,5 s): la porta si libera
            srv.server_close()
            if self.thread is not None:
                self.thread.join(1.0)
        self.thread = None
        self.port = None

    def refresh(self):
        try:
            self.lists_json = json.dumps(self.lists_fn(), ensure_ascii=False).encode("utf-8")
            st = self.state_fn()
            st["lists_rev"] = hash(self.lists_json) & 0xFFFFFFFF
            self.state_json = json.dumps(st, ensure_ascii=False).encode("utf-8")
        except Exception as e:          # lo stato non deve mai fermare lo show
            self.state_json = json.dumps({"error": str(e)}).encode("utf-8")


PAGE = r"""<!doctype html>
<html lang="it"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1">
<title>CueStage</title>
<style>
:root{--bg:#15161b;--panel:#20222a;--row:#262832;--line:#33363f;--text:#e9e9ee;--dim:#9a9caa;
--go:#1f8f45;--go2:#2bc060;--stop:#a52a2a;--warn:#c99a2e;--sel:#2f4f7a;}
*{box-sizing:border-box;-webkit-tap-highlight-color:transparent}
body{margin:0;background:var(--bg);color:var(--text);font:15px/1.35 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
header{position:sticky;top:0;z-index:5;background:var(--bg);padding:10px 12px 8px;border-bottom:1px solid var(--line)}
.top{display:flex;gap:8px;align-items:stretch}
button{font:inherit;color:#fff;border:0;border-radius:10px;padding:0 14px;cursor:pointer;min-height:44px}
button:active{filter:brightness(1.3)}
button:disabled{opacity:.35}
#go{background:var(--go);font-size:30px;font-weight:800;flex:0 0 120px;min-height:76px}
.side{display:flex;flex-direction:column;gap:6px;flex:1;min-width:0}
.standby{background:var(--panel);border-radius:10px;padding:6px 10px;min-height:34px}
.standby small{color:var(--dim);display:block;font-size:11px}
.standby b{display:block;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;font-size:17px}
.btns{display:flex;gap:6px}
.btns button{flex:1;min-height:36px;font-weight:700;font-size:13px}
#stop{background:var(--stop)} #pause{background:#7a6418} #resume{background:#2d5f8a}
.tabs{display:flex;gap:6px;overflow-x:auto;padding:10px 12px 0;scrollbar-width:none}
.tab{background:var(--panel);color:var(--text);border-radius:8px 8px 0 0;padding:8px 14px;white-space:nowrap;
min-height:38px;font-weight:700;display:flex;align-items:center;gap:6px}
.tab.sel{background:var(--sel)}
.dot{width:10px;height:10px;border-radius:3px;display:inline-block}
.tag{font-size:10px;background:var(--go);border-radius:4px;padding:1px 4px}
main{padding:0 12px 12px}
.localgo{display:flex;gap:8px;align-items:center;background:#24303f;border-radius:0 10px 10px 10px;padding:8px}
.localgo button{background:var(--go);font-weight:800;min-width:110px}
.localgo #lstop{background:#b3261e;min-width:90px}
.localgo span{flex:1;min-width:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
ul{list-style:none;margin:8px 0 0;padding:0}
li.cue{display:flex;gap:8px;align-items:center;background:var(--row);border-radius:8px;padding:8px 10px;margin-bottom:4px;
min-height:46px;border-left:4px solid transparent}
li.cue.stb{border-left-color:var(--go2);background:#22382b}
li.cue.run{background:#1c2d3f}
li.cue .st{font-size:11px;font-weight:800;border-radius:4px;padding:2px 6px;flex:none;background:var(--go);color:#fff}
li.cue .st.r{background:#2d5f8a}
li.cue .n{color:var(--dim);width:38px;flex:none;font-variant-numeric:tabular-nums}
li.cue .nm{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
li.cue .tr{color:var(--warn);font-size:12px;flex:none}
h3{font-size:13px;color:var(--dim);margin:16px 0 6px;text-transform:uppercase;letter-spacing:.05em}
.act{background:var(--panel);border-radius:10px;padding:8px 10px;margin-bottom:6px}
.act .t{display:flex;gap:6px;align-items:center}
.act .t b{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.badge{font-size:11px;font-weight:700;border-radius:4px;padding:2px 6px;flex:none}
.act button{min-height:34px;padding:0 10px;background:#4a4c58}
.act button.x{background:var(--stop)}
.bar{height:10px;background:#3a3c48;border-radius:5px;margin-top:6px;overflow:hidden}
.bar i{display:block;height:100%;background:var(--go2)}
.bar i.p{background:var(--warn)}
.time{font-size:12px;color:var(--dim);margin-top:3px;display:flex;justify-content:space-between}
.empty{color:var(--dim);text-align:center;padding:14px}
.pj{background:var(--panel);border-radius:10px;padding:10px;margin-top:8px}
.pj h4{margin:0;font-size:16px}.pj .sub{color:var(--dim);font-size:12px;margin-bottom:6px}
.pj .row{display:flex;flex-wrap:wrap;gap:6px;align-items:center;margin:6px 0}
.pj button{min-height:40px;background:#4a4c58;font-weight:600;padding:0 12px}
.pj button.on{background:#2c6b3c}.pj button.off{background:#6b2c2c}
.pj select,.pj input{font:inherit;background:#111;color:#fff;border:1px solid var(--line);border-radius:8px;min-height:40px;padding:0 8px}
.pj input.num{width:70px;text-align:center}
.pj .lens{display:grid;grid-template-columns:auto 52px 52px;gap:6px;align-items:center}
.pj .lens button{font-size:20px;padding:0}
.pj .res{font-size:13px;color:var(--dim);margin-top:6px;min-height:18px}
.pj .res.ok{color:#6c6}.pj .res.err{color:#e66}
.pj details{background:#1a1c22;border-radius:8px;padding:6px 8px;margin:6px 0}
.pj summary{font-weight:700;cursor:pointer;min-height:30px;display:flex;align-items:center}
.cg{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:6px;margin-top:6px}
.cg .c{background:#23252e;border-radius:8px;padding:5px;min-width:0}
.cg .c b{display:block;font-size:12px;color:var(--dim);margin-bottom:4px}
.cg .ax{display:flex;gap:4px;align-items:center;margin:3px 0}
.cg .ax span{width:14px;color:var(--dim);font-size:12px}
.cg .ax button{min-height:38px;width:38px;flex:none;padding:0;font-size:18px}
.cg .ax input{width:0;flex:1;min-width:40px;min-height:38px;text-align:center;padding:0 2px}
.warn{color:var(--warn);font-size:12px;margin-top:6px}
#conn{position:fixed;bottom:8px;right:8px;font-size:12px;background:#7a1f1f;padding:4px 8px;border-radius:6px;display:none}
#pin{position:fixed;inset:0;background:rgba(0,0,0,.8);display:none;align-items:center;justify-content:center;z-index:9}
#pin div{background:var(--panel);padding:20px;border-radius:12px;display:flex;flex-direction:column;gap:10px}
#pin input{font:inherit;font-size:22px;padding:8px;border-radius:8px;border:1px solid var(--line);background:#111;color:#fff;width:180px;text-align:center}
#pin button{background:var(--go)}
</style></head><body>
<header>
 <div class="top">
  <button id="go">GO</button>
  <div class="side">
   <div class="standby"><small id="stbl">In attesa</small><b id="stb">—</b></div>
   <div class="btns"><button id="xf" style="background:#5a3f8a">⇄ X-fade</button><button id="stop">■ STOP</button><button id="pause">❚❚ Pausa</button><button id="resume">▶ Riprendi</button></div>
  </div>
 </div>
</header>
<div class="tabs" id="tabs"></div>
<main>
 <div class="localgo" id="localgo"><button id="lgo">GO</button><button id="lstop">STOP</button><span id="lstb"></span></div>
 <ul id="cues"></ul>
 <div id="projs"></div>
 <h3 id="acth">Cue attive</h3>
 <div id="active"></div>
</main>
<div id="conn">connessione persa…</div>
<div id="pin"><div><b>PIN di CueStage</b><input id="pinin" type="password" inputmode="numeric" autocomplete="off"><button id="pinok">Entra</button></div></div>
<script>
const $=id=>document.getElementById(id);
let PIN=""; try{PIN=localStorage.getItem("cuestage_pin")||""}catch(e){}
if(location.hash.startsWith("#pin=")){   // dal QR code delle impostazioni
  PIN=decodeURIComponent(location.hash.slice(5)); try{localStorage.setItem("cuestage_pin",PIN)}catch(e){}
  history.replaceState(null,"",location.pathname);
}
let view=null, lists=null, rev=null, st=null;
try{view=sessionStorage.getItem("cuestage_view")}catch(e){}
const esc=s=>String(s).replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
const fmt=t=>{if(t==null)return"";t=Math.max(0,t);const m=Math.floor(t/60),s=(t%60).toFixed(1).padStart(4,"0");return`${String(m).padStart(2,"0")}:${s}`};
async function api(path,body){
  const o={headers:{"X-CueStage-Pin":PIN}};
  if(body){o.method="POST";o.headers["Content-Type"]="application/json";o.body=JSON.stringify(body)}
  const r=await fetch(path,o);
  if(r.status===401){$("pin").style.display="flex";throw new Error("pin")}
  return r.json();
}
const cmd=b=>api("/api/cmd",b).then(poll).catch(()=>{});
$("go").onclick=()=>cmd({cmd:"go"});
$("xf").onclick=()=>cmd({cmd:"xfade"});
$("stop").onclick=()=>cmd({cmd:"stop_all"});
$("pause").onclick=()=>cmd({cmd:"pause_all"});
$("resume").onclick=()=>cmd({cmd:"resume_all"});
$("lgo").onclick=()=>cmd({cmd:"go_list",list:view});
$("lstop").onclick=()=>cmd({cmd:"stop_list",list:view});
$("pinok").onclick=()=>{PIN=$("pinin").value;try{localStorage.setItem("cuestage_pin",PIN)}catch(e){};$("pin").style.display="none";rev=null;poll()};
function curList(){return lists&&(lists.lists.find(l=>l.id===view)||lists.lists[0])}
function renderTabs(){
  const L=lists.lists, go=st?st.go_list:null, run=new Set(st?st.running_lists:[]);
  $("tabs").innerHTML=L.map(l=>`<button class="tab ${l.id===view?"sel":""}" data-id="${l.id}">
   ${L.length>1?`<span class="dot" style="background:${l.color}"></span>`:""}${run.has(l.id)?"▶ ":""}${esc(l.name)}${l.id===go?' <span class="tag">GO</span>':""}</button>`).join("")
   +((lists.projectors||[]).length?`<button class="tab ${view==="__proj"?"sel":""}" data-id="__proj">📽 Proiettori</button>`:"");
  $("tabs").querySelectorAll(".tab").forEach(b=>b.onclick=()=>{view=b.dataset.id;try{sessionStorage.setItem("cuestage_view",view)}catch(e){};render()});
}
function renderCues(){
  const isP=view==="__proj";
  $("cues").style.display=isP?"none":""; $("projs").style.display=isP?"":"none";
  if(isP){$("localgo").style.display="none";renderProj();return}
  const l=curList(); if(!l){$("cues").innerHTML="";return}
  const ph=st&&st.playheads?st.playheads[l.id]:-1, running=new Set(st?st.active.map(a=>a.id):[]);
  $("cues").innerHTML=l.cues.length?l.cues.map((c,i)=>`<li class="cue ${i===ph?"stb":""} ${running.has(c.id)?"run":""}" data-i="${i}">
    <span class="n">${esc(c.number||"·")}</span><span>${c.icon}</span><span class="nm" style="${c.grp?'padding-left:18px':''}${c.isgroup?';font-weight:800':''}">${c.grp?'↳ ':''}${esc(c.name)}</span>
    ${c.trigger?`<span class="tr">${c.trigger}</span>`:""}${running.has(c.id)?'<span class="st r">▶ in play</span>':""}${i===ph?'<span class="st">IN ATTESA</span>':""}</li>`).join(""):'<div class="empty">Lista vuota</div>';
  $("cues").querySelectorAll("li.cue").forEach(li=>li.onclick=()=>cmd({cmd:"playhead",list:l.id,index:+li.dataset.i}));
  const show=st&&st.go_target==="main"&&!l.main;
  $("localgo").style.display=show?"flex":"none";
  if(show){const c=l.cues[ph];$("lgo").textContent=l.name.length<=10?"GO "+l.name:"GO";$("lgo").disabled=!c;
    $("lstop").textContent=l.name.length<=10?"STOP "+l.name:"STOP";$("lstop").disabled=!(st&&st.running_lists.includes(l.id));
    $("lstb").textContent=c?`In attesa: ${c.number||""} ${c.name}`:"— fine della lista —"}
}
function renderTop(){
  const gl=lists.lists.find(x=>x.id===st.go_list); const ph=st.playheads[st.go_list]; const c=gl&&gl.cues[ph];
  $("stbl").textContent="In attesa"+(lists.lists.length>1&&gl?" · "+gl.name:"");
  $("stb").textContent=c?`${c.icon} ${c.number||""} ${c.name}`:"— fine della lista —";
  $("go").disabled=!c;
  $("pause").style.display=st.paused_all?"none":""; $("resume").style.display=st.paused_all?"":"none";
}
function renderActive(){
  const A=st.active, multi=lists.lists.length>1;
  $("active").innerHTML=A.length?A.map(a=>{
    const pct=a.progress==null?100:Math.round(a.progress*100);
    return `<div class="act"><div class="t">${multi?`<span class="badge" style="background:${a.color}">${esc(a.list)}</span>`:""}
     <b>${a.icon} ${esc(a.number||"")} ${esc(a.name)}</b>
     ${a.state==="wait"?"":`<button data-p="${a.id}">${a.paused?"▶":"❚❚"}</button>`}<button class="x" data-s="${a.id}">■</button></div>
     ${a.state==="wait"?`<div class="time"><span>in attesa ⧗ ${fmt(a.wait)}</span></div>`:
     `<div class="bar"><i class="${a.paused?"p":""}" style="width:${pct}%"></i></div>
      <div class="time"><span>${fmt(a.elapsed)}</span><span>${a.remaining==null?"∞":"-"+fmt(a.remaining)}</span></div>`}</div>`}).join("")
    :'<div class="empty">Nessuna cue attiva</div>';
  $("active").querySelectorAll("[data-s]").forEach(b=>b.onclick=()=>cmd({cmd:"stop_cue",cue:b.dataset.s}));
  $("active").querySelectorAll("[data-p]").forEach(b=>b.onclick=()=>cmd({cmd:"pause_cue",cue:b.dataset.p}));
}
function render(){ if(!lists||!st)return;
  if(view==="__proj"&&!(lists.projectors||[]).length)view=null;
  if(view!=="__proj"&&!lists.lists.some(l=>l.id===view))view=lists.lists[0].id;
  renderTabs();renderCues();renderTop();renderActive() }
// ---------------------------------------------------------------- proiettori
let projBuilt=null; const cv={}; let lastEdit=0;
const pcmd=b=>api("/api/cmd",b).then(()=>setTimeout(poll,150)).catch(()=>{});
function hold(btn,fn,ms){   // pulsante tenuto premuto: ripete
  let t=null; const stop=()=>{if(t){clearInterval(t);t=null}};
  btn.addEventListener("pointerdown",e=>{e.preventDefault();btn.setPointerCapture&&btn.setPointerCapture(e.pointerId);fn();stop();t=setInterval(fn,ms)});
  ["pointerup","pointercancel","lostpointercapture"].forEach(ev=>btn.addEventListener(ev,stop));
  btn.addEventListener("contextmenu",e=>e.preventDefault());
}
const LENS={zoom:["Zoom","−","+"],focus:["Fuoco","−","+"],shift_h:["Shift orizz.","◀","▶"],shift_v:["Shift vert.","▼","▲"]};
function renderProj(){
  const P=lists.projectors||[];
  if(projBuilt!==rev){projBuilt=rev;buildProj(P)}
  for(const p of P){
    const d=(st.proj||{})[p.id]||{}, r=document.getElementById("res_"+p.id);
    if(r){r.textContent=d.result||"—";r.className="res"+(d.ok===true?" ok":d.ok===false?" err":"")}
    if(p.corners&&d.corners&&Date.now()-lastEdit>1500){
      for(const k in d.corners){for(const [i,a] of [[0,"x"],[1,"y"]]){
        const inp=document.getElementById(`c_${p.id}_${k}_${a}`); const v=d.corners[k][i];
        if(inp&&document.activeElement!==inp&&+inp.value!==v){inp.value=v;cv[p.id][k][a]=v}}}
    }
  }
}
function buildProj(P){
  const box=$("projs"); box.innerHTML="";
  for(const p of P){
    const c=document.createElement("div"); c.className="pj"; const has=a=>p.caps.includes(a);
    let h=`<h4>${esc(p.name)}</h4><div class="sub">${esc(p.driver)}${p.host?" · "+esc(p.host):""}</div><div class="row">`;
    if(has("power_on"))h+=`<button class="on" data-a="power_on">⏻ Accendi</button>`;
    if(has("power_off"))h+=`<button class="off" data-a="power_off">⏻ Spegni</button>`;
    if(has("shutter_close"))h+=`<button data-a="shutter_close">■ Chiudi otturatore</button>`;
    if(has("shutter_open"))h+=`<button data-a="shutter_open">□ Apri otturatore</button>`;
    h+=`</div>`;
    if(has("freeze_on")||has("input")){h+=`<div class="row">`;
      if(has("freeze_on"))h+=`<span>Freeze</span><button data-a="freeze_on">attiva</button><button data-a="freeze_off">disattiva</button>`;
      if(has("input"))h+=`<select id="in_${p.id}">${(p.inputs||[]).map(([c,l])=>`<option value="${esc(c)}">${esc(l)}</option>`).join("")}</select><button data-in="1">Seleziona</button>`;
      h+=`</div>`}
    if((p.lens||[]).length){h+=`<div class="lens">`;
      for(const a of p.lens){const L=LENS[a];h+=`<span>${L[0]}</span><button data-lens="${a}" data-d="-1">${L[1]}</button><button data-lens="${a}" data-d="1">${L[2]}</button>`}
      h+=`</div><div class="row"><span>Velocità</span><select id="sp_${p.id}">${p.speeds.map(([k,v])=>`<option value="${k}" ${k==="normal"?"selected":""}>${esc(v)}</option>`).join("")}</select>
        <span>Passi</span><input class="num" id="st_${p.id}" type="number" min="1" max="50" value="1"></div>`}
    if(has("lens_memory"))h+=`<div class="row"><span>Lens memory</span><input class="num" id="lm_${p.id}" type="number" min="1" max="10" value="1"><button data-lm="1">Richiama</button></div>`;
    if((p.extra||[]).length){h+=`<div class="row">`;
      for(const e of p.extra){if(e.param)h+=`<input id="ex_${p.id}_${e.id}" placeholder="${esc(e.param)}" style="width:100px">`;h+=`<button data-ex="${e.id}">${esc(e.label)}</button>`}
      h+=`</div>`}
    if(p.corners){cv[p.id]={};h+=`<details><summary>Correzione 4 angoli</summary><div class="cg">`;
      for(const k of ["tl","tr","bl","br"]){const C=p.corners[k];cv[p.id][k]={x:0,y:0};
        h+=`<div class="c"><b>${esc(C.label)}</b>`+["x","y"].map(a=>`<div class="ax"><span>${a.toUpperCase()}</span>
          <button data-c="${k}" data-ax="${a}" data-d="-1">−</button><input id="c_${p.id}_${k}_${a}" type="number" min="${C[a][0]}" max="${C[a][1]}" value="0" data-c="${k}" data-ax="${a}">
          <button data-c="${k}" data-ax="${a}" data-d="1">+</button></div>`).join("")+`</div>`}
      h+=`</div><div class="row"><button data-cr="1">Leggi</button><button data-cz="1">Azzera</button><button data-call="1">Invia tutti</button>${has("corners_off")?'<button data-a="corners_off">Disattiva</button>':""}</div>
        ${p.corners_note?`<div class="warn">⚠ ${esc(p.corners_note)}</div>`:""}</details>`}
    if(has("raw"))h+=`<div class="row"><input id="raw_${p.id}" placeholder="comando libero" style="flex:1;min-width:120px"><button data-raw="1">Invia</button></div>`;
    h+=`<div class="row"><button data-stat="1">Leggi stato</button></div><div class="res" id="res_${p.id}">—</div>`;
    c.innerHTML=h; box.appendChild(c); wireProj(c,p);
  }
}
function wireProj(c,p){
  const send=(action,extra)=>pcmd(Object.assign({cmd:"proj",pid:p.id,action},extra||{}));
  c.querySelectorAll("[data-a]").forEach(b=>b.onclick=()=>{const a=b.dataset.a;
    if(a==="power_off"&&!confirm(`Mettere in standby «${p.name}»?`))return; send(a)});
  const inb=c.querySelector("[data-in]"); if(inb)inb.onclick=()=>send("input",{value:$("in_"+p.id).value});
  c.querySelectorAll("[data-lens]").forEach(b=>hold(b,()=>send(b.dataset.lens,{direction:+b.dataset.d,
    speed:$("sp_"+p.id).value,steps:+$("st_"+p.id).value||1}),300));
  const lm=c.querySelector("[data-lm]"); if(lm)lm.onclick=()=>send("lens_memory",{value:$("lm_"+p.id).value});
  c.querySelectorAll("[data-ex]").forEach(b=>b.onclick=()=>{const i=$(`ex_${p.id}_${b.dataset.ex}`);send(b.dataset.ex,{value:i?i.value:""})});
  const rw=c.querySelector("[data-raw]"); if(rw)rw.onclick=()=>{const v=$("raw_"+p.id).value.trim();if(v)send("raw",{value:v})};
  c.querySelector("[data-stat]").onclick=()=>pcmd({cmd:"proj_status",pid:p.id});
  if(!p.corners)return;
  const setC=(k,a,v)=>{const C=p.corners[k][a];v=Math.max(C[0],Math.min(C[1],Math.round(v)||0));
    cv[p.id][k][a]=v;$(`c_${p.id}_${k}_${a}`).value=v;lastEdit=Date.now();
    api("/api/cmd",{cmd:"proj_corner",pid:p.id,corner:k,axis:a,value:v}).catch(()=>{})};
  c.querySelectorAll("button[data-c]").forEach(b=>hold(b,()=>{const k=b.dataset.c,a=b.dataset.ax;setC(k,a,cv[p.id][k][a]+(+b.dataset.d))},90));
  c.querySelectorAll("input[data-c]").forEach(i=>i.oninput=()=>{if(i.value!==""&&i.value!=="-")setC(i.dataset.c,i.dataset.ax,+i.value)});
  c.querySelector("[data-cr]").onclick=()=>{lastEdit=0;pcmd({cmd:"proj_corners_read",pid:p.id})};
  c.querySelector("[data-cz]").onclick=()=>{for(const k in cv[p.id])for(const a of["x","y"]){cv[p.id][k][a]=0;$(`c_${p.id}_${k}_${a}`).value=0}
    lastEdit=Date.now();send("corners",{value:Array(8).fill(0).join(" ")})};
  c.querySelector("[data-call]").onclick=()=>{lastEdit=Date.now();
    send("corners",{value:["tl","tr","bl","br"].map(k=>cv[p.id][k].x+" "+cv[p.id][k].y).join(" ")})};
}
let busy=false;
async function poll(){
  if(busy)return; busy=true;
  try{
    st=await api("/api/state");
    if(st.lists_rev!==rev){lists=await api("/api/lists");rev=st.lists_rev;projBuilt=null}
    document.title="CueStage · "+(st.show||"");
    $("conn").style.display="none"; render();
  }catch(e){ if(e.message!=="pin")$("conn").style.display="block" }
  busy=false;
}
poll(); setInterval(poll,300);
</script></body></html>
"""
