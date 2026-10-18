"""
Aiden PC — agente de terminal que controla o computador.
Precisa do aiden_core.py e aiden_auth.py na mesma pasta.
"""

import aiden_core as core
core.instalar_se_faltar({"groq": "groq", "pyautogui": "pyautogui", "PIL": "pillow", "pyperclip": "pyperclip"})

import os, io, re, sys, json, time, base64, shutil, platform, difflib
import subprocess as sp
import webbrowser
from pathlib import Path
from urllib.parse import quote
from aiden_core import confirmar

try:
    import pyautogui
    pyautogui.FAILSAFE = True; pyautogui.PAUSE = 0.3
    HAS_GUI = True
except Exception: HAS_GUI = False
try:
    from PIL import Image
    HAS_PIL = True
except Exception: HAS_PIL = False
try:
    import pyperclip
    HAS_CLIP = True
except Exception: HAS_CLIP = False

core.iniciar_cliente()

SO = platform.system()
WORKDIR = Path.home() / "aiden_workspace"
WORKDIR.mkdir(exist_ok=True)
SCRIPT_DIR = Path(__file__).parent.resolve()
PASTA = {"atual": None}

_PROIBIDOS = (".aiden_config", ".ssh", ".gnupg", ".env", "id_rsa", "id_ed25519", ".aws", ".netrc")
_IGNORAR = {".git", "node_modules", "venv", ".venv", "__pycache__", ".idea", ".gradle", "build", "dist"}
_PERIGO = [r"\brm\s+(-[\w-]+\s+)+(/|~|\$home|\*)(\s|$)", r"\bmkfs", r"\bdd\s+.*of=/dev/", r":\(\)\s*\{", r">\s*/dev/sd", r"\bformat\s+[a-z]:", r"\bdel\s+.*[a-z]:\\", r"\brmdir\s+/s", r"\bshutdown\b", r"\bchmod\s+-r\s+777\s+/"]

def _seguro(p):
    try: f = Path(os.path.expanduser(str(p))).resolve()
    except Exception: return None
    if any(x in str(f) for x in _PROIBIDOS): return None
    return f

def _base(pasta=""):
    if pasta and pasta != ".": return _seguro(pasta)
    if PASTA["atual"]: return Path(PASTA["atual"])
    return Path.cwd()

def definir_pasta(caminho=""):
    caminho = (caminho or "").strip().strip('"')
    if not caminho: return f"pasta do projeto: {PASTA['atual'] or '(nenhuma)'}  ·  use /pasta CAMINHO  (ou /pasta - pra limpar)"
    if caminho == "-": PASTA["atual"] = None; return "pasta do projeto limpa"
    f = _seguro(caminho)
    if not f or not f.is_dir(): return "❌ pasta inválida ou protegida"
    PASTA["atual"] = str(f); return f"✅ pasta do projeto: {f}"

def _fora_do_workspace(f): return WORKDIR.resolve() not in f.parents and f != WORKDIR.resolve()
def _ignorado(p, base):
    try: partes = p.relative_to(base).parts
    except ValueError: partes = p.parts
    return any(x in _IGNORAR for x in partes) or any(x in str(p) for x in _PROIBIDOS)

# ═══════ ARQUIVOS E PASTAS ═══════
def exec_comando(cmd, timeout=60):
    c = re.sub(r"\s+", " ", cmd.lower())
    if any(re.search(p, c) for p in _PERIGO): return "🚫 bloqueado"
    base = _base()
    if not confirmar(f"rodar {cmd}" + (f"  (em {base})" if PASTA["atual"] else "")): return "cancelado"
    t = max(5, min(int(timeout or 60), 300))
    try:
        r = sp.run(cmd, shell=True, capture_output=True, text=True, timeout=t, cwd=str(base), encoding="utf-8", errors="replace")
        return ((r.stdout or "") + (r.stderr or "")).strip() or f"(cód {r.returncode})"
    except sp.TimeoutExpired: return f"⏱ timeout ({t}s)"
    except Exception as e: return f"Erro: {e}"

