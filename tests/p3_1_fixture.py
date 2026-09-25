"""P3.1 isolated native-Windows UI fixture. Real service + real Kernel.
Only extraction is a labelled local deterministic fixture, never a cloud model.
Credentials and databases stay in ignored .cache / system temporary folders.
"""
import json, os, secrets, subprocess, sys, tempfile, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
LOCAL = ROOT / '.cache' / 'p3_1'
LOCAL.mkdir(parents=True, exist_ok=True)
DATA = Path(tempfile.mkdtemp(prefix='zhiwo-p31-ui-')).resolve()
OWNER = secrets.token_urlsafe(32)
KEY = secrets.token_urlsafe(32)
CONTROL = LOCAL / 'control.json'
CONTROL.write_text('{"running":true}',encoding='utf-8')
class Extractor(BaseHTTPRequestHandler):
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        text = json.loads(body['messages'][-1]['content'])['source_text']
        if '无效提取样本' in text:
            content = 'invalid synthetic extraction result'
        else:
            candidates=[]
            for line in text.splitlines():
                line=line.strip()
                if not line or line.startswith('#'): continue
                category='preference'
                for word,value in [('身份','identity'),('目标','goal'),('项目','project'),('事件','event')]:
                    if word in line: category=value; break
                candidates.append(dict(content=line,kind='event' if category=='event' else 'fact',category=category,scope=None,evidence=line))
            content=json.dumps({'candidates':candidates},ensure_ascii=False)
        raw=json.dumps({'choices':[{'message':{'content':content}}]},ensure_ascii=False).encode('utf-8')
        self.send_response(200); self.send_header('Content-Type','application/json; charset=utf-8'); self.send_header('Content-Length',str(len(raw))); self.end_headers(); self.wfile.write(raw)
    def log_message(self,*args): pass
extractor=ThreadingHTTPServer(('127.0.0.1',8796),Extractor)
threading.Thread(target=extractor.serve_forever,daemon=True).start()
ENV={key:os.environ[key] for key in ('PATH','PATHEXT','SYSTEMROOT','SYSTEMDRIVE','TEMP','TMP','USERPROFILE','APPDATA','LOCALAPPDATA','HOMEDRIVE','HOMEPATH') if key in os.environ}
ENV.update(PYTHONUTF8='1',PYTHONIOENCODING='utf-8',PYTHONPATH=str(ROOT/'server'),HF_HUB_OFFLINE='1',ZHIWO_DATA_DIR=str(DATA),ZHIWO_OWNER_CREDENTIAL=OWNER,ZHIWO_TEST_MODE='1',ZHIWO_FASTEMBED_CACHE_DIR=str(ROOT/'experiments/kernel_spike/runs/p0_3/fastembed-cache'),ZHIWO_EXTRACTOR_BASE_URL='http://127.0.0.1:8796/v1',ZHIWO_EXTRACTOR_MODEL='labelled-synthetic-ui-fixture',ZHIWO_EXTRACTOR_API_KEY=KEY)
state={'origin':'http://127.0.0.1:8765','owner':OWNER,'data_dir':str(DATA),'fixture_pid':os.getpid(),'extraction':'synthetic-local-fixture'}
(LOCAL/'state.json').write_text(json.dumps(state),encoding='utf-8')
log=(LOCAL/'service.log').open('w',encoding='utf-8')
child=None
try:
    print('P3.1 fixture ready. Credentials are stored only in ignored local state.',flush=True)
    while True:
        try: wanted=json.loads(CONTROL.read_text(encoding='utf-8-sig')).get('running',True)
        except (OSError,ValueError): wanted=True
        if wanted and (child is None or child.poll() is not None):
            child=subprocess.Popen([sys.executable,'-m','uvicorn','zhiwo.api.app:app','--host','127.0.0.1','--port','8765','--no-access-log'],cwd=ROOT/'server',env=ENV,stdout=log,stderr=log,creationflags=subprocess.CREATE_NO_WINDOW)
        if not wanted and child is not None and child.poll() is None:
            child.terminate();child.wait(timeout=10);child=None
        time.sleep(.3)
finally:
    if child and child.poll() is None: child.terminate();child.wait(timeout=10)
    extractor.shutdown();log.close()
