"""
Aiden Voz — o cérebro que atende o ESP32.

ESP32 (botão BOOT + microfone) → manda o áudio por Wi-Fi → este servidor
transcreve (Whisper da Groq) → roda o agente (aiden_pc ou aiden_termux)
→ devolve o texto pra tela do ESP32 e fala a resposta com voz neural pt-BR.

Uso:   python aiden_voz.py [pc|termux]
Voz:   pip install edge-tts      (no PC: instale também ffplay ou mpv)
       No Termux: pkg install termux-api; a voz toca por termux-media-player.
Obs:   pedidos de confirmação [s/N] (SMS, etc.) continuam aparecendo neste terminal.
"""

import aiden_core as core
import os, sys, io, wave, re, shutil, subprocess, threading, socket, secrets
import unicodedata, asyncio, tempfile, time, importlib, json
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path

PORTA = 8765
VOZ = "pt-BR-AntonioNeural"     # outras: pt-BR-FranciscaNeural, pt-BR-ThalitaMultilingualNeural
RITMO = "+8%"                   # velocidade da fala
MAX_SEG = 60                    # limite de áudio por pedido

modo = (sys.argv[1] if len(sys.argv) > 1 else
        ("termux" if shutil.which("termux-battery-status") else "pc")).lower()
mod = importlib.import_module(f"aiden_{modo}")
agente = mod.agente

try:
    import edge_tts
except ImportError:
    try:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "--quiet", "edge-tts"])
        import edge_tts
    except Exception:
        edge_tts = None
        print(f"{core.YL}⚠ edge-tts não instalou; vou usar a voz do sistema{core.R}")

cfg = core.carregar_cfg()
TOKEN = cfg.get("voz_token") or secrets.token_hex(6)
if cfg.get("voz_token") != TOKEN:
    cfg["voz_token"] = TOKEN
    core.salvar_cfg(cfg)

rec = {"buf": bytearray(), "sr": 16000}
lock_agente = threading.Lock()
hist = [{"role": "system", "content": agente.build_sys()}] + core.carregar_mem()
proc_fala = None

# ═══════ VOZ ═══════
def limpar_fala(t):
    t = re.sub(r"https?://\S+", "link", t)
    t = re.sub(r"[*_`#>~|]", "", t)
    t = re.sub(r"[\U00010000-\U0010ffff\u2600-\u27bf\u2b00-\u2bff\ufe0f]", "", t)
    return re.sub(r"\s+", " ", t).strip()[:700]

def parar_fala():
    if shutil.which("termux-media-player"):
        subprocess.run(["termux-media-player", "stop"], capture_output=True)
    if proc_fala and proc_fala.poll() is None:
        proc_fala.terminate()

def tocar(arq):
    global proc_fala
    if shutil.which("termux-media-player"):
        subprocess.run(["termux-media-player", "play", arq], capture_output=True); return
    for cmd in (["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", arq],
                ["mpv", "--no-video", "--really-quiet", arq]):
        if shutil.which(cmd[0]):
            proc_fala = subprocess.Popen(cmd); proc_fala.wait(); return
    if hasattr(os, "startfile"):
        os.startfile(arq); return
    print("instale ffplay ou mpv pra ouvir a voz")

def falar_resposta(texto):
    texto = limpar_fala(texto)
    if not texto: return
    try:
        if edge_tts is None: raise RuntimeError("edge-tts indisponível")
        arq = str(Path(tempfile.gettempdir()) / f"aiden_voz_{int(time.time())}.mp3")
        asyncio.run(edge_tts.Communicate(texto, VOZ, rate=RITMO).save(arq))
        tocar(arq)
    except Exception as e:
        print(f"{core.YL}⚠ voz neural falhou ({e}); tentando voz do sistema{core.R}")
        f = getattr(mod, "falar", None)
        if f: f(texto)

# ═══════ PEDIDO ═══════
def _ascii(s):   # a fonte da tela não tem acento
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()

def _ultima_resposta():
    for m in reversed(hist):
        if m.get("role") == "assistant" and isinstance(m.get("content"), str) and m["content"].strip():
            return m["content"].strip()
    return "pronto"

def processar():
    global hist
    pcm = bytes(rec["buf"]); sr = rec["sr"]
    if len(pcm) < sr * 2 * 0.5:
        return {"ok": False, "erro": "audio curto"}
    b = io.BytesIO()
    with wave.open(b, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr); w.writeframes(pcm)
    try:
        r = core.cliente.audio.transcriptions.create(
            file=("fala.wav", b.getvalue()), model="whisper-large-v3-turbo",
            language="pt", temperature=0, prompt="Aiden, Jarvis, Termux, ESP32.")
    except Exception as e:
        return {"ok": False, "erro": _ascii(f"whisper: {e}")[:80]}
    pergunta = (getattr(r, "text", "") or "").strip()
    if len(pergunta) < 2:
        return {"ok": False, "erro": "nao entendi"}
    print(f"\n{core.O}🎤{core.R} {pergunta}")
    with lock_agente:
        hist = agente.rodar(pergunta, hist)
        resposta = _ultima_resposta()
    threading.Thread(target=falar_resposta, args=(resposta,), daemon=True).start()
    return {"ok": True, "pergunta": _ascii(pergunta)[:120], "resposta": _ascii(resposta)[:500]}

# ═══════ HTTP ═══════
class H(BaseHTTPRequestHandler):
    def _json(self, obj, code=200):
        d = json.dumps(obj, ensure_ascii=True).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(d)))
        self.end_headers(); self.wfile.write(d)

    def _auth(self):
        if self.headers.get("X-Aiden-Token") != TOKEN:
            self._json({"ok": False, "erro": "token"}, 401); return False
        return True

    def do_GET(self):
        if self._auth() and self.path.startswith("/ping"):
            self._json({"ok": True, "nome": "aiden", "modo": modo})

    def do_POST(self):
        if not self._auth(): return
        n = int(self.headers.get("Content-Length", 0))
        corpo = self.rfile.read(n) if n else b""
        path, _, qs = self.path.partition("?")
        if path == "/start":
            parar_fala()
            rec["buf"] = bytearray()
            m = re.search(r"sr=(\d+)", qs)
            rec["sr"] = int(m.group(1)) if m else 16000
            self._json({"ok": True})
        elif path == "/chunk":
            if len(rec["buf"]) < rec["sr"] * 2 * MAX_SEG:
                rec["buf"].extend(corpo)
            self._json({"ok": True})
        elif path == "/stop":
            self._json(processar())
        else:
            self._json({"ok": False, "erro": "rota"}, 404)

    def log_message(self, *a): pass

def meu_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1)); return s.getsockname()[0]
    except Exception: return "127.0.0.1"
    finally: s.close()

if __name__ == "__main__":
    print(f"\n{core.O}{core.B}Aiden Voz{core.R} {core.G}({modo}){core.R}")
    print(f"  {core.G}IP   {core.R} {meu_ip()}")
    print(f"  {core.G}porta{core.R} {PORTA}")
    print(f"  {core.G}token{core.R} {TOKEN}   {core.GD}(digite no portal do ESP32){core.R}")
    print(f"  {core.G}voz  {core.R} {VOZ if edge_tts else 'sistema'}\n")
    try:
        ThreadingHTTPServer(("0.0.0.0", PORTA), H).serve_forever()
    except KeyboardInterrupt:
        print(f"\n{core.G}flw{core.R}")