def ler_arq(p, offset=0, limit=2000):
    f = _seguro(p)
    if not f: return "🚫 arquivo protegido"
    if not f.is_file(): return f"não existe: {p}"
    try:
        linhas = f.read_text(encoding="utf-8", errors="replace").splitlines()
        total = len(linhas); ini = max(0, int(offset or 0)); fim = min(total, ini + int(limit or 2000))
        out = [f"{i+1:>5}  {linhas[i]}" for i in range(ini, fim)]
        if fim < total: out.append(f"... ({total - fim} restantes, use offset={fim})")
        return "\n".join(out) or "(vazio)"
    except Exception as e: return f"Erro: {e}"

def escrever_arq(p, c, sobrescrever=False):
    f = Path(os.path.expanduser(str(p)))
    if not f.is_absolute(): f = WORKDIR / f
    f = f.resolve()
    if any(x in str(f) for x in _PROIBIDOS): return "🚫 arquivo protegido"
    if f.exists() and not sobrescrever: return "❌ arquivo existe — use edit_arquivo"
    if f.exists():
        if not confirmar(f"SOBRESCREVER {f}", forcar=True): return "cancelado"
    elif _fora_do_workspace(f):
        if not confirmar(f"criar {f} (fora do workspace)", forcar=True): return "cancelado"
    try:
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(c, encoding="utf-8")
        return f"✅ {'sobrescrito' if sobrescrever else 'criado'}: {f} ({len(c)} chars)"
    except Exception as e: return f"Erro: {e}"

def _pedir_edicao(f, antes, depois, extra=""):
    diff = "\n".join(list(difflib.unified_diff(antes.splitlines(), depois.splitlines(), "antes", "depois", lineterm="", n=1))[:14])
    return confirmar(f"editar {f}{extra}\n{diff}", forcar=_fora_do_workspace(f))

def edit_arquivo(p, old_string, new_string, replace_all=False):
    f = _seguro(p)
    if not f: return "🚫 arquivo protegido"
    if not f.is_file(): return f"não existe: {p}"
    try:
        c = f.read_text(encoding="utf-8")
        n = c.count(old_string)
        if not old_string: return "❌ old_string vazia"
        if n == 0: return "❌ old_string não encontrada (confira espaços e quebras de linha)"
        if n > 1 and not replace_all: return f"❌ old_string aparece {n} vezes — seja mais específico ou use replace_all"
        novo = c.replace(old_string, new_string) if replace_all else c.replace(old_string, new_string, 1)
        if not _pedir_edicao(f, c, novo): return "cancelado"
        f.with_suffix(f.suffix + ".bak").write_text(c, encoding="utf-8")
        f.write_text(novo, encoding="utf-8")
        return f"✅ editado: {f} ({n if replace_all else 1} troca, backup .bak)"
    except Exception as e: return f"Erro: {e}"

def multi_edit(p, edits):
    f = _seguro(p)
    if not f: return "🚫 arquivo protegido"
    if not f.is_file(): return f"não existe: {p}"
    if not isinstance(edits, list) or not edits: return "❌ edits precisa ser uma lista não vazia"
    try:
        original = c = f.read_text(encoding="utf-8")
        for i, ed in enumerate(edits, 1):
            if not isinstance(ed, dict): return f"❌ edit {i}: formato inválido"
            old = ed.get("old_string", ""); new = ed.get("new_string", ""); ra = bool(ed.get("replace_all"))
            if not old: return f"❌ edit {i}: old_string vazia"
            n = c.count(old)
            if n == 0: return f"❌ edit {i}: old_string não encontrada (nada foi alterado)"
            if n > 1 and not ra: return f"❌ edit {i}: old_string aparece {n} vezes (nada foi alterado)"
            c = c.replace(old, new) if ra else c.replace(old, new, 1)
        if not _pedir_edicao(f, original, c, f" ({len(edits)} edições)"): return "cancelado"
        f.with_suffix(f.suffix + ".bak").write_text(original, encoding="utf-8")
        f.write_text(c, encoding="utf-8")
        return f"✅ {len(edits)} edições aplicadas em {f}"
    except Exception as e: return f"Erro: {e}"

