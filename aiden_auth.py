import os, sys, json, time, getpass
from datetime import datetime
from pathlib import Path
import urllib.request, urllib.error
from urllib.parse import urlparse

SESSION_FILE = Path.home() / ".aiden_session.json"

def _normaliza_url(url):
    url = (url or "").strip().rstrip("/")
    if not url: return None
    if not url.startswith(("http://", "https://")): url = "https://" + url
    try:
        p = urlparse(url)
        if not p.scheme or not p.netloc: return None
    except: return None
    return url

def _post(url, body, timeout=15):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r: return json.loads(r.read())
    except urllib.error.HTTPError as e:
        try: return json.loads(e.read())
        except: return {"ok": False, "erro": f"HTTP {e.code}"}
    except Exception as e: return {"ok": False, "erro": str(e)}

def servidor_online(servidor, timeout=8):
    try:
        req = urllib.request.Request(f"{servidor}/health")
        with urllib.request.urlopen(req, timeout=timeout) as r: return r.status == 200
    except: return False

def esperar_servidor(servidor, max_minutos=10):
    print(f"\n⚠  servidor do Aiden está offline ({servidor})")
    print(f"   aguardando reconexão... (Ctrl+C pra sair)")
    t0 = time.time(); tentativa = 0
    while (time.time() - t0) < max_minutos * 60:
        tentativa += 1
        if servidor_online(servidor):
            print(f"\n✅ servidor voltou!"); return True
        time.sleep(min(30, 2 ** min(tentativa, 5)))
    return False

def carregar():
    if not SESSION_FILE.exists(): return None
    try: return json.loads(SESSION_FILE.read_text(encoding="utf-8"))
    except: return None

def salvar(dados):
    SESSION_FILE.write_text(json.dumps(dados, indent=2), encoding="utf-8")
    try: os.chmod(SESSION_FILE, 0o600)
    except: pass

def apagar():
    try: SESSION_FILE.unlink()
    except: pass

def validar(dados):
    if not dados or not dados.get("token"): return False
    req = urllib.request.Request(f"{dados['server']}/auth/check", headers={"Authorization": f"Bearer {dados['token']}"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r: return json.loads(r.read()).get("ok", False)
    except: return False

def _login(servidor):
    print(f"\n═══ login no Aiden ═══")
    print(f"servidor: {servidor}")
    for _ in range(3):
        try:
            user = input("usuário: ").strip()
            senha = getpass.getpass("senha: ")
        except: return None
        r = _post(f"{servidor}/auth/login", {"username": user, "password": senha})
        if r.get("ok"):
            dados = {"username": user, "token": r["token"], "server": servidor, "expires_at": r.get("expires_at")}
            salvar(dados); print(f"✅ logado como {user}"); return dados
        print(f"❌ {r.get('erro', 'falha')}")
    return None

def obter_sessao(servidor=None):
    servidor = _normaliza_url(servidor or os.environ.get("AIDEN_SERVER") or "http://127.0.0.1:8080")
    if not servidor: sys.exit(2)
    
    tem_terminal = sys.stdin.isatty() if sys.stdin else False
    if not servidor_online(servidor):
        if not esperar_servidor(servidor, 10 if tem_terminal else 2): sys.exit(2)

    dados = carregar()
    if dados and _normaliza_url(dados.get("server")) != servidor:
        apagar(); dados = None

    if dados and validar(dados):
        if tem_terminal:
            print(f"\nvocê estava logado como '{dados['username']}'.")
            if input("continuar com essa conta? (S/n): ").strip().lower() in ("", "s", "sim", "y"): return dados
        else:
            return dados
        apagar()
        
    return _login(servidor)