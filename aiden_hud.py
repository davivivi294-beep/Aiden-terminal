"""
Aiden HUD — painel futurista (estilo Jarvis) pro Aiden.

  • Reator central animado que muda com o estado: online, pensando, ouvindo, falando, erro
  • Anéis de sistema (CPU, RAM/SWAP, bateria, disco), relógio, calendário do mês e gráfico de rede
  • Clima de verdade (Open-Meteo, sem chave): /cidade NOME
  • Console de conversa com o Aiden, confirmações em janela HUD e lista de tarefas
  • Timers (peça "timer de 10 minutos" ou /timer 10), lançador de apps, 3 temas
  • Voz: o Aiden fala (voz neural edge-tts) e você fala de volta pelo microfone (botão MIC)

Roda: python aiden_hud.py
Atalhos: F11 tela cheia · Esc sai da tela cheia · Ctrl+L limpa · Ctrl+P plan mode · Ctrl+M microfone
"""

import sys, os, io, re, json, math, time, wave, queue, random, shutil, asyncio, calendar
import datetime, tempfile, threading, subprocess
import urllib.parse, urllib.request
from array import array
from pathlib import Path
import tkinter as tk
from tkinter import font as tkfont

sys.path.insert(0, str(Path(__file__).parent))
import aiden_core as core
core.INTERATIVO = False        # nunca chama input()
core.set_plain()               # sem códigos de cor
try:
    import aiden_pc
except Exception as e:
    print(f"erro importando aiden_pc: {e}"); sys.exit(1)

CFG_FILE = Path.home() / ".aiden_hud.json"
CFG_PADRAO = {"tema": "ciano", "cidade": "", "voz": True, "apps": ["chrome", "code", "explorer", "spotify"]}

TEMAS = {
    "ciano":   dict(nome="CIANO",   br="#19f0f0", md="#0fa3ad", dm="#0a5560", es="#06262c", bg="#02080a", vm="#e03a3a", tx="#c8ffff"),
    "azul":    dict(nome="AZUL",    br="#5bc8ff", md="#2f86c4", dm="#1c4f7a", es="#0b2236", bg="#02060c", vm="#e8604a", tx="#d4efff"),
    "laranja": dict(nome="LARANJA", br="#ff9a6a", md="#c0643f", dm="#7a3f28", es="#2e170e", bg="#0a0504", vm="#ff3b3b", tx="#ffe2d4"),
}

# ═══════ UTILIDADES ═══════
def _rgb(h):
    h = h.lstrip("#"); return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))

def mix(a, b, f):
    ra, rb = _rgb(a), _rgb(b)
    return "#%02x%02x%02x" % tuple(int(ra[i] + (rb[i] - ra[i]) * f) for i in range(3))

def carregar_cfg():
    try:
        d = json.loads(CFG_FILE.read_text(encoding="utf-8"))
        return {**CFG_PADRAO, **d}
    except Exception:
        return dict(CFG_PADRAO)

def fmt_bytes(v):
    for u in ("B", "kB", "MB", "GB"):
        if v < 1024 or u == "GB": return f"{v:.0f} {u}/s" if u == "B" else f"{v:.1f} {u}/s"
        v /= 1024

def _importar(nome, pip):
    try: return __import__(nome)
    except Exception: pass               # ImportError, ou OSError se faltar a biblioteca de áudio
    try:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "--quiet", pip],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return __import__(nome)
    except Exception:
        return None

# módulos opcionais, carregados em segundo plano pra janela abrir na hora
OPC = {"psutil": None, "sd": None, "edge": None}
def _carregar_opcionais():
    OPC["psutil"] = _importar("psutil", "psutil")
    OPC["edge"] = _importar("edge_tts", "edge-tts")
    OPC["sd"] = _importar("sounddevice", "sounddevice")

# ═══════ CLIMA (Open-Meteo, sem chave) ═══════
COND = {0: "Céu limpo", 1: "Quase limpo", 2: "Parcialmente nublado", 3: "Nublado", 45: "Nevoeiro", 48: "Nevoeiro",
        51: "Garoa fraca", 53: "Garoa", 55: "Garoa forte", 56: "Garoa gelada", 57: "Garoa gelada",
        61: "Chuva fraca", 63: "Chuva", 65: "Chuva forte", 66: "Chuva gelada", 67: "Chuva gelada",
        71: "Neve fraca", 73: "Neve", 75: "Neve forte", 77: "Grãos de neve", 80: "Pancadas fracas",
        81: "Pancadas de chuva", 82: "Pancadas fortes", 85: "Pancadas de neve", 86: "Pancadas de neve",
        95: "Tempestade", 96: "Tempestade c/ granizo", 99: "Tempestade c/ granizo"}
DIAS = ["seg", "ter", "qua", "qui", "sex", "sáb", "dom"]

def glifo(cod):
    if cod == 0: return "☀"
    if cod in (1, 2): return "⛅"
    if cod == 3: return "☁"
    if cod in (45, 48): return "≋"
    if 51 <= cod <= 67 or 80 <= cod <= 82: return "☂"
    if 71 <= cod <= 77 or cod in (85, 86): return "❄"
    if cod >= 95: return "⚡"
    return "·"