def glob(padrao, base=""):
    try:
        b = _base(base)
        if not b or not b.is_dir(): return f"base não existe ou é protegida: {base or '(atual)'}"
        achados = []
        for p in b.glob(padrao):
            if _ignorado(p, b): continue
            achados.append(str(p.relative_to(b)).replace("\\", "/"))
            if len(achados) >= 100: break
        return "\n".join(sorted(achados)) if achados else "(nenhum)"
    except Exception as e: return f"Erro: {e}"

def grep(padrao, base="", ext=None, max_res=50):
    try:
        b = _base(base)
        if not b or not b.exists(): return f"base não existe ou é protegida: {base or '(atual)'}"
        try: rx = re.compile(padrao, re.I)
        except re.error as e: return f"regex inválido: {e}"
        arquivos = [b] if b.is_file() else b.rglob("*")
        achados = []
        for p in arquivos:
            if not p.is_file() or _ignorado(p, b if b.is_dir() else p.parent): continue
            if ext and not p.name.endswith(ext): continue
            try:
                if p.stat().st_size > 2_000_000: continue
                with open(p, "rb") as fb:
                    if b"\x00" in fb.read(2048): continue
                texto = p.read_text(encoding="utf-8", errors="ignore")
            except Exception: continue
            for i, l in enumerate(texto.splitlines(), 1):
                if rx.search(l):
                    nome = p.name if b.is_file() else str(p.relative_to(b))
                    achados.append(f"{nome}:{i}:{l[:150]}")
                    if len(achados) >= int(max_res or 50): break
            if len(achados) >= int(max_res or 50): break
        return "\n".join(achados) if achados else "(nada)"
    except Exception as e: return f"Erro: {e}"

def listar_dir(p=""):
    try:
        d = _base(p)
        if not d or not d.is_dir(): return f"pasta não existe ou é protegida: {p or '(atual)'}"
        return "\n".join(f"{'📁' if i.is_dir() else '📄'} {i.name}" for i in sorted(d.iterdir())) or "(vazio)"
    except Exception as e: return f"Erro: {e}"

# ═══════ APPS, NAVEGADOR, TELA ═══════
_NOME_OK = re.compile(r"^[\w .+()\-]+$", re.UNICODE)

def abrir_app(nome):
    nome = nome.strip()
    if not nome: return "❌ nome vazio"
    if not _NOME_OK.match(nome): return "❌ caractere inválido"
    enc = shutil.which(nome)
    try:
        if SO == "Windows":
            if enc: sp.Popen([nome]); time.sleep(1); return f"✅ {nome}"
            r = sp.run(["cmd", "/c", "start", "", nome], capture_output=True, text=True, timeout=5)
            if r.returncode != 0: return f"❌ '{nome}' não achei"
            time.sleep(1); return f"✅ {nome}"
        if SO == "Darwin": sp.Popen(["open", "-a", nome]); time.sleep(1); return f"✅ {nome}"
        if not enc: return f"❌ '{nome}' não instalado"
        sp.Popen([nome], stdout=sp.DEVNULL, stderr=sp.DEVNULL); time.sleep(1)
        return f"✅ {nome}"
    except Exception as e: return f"❌ {e}"

def procurar_instalar(nome, fonte="auto"):
    nome = nome.strip()
    if not nome: return "❌ nome vazio"
    try:
        if SO == "Windows" and fonte in ("auto", "loja"):
            os.startfile(f"ms-windows-store://search/?query={quote(nome)}")
            return f"✅ Store aberta: {nome}"
        webbrowser.open("https://google.com/search?q=" + quote(f"como instalar {nome} no {SO}"))
        return f"✅ Google: {nome}"
    except Exception as e: return f"Erro: {e}"

