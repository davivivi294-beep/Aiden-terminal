"""
Aiden Termux — assistente no Android.
Precisa do aiden_core.py na mesma pasta.

Setup no Termux:
  pkg install termux-api python
  (e instalar o app Termux:API, da mesma fonte do Termux, ex: F-Droid)
  termux-setup-storage          # pra salvar fotos na galeria
  pip install groq              # se travar no pydantic-core: pkg install rust binutils
"""

import aiden_core as core
core.instalar_se_faltar({"groq": "groq"})

import os, json, time, base64, shutil, subprocess, platform, re, io
from pathlib import Path
from aiden_core import confirmar

# termux-api são binários de linha de comando, não módulo Python
HAS_TERMUX = shutil.which("termux-battery-status") is not None
if not HAS_TERMUX:
    print("⚠ termux-api não encontrado (pkg install termux-api + app Termux:API)")

try:
    from PIL import Image
    HAS_PIL = True
except Exception:
    HAS_PIL = False

core.iniciar_cliente()

PACOTES_APPS = {
    "whatsapp":"com.whatsapp","zap":"com.whatsapp","zapzap":"com.whatsapp",
    "chrome":"com.android.chrome","instagram":"com.instagram.android",
    "insta":"com.instagram.android","youtube":"com.google.android.youtube",
    "yt":"com.google.android.youtube","gmail":"com.google.android.gm",
    "email":"com.google.android.gm","camera":"com.android.camera2",
    "câmera":"com.android.camera2","configuracoes":"com.android.settings",
    "settings":"com.android.settings","spotify":"com.spotify.music",
    "telegram":"org.telegram.messenger","facebook":"com.facebook.katana",
    "tiktok":"com.zhiliaoapp.musically","maps":"com.google.android.apps.maps",
    "drive":"com.google.android.apps.docs","fotos":"com.google.android.apps.photos",
    "calculadora":"com.android.calculator2","netflix":"com.netflix.mediaclient",
    "twitter":"com.twitter.android","x":"com.twitter.android",
    "discord":"com.discord","ifood":"br.com.brainweb.ifood",
    "nubank":"com.nu.production","uber":"com.ubercab",
}

TMP = Path.home() / ".aiden_tmp"
TMP.mkdir(exist_ok=True)

def rodar_termux(args, timeout=30):
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        if r.returncode == 0: return True, r.stdout.strip()
        return False, (r.stderr or r.stdout).strip()
    except FileNotFoundError:
        return False, f"não achei: {args[0]}"
    except subprocess.TimeoutExpired: return False, "timeout"
    except Exception as e: return False, str(e)

def _capturar(camera, destino):
    cid = "0" if camera.lower() in ("traseira", "trás", "tras", "back", "0") else "1"
    return rodar_termux(["termux-camera-photo", "-c", cid, str(destino)], timeout=45)

def tirar_foto(camera="traseira"):
    if not HAS_TERMUX: return "termux-api off"
    pasta = Path("/sdcard/DCIM/Camera")
    try: pasta.mkdir(parents=True, exist_ok=True)
    except Exception: pass
    if not pasta.exists():
        return "❌ sem acesso à galeria (rode termux-setup-storage)"
    caminho = pasta / f"aiden_{int(time.time())}.jpg"
    ok, saida = _capturar(camera, caminho)
    if not ok: return f"❌ {saida}"
    rodar_termux(["termux-media-scan", str(caminho)], timeout=10)
    return f"✅ foto salva: {caminho}"

def _preparar_jpeg(caminho):
    """Devolve bytes JPEG <= ~3MB (limite do base64 na API), ou None."""
    dados = Path(caminho).read_bytes()
    if HAS_PIL:
        img = Image.open(io.BytesIO(dados)).convert("RGB")
        if max(img.size) > 1280:
            img.thumbnail((1280, 1280))
        b = io.BytesIO(); img.save(b, format="JPEG", quality=75)
        return b.getvalue()
    return dados if len(dados) <= 3_000_000 else None

def tirar_foto_e_ver(pergunta="o que tem nessa foto", camera="traseira"):
    if not HAS_TERMUX: return "termux-api off"
    caminho = TMP / "foto.jpg"          # fica fora da galeria
    try:
        ok, saida = _capturar(camera, caminho)
        if not ok: return f"❌ {saida}"
        jpeg = _preparar_jpeg(caminho)
        if jpeg is None:
            return "❌ foto grande demais (pkg install python-pillow resolve)"
        return f"👁 {core.visao(base64.b64encode(jpeg).decode(), pergunta, 600)}"
    except Exception as e: return f"Erro visão: {e}"
    finally:
        try: caminho.unlink()
        except Exception: pass