def _http_json(url, timeout=8):
    req = urllib.request.Request(url, headers={"User-Agent": "AidenHUD/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))

def buscar_clima(cidade):
    g = _http_json("https://geocoding-api.open-meteo.com/v1/search?" + urllib.parse.urlencode(
        {"name": cidade, "count": 1, "language": "pt", "format": "json"}))
    if not g.get("results"): raise ValueError("cidade não encontrada")
    r0 = g["results"][0]
    w = _http_json("https://api.open-meteo.com/v1/forecast?" + urllib.parse.urlencode({
        "latitude": r0["latitude"], "longitude": r0["longitude"], "timezone": "auto", "forecast_days": 6,
        "current": "temperature_2m,apparent_temperature,relative_humidity_2m,precipitation,weather_code,wind_speed_10m",
        "daily": "weather_code,temperature_2m_max,temperature_2m_min,sunrise,sunset,precipitation_probability_max"}))
    cur, dia = w["current"], w["daily"]
    hoje = datetime.date.today()
    dias = []
    for i in range(1, min(6, len(dia["time"]))):
        d = datetime.date.fromisoformat(dia["time"][i])
        rot = "Amanhã" if (d - hoje).days == 1 else DIAS[d.weekday()]
        dias.append({"rot": rot, "max": round(dia["temperature_2m_max"][i]), "min": round(dia["temperature_2m_min"][i]),
                     "cod": int(dia["weather_code"][i]), "chuva": dia["precipitation_probability_max"][i] or 0})
    cod = int(cur["weather_code"])
    return {"cidade": ", ".join(x for x in (r0.get("name"), r0.get("country_code")) if x),
            "temp": round(cur["temperature_2m"]), "sens": round(cur["apparent_temperature"]),
            "umid": round(cur["relative_humidity_2m"]), "vento": round(cur["wind_speed_10m"]),
            "prec": cur.get("precipitation") or 0, "cod": cod, "cond": COND.get(cod, "—"),
            "max": round(dia["temperature_2m_max"][0]), "min": round(dia["temperature_2m_min"][0]),
            "chuva": dia["precipitation_probability_max"][0] or 0,
            "nascer": dia["sunrise"][0][11:16], "por": dia["sunset"][0][11:16], "dias": dias}

def resumo_clima(c):
    prox = "; ".join(f"{d['rot']} {d['min']}–{d['max']}°C {COND.get(d['cod'], '')} (chuva {d['chuva']}%)" for d in c["dias"])
    return (f"{c['cidade']}: {c['temp']}°C (sensação {c['sens']}°C), {c['cond']}, umidade {c['umid']}%, "
            f"vento {c['vento']} km/h. Hoje {c['min']}–{c['max']}°C, chance de chuva {c['chuva']}%. "
            f"Sol {c['nascer']}–{c['por']}. Próximos dias: {prox}")

# ═══════ FERRAMENTAS EXTRAS PRO AIDEN ═══════
HUD_REF = None

def _tool_timer(minutos, nome="timer"):
    try: m = float(minutos)
    except (TypeError, ValueError): return "❌ minutos inválido"
    if not 0.05 <= m <= 600: return "❌ use entre 3 segundos e 10 horas"
    if HUD_REF is None: return "❌ o HUD não está aberto"
    HUD_REF.q.put(("timer_novo", (str(nome)[:24] or "timer", m * 60)))
    return f"✅ timer '{nome}' de {m:g} min iniciado"

def _tool_clima(cidade=""):
    cid = (cidade or "").strip() or (HUD_REF.cfg.get("cidade") if HUD_REF else "")
    if not cid: return "❌ diga a cidade (ou use /cidade NOME no HUD)"
    try: return resumo_clima(buscar_clima(cid))
    except Exception as e: return f"❌ não consegui o clima: {e}"

def _tool_status():
    ps = OPC["psutil"]
    if ps is None: return "❌ psutil não está disponível"
    vm = ps.virtual_memory(); du = ps.disk_usage(os.path.abspath(os.sep)); bat = ps.sensors_battery()
    return (f"CPU {ps.cpu_percent(interval=0.3):.0f}% · RAM {vm.percent:.0f}% ({vm.used / 1e9:.1f}/{vm.total / 1e9:.1f} GB) · "
            f"disco {du.percent:.0f}% ({du.free / 1e9:.0f} GB livres)" +
            (f" · bateria {bat.percent:.0f}%{' (carregando)' if bat.power_plugged else ''}" if bat else ""))

def registrar_ferramentas():
    ag = aiden_pc.agente
    ag.tools += [
        core._t("timer", "Inicia um timer que aparece no painel e avisa por voz quando acaba.",
                {"minutos": {"type": "number"}, "nome": {"type": "string"}}, ["minutos"]),
        core._t("clima", "Clima atual e previsão de uma cidade (sem cidade usa a do usuário).",
                {"cidade": {"type": "string"}}),
        core._t("status_sistema", "Uso de CPU, RAM, disco e bateria do PC."),
    ]
    ag.funcs.update(timer=_tool_timer, clima=_tool_clima, status_sistema=_tool_status)
    ag.read_only |= {"clima", "status_sistema"}

# ═══════ VOZ (fala e microfone) ═══════
def _limpar_fala(t):
    t = re.sub(r"https?://\S+", "link", t)
    t = re.sub(r"[*_`#>~|]", "", t)
    t = re.sub(r"[\U00010000-\U0010ffff\u2600-\u27bf\u2b00-\u2bff\ufe0f]", "", t)
    return re.sub(r"\s+", " ", t).strip()[:700]

class Voz:
    VOZ = "pt-BR-AntonioNeural"
    def __init__(self):
        self.falando = False; self._proc = None; self._ger = 0

    def parar(self):
        self._ger += 1
        try:
            if os.name == "nt":
                import ctypes
                ctypes.windll.winmm.mciSendStringW("stop aidenvoz", None, 0, None)
            if self._proc and self._proc.poll() is None: self._proc.terminate()
        except Exception: pass
        self.falando = False

    def falar(self, texto):
        texto = _limpar_fala(texto)
        if not texto: return
        self.parar()
        g = self._ger
        threading.Thread(target=self._run, args=(texto, g), daemon=True).start()

    def _tocar_mp3(self, arq, g):
        if os.name == "nt":
            import ctypes
            mci = ctypes.windll.winmm.mciSendStringW
            mci("close aidenvoz", None, 0, None)
            mci(f'open "{arq}" type mpegvideo alias aidenvoz', None, 0, None)
            mci("play aidenvoz wait", None, 0, None)
            mci("close aidenvoz", None, 0, None)
            return True
        for cmd in (["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", arq], ["mpv", "--no-video", "--really-quiet", arq]):
            if shutil.which(cmd[0]):
                self._proc = subprocess.Popen(cmd); self._proc.wait(); return True
        return False

    def _run(self, texto, g):
        self.falando = True
        try:
            ok = False
            if OPC["edge"] is not None:
                try:
                    arq = str(Path(tempfile.gettempdir()) / f"aiden_hud_{int(time.time() * 1000)}.mp3")
                    asyncio.run(OPC["edge"].Communicate(texto, self.VOZ, rate="+8%").save(arq))
                    if g == self._ger: ok = self._tocar_mp3(arq, g)
                    else: ok = True
                except Exception: ok = False
            if not ok and g == self._ger:
                try:
                    import pyttsx3
                    e = pyttsx3.init(); e.say(texto); e.runAndWait()
                except Exception: pass
        finally:
            if g == self._ger: self.falando = False

class Mic:
    TAXA = 16000
    def __init__(self):
        self.stream = None; self.buf = bytearray(); self.nivel = 0.0; self.t0 = 0.0

    def iniciar(self):
        sd = OPC["sd"]
        if sd is None: raise RuntimeError("sounddevice não instalado")
        self.buf = bytearray(); self.nivel = 0.0; self.t0 = time.time()
        def cb(indata, frames, tempo, status):
            b = bytes(indata)
            self.buf += b
            a = array("h"); a.frombytes(b[:len(b) // 2 * 2])
            rms = math.sqrt(sum(x * x for x in a) / max(1, len(a)))
            self.nivel = min(1.0, rms / 5000.0)
        self.stream = sd.RawInputStream(samplerate=self.TAXA, channels=1, dtype="int16", blocksize=1600, callback=cb)
        self.stream.start()

    def parar(self):
        """Devolve o WAV, ou None se foi curto demais ou só silêncio."""
        try:
            if self.stream: self.stream.stop(); self.stream.close()
        except Exception: pass
        self.stream = None; self.nivel = 0.0
        pcm = bytes(self.buf)
        if len(pcm) < self.TAXA * 2 * 0.6: return None
        a = array("h"); a.frombytes(pcm[:len(pcm) // 2 * 2])
        if math.sqrt(sum(x * x for x in a[::8]) / max(1, len(a[::8]))) < 120: return None
        b = io.BytesIO()
        with wave.open(b, "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(self.TAXA); w.writeframes(pcm)
        return b.getvalue()

# ═══════ SAÍDA DO NÚCLEO → FILA ═══════
class SaidaFila(core.Saida):
    def __init__(self, q): self.q = q
    def pensando(self, on, msg="pensando"): self.q.put(("pensando", on))
    def texto(self, s): self.q.put(("texto", s))
    def texto_fim(self): self.q.put(("texto_fim", None))
    def chamada(self, nome, args): self.q.put(("chamada", (nome, args)))
    def resultado(self, res): self.q.put(("resultado", res))
    def aviso(self, m): self.q.put(("aviso", m))
    def erro(self, m): self.q.put(("erro", m))
    def info(self, m): self.q.put(("info", m))

# ═══════ O HUD ═══════
class HUD:
    def __init__(self):
        global HUD_REF
        HUD_REF = self
        self.cfg = carregar_cfg()
        self.tema = TEMAS.get(self.cfg["tema"], TEMAS["ciano"])
        self.root = tk.Tk()
        self.root.title("AIDEN · HUD")
        self.root.geometry("1366x768")
        self.root.minsize(1000, 620)
        fams = set(tkfont.families(self.root))
        self.fam = next((f for f in ("Bahnschrift", "Segoe UI", "Consolas", "DejaVu Sans", "Courier") if f in fams), "TkDefaultFont")
        self.fam_sim = "Segoe UI Symbol" if "Segoe UI Symbol" in fams else self.fam

        self.q = queue.Queue()
        core.set_saida(SaidaFila(self.q))
        core.set_confirm_handler(self._confirmar_da_thread)
        mem = core.carregar_mem()
        self.hist = [{"role": "system", "content": aiden_pc.agente.build_sys()}] + mem
        registrar_ferramentas()

        self.voz = Voz(); self.mic = Mic()
        self.voz_on = bool(self.cfg["voz"])
        self.processando = False; self.pensando = False; self.gravando = False
        self.erro_ate = 0.0; self.alerta_ate = 0.0
        self.resp_buf = ""; self.ai_aberto = False
        self.timers = []
        self.clima = None; self.clima_msg = ""
        self.todos = []
        self.d = {"cpu": 0.0, "ram": 0.0, "swap": 0.0, "disco": 0.0, "disco_livre": 0.0, "bat": None, "up_s": 0.0}
        self.net_down = [0.0] * 60; self.net_up = [0.0] * 60; self._net_ant = None
        self.rot = 0.0; self._tprev = time.time()
        self.W, self.H, self.s = 1366, 768, 1.0
        self.cx, self.cy, self.R = 683.0, 350.0, 173.0
        self.cmd_hist = []; self.cmd_idx = -1
        self.hover = None
        rnd = random.Random(11)
        self.part = [[rnd.random(), rnd.random(), rnd.uniform(0.002, 0.006), rnd.uniform(-1, 1)] for _ in range(26)]

        self.canvas = tk.Canvas(self.root, bg=self.tema["bg"], highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        self.con = tk.Text(self.root, wrap="word", bd=0, highlightthickness=0, padx=8, pady=6,
                           font=(self.fam, 10), state="disabled", cursor="arrow")
        self.ent = tk.Entry(self.root, bd=0, highlightthickness=1, font=(self.fam, 11))
        self.win_con = self.canvas.create_window(0, 0, window=self.con, anchor="nw")
        self.win_ent = self.canvas.create_window(0, 0, window=self.ent, anchor="nw")
        self._cores_widgets()

        self.canvas.bind("<Configure>", self._ao_redimensionar)
        self.ent.bind("<Return>", lambda e: self._enviar())
        self.ent.bind("<Up>", self._hist_up)
        self.ent.bind("<Down>", self._hist_down)
        self.root.bind("<F11>", lambda e: self._tela_cheia())
        self.root.bind("<Escape>", lambda e: self.root.attributes("-fullscreen", False))
        self.root.bind("<Control-l>", lambda e: self._limpar_console())
        self.root.bind("<Control-p>", lambda e: self._alternar_plan())
        self.root.bind("<Control-m>", lambda e: self._alternar_mic())
        self.root.protocol("WM_DELETE_WINDOW", self._fechar)

        self._boas_vindas(len(mem))
        self.ent.focus()
        threading.Thread(target=_carregar_opcionais, daemon=True).start()
        self._atualizar_clima()
        self.root.after(30, self._poll)
        self.root.after(500, self._tick_sistema)
        self.root.after(40, self._desenhar)
        if core.cliente is None: self.root.after(500, self._pedir_chave)

    # ═══════ CORES E LAYOUT ═══════
    def _cores_widgets(self):
        t = self.tema
        self.con.configure(bg=t["bg"], fg=t["tx"], insertbackground=t["br"], selectbackground=t["dm"])
        self.ent.configure(bg=t["bg"], fg=t["br"], insertbackground=t["br"],
                           highlightbackground=t["dm"], highlightcolor=t["br"])
        for tag, cor in (("user", t["br"]), ("ai", t["tx"]), ("dim", t["md"]), ("ok", "#5be08a"),
                         ("err", t["vm"]), ("warn", "#e6b450"), ("tool", t["md"])):
            self.con.tag_config(tag, foreground=cor)
        self.con.tag_config("user", font=(self.fam, 10, "bold"))

    def _ao_redimensionar(self, ev):
        if ev.width < 200 or ev.height < 200: return
        self.W, self.H = ev.width, ev.height
        self.s = min(self.W / 1366.0, self.H / 768.0)
        self._estatico()

    def _f(self, px, bold=False, simbolo=False):
        return (self.fam_sim if simbolo else self.fam, -max(7, int(px * self.s)), "bold" if bold else "normal")

    # ═══════ CAMADA ESTÁTICA (só redesenha ao mudar tamanho/tema/estado dos botões) ═══════
    def _estatico(self):
        c = self.canvas; t = self.tema; W, H, s = self.W, self.H, self.s
        c.delete("est")
        c.configure(bg=t["bg"])
        rnd = random.Random(7)
        for _ in range(60):                                   # poeira de fundo
            x, y, r = rnd.random() * W, rnd.random() * H, rnd.choice((1, 1, 2))
            c.create_oval(x, y, x + r, y + r, fill=rnd.choice((t["es"], t["dm"], t["es"])), outline="", tags="est")

        self.cx, self.cy, self.R = W * 0.5, H * 0.455, H * 0.225

        def painel(x0, y0, x1, y1, titulo):
            x0, y0, x1, y1 = x0 * W, y0 * H, x1 * W, y1 * H
            k = 16 * s
            for (xa, ya, dx, dy) in ((x0, y0, 1, 1), (x1, y0, -1, 1), (x0, y1, 1, -1), (x1, y1, -1, -1)):
                c.create_line(xa, ya + dy * k, xa, ya, xa + dx * k, ya, fill=t["md"], width=2, tags="est")
            c.create_line(x0 + k * 2.6, y0, x1 - k, y0, fill=t["es"], tags="est")
            c.create_text(x0 + k * 1.2, y0 - 1, text=titulo, fill=t["md"], anchor="w", font=self._f(10, True), tags="est")

        painel(0.012, 0.075, 0.245, 0.945, "SISTEMA")
        painel(0.765, 0.075, 0.988, 0.735, "CLIMA")
        painel(0.725, 0.775, 0.988, 0.945, "REDE")
        painel(0.292, 0.700, 0.708, 0.945, "CONSOLE")

        # traços de circuito ligando o reator aos painéis
        cx, cy, R = self.cx, self.cy, self.R
        def tr(pts, cor=None):
            flat = [v for p in pts for v in (p[0] * W, p[1] * H)]
            c.create_line(*flat, fill=cor or t["dm"], width=1, tags="est")
            ex, ey = pts[-1][0] * W, pts[-1][1] * H
            c.create_oval(ex - 3 * s, ey - 3 * s, ex + 3 * s, ey + 3 * s, fill=t["md"], outline="", tags="est")
        tr([(0.245, 0.50), (0.265, 0.47), (cx / W - R / W - 0.004, 0.47)])
        tr([(0.765, 0.50), (0.745, 0.47), (cx / W + R / W + 0.004, 0.47)])
        tr([(0.5, (cy + R) / H + 0.01), (0.5, 0.70)])
        tr([(0.245, 0.30), (0.30, 0.30), (0.34, 0.255)])
        tr([(0.765, 0.30), (0.71, 0.30), (0.665, 0.255)])

        # lançador de apps (à esquerda do reator)
        c.create_text(cx - R - 0.012 * W, cy - R * 0.62, text="LANÇADOR", fill=t["md"], anchor="e",
                      font=self._f(10, True), tags="est")
        for i, app in enumerate(self.cfg["apps"][:8]):
            y = cy - R * 0.42 + i * 0.043 * H
            x = cx - R - 0.012 * W
            item = c.create_text(x, y, text=app.upper(), fill=t["br"], anchor="e", font=self._f(12), tags=("est", "app"))
            dot = c.create_oval(x + 8 * s, y - 4 * s, x + 16 * s, y + 4 * s, fill=t["dm"], outline=t["md"], tags="est")
            for it in (item, dot):
                c.tag_bind(it, "<Button-1>", lambda e, a=app: self._abrir_app(a))
                c.tag_bind(it, "<Enter>", lambda e, i_=item: c.itemconfigure(i_, fill="white"))
                c.tag_bind(it, "<Leave>", lambda e, i_=item: c.itemconfigure(i_, fill=t["br"]))

        # botões na base
        chips = [("VOZ " + ("ON" if self.voz_on else "OFF"), self._alternar_voz, self.voz_on),
                 ("MIC " + ("● GRAVANDO" if self.gravando else "OFF"), self._alternar_mic, self.gravando),
                 ("PLAN " + ("ON" if core.ESTADO["plan"] else "OFF"), self._alternar_plan, core.ESTADO["plan"]),
                 ("TEMA " + t["nome"], self._trocar_tema, False),
                 ("TELA CHEIA", self._tela_cheia, False)]
        x = W * 0.5 - (len(chips) * 128 * s) / 2 + 64 * s
        for nome, fn, ativo in chips:
            txt = c.create_text(x, H * 0.974, text=nome, fill=t["bg"] if ativo else t["br"], font=self._f(10, True), tags="est")
            bb = c.bbox(txt)
            ret = c.create_rectangle(bb[0] - 8 * s, bb[1] - 3 * s, bb[2] + 8 * s, bb[3] + 3 * s,
                                     outline=t["md"], fill=t["br"] if ativo else "", tags="est")
            c.tag_raise(txt, ret)
            for it in (txt, ret): c.tag_bind(it, "<Button-1>", lambda e, f=fn: f())
            x += 128 * s

        # widgets do console
        cx0, cy0 = 0.298 * W, 0.722 * H
        c.coords(self.win_con, cx0, cy0)
        c.itemconfigure(self.win_con, width=0.404 * W, height=0.170 * H)
        c.coords(self.win_ent, cx0, 0.900 * H)
        c.itemconfigure(self.win_ent, width=0.404 * W, height=max(24, 0.034 * H))
        c.tag_lower("est")

    # ═══════ DESENHO DINÂMICO ═══════
    def _txt(self, x, y, s_, cor, px, anchor="center", bold=False, simbolo=False):
        self.canvas.create_text(x, y, text=s_, fill=cor, font=self._f(px, bold, simbolo), anchor=anchor, tags="dyn")

    def _arco(self, cx, cy, r, ini, ext, cor, larg):
        self.canvas.create_arc(cx - r, cy - r, cx + r, cy + r, start=ini, extent=ext, style="arc",
                               outline=cor, width=max(1, larg), tags="dyn")

    def _arco_glow(self, cx, cy, r, ini, ext, larg, cor_base, cor_brilho):
        self._arco(cx, cy, r, ini, ext, cor_base, larg)
        self._arco(cx, cy, r, ini, ext, cor_brilho, max(1, larg * 0.25))

    def _anel(self, cx, cy, r, pct, larg, cor):
        pct = max(0.0, min(100.0, pct))
        self._arco(cx, cy, r, 0, 359.9, self.tema["es"], larg)
        if pct > 0.3: self._arco(cx, cy, r, 90, -pct * 3.599, cor, larg)

    def _estado(self):
        if self.gravando: return "ouvindo"
        if self.pensando or self.processando: return "pensando"
        if self.voz.falando: return "falando"
        if time.time() < self.erro_ate: return "erro"
        return "online"

    def _desenhar(self):
        try:
            agora = time.time()
            dt = min(0.1, agora - self._tprev); self._tprev = agora
            self.canvas.delete("dyn")
            est = self._estado()
            self._particulas(dt)
            self._reator(dt, est)
            self._topo()
            self._col_esq()
            self._col_dir()
            self._rede()
        except Exception as e:
            core.dbg(f"hud: {e}")
        finally:
            self.canvas.tag_raise("dyn")
            self.canvas.tag_raise("app")
            self.root.after(40, self._desenhar)

    def _particulas(self, dt):
        t = self.tema; W, H = self.W, self.H
        for p in self.part:
            p[1] -= p[2] * dt * 10
            p[0] += p[3] * 0.0008 * dt * 10
            if p[1] < 0: p[1] = 1.0; p[0] = random.random()
            x, y = p[0] * W, p[1] * H
            self.canvas.create_oval(x, y, x + 2 * self.s, y + 2 * self.s, fill=t["md"], outline="", tags="dyn")

    def _reator(self, dt, est):
        c = self.canvas; t = self.tema; s = self.s
        cx, cy, R = self.cx, self.cy, self.R
        vel = {"online": 1.0, "pensando": 3.6, "ouvindo": 1.7, "falando": 2.3, "erro": 0.5}[est]
        self.rot += dt * vel
        rot = self.rot
        pulso = 0.5 + 0.5 * math.sin(time.time() * (1.4 if est == "online" else 3.4))
        alerta = time.time() < self.alerta_ate and int(time.time() * 4) % 2 == 0
        cor = t["vm"] if (est == "erro" or alerta) else t["br"]
        base = mix(t["bg"], cor, 0.35)

        # fundo
        c.create_oval(cx - R * 1.04, cy - R * 1.04, cx + R * 1.04, cy + R * 1.04, fill=t["es"], outline="", tags="dyn")
        c.create_oval(cx - R * 0.86, cy - R * 0.86, cx + R * 0.86, cy + R * 0.86, fill=t["bg"], outline="", tags="dyn")
        # anel de marcas
        n = 90
        for i in range(n):
            a = rot * 0.25 + i * 2 * math.pi / n
            r0 = R * 0.93; r1 = R * (1.0 if i % 6 == 0 else 0.965)
            c.create_line(cx + math.cos(a) * r0, cy - math.sin(a) * r0, cx + math.cos(a) * r1, cy - math.sin(a) * r1,
                          fill=cor if i % 6 == 0 else t["md"], tags="dyn")
        # anéis finos
        for f in (0.88, 0.74, 0.60, 0.46):
            c.create_oval(cx - R * f, cy - R * f, cx + R * f, cy + R * f, outline=t["dm"], tags="dyn")
        g = math.degrees(rot)
        # arcos giratórios
        self._arco_glow(cx, cy, R * 0.88, g * 0.9, 105, R * 0.07, base, cor)
        self._arco_glow(cx, cy, R * 0.88, g * 0.9 + 180, 62, R * 0.07, base, cor)
        self._arco_glow(cx, cy, R * 0.74, -g * 1.3, 150, R * 0.05, base, cor)
        self._arco_glow(cx, cy, R * 0.74, -g * 1.3 + 205, 80, R * 0.05, base, cor)
        self._arco_glow(cx, cy, R * 0.60, g * 1.8, 190, R * 0.035, base, cor)
        # arco vermelho
        self._arco(cx, cy, R * 0.80, g * (1.0 if est != "pensando" else 3.0) + 120, 48 if est != "pensando" else 95,
                   t["vm"], max(2, R * 0.022))
        # marcas internas
        for i in range(36):
            a = -rot * 0.6 + i * 2 * math.pi / 36
            c.create_line(cx + math.cos(a) * R * 0.50, cy - math.sin(a) * R * 0.50,
                          cx + math.cos(a) * R * 0.545, cy - math.sin(a) * R * 0.545, fill=t["dm"], tags="dyn")
        # nível de voz (ouvindo/falando)
        nivel = self.mic.nivel if est == "ouvindo" else (0.35 + 0.5 * pulso if est == "falando" else 0.0)
        if nivel > 0.02:
            rr = R * (0.50 + 0.30 * nivel)
            c.create_oval(cx - rr, cy - rr, cx + rr, cy + rr, outline=cor, width=max(1, 2 * s), tags="dyn")
        # núcleo em camadas
        for k in range(7):
            rr = R * (0.36 - k * 0.043) * (1 + 0.035 * pulso)
            c.create_oval(cx - rr, cy - rr, cx + rr, cy + rr, fill=mix(t["es"], cor, k / 6.5), outline="", tags="dyn")
        self._arco(cx, cy, R * 0.25, g * 2.2, 110, t["bg"], max(2, R * 0.03))
        self._arco(cx, cy, R * 0.25, g * 2.2 + 180, 110, t["bg"], max(2, R * 0.03))
        rb = R * 0.07
        c.create_oval(cx - rb, cy - rb, cx + rb, cy + rb, fill="white" if est != "erro" else t["vm"], outline="", tags="dyn")
        # estado
        nomes = {"online": "ONLINE", "pensando": "PENSANDO…", "ouvindo": "OUVINDO", "falando": "FALANDO", "erro": "ERRO"}
        self._txt(cx, cy + R + 0.03 * self.H, f"AIDEN · {nomes[est]}", cor, 14, bold=True)

    def _topo(self):
        c = self.canvas; t = self.tema; W, H, s = self.W, self.H, self.s
        hoje = datetime.date.today()
        n = calendar.monthrange(hoje.year, hoje.month)[1]
        for d in range(1, n + 1):
            x = W * 0.03 + (d - 1) * (W * 0.94) / (n - 1)
            if d == hoje.day:
                c.create_rectangle(x - 13 * s, H * 0.012, x + 13 * s, H * 0.052, fill=t["br"], outline="", tags="dyn")
                self._txt(x, H * 0.032, f"{d:02d}", t["bg"], 13, bold=True)
            else:
                self._txt(x, H * 0.032, f"{d:02d}", t["md"], 11)
        # RAM / SWAP
        x, y, r = W * 0.405, H * 0.150, H * 0.070
        self._anel(x, y, r, self.d["ram"], r * 0.20, t["br"])
        self._anel(x, y, r * 0.70, self.d["swap"], r * 0.15, t["md"])
        self._txt(x, y - r * 0.12, f"RAM {self.d['ram']:.0f}", t["br"], 12, bold=True)
        self._txt(x, y + r * 0.22, f"SWAP {self.d['swap']:.0f}", t["md"], 10)
        # timer ou relógio
        x = W * 0.595
        agora = time.time()
        self.timers = [tm for tm in self.timers if tm["fim"] > agora - 0.01]
        if self.timers:
            tm = min(self.timers, key=lambda z: z["fim"])
            resta = max(0.0, tm["fim"] - agora)
            self._anel(x, y, r, resta / tm["total"] * 100, r * 0.20, t["br"])
            mm, ss = divmod(int(resta + 0.99), 60)
            self._txt(x, y - r * 0.08, f"{mm}:{ss:02d}" if mm < 60 else f"{mm // 60}h{mm % 60:02d}", t["tx"], 20, bold=True)
            self._txt(x, y + r * 0.34, tm["nome"].upper()[:14], t["md"], 10)
        else:
            now = datetime.datetime.now()
            self._anel(x, y, r, now.second / 60 * 100, r * 0.12, t["br"])
            self._txt(x, y - r * 0.05, now.strftime("%H:%M"), t["tx"], 22, bold=True)
            self._txt(x, y + r * 0.40, now.strftime("%S"), t["md"], 11)

    def _col_esq(self):
        c = self.canvas; t = self.tema; W, H, s = self.W, self.H, self.s
        hoje = datetime.date.today()
        meses = ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto", "setembro", "outubro", "novembro", "dezembro"]
        sem = ["segunda", "terça", "quarta", "quinta", "sexta", "sábado", "domingo"]
        # data
        x, y, r = W * 0.128, H * 0.215, H * 0.082
        n = calendar.monthrange(hoje.year, hoje.month)[1]
        self._anel(x, y, r, hoje.day / n * 100, r * 0.12, t["br"])
        self._txt(x, y - r * 0.50, meses[hoje.month - 1].upper(), t["md"], 11, bold=True)
        self._txt(x, y, f"{hoje.day}", t["tx"], 34, bold=True)
        self._txt(x, y + r * 0.58, sem[hoje.weekday()], t["md"], 11)
        # relógio digital
        now = datetime.datetime.now()
        self._txt(W * 0.128, H * 0.352, now.strftime("%H:%M:%S"), t["br"], 22, bold=True)
        # CPU e bateria
        y2 = H * 0.515; r2 = H * 0.052
        self._anel(W * 0.075, y2, r2, self.d["cpu"], r2 * 0.22, t["br"])
        self._txt(W * 0.075, y2 - 5 * s, f"{self.d['cpu']:.0f}%", t["tx"], 14, bold=True)
        self._txt(W * 0.075, y2 + 13 * s, "CPU", t["md"], 9)
        bat = self.d["bat"]
        self._anel(W * 0.178, y2, r2, bat[0] if bat else 100, r2 * 0.22, "#5be08a" if (bat and bat[1]) else t["br"])
        self._txt(W * 0.178, y2 - 5 * s, f"{bat[0]:.0f}%" if bat else "AC", t["tx"], 14, bold=True)
        self._txt(W * 0.178, y2 + 13 * s, "ENERGIA" if bat else "TOMADA", t["md"], 9)
        # disco
        yd = H * 0.620
        self._txt(W * 0.024, yd, f"DISCO {self.d['disco']:.0f}%", t["br"], 11, "w", True)
        self._txt(W * 0.236, yd, f"{self.d['disco_livre']:.0f} GB livres", t["md"], 10, "e")
        x0, x1 = W * 0.024, W * 0.236
        c.create_rectangle(x0, yd + 12 * s, x1, yd + 18 * s, outline=t["dm"], tags="dyn")
        c.create_rectangle(x0, yd + 12 * s, x0 + (x1 - x0) * self.d["disco"] / 100, yd + 18 * s, fill=t["br"], outline="", tags="dyn")
        up = int(self.d["up_s"]); dias, resto = divmod(up, 86400)
        self._txt(W * 0.024, yd + 34 * s, f"ATIVO HÁ {dias}d {resto // 3600}h {resto % 3600 // 60:02d}m", t["md"], 10, "w")
        if not OPC["psutil"]: self._txt(W * 0.128, H * 0.575, "(instalando psutil…)", t["md"], 9)
        # tarefas
        yt = H * 0.720
        self._txt(W * 0.024, yt, "TAREFAS", t["br"], 11, "w", True)
        if not self.todos: self._txt(W * 0.024, yt + 20 * s, "nenhuma — peça ao Aiden", t["dm"], 10, "w")
        icone = {"pending": "○", "in_progress": "◐", "completed": "●"}
        cor = {"pending": t["md"], "in_progress": "#e6b450", "completed": "#5be08a"}
        for i, tk_ in enumerate(self.todos[:6]):
            self._txt(W * 0.024, yt + (20 + i * 17) * s, f"{icone.get(tk_['status'], '○')} {tk_['content'][:27]}",
                      cor.get(tk_["status"], t["md"]), 10, "w")

    def _col_dir(self):
        t = self.tema; W, H, s = self.W, self.H, self.s
        x0, x1 = W * 0.778, W * 0.976
        cl = self.clima
        if not cl:
            msg = self.clima_msg or ("carregando clima…" if self.cfg["cidade"] else "defina a cidade:  /cidade NOME")
            self._txt((x0 + x1) / 2, H * 0.22, msg, t["md"], 12)
            return
        self._txt(x1, H * 0.100, cl["cidade"].upper(), t["md"], 12, "e", True)
        self._txt(x0, H * 0.170, f"{cl['temp']}°", t["tx"], 52, "w", True)
        self._txt(x1, H * 0.168, glifo(cl["cod"]), t["br"], 44, "e", simbolo=True)
        self._txt(x0, H * 0.245, cl["cond"], t["br"], 14, "w", True)
        linhas = [f"Máx/Mín hoje: {cl['max']}° / {cl['min']}°", f"Sensação: {cl['sens']}°",
                  f"Umidade: {cl['umid']}%    Chuva: {cl['chuva']}%", f"Vento: {cl['vento']} km/h",
                  f"Sol: {cl['nascer']} → {cl['por']}"]
        for i, ln in enumerate(linhas): self._txt(x0, H * (0.288 + i * 0.030), ln, t["tx"], 11, "w")
        for i, d in enumerate(cl["dias"][:5]):
            y = H * (0.470 + i * 0.050)
            self._txt(x0, y, d["rot"].upper(), t["br"], 12, "w", True)
            self._txt(x0 + 90 * s, y, glifo(d["cod"]), t["br"], 16, "w", simbolo=True)
            self._txt(x1, y - 7 * s, f"{d['max']}° / {d['min']}°", t["tx"], 12, "e")
            self._txt(x1, y + 9 * s, f"{COND.get(d['cod'], '')[:20]} · {d['chuva']:.0f}%", t["md"], 9, "e")

    def _rede(self):
        c = self.canvas; t = self.tema; W, H, s = self.W, self.H, self.s
        x0, x1, y0, y1 = W * 0.735, W * 0.978, H * 0.810, H * 0.925
        c.create_line(x0, y1, x1, y1, fill=t["dm"], tags="dyn")
        mx = max(max(self.net_down), max(self.net_up), 20 * 1024.0)
        for serie, cor, larg in ((self.net_up, t["md"], 1), (self.net_down, t["br"], 2)):
            pts = []
            for i, v in enumerate(serie):
                pts += [x0 + (x1 - x0) * i / (len(serie) - 1), y1 - (y1 - y0) * (v / mx)]
            c.create_line(*pts, fill=cor, width=larg, tags="dyn")
        self._txt(x0, y0 - 8 * s, f"↓ {fmt_bytes(self.net_down[-1])}", t["br"], 10, "w", True)
        self._txt(x1, y0 - 8 * s, f"↑ {fmt_bytes(self.net_up[-1])}", t["md"], 10, "e")

    # ═══════ DADOS DO SISTEMA ═══════
    def _tick_sistema(self):
        try:
            ps = OPC["psutil"]
            if ps:
                self.d["cpu"] = ps.cpu_percent(None)
                self.d["ram"] = ps.virtual_memory().percent
                self.d["swap"] = ps.swap_memory().percent
                du = ps.disk_usage(os.path.abspath(os.sep))
                self.d["disco"] = du.percent; self.d["disco_livre"] = du.free / 1e9
                b = ps.sensors_battery()
                self.d["bat"] = (b.percent, b.power_plugged) if b else None
                self.d["up_s"] = time.time() - ps.boot_time()
                n = ps.net_io_counters(); agora = time.time()
                if self._net_ant:
                    dt = max(0.2, agora - self._net_ant[0])
                    self.net_down = self.net_down[1:] + [(n.bytes_recv - self._net_ant[1]) / dt]
                    self.net_up = self.net_up[1:] + [(n.bytes_sent - self._net_ant[2]) / dt]
                self._net_ant = (agora, n.bytes_recv, n.bytes_sent)
            self.todos = core._carregar_todos()
            if self.gravando and time.time() - self.mic.t0 > 30: self._alternar_mic()
        except Exception as e:
            core.dbg(f"tick: {e}")
        self.root.after(1000, self._tick_sistema)

    def _atualizar_clima(self):
        cid = self.cfg["cidade"]
        if cid:
            def run():
                try: self.clima = buscar_clima(cid); self.clima_msg = ""
                except Exception as e: self.clima_msg = f"clima indisponível ({str(e)[:40]})"
            threading.Thread(target=run, daemon=True).start()
        self.root.after(20 * 60 * 1000, self._atualizar_clima)

    # ═══════ CONSOLE ═══════
    def _con(self, texto, tag="ai"):
        self.con.configure(state="normal"); self.con.insert("end", texto, tag)
        self.con.see("end"); self.con.configure(state="disabled")

    def _limpar_console(self):
        self.con.configure(state="normal"); self.con.delete("1.0", "end"); self.con.configure(state="disabled")

    def _boas_vindas(self, n_mem):
        self._con("AIDEN HUD ONLINE\n", "user")
        self._con(f"modelo {core.ESTADO['modelo']} · /ajuda pra comandos\n", "dim")
        if n_mem: self._con(f"lembrei de {n_mem} mensagens\n", "dim")
        if not self.cfg["cidade"]: self._con("dica: /cidade SUA CIDADE pra ligar o clima\n", "dim")

    def _enviar(self):
        txt = self.ent.get().strip()
        if not txt: return
        self.ent.delete(0, "end")
        self._enviar_texto(txt)

    def _enviar_texto(self, txt):
        self.cmd_hist.append(txt); self.cmd_idx = -1
        self._con(f"\n> {txt}\n", "user")
        if txt.startswith("/"): self._comando(txt); return
        if self.processando:
            self._con("espera a resposta anterior terminar\n", "warn"); return
        self.voz.parar()
        self.processando = True; self.resp_buf = ""
        threading.Thread(target=self._rodar, args=(txt,), daemon=True).start()

    def _rodar(self, pergunta):
        try: self.hist = aiden_pc.agente.rodar(pergunta, self.hist)
        except Exception as e: self.q.put(("erro", str(e)))
        finally: self.q.put(("fim", None))

    # ═══════ COMANDOS ═══════
    def _comando(self, cmd):
        partes = cmd.split(); c = partes[0].lower(); resto = cmd[len(partes[0]):].strip()
        if c in ("/sair", "/quit", "/exit"): self._fechar(); return
        if c == "/limpar":
            self.hist = [{"role": "system", "content": aiden_pc.agente.build_sys()}]
            self._limpar_console(); self._con("histórico limpo\n", "dim"); return
        if c == "/esquecer": core.esquecer_mem(); self._con("histórico salvo apagado\n", "ok"); return
        if c == "/chave": self._pedir_chave(); return
        if c == "/tema": self._trocar_tema(resto.lower() or None); return
        if c == "/voz": self._alternar_voz(); return
        if c == "/mic": self._alternar_mic(); return
        if c == "/full": self._tela_cheia(); return
        if c == "/cidade":
            if not resto: self._con(f"cidade atual: {self.cfg['cidade'] or '(nenhuma)'}\n", "dim"); return
            self.cfg["cidade"] = resto; self._salvar_cfg(); self.clima = None; self.clima_msg = ""
            self._atualizar_clima(); self._con(f"cidade: {resto}\n", "ok"); return
        if c == "/timer":
            m = re.match(r"^(\d+(?:[.,]\d+)?)\s*([smh]?)\s*(.*)$", resto)
            if not m: self._con("uso: /timer 10   ou   /timer 30s café   ou   /timer 2h\n", "warn"); return
            val = float(m.group(1).replace(",", ".")) * {"": 60, "m": 60, "s": 1, "h": 3600}[m.group(2)]
            self._novo_timer(m.group(3) or "timer", val); return
        if c == "/app":
            p = resto.split(None, 1)
            if not p: self._con("lançador: " + ", ".join(self.cfg["apps"]) + "\nuso: /app add NOME · /app rm NOME\n", "dim"); return
            if p[0] == "add" and len(p) > 1 and p[1] not in self.cfg["apps"] and len(self.cfg["apps"]) < 8:
                self.cfg["apps"].append(p[1].strip()); self._salvar_cfg(); self._estatico(); self._con("adicionado\n", "ok")
            elif p[0] == "rm" and len(p) > 1 and p[1] in self.cfg["apps"]:
                self.cfg["apps"].remove(p[1]); self._salvar_cfg(); self._estatico(); self._con("removido\n", "ok")
            else: self._con("não deu (máx. 8 apps, sem repetir)\n", "warn")
            return
        if c == "/plan": self._alternar_plan(); return
        if c == "/ajuda":
            self._con(aiden_pc.agente.texto_ajuda() + "\n  /tema [nome]  /cidade X  /timer N  /app add|rm  /voz  /mic  /full\n", "dim"); return
        txt = aiden_pc.agente.comando(cmd)
        self._con((txt if txt is not None else f"comando desconhecido: {cmd}") + "\n", "dim" if txt is not None else "warn")

    def _salvar_cfg(self):
        try: CFG_FILE.write_text(json.dumps(self.cfg, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as e: core.dbg(f"cfg hud: {e}")

    def _abrir_app(self, nome):
        self._con(f"abrindo {nome}…\n", "dim")
        threading.Thread(target=lambda: self.q.put(("info", aiden_pc.abrir_app(nome))), daemon=True).start()

    def _alternar_voz(self):
        self.voz_on = not self.voz_on; self.cfg["voz"] = self.voz_on; self._salvar_cfg()
        if not self.voz_on: self.voz.parar()
        self._estatico()

    def _alternar_plan(self):
        core.ESTADO["plan"] = not core.ESTADO["plan"]
        self._con("plan mode " + ("LIGADO: só leitura" if core.ESTADO["plan"] else "desligado") + "\n", "warn")
        self._estatico()

    def _trocar_tema(self, nome=None):
        chaves = list(TEMAS)
        if nome not in TEMAS: nome = chaves[(chaves.index(self.cfg["tema"]) + 1) % len(chaves)] if self.cfg["tema"] in chaves else chaves[0]
        self.cfg["tema"] = nome; self.tema = TEMAS[nome]; self._salvar_cfg()
        self._cores_widgets(); self._estatico()

    def _tela_cheia(self):
        self.root.attributes("-fullscreen", not bool(self.root.attributes("-fullscreen")))

    def _novo_timer(self, nome, seg):
        seg = max(3.0, min(seg, 36000.0))
        self.timers.append({"nome": nome, "fim": time.time() + seg, "total": seg})
        self._con(f"⏱ timer '{nome}' de {seg / 60:.1f} min\n", "ok")
        self.root.after(int(seg * 1000) + 200, lambda n=nome: self._timer_acabou(n))

    def _timer_acabou(self, nome):
        self._con(f"⏰ timer '{nome}' acabou!\n", "warn")
        self.alerta_ate = time.time() + 6
        try:
            if os.name == "nt":
                import winsound; winsound.Beep(1000, 400); winsound.Beep(1300, 400)
            else: self.root.bell()
        except Exception: pass
        if self.voz_on: self.voz.falar(f"O timer {nome} acabou.")

    # ═══════ MICROFONE ═══════
    def _alternar_mic(self):
        if self.gravando:
            self.gravando = False; wav = self.mic.parar(); self._estatico()
            if not wav: self._con("áudio curto demais ou sem som\n", "warn"); return
            self._con("🎤 transcrevendo…\n", "dim")
            threading.Thread(target=self._transcrever, args=(wav,), daemon=True).start(); return
        if OPC["sd"] is None:
            self._con("microfone indisponível (instalando sounddevice… tente de novo em instantes)\n", "warn"); return
        if self.processando: return
        self.voz.parar()
        try: self.mic.iniciar(); self.gravando = True; self._estatico()
        except Exception as e: self._con(f"microfone: {e}\n", "err")

    def _transcrever(self, wav):
        try:
            r = core.cliente.audio.transcriptions.create(
                file=("fala.wav", wav), model="whisper-large-v3-turbo", language="pt",
                temperature=0, prompt="Aiden, Jarvis.")
            t = (getattr(r, "text", "") or "").strip()
        except Exception as e:
            self.q.put(("erro", f"transcrição: {e}")); return
        if len(t) < 2: self.q.put(("aviso", "não entendi, tenta de novo")); return
        self.q.put(("voz_texto", t))

    # ═══════ FILA DE EVENTOS (thread principal) ═══════
    def _poll(self):
        try:
            while True:
                tipo, dado = self.q.get_nowait()
                if tipo == "pensando": self.pensando = bool(dado)
                elif tipo == "texto":
                    if not self.ai_aberto: self.ai_aberto = True; self._con("● ", "user")
                    self._con(dado, "ai"); self.resp_buf += dado
                elif tipo == "texto_fim":
                    if self.ai_aberto: self.ai_aberto = False; self._con("\n", "ai")
                elif tipo == "chamada":
                    nome, args = dado; self._con(f"  ▸ {nome}({args[:70]})\n", "tool")
                elif tipo == "resultado":
                    self._con("    ⎿ " + (dado.splitlines() or [""])[0][:90] + "\n", "dim")
                elif tipo == "aviso": self._con(f"⚠ {dado}\n", "warn")
                elif tipo == "erro":
                    self._con(f"❌ {dado}\n", "err"); self.erro_ate = time.time() + 4
                elif tipo == "info": self._con(f"{dado}\n", "dim")
                elif tipo == "timer_novo": self._novo_timer(*dado)
                elif tipo == "voz_texto": self._enviar_texto(dado)
                elif tipo == "confirmar":
                    desc, ev, box = dado
                    box["ok"] = self._pedir_confirmacao(desc); ev.set()
                elif tipo == "fim":
                    self.processando = False; self.pensando = False; self.ai_aberto = False
                    if self.voz_on and self.resp_buf.strip(): self.voz.falar(self.resp_buf)
                    self.resp_buf = ""
        except queue.Empty:
            pass
        self.root.after(30, self._poll)

    # ═══════ JANELAS HUD ═══════
    def _janela(self, titulo):
        t = self.tema
        top = tk.Toplevel(self.root)
        top.title(titulo); top.configure(bg=t["bg"], highlightbackground=t["br"], highlightthickness=2)
        top.transient(self.root); top.resizable(False, False)
        return top

    def _centralizar(self, top):
        top.update_idletasks()
        w, h = top.winfo_width(), top.winfo_height()
        x = self.root.winfo_rootx() + (self.root.winfo_width() - w) // 2
        y = self.root.winfo_rooty() + (self.root.winfo_height() - h) // 2
        top.geometry(f"+{max(0, x)}+{max(0, y)}")
        try: top.grab_set()
        except tk.TclError: pass
        top.focus_force()

    def _pedir_confirmacao(self, desc):
        t = self.tema; res = {"v": False}
        top = self._janela("Aiden pede permissão")
        tk.Label(top, text="◈ AUTORIZAÇÃO NECESSÁRIA", bg=t["bg"], fg=t["vm"], font=(self.fam, 12, "bold")).pack(padx=24, pady=(18, 6))
        tk.Label(top, text=desc, bg=t["bg"], fg=t["tx"], font=(self.fam, 10), justify="left", wraplength=520).pack(padx=24, pady=6)
        linha = tk.Frame(top, bg=t["bg"]); linha.pack(pady=(10, 18))
        def fim(v): res["v"] = v; top.destroy()
        for txt, v, cor in (("AUTORIZAR  [S]", True, t["br"]), ("NEGAR  [N]", False, t["vm"])):
            tk.Button(linha, text=txt, command=lambda v=v: fim(v), bg=t["bg"], fg=cor, activebackground=cor,
                      activeforeground=t["bg"], bd=1, relief="solid", font=(self.fam, 10, "bold"),
                      padx=16, pady=6, cursor="hand2").pack(side="left", padx=10)
        top.bind("<Key-s>", lambda e: fim(True)); top.bind("<Key-S>", lambda e: fim(True))
        top.bind("<Key-n>", lambda e: fim(False)); top.bind("<Key-N>", lambda e: fim(False))
        top.bind("<Escape>", lambda e: fim(False))
        top.protocol("WM_DELETE_WINDOW", lambda: fim(False))
        self._centralizar(top)
        self.root.wait_window(top)
        return res["v"]

    def _confirmar_da_thread(self, desc):
        ev = threading.Event(); box = {"ok": False}
        self.q.put(("confirmar", (desc, ev, box)))
        ev.wait(timeout=180)
        return box["ok"]

    def _pedir_chave(self):
        t = self.tema; res = {"v": None}
        top = self._janela("Chave da Groq")
        tk.Label(top, text="◈ CHAVE DA GROQ", bg=t["bg"], fg=t["br"], font=(self.fam, 12, "bold")).pack(padx=24, pady=(18, 4))
        tk.Label(top, text="pega em https://console.groq.com/keys", bg=t["bg"], fg=t["md"], font=(self.fam, 9)).pack()
        e = tk.Entry(top, show="*", width=46, bg=t["bg"], fg=t["br"], insertbackground=t["br"], bd=0,
                     highlightthickness=1, highlightbackground=t["dm"], highlightcolor=t["br"], font=(self.fam, 11))
        e.pack(padx=24, pady=12, ipady=6)
        def fim(v): res["v"] = v; top.destroy()
        e.bind("<Return>", lambda ev: fim(e.get())); top.bind("<Escape>", lambda ev: fim(None))
        tk.Button(top, text="SALVAR", command=lambda: fim(e.get()), bg=t["bg"], fg=t["br"], activebackground=t["br"],
                  activeforeground=t["bg"], bd=1, relief="solid", font=(self.fam, 10, "bold"), padx=16, pady=4).pack(pady=(0, 16))
        self._centralizar(top); e.focus_set()
        self.root.wait_window(top)
        if res["v"]:
            msg = core.trocar_chave(res["v"]); self._con(msg + "\n", "ok" if msg.startswith("✅") else "err")
        else: self._con("chave não trocada\n", "warn")

    # ═══════ HISTÓRICO DO CAMPO, FECHAR ═══════
    def _hist_up(self, ev):
        if not self.cmd_hist: return "break"
        if self.cmd_idx == -1: self.cmd_idx = len(self.cmd_hist)
        self.cmd_idx = max(0, self.cmd_idx - 1)
        self.ent.delete(0, "end"); self.ent.insert(0, self.cmd_hist[self.cmd_idx]); return "break"

    def _hist_down(self, ev):
        if self.cmd_idx == -1: return "break"
        self.cmd_idx += 1; self.ent.delete(0, "end")
        if self.cmd_idx < len(self.cmd_hist): self.ent.insert(0, self.cmd_hist[self.cmd_idx])
        else: self.cmd_idx = -1
        return "break"

    def _fechar(self):
        self.voz.parar()
        if self.gravando:
            try: self.mic.parar()
            except Exception: pass
        self.root.destroy()

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    HUD().run()