def abrir_nav(url):
    try:
        url = url.strip()
        if not url.startswith(("http://", "https://")): url = "https://" + url
        webbrowser.open(url); return f"✅ {url}"
    except Exception as e: return f"Erro: {e}"

def teclas(t):
    if not HAS_GUI: return "pyautogui off"
    try:
        if "+" in t and len(t.split("+")) <= 4: pyautogui.hotkey(*[p.strip() for p in t.split("+")])
        else: pyautogui.write(t, interval=.02)
        return f"✅ {t}"
    except Exception as e: return f"Erro: {e}"

def colar(texto):
    if not (HAS_GUI and HAS_CLIP): return "indisponível"
    try:
        pyperclip.copy(texto); time.sleep(.2)
        pyautogui.hotkey("command" if SO == "Darwin" else "ctrl", "v")
        return f"✅ colado ({len(texto)} chars)"
    except Exception as e: return f"Erro: {e}"

def clicar(x, y, clique="esquerdo"):
    if not HAS_GUI: return "pyautogui off"
    try:
        pyautogui.moveTo(int(x), int(y), duration=.3)
        if clique == "duplo": pyautogui.doubleClick()
        else: pyautogui.click(button={"direito": "right"}.get(clique, "left"))
        return f"✅ ({x},{y})"
    except Exception as e: return f"Erro: {e}"

def screenshot(caminho="screenshot.png"):
    if not HAS_GUI: return "pyautogui off"
    try:
        p = Path(caminho)
        if not p.is_absolute(): p = WORKDIR / p
        i = pyautogui.screenshot(); i.save(p)
        return f"✅ {p} ({i.size[0]}x{i.size[1]})"
    except Exception as e: return f"Erro: {e}"

def olhar_tela(pergunta="o que tem na tela"):
    if not HAS_GUI: return "pyautogui off"
    if not HAS_PIL: return "Pillow off"
    try:
        img = pyautogui.screenshot()
        if img.width > 1280: img = img.resize((1280, int(img.height * (1280 / img.width))))
        b = io.BytesIO(); img.convert("RGB").save(b, format="JPEG", quality=70)
        return f"👁 {core.visao(base64.b64encode(b.getvalue()).decode(), pergunta)}"
    except Exception as e: return f"Erro visão: {e}"

def info_sys():
    return (f"SO: {SO} {platform.release()}\nuser: {os.getenv('USER') or os.getenv('USERNAME')}\ncwd: {os.getcwd()}\nworkspace: {WORKDIR}\ngui: {HAS_GUI} | visão: {HAS_PIL}")

# ═══════ INTELIGÊNCIA DE CÓDIGO ═══════
_MARCADORES = {"package.json": "Node/JS", "pyproject.toml": "Python", "requirements.txt": "Python", "setup.py": "Python", "Cargo.toml": "Rust", "go.mod": "Go", "pom.xml": "Java/Maven", "build.gradle": "Java/Android (Gradle)", "platformio.ini": "PlatformIO", "CMakeLists.txt": "C/C++ (CMake)", "Makefile": "Make", "Dockerfile": "Docker", ".git": "repositório git"}

def mapa_projeto(pasta="", profundidade=3):
    b = _base(pasta)
    if not b or not b.is_dir(): return "❌ pasta inválida ou protegida"
    prof = max(1, min(int(profundidade or 3), 6))
    tipos = sorted({v for k, v in _MARCADORES.items() if (b / k).exists()})
    linhas = [f"{b.name or b}  —  {', '.join(tipos) or 'tipo não identificado'}"]
    total = [0]
    def rec(d, nivel, pref):
        try: itens = sorted(d.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower()))
        except Exception: return
        for it in itens:
            if _ignorado(it, b): continue
            if total[0] >= 200: linhas.append(pref + "..."); return
            total[0] += 1
            if it.is_dir():
                linhas.append(f"{pref}📁 {it.name}/")
                if nivel < prof: rec(it, nivel + 1, pref + "  ")
            else:
                try: kb = it.stat().st_size / 1024
                except Exception: kb = 0
                linhas.append(f"{pref}📄 {it.name}" + (f" ({kb:.0f} KB)" if kb >= 1 else ""))
    rec(b, 1, "")
    return "\n".join(linhas)