def obter_localizacao():
    if not HAS_TERMUX: return "termux-api off"
    ok, saida = rodar_termux(["termux-location", "-p", "gps"], timeout=40)
    if not ok: ok, saida = rodar_termux(["termux-location", "-p", "network"], timeout=30)
    if not ok: return f"❌ {saida}"
    try:
        d = json.loads(saida)
        lat = d.get("latitude"); lon = d.get("longitude")
        if lat is not None: return f"✅ lat {lat}, lon {lon}"
        return saida[:200]
    except Exception: return saida[:200]

def notificar(titulo, mensagem):
    ok, saida = rodar_termux(["termux-notification", "-t", titulo, "-c", mensagem], timeout=10)
    return "✅ notificação enviada" if ok else f"❌ {saida}"

def ler_clipboard():
    ok, saida = rodar_termux(["termux-clipboard-get"], timeout=10)
    return saida if ok and saida else "(vazio)"

def escrever_clipboard(texto):
    ok, saida = rodar_termux(["termux-clipboard-set", texto], timeout=10)
    return "✅ copiado" if ok else f"❌ {saida}"

def falar(texto):
    texto = texto[:500]                 # TTS bloqueia até acabar
    ok, saida = rodar_termux(["termux-tts-speak", texto], timeout=90)
    return f"✅ falando: {texto[:50]}" if ok else f"❌ {saida}"

def vibrar(duracao=500):
    d = max(50, min(int(duracao), 3000))
    ok, saida = rodar_termux(["termux-vibrate", "-d", str(d)], timeout=10)
    return "✅ vibrou" if ok else f"❌ {saida}"

_NUM = re.compile(r"^\+?\d{8,15}$")

def enviar_sms(numero, mensagem):
    numero = re.sub(r"[\s\-()]", "", str(numero))
    if not _NUM.match(numero): return "❌ número inválido"
    if len(mensagem) > 480: return "❌ mensagem longa demais"
    ok, saida = rodar_termux(["termux-sms-send", "-n", numero, mensagem], timeout=30)
    return "✅ SMS enviado" if ok else f"❌ {saida}"

def listar_contatos():
    ok, saida = rodar_termux(["termux-contact-list"], timeout=15)
    if not ok: return f"❌ {saida}"
    try:
        contatos = json.loads(saida)
        nomes = [c.get("name", "?") for c in contatos[:30]]
        return "✅ " + ", ".join(nomes) + ("..." if len(contatos) > 30 else "")
    except Exception as e: return f"Erro: {e}"

def status_bateria():
    ok, saida = rodar_termux(["termux-battery-status"], timeout=10)
    if not ok: return f"❌ {saida}"
    try:
        d = json.loads(saida)
        return f"✅ {d.get('percentage')}% ({d.get('status')})"
    except Exception: return saida[:100]

def info_wifi():
    ok, saida = rodar_termux(["termux-wifi-connectioninfo"], timeout=10)
    if not ok: return f"❌ {saida}"
    try:
        d = json.loads(saida)
        return f"✅ {d.get('ssid')} | ip {d.get('ip')}"
    except Exception: return saida[:200]

def _pacotes():
    ok, saida = rodar_termux(["pm", "list", "packages"], timeout=10)
    if not ok: return []
    return [l.replace("package:", "").strip() for l in saida.splitlines() if l.strip()]

def abrir_app(nome):
    chave = nome.strip().lower()
    pacote = PACOTES_APPS.get(chave, nome.strip())
    if "." not in pacote:
        todos = _pacotes()
        exato = [p for p in todos if p.lower().endswith("." + chave)]
        parcial = [p for p in todos if len(chave) >= 4 and chave in p.lower()]
        achados = exato or parcial
        if achados: pacote = sorted(achados, key=len)[0]
    ok, saida = rodar_termux(["am", "start", "--user", "0",
                              "-a", "android.intent.action.MAIN",
                              "-c", "android.intent.category.LAUNCHER",
                              "-p", pacote], timeout=15)
    if ok and "error" not in saida.lower(): return f"✅ abrindo {nome}"
    ok, saida = rodar_termux(["monkey", "-p", pacote,
                              "-c", "android.intent.category.LAUNCHER", "1"], timeout=15)
    if ok and "Events injected" in saida: return f"✅ abrindo {nome}"
    return f"❌ não consegui abrir '{nome}' ({pacote})"

