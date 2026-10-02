"""当該worktreeの固定buildをsoftware fixtureで確認する。実device受入ではない。"""
import base64,json,os,secrets,signal,subprocess,time,urllib.request,sys,tempfile
from urllib.parse import urlsplit
from pathlib import Path
from websockets.sync.client import connect
def _owned_debugger_tab(browser, profile_dir: Path, timeout_s: float = 10.0):
    """今回だけのprofileへChromiumが書いたendpoint以外には接続しない。"""
    deadline = time.monotonic() + timeout_s
    active_port = profile_dir / "DevToolsActivePort"
    while time.monotonic() < deadline:
        if browser.poll() is not None:
            raise RuntimeError("owned Chromium exited before CDP discovery")
        try:
            lines = active_port.read_text(encoding="utf-8").splitlines()
        except FileNotFoundError:
            time.sleep(.05)
            continue
        if len(lines) < 2:
            time.sleep(.05)
            continue
        port = int(lines[0])
        path = lines[1]
        if not 0 < port < 65536 or not path.startswith("/devtools/browser/"):
            raise RuntimeError("invalid owned Chromium endpoint")
        origin = f"http://127.0.0.1:{port}"
        with urllib.request.urlopen(origin + "/json/version", timeout=1) as response:
            version = json.load(response)
        if version.get("webSocketDebuggerUrl") != f"ws://127.0.0.1:{port}{path}":
            raise RuntimeError("owned Chromium browser identity mismatch")
        with urllib.request.urlopen(origin + "/json/list", timeout=1) as response:
            tabs = json.load(response)
        tab = next((item for item in tabs if item.get("type") == "page"), None)
        if tab is None:
            time.sleep(.05)
            continue
        endpoint = urlsplit(tab.get("webSocketDebuggerUrl", ""))
        if (endpoint.scheme != "ws" or endpoint.netloc != f"127.0.0.1:{port}"
                or not endpoint.path.startswith("/devtools/page/") or endpoint.query or endpoint.fragment):
            raise RuntimeError("owned Chromium page endpoint mismatch")
        if browser.poll() is not None:
            raise RuntimeError("owned Chromium exited during CDP discovery")
        return tab
    raise RuntimeError("owned Chromium CDP discovery timeout")


ROOT=Path(sys.argv[1])
BASE=Path(sys.argv[4])
E=BASE/('latency-'+sys.argv[3])
E.mkdir(exist_ok=True)
TEMP=Path(sys.argv[5])/('latency-'+sys.argv[3])
TEMP.mkdir(parents=True,exist_ok=True)
PY=Path(sys.executable)
env={**os.environ,'PYTHONPATH':str(ROOT/'src')+';'+str(ROOT/'src/xpotato_sim/plugins/robots/fast_arm/core/src'),
 'PYTHONUTF8':'1','PYTHONDONTWRITEBYTECODE':'1','PYTHONUNBUFFERED':'1','TMP':str(TEMP),'TEMP':str(TEMP)}
cap=secrets.token_urlsafe(32)
args=[str(PY),'-c','from xpotato_sim.cli import main; main()','workbench','--temporary-root',str(TEMP),
 '--result-root',str(E/'trial-results'),'--software-revision','610-parent-latency-'+sys.argv[3],
 '--web-dist',sys.argv[2],'--web-port','5396','--backend-port','8986',
 '--control-stdin','--ticks','6000','--input-wait-s','15','--wall-s','120']