_GIT = {"status": ["status", "-sb"], "diff": ["diff", "--stat"], "diff_completo": ["diff"], "staged": ["diff", "--cached"], "log": ["log", "--oneline", "-n", "15"], "branch": ["branch", "-vv"]}

def git_info(acao="status", pasta=""):
    git = shutil.which("git")
    if not git: return "❌ git não instalado"
    if acao not in _GIT: return f"❌ ação inválida. Use: {', '.join(_GIT)}"
    b = _base(pasta)
    if not b or not b.is_dir(): return "❌ pasta inválida ou protegida"
    try:
        r = sp.run([git, "-C", str(b), *_GIT[acao]], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=20)
    except Exception as e: return f"Erro: {e}"
    return ((r.stdout or "") + (r.stderr or "")).strip()[:6000] or "(sem saída)"

def verificar_sintaxe(p):
    f = _seguro(p)
    if not f or not f.is_file(): return f"não existe ou é protegido: {p}"
    texto = f.read_text(encoding="utf-8", errors="replace")
    try:
        if f.suffix == ".py": compile(texto, str(f), "exec"); return "✅ sintaxe Python ok"
        if f.suffix == ".json": json.loads(texto); return "✅ JSON válido"
    except SyntaxError as e: return f"❌ linha {e.lineno}: {e.msg}\n    {(e.text or '').strip()}"
    except json.JSONDecodeError as e: return f"❌ JSON linha {e.lineno}: {e.msg}"
    if f.suffix in (".js", ".mjs") and shutil.which("node"):
        r = sp.run([shutil.which("node"), "--check", str(f)], capture_output=True, text=True, timeout=20)
        return "✅ sintaxe JS ok" if r.returncode == 0 else f"❌ {(r.stderr or '').strip()[:500]}"
    return f"(sem verificador pra {f.suffix or 'esse tipo de arquivo'})"

# ═══════ SUBAGENTE E CLAUDE CODE ═══════
_LEITURA = ("ler_arq", "glob", "grep", "listar_dir", "info_sys", "mapa_projeto", "git_info", "verificar_sintaxe")

def task(descricao, prompt, subagent_type="general-purpose"):
    core.saida.info(f"  ↳ subagente [{subagent_type}]: {descricao[:60]}")
    nomes = set(_LEITURA)
    if subagent_type == "general-purpose": nomes.add("exec_comando")
    sub = core.Agente(
        persona=f"Você é um subagente do Aiden (tipo: {subagent_type}). Faz só a tarefa pedida e devolve um resumo curto e objetivo.",
        tools=[t for t in TOOLS if t["function"]["name"] in nomes], funcs=FUNCS,
        nao_persistir={"ler_arq", "exec_comando"}, read_only=set(_LEITURA))
    return f"✅ [{descricao[:40]}] {sub.rodar_isolado(prompt, max_passos=6)[:1500]}"