def abrir_url(url):
    url = url.strip()
    if not url.startswith(("http://", "https://")): url = "https://" + url
    ok, saida = rodar_termux(["termux-open-url", url], timeout=10)
    return f"✅ {url}" if ok else f"❌ {saida}"

def listar_apps():
    todos = _pacotes()
    if not todos: return "❌ sem acesso à lista de apps neste aparelho"
    ok, saida = rodar_termux(["pm", "list", "packages", "-3"], timeout=10)
    pacotes = [l.replace("package:", "").strip() for l in saida.splitlines() if l.strip()]
    filtrados = [p for p in pacotes
                 if not p.startswith(("com.android.", "com.google.android.gms"))]
    return "✅ " + ", ".join(sorted(filtrados)[:40])

def info_sistema():
    return (f"sistema: Termux/Android\narquitetura: {platform.machine()}\n"
            f"python: {platform.python_version()}\ntermux-api: {HAS_TERMUX}")

def _t(nome, desc, props=None, req=()):
    return {"type": "function", "function": {"name": nome, "description": desc,
            "parameters": {"type": "object", "properties": props or {},
                           "required": list(req)}}}
_s = {"type": "string"}; _i = {"type": "integer"}
_cam = {"type": "string", "enum": ["traseira", "frontal"]}

TOOLS = [
    _t("abrir_app", "Abre app (whatsapp, chrome, instagram, etc).", {"nome": _s}, ["nome"]),
    _t("abrir_url", "Abre um link no navegador.", {"url": _s}, ["url"]),
    _t("listar_apps", "Lista apps instalados."),
    _t("tirar_foto", "Tira foto e salva na galeria.", {"camera": _cam}),
    _t("tirar_foto_e_ver", "Tira foto e analisa com visão.", {"pergunta": _s, "camera": _cam}),
    _t("obter_localizacao", "Retorna lat/lon do GPS."),
    _t("notificar", "Envia notificação.", {"titulo": _s, "mensagem": _s}, ["titulo", "mensagem"]),
    _t("ler_clipboard", "Lê área de transferência."),
    _t("escrever_clipboard", "Copia texto.", {"texto": _s}, ["texto"]),
    _t("falar", "Fala texto em voz alta.", {"texto": _s}, ["texto"]),
    _t("vibrar", "Faz o celular vibrar.", {"duracao": _i}),
    _t("enviar_sms", "Envia SMS.", {"numero": _s, "mensagem": _s}, ["numero", "mensagem"]),
    _t("listar_contatos", "Lista contatos."),
    _t("status_bateria", "Nível da bateria."),
    _t("info_wifi", "Info do WiFi."),
    _t("info_sistema", "Info do sistema."),
]

FUNCS = {f.__name__: f for f in (
    abrir_app, abrir_url, listar_apps, tirar_foto, tirar_foto_e_ver,
    obter_localizacao, notificar, ler_clipboard, escrever_clipboard, falar,
    vibrar, enviar_sms, listar_contatos, status_bateria, info_wifi, info_sistema)}

agente = core.Agente(
    persona="Você é o Aiden, assistente no celular Android via Termux. Fala como parceiro brasileiro, informal e direto.",
    tools=TOOLS, funcs=FUNCS,
    prompt_extra=("Você controla o CELULAR. Ferramentas de dados pessoais "
                  "(contatos, localização, clipboard, foto, SMS) pedem confirmação do usuário; "
                  "se ele negar, aceite e não insista.\n"
                  f"Sistema: Termux/Android | termux-api: {HAS_TERMUX}"),
    banner_linhas=[f"{core.G}sistema{core.R} Termux/Android  {core.GD}·{core.R}  "
                   f"{core.G}hardware{core.R} {'ok' if HAS_TERMUX else 'off'}"],
    # SMS sempre confirma, até em modo autônomo
    sempre_confirmar={"enviar_sms"},
    sensiveis={"ler_clipboard", "listar_contatos", "obter_localizacao",
               "tirar_foto", "tirar_foto_e_ver"},
    nao_persistir={"ler_clipboard", "listar_contatos", "obter_localizacao",
                   "tirar_foto_e_ver"},
    comandos={"/apps": ("lista apps instalados", listar_apps)},
)

if __name__ == "__main__":
    agente.main()