log=(E/'browser-app.log').open('w',encoding='utf-8')
app=subprocess.Popen(args,cwd=ROOT,env=env,stdin=subprocess.PIPE,stdout=log,stderr=subprocess.STDOUT,creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
app.stdin.write((cap+'\n').encode());app.stdin.close()
browser=None;wire=None;counter=0;report={'software_fixture':True,'checks':[],'performance':[]}
def cdp(method,params=None):
 global counter
 report['last_cdp_method']=method
 counter+=1;rid=counter;wire.send(json.dumps({'id':rid,'method':method,'params':params or {}}))
 while True:
  try: msg=json.loads(wire.recv(timeout=40))
  except TimeoutError as error: raise RuntimeError('CDP timeout: '+method) from error
  if msg.get('id')==rid:
   if 'error' in msg:raise RuntimeError(msg['error'])
   return msg.get('result',{})
def js(expression):
 r=cdp('Runtime.evaluate',{'expression':expression,'awaitPromise':True,'returnByValue':True})
 if 'exceptionDetails' in r:raise RuntimeError(r['exceptionDetails'])
 return r.get('result',{}).get('value')
def wait(expression,seconds=30):
 return js(f"(async()=>{{const end=Date.now()+{seconds*1000};while(Date.now()<end){{if({expression})return true;await new Promise(r=>setTimeout(r,50));}}throw new Error('wait timeout '+document.body.innerText.slice(0,1500))}})()")
def click(text):
 return js(f"(()=>{{const b=[...document.querySelectorAll('button')].find(b=>b.textContent==={json.dumps(text)});if(!b||b.disabled)throw new Error('disabled '+{json.dumps(text)});b.click();return true}})()")
def size(w,h,dpr=1):cdp('Emulation.setDeviceMetricsOverride',{'width':w,'height':h,'deviceScaleFactor':dpr,'mobile':False})
def shot(name):
 report['stage']=name; print(name,flush=True); time.sleep(.2)
 assert '?' * 6 not in js('document.body.innerText'), name
 if name.startswith('operate-') and name not in ['operate-narrow','operate-200-percent-equivalent']:
  styles=js("({color:getComputedStyle(document.querySelector('.operation-shell')).color,bg:getComputedStyle(document.querySelector('.operation-shell')).backgroundColor,selected:[...document.querySelectorAll('.operation-controls button[aria-pressed=true]')].map(b=>getComputedStyle(b).backgroundColor),other:[...document.querySelectorAll('.operation-controls button[aria-pressed=false]')].map(b=>getComputedStyle(b).backgroundColor)})")
  assert styles['color']=='rgb(40, 51, 56)',styles
  assert styles['bg']=='rgb(244, 245, 244)',styles
  assert all(v=='rgb(217, 231, 237)' for v in styles['selected']),styles
  report.setdefault('style_checks',[]).append({'screen':name,**styles})
 (E/(name+'.png')).write_bytes(base64.b64decode(cdp('Page.captureScreenshot',{'format':'png'})['data']))
def geometry():
 return js("({w:innerWidth,h:innerHeight,bodyScroll:document.documentElement.scrollHeight>innerHeight,canvasCount:document.querySelectorAll('canvas').length,panes:[...document.querySelectorAll('[data-scene-pane]')].map(e=>({id:e.dataset.scenePane,x:e.getBoundingClientRect().x,y:e.getBoundingClientRect().y,width:e.clientWidth,height:e.clientHeight})),stop:[...document.querySelectorAll('button')].find(b=>b.textContent==='停止を要求')?.getBoundingClientRect().toJSON()})")
try:
 for _ in range(150):
  if app.poll() is not None:raise RuntimeError('app exit '+str(app.returncode))
  try:urllib.request.urlopen('http://127.0.0.1:5396/apps/mujoco-viewer/',timeout=1);break
  except Exception:time.sleep(.1)
 else:raise RuntimeError('server startup timeout')
 # 新規profileとOS選択portを使い、既存CDPへ接続・Browser.closeしない。
 browser_profile=Path(tempfile.mkdtemp(prefix='owned-chromium-',dir=TEMP))
 browser=subprocess.Popen([sys.argv[6],'--headless=new','--no-first-run',
  '--remote-debugging-port=0','--user-data-dir='+str(browser_profile),'--disable-background-networking','--use-angle=d3d11','about:blank'],env=env,stdout=subprocess.DEVNULL,stderr=(E/'browser-stderr.log').open('w',encoding='utf-8'))
 tab=_owned_debugger_tab(browser,browser_profile)
 report['browser_ownership']={'method':'fresh profile / DevToolsActivePort / matching browser endpoint','pid':browser.pid,'profile':str(browser_profile),'page_endpoint':tab['webSocketDebuggerUrl']}
 wire=connect(tab['webSocketDebuggerUrl'],proxy=None,max_size=32*2**20)
 cdp('Page.enable');cdp('Runtime.enable');size(1440,900)
 cdp('Page.addScriptToEvaluateOnNewDocument',{'source':"window.__qaPad={mapping:'standard',id:'software-standard-pad',index:0,connected:true,axes:[0,0,0,0],buttons:Array.from({length:17},()=>({pressed:false,touched:false,value:0})),get timestamp(){return performance.now()}};window.__qaDeviceVisible=false;Object.defineProperty(navigator,'getGamepads',{value:()=>{return window.__qaDeviceVisible?[window.__qaPad,null,null,null]:[null,null,null,null]}});window.__qaLastFrame=null;const NativeWebSocket=WebSocket;window.WebSocket=class extends NativeWebSocket{send(value){try{const m=JSON.parse(value);if(m.op==='input'){window.__qaLastInput=m;window.__qaControl=this;if(window.__latencyPending&&!window.__latencyPending.sequence){const s=JSON.parse(m.message);if(s.gamepad.raw_axes[0]===window.__latencyPending.value)window.__latencyPending.sequence=s.sequence;}}}catch{}super.send(value)}constructor(...args){super(...args);this.addEventListener('message',e=>{try{const m=JSON.parse(e.data);if(m.type==='frame')window.__qaLastFrame=m;}catch{}})}};"})
 cdp('Emulation.setFocusEmulationEnabled',{'enabled':True})
 cdp('Page.navigate',{'url':f'http://127.0.0.1:5396/apps/mujoco-viewer/?workbench=8986#capability={cap}'})
 wait("[...document.querySelectorAll('button')].some(b=>b.textContent==='操作権を取得')")
 click('操作権を取得');wait("document.body.innerText.includes('操作権あり')")
 js("(()=>{const s=document.querySelector('[aria-label=\"次の条件\"]');Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype,'value').set.call(s,'dynamic-cube-drop');s.dispatchEvent(new Event('change',{bubbles:true}))})()")
 wait("[...document.querySelectorAll('button')].some(b=>b.textContent==='検証・準備'&&!b.disabled)")
 click('検証・準備');wait("[...document.querySelectorAll('button')].some(b=>b.textContent==='操作画面へ'&&!b.disabled)")
 assert js("[...document.querySelectorAll('button')].find(b=>b.textContent==='開始').disabled")

 click('操作画面へ');click('Assist');click('操作視点')
 wait("document.querySelectorAll('[data-scene-pane]').length===3")
 click('開始');js('window.__qaDeviceVisible=true')
 wait("document.body.innerText.includes('実行中') && window.__workbenchCounters().renderedInputSequence!==null")
 report['method']='Synthetic input change to matching sequence CPU draw submission; not GPU completion or physical gamepad latency'
 report['source']=str(ROOT);report['dist']=sys.argv[2]
 report['gpu']=js("(()=>{const g=document.querySelector('canvas').getContext('webgl2'),d=g.getExtension('WEBGL_debug_renderer_info');return g.getParameter(d?d.UNMASKED_RENDERER_WEBGL:g.RENDERER)})()")
 cdp('Performance.enable')
 for layout in ['Single','Assist']:
  click(layout)
  metrics_before={v['name']:v['value'] for v in cdp('Performance.getMetrics')['metrics']}
  wall_before=time.perf_counter()
  rows=js("(async()=>{const out=[];for(let i=0;i<52;i++){await new Promise(r=>setTimeout(r,35+(i*17)%41));const value=i%2?.16:-.16;window.__latencyPending={value,started:performance.now(),sequence:null};window.__qaPad.axes=[value,0,0,0];const deadline=performance.now()+2500;while(true){const c=window.__workbenchCounters(),p=window.__latencyPending;if(p.sequence!==null&&c.renderedInputSequence>=p.sequence){out.push({latency_ms:c.renderedAtMs-p.started,sequence:p.sequence});break}if(performance.now()>deadline)throw new Error('latency deadline');await new Promise(r=>setTimeout(r,2));}}window.__qaPad.axes=[0,0,0,0];return out})()")
  wall_elapsed=time.perf_counter()-wall_before
  metrics_after={v['name']:v['value'] for v in cdp('Performance.getMetrics')['metrics']}
  renderer_cpu=metrics_after['TaskDuration']-metrics_before['TaskDuration']
  report.setdefault('resources',[]).append({'layout':layout,'wall_s':wall_elapsed,'renderer_task_cpu_s':renderer_cpu,'renderer_one_core_percent':100*renderer_cpu/wall_elapsed,'renderer_js_heap_used_bytes':metrics_after.get('JSHeapUsedSize'),'scope':'renderer main-thread CDP TaskDuration; excludes worker, GPU and other Chromium processes'})
  valid=rows[4:];values=sorted(r['latency_ms'] for r in valid)
  report['performance'].append({'layout':layout,'samples':len(values),'median_ms':values[len(values)//2],'p95_ms':values[int((len(values)-1)*.95)],'max_ms':max(values),'raw':valid})
  assert js("document.body.innerText.includes('実行中')"),js('document.body.innerText')
 click('停止を要求');wait("document.body.innerText.includes('結果保存済み')")
 report['result']='PASS'
 print(json.dumps({k:v for k,v in report.items() if k not in ['checks']},ensure_ascii=False),flush=True)

except Exception as error:
 report['result']='FAIL';report['error']=str(error);raise
finally:
 (E/'browser-validation.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
 if wire:
  try:cdp('Browser.close')
  except Exception:pass
  wire.close()
 if browser:
  try:browser.wait(timeout=8)
  except subprocess.TimeoutExpired:browser.terminate();browser.wait(timeout=8)
 if app.poll() is None:
  app.send_signal(signal.CTRL_BREAK_EVENT)
  try:app.wait(timeout=10)
  except subprocess.TimeoutExpired:app.terminate();app.wait(timeout=10)
 log.close()