def claude_code(tarefa, editar=False, pasta=""):
    exe = shutil.which("claude")
    if not exe: return "❌ não achei o comando 'claude'"
    base = _base(pasta) if (pasta or PASTA["atual"]) else WORKDIR
    if not base or not base.is_dir(): return "❌ pasta inválida ou protegida"
    modo = "EDITAR arquivos e rodar comandos" if editar else "só LER e analisar (não altera nada)"
    if not confirmar(f"Claude Code em {base} ({modo})\n    tarefa: {tarefa[:300]}", forcar=True): return "cancelado"
    cmd = [exe, "-p", "--output-format", "json"]
    if editar: cmd += ["--permission-mode", "acceptEdits", "--allowedTools", "Read,Edit,Write,Bash,Grep,Glob"]
    else: cmd += ["--allowedTools", "Read,Grep,Glob"]
    try:
        r = sp.run(cmd, input=tarefa, capture_output=True, text=True, encoding="utf-8", timeout=600, cwd=str(base))
    except sp.TimeoutExpired: return "⏱ timeout (10 min)"
    except Exception as e: return f"Erro: {e}"
    saida = (r.stdout or "").strip()
    try: texto = json.loads(saida).get("result") or saida
    except Exception: texto = saida or (r.stderr or "").strip()
    return texto[:4000] or f"(cód {r.returncode})"

# ═══════ INTERFACES ═══════
def _abrir_script(nome_arquivo, console):
    arq = SCRIPT_DIR / nome_arquivo
    if not arq.exists(): return f"❌ {nome_arquivo} não encontrado na mesma pasta"
    try:
        py = sys.executable
        kw = {"cwd": str(SCRIPT_DIR)}
        if SO == "Windows":
            if console: kw["creationflags"] = sp.CREATE_NEW_CONSOLE
            else:
                pyw = Path(py).with_name("pythonw.exe")
                if pyw.exists(): py = str(pyw)
        sp.Popen([py, "-u", str(arq)], **kw)
        return f"✅ {nome_arquivo} abrindo em outra janela..."
    except Exception as e: return f"❌ erro: {e}"

def abrir_gui(): return _abrir_script("aiden_gui.py", console=False)
def abrir_tui(): return _abrir_script("aiden_tui.py", console=True)
def abrir_arc(): return _abrir_script("aiden_arc.py", console=False)
def abrir_stark(): return _abrir_script("aiden_stark.py", console=False)

# ═══════ DEFINIÇÕES DE FERRAMENTAS ═══════
def _t(nome, desc, props=None, req=()):
    return {"type": "function", "function": {"name": nome, "description": desc, "parameters": {"type": "object", "properties": props or {}, "required": list(req)}}}
_s = {"type": "string"}; _i = {"type": "integer"}; _b = {"type": "boolean"}

TOOLS = [
    _t("exec_comando", "Roda comando no terminal. timeout em segundos (máx 300).", {"cmd": _s, "timeout": _i}, ["cmd"]),
    _t("ler_arq", "Lê arquivo com números de linha. Use offset/limit.", {"p": _s, "offset": _i, "limit": _i}, ["p"]),
    _t("escrever_arq", "Cria arquivo NOVO. Falha se existe; use edit_arquivo.", {"p": _s, "c": _s, "sobrescrever": _b}, ["p", "c"]),
    _t("edit_arquivo", "Substitui old_string por new_string (precisa ser única). Mostra diff e faz backup .bak.", {"p": _s, "old_string": _s, "new_string": _s, "replace_all": _b}, ["p", "old_string", "new_string"]),
    _t("multi_edit", "Várias edições num arquivo, tudo ou nada.", {"p": _s, "edits": {"type": "array", "items": {"type": "object"}}}, ["p", "edits"]),
    _t("mapa_projeto", "Mostra a árvore do projeto e o tipo.", {"pasta": _s, "profundidade": _i}),
    _t("git_info", "Git somente leitura: status, diff, diff_completo, staged, log, branch.", {"acao": {"type": "string", "enum": list(_GIT)}, "pasta": _s}),
    _t("verificar_sintaxe", "Confere a sintaxe de um arquivo (.py, .json, .js).", {"p": _s}, ["p"]),
    _t("glob", "Encontra arquivos por padrão. Ex: '**/*.py'.", {"padrao": _s, "base": _s}, ["padrao"]),
    _t("grep", "Busca regex em arquivos.", {"padrao": _s, "base": _s, "ext": _s, "max_res": _i}, ["padrao"]),
    _t("listar_dir", "Lista pasta.", {"p": _s}),
    _t("abrir_app", "Abre app instalado.", {"nome": _s}, ["nome"]),
    _t("procurar_instalar", "Abre Store/Google pra instalar.", {"nome": _s, "fonte": {"type": "string", "enum": ["auto", "loja", "google"]}}, ["nome"]),
    _t("abrir_nav", "Abre URL.", {"url": _s}, ["url"]),
    _t("teclas", "Envia teclas (ctrl+c).", {"t": _s}, ["t"]),
    _t("colar", "Cola texto.", {"texto": _s}, ["texto"]),
    _t("clicar", "Clica em (x,y).", {"x": _i, "y": _i, "clique": {"type": "string", "enum": ["esquerdo", "direito", "duplo"]}}, ["x", "y"]),
    _t("screenshot", "Salva screenshot.", {"caminho": _s}),
    _t("olhar_tela", "Vê a tela.", {"pergunta": _s}),
    _t("info_sys", "Info do PC."),
    _t("task", "Cria um subagente isolado pra explorar ou fazer uma tarefa grande.", {"descricao": _s, "prompt": _s, "subagent_type": {"type": "string", "enum": ["general-purpose", "Explore", "Plan"]}}, ["descricao", "prompt"]),
    _t("claude_code", "Entrega uma tarefa pro Claude Code.", {"tarefa": _s, "editar": _b, "pasta": _s}, ["tarefa"]),
]

FUNCS = {f.__name__: f for f in (
    exec_comando, ler_arq, escrever_arq, edit_arquivo, multi_edit, glob, grep, listar_dir,
    abrir_app, procurar_instalar, abrir_nav, teclas, colar, clicar, screenshot, olhar_tela,
    info_sys, task, claude_code, mapa_projeto, git_info, verificar_sintaxe)}

DEV_FERRAMENTAS = {"exec_comando", "ler_arq", "escrever_arq", "edit_arquivo", "multi_edit", "glob", "grep", "listar_dir", "mapa_projeto", "git_info", "verificar_sintaxe", "task", "claude_code", "info_sys"}

def _extra():
    return (f"Sistema: {SO} | cwd: {os.getcwd()} | workspace: {WORKDIR} | pasta do projeto: {PASTA['atual'] or '(nenhuma)'}")

agente = core.Agente(
    persona="Você é o Aiden, agente que controla o PC. Fala como parceiro brasileiro, informal e direto. "
            "Suas ferramentas funcionam como as do Claude Code: ler_arq, escrever_arq, edit_arquivo, multi_edit, glob, grep, exec_comando, task e todo_write. "
            "Prefira edit_arquivo a escrever_arq quando o arquivo já existe, e glob/grep a find/grep no terminal. "
            "Leia o arquivo antes de editar.",
    tools=TOOLS, funcs=FUNCS,
    prompt_extra=_extra,
    banner_linhas=[
        f"{core.G}sistema{core.R} {SO}  {core.GD}·{core.R}  "
        f"{core.G}gui{core.R} {'ok' if HAS_GUI else 'off'}  {core.GD}·{core.R}  "
        f"{core.G}visão{core.R} {'ok' if HAS_PIL else 'off'}"],
    sensiveis={"teclas", "colar", "clicar"},
    read_only=set(_LEITURA), dev_tools=DEV_FERRAMENTAS,
    nao_persistir={"ler_arq", "olhar_tela", "exec_comando", "grep", "claude_code"},
    comandos={
        "/gui": ("abre a interface gráfica", abrir_gui),
        "/tui": ("abre a interface de terminal", abrir_tui),
        "/arc": ("abre o HUD azul (ARC)", abrir_arc),
        "/stark": ("abre o HUD STARK", abrir_stark),
        "/login": ("faz login de novo (troca de conta)", core.trocar_chave),
        "/logout": ("sai da conta e apaga a sessão local", core.fazer_logout),
        "/pasta": ("define a pasta do projeto", definir_pasta),
    },
)

if __name__ == "__main__":
    agente.main()