"""
Aiden STARK — interface HUD estilo Stark/Jarvis, toda em azul.

  • Reator central com a esfera 3D girando (acelera quando o Aiden pensa)
  • Monitores ao vivo: CPU, RAM, disco, bateria, rede, relógio e calendário do mês
  • Lista de tarefas do Aiden, console de conversa, atalhos de apps, pedidos de permissão no HUD
  • MODO DESENVOLVEDOR (Ctrl+D): só código. O HUD troca os painéis por PROJETO e GIT,
    liga o raciocínio de engenheiro sênior e libera os botões TESTES e REVISAR
  • Plan mode, pasta do projeto, modos Verbose/Normal/Summary

Roda sozinho: python aiden_stark.py
Atalhos: Ctrl+D dev · Ctrl+P plan · Ctrl+L limpa a conversa · F11 tela cheia
"""

import sys, os, time, math, json, queue, threading, calendar, random
from collections import deque
from pathlib import Path
import tkinter as tk
import tkinter.font as tkfont
from tkinter import filedialog, simpledialog

sys.path.insert(0, str(Path(__file__).parent))
import aiden_core as core
core.INTERATIVO = False        # nunca chama input() (pythonw não tem terminal)
core.set_plain()
core.instalar_se_faltar({"psutil": "psutil"})
import psutil
try:
    import aiden_pc
except Exception as e:
    print(f"erro importando aiden_pc: {e}"); sys.exit(1)

CFG = Path.home() / ".aiden_stark.json"

# ═══════ PALETA (azul) ═══════
BG = "#030a12"; PAINEL = "#04121f"
AZ = "#3fb6ff"; AZ2 = "#1f86d6"; AZ3 = "#12507f"; AZ4 = "#0a2b47"; AZ5 = "#071b2d"
BR = "#c8ecff"; VERM = "#ff5a6a"; AMB = "#ffc857"; VERDE = "#5fe3a8"

MESES = ["JAN", "FEV", "MAR", "ABR", "MAI", "JUN", "JUL", "AGO", "SET", "OUT", "NOV", "DEZ"]
DIAS = ["SEGUNDA", "TERÇA", "QUARTA", "QUINTA", "SEXTA", "SÁBADO", "DOMINGO"]
DIAS_CURTO = ["SEG", "TER", "QUA", "QUI", "SEX", "SÁB", "DOM"]

PROMPT_TESTES = ("Descubra como testar este projeto (comece pelo mapa_projeto) e rode os testes. "
                 "Se algo falhar, investigue a causa, corrija com edições mínimas e rode de novo.")
PROMPT_REVISAR = ("Revise as mudanças atuais do git (git_info com diff_completo): procure bugs, casos de borda, "
                  "erros de lógica e problemas de estilo. Liste o que corrigir, por prioridade.")


def hexa(r, g, b):
    return "#%02x%02x%02x" % (int(max(0, min(255, r))), int(max(0, min(255, g))), int(max(0, min(255, b))))


def dev_ligado():
    return core.ESTADO["dev"]


# ═══════ ESFERA 3D (a mesma do ESP32) ═══════
class Esfera:
    N = 72

    def __init__(self):
        ouro = math.pi * (3 - math.sqrt(5))
        self.nos = []
        for i in range(self.N):
            y = 1 - 2 * (i + 0.5) / self.N
            r = math.sqrt(1 - y * y)
            self.nos.append((math.cos(ouro * i) * r, y, math.sin(ouro * i) * r))
        self.arestas = []
        vistos = set()
        for i in range(self.N):
            viz = sorted(range(self.N), key=lambda j: sum((self.nos[i][k] - self.nos[j][k]) ** 2 for k in range(3)))[1:4]
            for j in viz:
                par = (min(i, j), max(i, j))
                if par not in vistos:
                    vistos.add(par); self.arestas.append(par)

    def desenha(self, c, cx, cy, raio, ang_y, ang_x, tag="d"):
        sy, cyy, sx, cxx = math.sin(ang_y), math.cos(ang_y), math.sin(ang_x), math.cos(ang_x)
        pts = []
        for x0, y0, z0 in self.nos:
            x = x0 * cyy + z0 * sy
            z = -x0 * sy + z0 * cyy
            y = y0 * cxx - z * sx
            z = y0 * sx + z * cxx
            pts.append((cx + x * raio, cy + y * raio, z))
        for passo in (0, 1):                      # 0 = trás, 1 = frente
            for a, b in self.arestas:
                z = (pts[a][2] + pts[b][2]) / 2
                if (z >= 0) != (passo == 1): continue
                cor = hexa(30 + 60 * z, 110 + 80 * z, 185 + 70 * z) if z >= 0 else "#0c2842"
                c.create_line(pts[a][0], pts[a][1], pts[b][0], pts[b][1], fill=cor, tags=tag)
        for x, y, z in pts:
            if z > -0.1:
                r = 2 if z > 0.5 else 1
                c.create_oval(x - r, y - r, x + r, y + r, fill=AZ, outline="", tags=tag)


class SaidaFila(core.Saida):
    """Troca os print do núcleo por eventos consumidos na thread principal."""
    def __init__(self, q): self.q = q
    def pensando(self, on, msg="pensando"): self.q.put(("pensando", on))
    def texto(self, s): self.q.put(("texto", s))
    def texto_fim(self): self.q.put(("texto_fim", None))
    def chamada(self, nome, args): self.q.put(("chamada", (nome, args)))
    def resultado(self, res): self.q.put(("resultado", res))
    def aviso(self, m): self.q.put(("aviso", m))
    def erro(self, m): self.q.put(("erro", m))
    def info(self, m): self.q.put(("info", m))


class AidenStark:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("AIDEN · STARK")
        self.root.geometry("1280x800")
        self.root.minsize(1000, 640)
        self.root.configure(bg=BG)
        self.root.protocol("WM_DELETE_WINDOW", self._fechar)
        fams = set(tkfont.families())
        self.fam = next((f for f in ("Consolas", "Cascadia Mono", "DejaVu Sans Mono", "Courier New") if f in fams), "Courier")

        self.queue = queue.Queue()
        core.set_saida(SaidaFila(self.queue))
        core.set_confirm_handler(self._confirmar_da_thread)

        cfg = self._ler_cfg()
        self.atalhos = cfg.get("atalhos") or ["chrome", "code", "explorer", "calc"]
        self.modo = cfg.get("modo", "Normal")
        if cfg.get("pasta") and Path(cfg["pasta"]).is_dir(): aiden_pc.PASTA["atual"] = cfg["pasta"]

        mem = core.carregar_mem()
        self.hist = [{"role": "system", "content": aiden_pc.agente.build_sys()}] + mem
        self.processando = False
        self.pensando = False
        self.confirm = None            # {"texto", "ev", "box"}
        self.ai_aberto = False
        self._cmd_hist = []; self._cmd_idx = -1
        self._fullscreen = False

        # monitores
        self.cpu = 0.0; self.ram = 0.0; self.ram_txt = ""
        self.disco = 0.0; self.disco_txt = ""; self.bateria = None
        self.net_down = deque([0.0] * 60, maxlen=60); self.net_up = 0.0; self.net_down_v = 0.0
        self._net_ant = None; self.boot = psutil.boot_time()
        self.todos = []
        self.git = {"tem": False, "branch": "-", "mudancas": [], "ultimo": ""}
        self.projeto = []
        self._git_busy = False
        psutil.cpu_percent(None)

        self.esfera = Esfera()
        self.particulas = [[random.random(), random.random(), random.uniform(0.004, 0.02)] for _ in range(36)]
        self.ang = 0.0; self.vel = 1.0; self._ult = time.time()
        self._geom = (1.0, 0.0, 0.0); self._geom_widgets = None
        self.acoes = {}

        self._build()
        self._banner()
        self._stats()
        self._git_loop()
        self.root.after(33, self._frame)
        self.root.after(40, self._poll)
        if core.cliente is None:
            self.root.after(600, self._pedir_chave)

    # ═══════ CONFIG ═══════
    def _ler_cfg(self):
        try: return json.loads(CFG.read_text(encoding="utf-8"))
        except Exception: return {}

    def _salvar_cfg(self):
        try:
            CFG.write_text(json.dumps({"atalhos": self.atalhos, "modo": self.modo,
                                       "pasta": aiden_pc.PASTA["atual"]}, indent=2), encoding="utf-8")
        except Exception: pass

    # ═══════ CONSTRUÇÃO ═══════
    def _build(self):
        self.c = tk.Canvas(self.root, bg=BG, highlightthickness=0, cursor="arrow")
        self.c.pack(fill="both", expand=True)

        self.txt = tk.Text(self.c, wrap="word", bg=PAINEL, fg="#cfe9ff", insertbackground=AZ,
                           font=(self.fam, 10), bd=0, highlightthickness=0, padx=8, pady=6, spacing3=2,
                           state="disabled")
        for tag, cor in (("user", "#7fd0ff"), ("ai", "#d8ecff"), ("tool", AZ), ("dim", "#4a7fa8"),
                         ("ok", VERDE), ("err", VERM), ("warn", AMB)):
            self.txt.tag_config(tag, foreground=cor)
        self.txt.tag_config("user", font=(self.fam, 10, "bold"))
        self.w_txt = self.c.create_window(0, 0, window=self.txt, anchor="nw")

        self.entry = tk.Entry(self.c, bg=PAINEL, fg=BR, insertbackground=AZ, relief="flat",
                              font=(self.fam, 11), highlightthickness=1,
                              highlightbackground=AZ3, highlightcolor=AZ)
        self.w_entry = self.c.create_window(0, 0, window=self.entry, anchor="nw")
        self.entry.bind("<Return>", lambda e: self._enviar())
        self.entry.bind("<Up>", self._hist_up)
        self.entry.bind("<Down>", self._hist_down)
        self.entry.bind("<Key>", self._tecla_confirm)
        self.entry.focus_set()

        self.c.tag_bind("btn", "<Button-1>", self._clique)
        self.c.tag_bind("btn", "<Enter>", lambda e: self.c.config(cursor="hand2"))
        self.c.tag_bind("btn", "<Leave>", lambda e: self.c.config(cursor="arrow"))
        self.root.bind_all("<Key>", self._tecla_confirm, add="+")
        for seq, chave in (("<Control-d>", "dev"), ("<Control-p>", "plan"), ("<Control-l>", "limpar"), ("<F11>", "tela_cheia")):
            self.root.bind_all(seq, lambda e, ch=chave: (self._acao(ch), "break")[1])

        self.acoes = {
            "dev": self._toggle_dev, "plan": self._toggle_plan, "modo": self._ciclar_modo,
            "pasta": self._escolher_pasta, "limpar": self._limpar, "ajuda": lambda: self._cmd("/ajuda"),
            "tela_cheia": self._tela_cheia, "novo_atalho": self._novo_atalho,
            "testes": lambda: self._pedido_dev(PROMPT_TESTES), "revisar": lambda: self._pedido_dev(PROMPT_REVISAR),
            "sim": lambda: self._responder_confirm(True), "nao": lambda: self._responder_confirm(False),
        }

    # ═══════ DESENHO: UTILITÁRIOS ═══════
    def _X(self, x): return self._geom[1] + x * self._geom[0]
    def _Y(self, y): return self._geom[2] + y * self._geom[0]
    def _S(self, v): return v * self._geom[0]

    def _t(self, x, y, texto, tam=10, cor=AZ, ancora="w", bold=False, largura=None):
        k = self._geom[0]
        opts = dict(text=texto, fill=cor, anchor=ancora, tags="d",
                    font=(self.fam, max(6, int(tam * k)), "bold" if bold else "normal"))
        if largura: opts["width"] = largura * k
        self.c.create_text(self._X(x), self._Y(y), **opts)

    def _arco(self, cx, cy, r, ini, ext, cor, w=2, dash=None):
        X, Y, S = self._X, self._Y, self._S
        self.c.create_arc(X(cx) - S(r), Y(cy) - S(r), X(cx) + S(r), Y(cy) + S(r), start=ini, extent=ext,
                          style="arc", outline=cor, width=max(1, S(w)), dash=dash, tags="d")

    def _circulo(self, cx, cy, r, cor, w=1, fill="", dash=None):
        X, Y, S = self._X, self._Y, self._S
        self.c.create_oval(X(cx) - S(r), Y(cy) - S(r), X(cx) + S(r), Y(cy) + S(r), outline=cor,
                           width=max(1, S(w)), fill=fill, dash=dash, tags="d")

    def _linha(self, x0, y0, x1, y1, cor=AZ3, w=1, dash=None):
        self.c.create_line(self._X(x0), self._Y(y0), self._X(x1), self._Y(y1), fill=cor,
                           width=max(1, self._S(w)), dash=dash, tags="d")

    def _ret(self, x, y, w, h, cor=AZ3, fill="", ww=1):
        self.c.create_rectangle(self._X(x), self._Y(y), self._X(x + w), self._Y(y + h), outline=cor,
                                fill=fill, width=max(1, self._S(ww)), tags="d")

    def _anel(self, cx, cy, r, frac, titulo, sub="", cor=AZ, valor=None):
        frac = max(0.0, min(1.0, frac))
        if frac > 0.85: cor = VERM
        self._circulo(cx, cy, r, AZ4, w=7)
        if frac > 0.005:
            self._arco(cx, cy, r, 90, -359.0 * frac, AZ3, w=11)       # brilho
            self._arco(cx, cy, r, 90, -359.0 * frac, cor, w=6)
        self._circulo(cx, cy, r - 9, AZ4, w=1)
        self._t(cx, cy - 10, titulo, 9, AZ2, "center")
        if valor is not None: self._t(cx, cy + 8, valor, 14, BR, "center", True)
        if sub: self._t(cx, cy + r + 12, sub, 8, AZ2, "center")

    def _painel(self, x, y, w, h, titulo):
        self._ret(x, y, w, h, AZ4, AZ5)
        for dx, dy, sx, sy in ((0, 0, 1, 1), (w, 0, -1, 1), (0, h, 1, -1), (w, h, -1, -1)):
            self._linha(x + dx, y + dy, x + dx + 14 * sx, y + dy, AZ, 2)
            self._linha(x + dx, y + dy, x + dx, y + dy + 14 * sy, AZ, 2)
        self._ret(x + 10, y + 11, 6, 6, AZ, AZ)
        self._t(x + 24, y + 14, titulo, 9, AZ2, "w", True)

    def _botao(self, x, y, w, h, texto, chave, ativo=False, cor=AZ):
        self.c.create_rectangle(self._X(x), self._Y(y), self._X(x + w), self._Y(y + h),
                                outline=cor if ativo else AZ3, fill=cor if ativo else AZ5, width=1,
                                tags=("d", "btn", f"b_{chave}"))
        self.c.create_text(self._X(x + w / 2), self._Y(y + h / 2), text=texto,
                           fill="#02101c" if ativo else (BR if cor == AZ else cor), anchor="center",
                           font=(self.fam, max(6, int(9 * self._geom[0])), "bold"), tags=("d", "btn", f"b_{chave}"))

    # ═══════ QUADRO ═══════
    def _frame(self):
        c = self.c
        W = max(c.winfo_width(), 400); H = max(c.winfo_height(), 300)
        k = min(W / 1280, H / 800)
        self._geom = (k, (W - 1280 * k) / 2, (H - 800 * k) / 2)
        agora = time.time(); dt = min(0.1, agora - self._ult); self._ult = agora
        alvo = 5.0 if (self.pensando or self.processando) else 1.0
        self.vel += (alvo - self.vel) * min(1.0, dt * 3)
        self.ang += dt * self.vel
        c.delete("d")
        try:
            self._fundo(dt)
            self._dias()
            self._col_esq()
            self._reator()
            self._col_dir()
            self._baixo()
            if self.confirm is not None: self._overlay()
        except Exception as e:                       # um erro de desenho nunca derruba o HUD
            core.dbg(f"frame: {e}")
        self._posicionar()
        c.tag_raise("btn")
        self.root.after(33, self._frame)

    def _fundo(self, dt):
        X, Y = self._X, self._Y
        for p in self.particulas:                     # poeira de luz flutuando
            p[1] -= p[2] * dt * 6
            if p[1] < 0: p[1] = 1.0; p[0] = random.random()
            x = X(p[0] * 1280); y = Y(p[1] * 800)
            self.c.create_oval(x - 1, y - 1, x + 1, y + 1, fill=AZ3, outline="", tags="d")
        for pts in (((60, 60), (60, 170), (30, 200), (30, 500)), ((1220, 70), (1220, 170), (1250, 200), (1250, 500)),
                    ((310, 120), (420, 120), (450, 150), (520, 150)), ((960, 120), (860, 120), (830, 150), (760, 150))):
            self.c.create_line(*[v for p in pts for v in (X(p[0]), Y(p[1]))], fill=AZ5, width=1, tags="d")
        self._ret(4, 4, 1272, 792, AZ5)

    def _dias(self):
        t = time.localtime()
        n = calendar.monthrange(t.tm_year, t.tm_mon)[1]
        for d in range(1, n + 1):
            x = 40 + (d - 1) * (1200 / (n - 1))
            if d == t.tm_mday:
                self._ret(x - 14, 8, 28, 20, AZ, AZ)
                self._t(x, 18, f"{d:02d}", 10, "#02101c", "center", True)
            else:
                self._t(x, 18, f"{d:02d}", 9, AZ3, "center")
        self._t(640, 42, f"{DIAS[t.tm_wday]} · {MESES[t.tm_mon - 1]} {t.tm_year}", 9, AZ2, "center")

    def _col_esq(self):
        t = time.localtime(); n = calendar.monthrange(t.tm_year, t.tm_mon)[1]
        self._circulo(120, 125, 66, AZ4, w=1)
        self._anel(120, 125, 56, t.tm_mday / n, MESES[t.tm_mon - 1], DIAS[t.tm_wday], AZ, f"{t.tm_mday:02d}")
        self._anel(120, 272, 46, self.cpu / 100, "CPU", f"{psutil.cpu_count()} núcleos", AZ, f"{self.cpu:.0f}%")
        self._anel(120, 398, 46, self.ram / 100, "RAM", self.ram_txt, AZ, f"{self.ram:.0f}%")
        self._t(30, 470, "DISCO", 8, AZ2, "w", True)
        self._ret(30, 480, 180, 8, AZ3)
        cor = AZ if self.disco < 90 else VERM
        self._ret(30, 480, 180 * min(1, self.disco / 100), 8, cor, cor)
        self._t(30, 498, self.disco_txt, 8, AZ2)

    def _reator(self):
        cx, cy = 640, 268
        a = self.ang
        self._circulo(cx, cy, 190, AZ4, w=1)
        for i in range(60):
            ang = math.radians(i * 6 + a * 4)
            r0 = 186 if i % 5 else 178
            self._linha(cx + 190 * math.cos(ang), cy + 190 * math.sin(ang),
                        cx + r0 * math.cos(ang), cy + r0 * math.sin(ang), AZ3 if i % 5 else AZ2)
        v = a * 40
        for i, (r, comp, w, cor) in enumerate(((168, 80, 7, AZ), (168, 50, 7, AZ2), (150, 110, 4, AZ2), (132, 140, 3, AZ3))):
            n = 3 if i < 2 else 2
            for j in range(n):
                base = v * (1 if i % 2 == 0 else -1.3) + j * (360 / n)
                self._arco(cx, cy, r, base, comp, AZ4, w=w + 4)
                self._arco(cx, cy, r, base, comp, cor, w=w)
        self._circulo(cx, cy, 116, AZ3, w=1, dash=(3, 5))
        if dev_ligado(): self._arco(cx, cy, 178, 100, 40, VERM, w=4)      # arco vermelho do modo dev
        if core.ESTADO["plan"]: self._circulo(cx, cy, 124, AMB, w=1, dash=(8, 6))
        k = self._geom[0]
        self.esfera.desenha(self.c, self._X(cx), self._Y(cy), 98 * k, a * 0.9, 0.35 + 0.12 * math.sin(a * 0.6))
        pulso = 4 + 2 * math.sin(a * 3)
        self._circulo(cx, cy, pulso + 4, AZ3, w=1)
        self._circulo(cx, cy, pulso, BR, fill=BR)
        if self.confirm is not None: st, cor = "AGUARDANDO PERMISSÃO", AMB
        elif self.processando: st, cor = "PENSANDO…", AZ
        else: st, cor = "PRONTO", VERDE
        self._t(cx, cy + 210, st, 12, cor, "center", True)
        extras = (["‹/› DEV"] if dev_ligado() else []) + (["PLAN"] if core.ESTADO["plan"] else [])
        self._t(cx, cy + 228, " · ".join(extras + [core.ESTADO["modelo"]]), 9, AZ2, "center")

    def _col_dir(self):
        t = time.localtime(); seg = t.tm_sec + (time.time() % 1)
        self._anel(1160, 125, 56, seg / 60, DIAS_CURTO[t.tm_wday], "", AZ, f"{t.tm_hour:02d}:{t.tm_min:02d}")
        self._t(1160, 150, f":{t.tm_sec:02d}", 9, AZ2, "center")
        if dev_ligado():
            self._painel_git()
        else:
            self._painel(1010, 205, 240, 100, "REDE")
            mx = max(max(self.net_down), 1.0)
            pts = []
            for i, v in enumerate(self.net_down):
                pts += [self._X(1022 + i * (216 / 59)), self._Y(268 - 40 * v / mx)]
            self.c.create_line(*pts, fill=AZ, width=max(1, int(self._S(1.5))), tags="d")
            self._t(1022, 240, f"↓ {self._fmt_rate(self.net_down_v)}", 9, BR)
            self._t(1130, 240, f"↑ {self._fmt_rate(self.net_up)}", 9, AZ2)
            up = int(time.time() - self.boot)
            self._t(1022, 290, f"ligado há {up // 86400}d {up % 86400 // 3600}h {up % 3600 // 60}min", 8, AZ2)
        self._painel(1010, 320, 240, 180, "TAREFAS")
        if not self.todos: self._t(1024, 355, "(nenhuma)", 9, AZ3)
        icone = {"pending": "○", "in_progress": "◐", "completed": "●"}
        cor = {"pending": AZ2, "in_progress": AMB, "completed": VERDE}
        for i, tarefa in enumerate(self.todos[:7]):
            self._t(1024, 346 + i * 21, f"{icone.get(tarefa['status'], '○')} {tarefa['content'][:27]}", 9,
                    cor.get(tarefa["status"], AZ2))
        if self.bateria is not None:
            self._t(1010, 512, f"BATERIA {self.bateria[0]:.0f}% {'⚡' if self.bateria[1] else ''}", 8, AZ2, "w", True)

    def _painel_git(self):
        self._painel(1010, 205, 240, 100, "GIT")
        g = self.git
        if not aiden_pc.PASTA["atual"]:
            self._t(1022, 245, "defina a PASTA do projeto", 9, AZ3); return
        if not g["tem"]:
            self._t(1022, 245, "(sem repositório git)", 9, AZ3); return
        self._t(1022, 236, f"⎇ {g['branch'][:24]}", 10, BR, "w", True)
        n = len(g["mudancas"])
        self._t(1022, 254, "limpo, sem alterações" if n == 0 else f"{n} alteraç{'ão' if n == 1 else 'ões'}", 9,
                VERDE if n == 0 else AMB)
        for i, l in enumerate(g["mudancas"][:2]):
            self._t(1022, 270 + i * 14, l[:30], 8, AZ2)
        if g["ultimo"]: self._t(1022, 296, g["ultimo"][:32], 8, AZ3)

    def _baixo(self):
        dev = dev_ligado()
        if dev:
            nome = Path(aiden_pc.PASTA["atual"]).name if aiden_pc.PASTA["atual"] else "sem pasta"
            self._painel(20, 520, 270, 252, f"PROJETO · {nome[:16]}")
            if not self.projeto: self._t(32, 556, "use o botão PASTA →", 9, AZ3)
            for i, (eh_dir, nm) in enumerate(self.projeto[:10]):
                self._t(34, 552 + i * 20, ("▸ " if eh_dir else "· ") + nm[:30], 9, AZ if eh_dir else AZ2)
        else:
            self._painel(20, 520, 270, 252, "ATALHOS")
            for i, nome in enumerate(self.atalhos[:5]):
                self._botao(32, 548 + i * 36, 246, 30, nome.upper(), f"atalho:{nome}")
            self._botao(32, 548 + min(5, len(self.atalhos)) * 36, 246, 26, "+ NOVO ATALHO", "novo_atalho")
        self._painel(305, 520, 670, 252, f"CONSOLE · {'DEV' if dev else self.modo.upper()}")
        self._t(316, 752, "dev›" if dev else "›", 11, AZ, "w", True)
        self._painel(990, 520, 270, 252, "CONTROLES")
        self._botao(1002, 548, 122, 30, "‹/› DEV", "dev", dev, VERM)
        self._botao(1136, 548, 112, 30, "PLAN", "plan", core.ESTADO["plan"], AMB)
        self._botao(1002, 586, 246, 30, f"MODO · {self.modo.upper()}", "modo")
        pasta = aiden_pc.PASTA["atual"]
        self._botao(1002, 624, 246, 30, "PASTA · " + (Path(pasta).name[:18] if pasta else "nenhuma"), "pasta", bool(pasta))
        self._botao(1002, 662, 122, 28, "LIMPAR", "limpar")
        self._botao(1136, 662, 112, 28, "AJUDA", "ajuda")
        if dev:
            self._botao(1002, 698, 122, 28, "TESTES", "testes", False, VERDE)
            self._botao(1136, 698, 112, 28, "REVISAR", "revisar", False, AMB)
            self._t(1002, 744, f"GROQ · {'ok' if core.cliente else 'sem chave (/chave)'}", 8, VERDE if core.cliente else VERM)
            self._t(1002, 760, "Ctrl+D sai do modo dev", 8, AZ3)
        else:
            self._t(1002, 706, f"GROQ · {'ok' if core.cliente else 'sem chave (/chave)'}", 8, VERDE if core.cliente else VERM)
            self._t(1002, 724, "Ctrl+D dev · Ctrl+P plan", 8, AZ3)
            self._t(1002, 740, "Ctrl+L limpa · F11 tela cheia", 8, AZ3)

    def _overlay(self):
        x, y, w, h = 340, 290, 600, 230
        self._ret(x, y, w, h, AMB, "#050f1a", 2)
        self._t(x + 20, y + 22, "AIDEN PEDE PERMISSÃO", 12, AMB, "w", True)
        self._t(x + 20, y + 52, self.confirm["texto"][:420], 10, BR, "nw", largura=w - 40)
        self._botao(x + 150, y + h - 56, 130, 34, "SIM (S)", "sim", True, VERDE)
        self._botao(x + 320, y + h - 56, 130, 34, "NÃO (N)", "nao", False, VERM)

    def _posicionar(self):
        g = self._geom
        if g == self._geom_widgets: return
        self._geom_widgets = g
        k, ox, oy = g
        self.c.coords(self.w_txt, ox + 315 * k, oy + 546 * k)
        self.c.itemconfigure(self.w_txt, width=int(650 * k), height=int(184 * k))
        self.c.coords(self.w_entry, ox + 334 * k, oy + 739 * k)
        self.c.itemconfigure(self.w_entry, width=int(632 * k), height=int(26 * k))

    # ═══════ MONITORES, GIT E PROJETO ═══════
    @staticmethod
    def _fmt_rate(b):
        for u in ("B/s", "KB/s", "MB/s"):
            if b < 1024: return f"{b:.0f} {u}"
            b /= 1024
        return f"{b:.1f} GB/s"

    def _stats(self):
        try:
            self.cpu = psutil.cpu_percent(None)
            vm = psutil.virtual_memory(); self.ram = vm.percent
            self.ram_txt = f"{vm.used / 2**30:.1f}/{vm.total / 2**30:.1f} GB"
            du = psutil.disk_usage(Path.home().anchor or "/")
            self.disco = du.percent; self.disco_txt = f"{du.free / 2**30:.0f} GB livres de {du.total / 2**30:.0f}"
            b = psutil.sensors_battery() if hasattr(psutil, "sensors_battery") else None
            self.bateria = (b.percent, b.power_plugged) if b else None
            n = psutil.net_io_counters()
            if self._net_ant:
                self.net_down_v = max(0, n.bytes_recv - self._net_ant.bytes_recv)
                self.net_up = max(0, n.bytes_sent - self._net_ant.bytes_sent)
                self.net_down.append(self.net_down_v)
            self._net_ant = n
            self.todos = core._carregar_todos()
            self._listar_projeto()
        except Exception as e:
            core.dbg(f"stats: {e}")
        self.root.after(1000, self._stats)

    def _listar_projeto(self):
        pasta = aiden_pc.PASTA["atual"]
        if not dev_ligado() or not pasta: self.projeto = []; return
        try:
            base = Path(pasta)
            itens = sorted(base.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
            self.projeto = [(p.is_dir(), p.name) for p in itens if not aiden_pc._ignorado(p, base)][:10]
        except Exception: self.projeto = []

    def _git_loop(self):
        """Atualiza o painel GIT em segundo plano (só no modo dev)."""
        if dev_ligado() and aiden_pc.PASTA["atual"] and not self._git_busy:
            self._git_busy = True
            threading.Thread(target=self._git_thread, daemon=True).start()
        self.root.after(4000, self._git_loop)

    def _git_thread(self):
        try:
            st = aiden_pc.git_info("status")
            if st.startswith(("❌", "fatal", "Erro")) or not st.startswith("##"):
                self.git = {"tem": False, "branch": "-", "mudancas": [], "ultimo": ""}; return
            linhas = st.splitlines()
            branch = linhas[0][3:].split("...")[0]
            log = aiden_pc.git_info("log")
            self.git = {"tem": True, "branch": branch, "mudancas": [l.strip() for l in linhas[1:]],
                        "ultimo": log.splitlines()[0] if log and not log.startswith("(") else ""}
        except Exception as e:
            core.dbg(f"git: {e}")
        finally:
            self._git_busy = False

    # ═══════ CONSOLE ═══════
    def _escrever(self, texto, tag="ai"):
        self.txt.configure(state="normal")
        self.txt.insert("end", texto, tag)
        self.txt.see("end")
        self.txt.configure(state="disabled")

    def _banner(self):
        self._escrever("AIDEN · STARK\n", "user")
        self._escrever(f"modelo: {core.ESTADO['modelo']}   ·   /ajuda pra comandos   ·   Ctrl+D pro modo desenvolvedor\n", "dim")
        n = len(self.hist) - 1
        if n > 0: self._escrever(f"lembrei de {n} mensagens\n", "dim")
        if aiden_pc.PASTA["atual"]: self._escrever(f"pasta do projeto: {aiden_pc.PASTA['atual']}\n", "dim")
        self._escrever("\n", "dim")

    # ═══════ AÇÕES ═══════
    def _clique(self, ev):
        for tag in self.c.gettags("current"):
            if tag.startswith("b_"): self._acao(tag[2:]); return

    def _acao(self, chave):
        if chave.startswith("atalho:"):
            nome = chave.split(":", 1)[1]
            def run():
                self.queue.put(("info", f"atalho {nome}: {aiden_pc.abrir_app(nome)}"))
            threading.Thread(target=run, daemon=True).start(); return
        fn = self.acoes.get(chave)
        if fn: fn()

    def _toggle_dev(self):
        self._escrever(aiden_pc.agente.comando("/dev") + "\n", "warn")
        self._listar_projeto()

    def _toggle_plan(self): self._escrever(aiden_pc.agente.comando("/plan") + "\n", "warn")

    def _ciclar_modo(self):
        ordem = ["Verbose", "Normal", "Summary"]
        self.modo = ordem[(ordem.index(self.modo) + 1) % 3]
        self._salvar_cfg()

    def _escolher_pasta(self):
        ini = aiden_pc.PASTA["atual"] or str(Path.home())
        p = filedialog.askdirectory(initialdir=ini, title="Pasta do projeto")
        if p:
            self._escrever(aiden_pc.definir_pasta(p) + "\n", "ok"); self._salvar_cfg(); self._listar_projeto()

    def _novo_atalho(self):
        nome = simpledialog.askstring("Novo atalho", "Nome do app (ex: chrome, code, spotify):", parent=self.root)
        if nome and nome.strip() and nome.strip() not in self.atalhos:
            self.atalhos.append(nome.strip()); self._salvar_cfg()

    def _limpar(self):
        self.hist = [{"role": "system", "content": aiden_pc.agente.build_sys()}]
        self.txt.configure(state="normal"); self.txt.delete("1.0", "end"); self.txt.configure(state="disabled")
        self._escrever("conversa limpa\n\n", "dim")

    def _tela_cheia(self):
        self._fullscreen = not self._fullscreen
        self.root.attributes("-fullscreen", self._fullscreen)

    def _pedir_chave(self):
        nova = simpledialog.askstring("Chave da Groq", "Cola a chave da Groq:\n(pega em https://console.groq.com/keys)",
                                      parent=self.root, show="*")
        if not nova: self._escrever("chave não trocada\n", "warn"); return
        msg = core.trocar_chave(nova)
        self._escrever(msg + "\n", "ok" if msg.startswith("✅") else "err")

    def _pedido_dev(self, prompt):
        """Botões TESTES e REVISAR (só no modo dev e com pasta definida)."""
        if not dev_ligado(): self._escrever("liga o modo dev antes (Ctrl+D)\n", "warn"); return
        if not aiden_pc.PASTA["atual"]: self._escrever("defina a PASTA do projeto primeiro\n", "warn"); return
        self._mandar(prompt)

    # ═══════ ENVIO ═══════
    def _enviar(self):
        if self.confirm is not None: return
        txt = self.entry.get().strip()
        if not txt: return
        if self.processando and not txt.startswith("/"): return
        self.entry.delete(0, "end")
        self._cmd_hist.append(txt); self._cmd_idx = -1
        self._mandar(txt)

    def _mandar(self, txt):
        if self.processando and not txt.startswith("/"): return
        self._escrever(f"\n› {txt}\n", "user")
        if txt.startswith("/"): self._cmd(txt); return
        self.processando = True
        threading.Thread(target=self._rodar, args=(txt,), daemon=True).start()

    def _rodar(self, pergunta):
        try:
            self.hist = aiden_pc.agente.rodar(pergunta, self.hist)
        except Exception as e:
            self.queue.put(("erro", str(e)))
        finally:
            self.queue.put(("fim", None))

    def _cmd(self, cmd):
        partes = cmd.split(); c = partes[0].lower()
        if c in ("/sair", "/quit", "/exit"): self._fechar(); return
        if c == "/limpar": self._limpar(); return
        if c == "/esquecer": core.esquecer_mem(); self._escrever("histórico salvo apagado\n", "ok"); return
        if c == "/chave": self._pedir_chave(); return
        if c == "/stark": self._escrever("você já está no STARK\n", "dim"); return
        if c == "/modo":
            alvo = (partes[1].capitalize() if len(partes) > 1 else "")
            if alvo in ("Verbose", "Normal", "Summary"): self.modo = alvo; self._salvar_cfg()
            self._escrever(f"modo: {self.modo}\n", "dim"); return
        if c == "/atalho":
            if len(partes) >= 3 and partes[1] == "add":
                self.atalhos.append(" ".join(partes[2:])); self._salvar_cfg(); self._escrever("atalho criado\n", "ok")
            elif len(partes) >= 3 and partes[1] == "del":
                nome = " ".join(partes[2:])
                if nome in self.atalhos: self.atalhos.remove(nome); self._salvar_cfg(); self._escrever("atalho removido\n", "ok")
                else: self._escrever("não achei esse atalho\n", "warn")
            else: self._escrever("uso: /atalho add NOME  ·  /atalho del NOME\n", "dim")
            return
        txt = aiden_pc.agente.comando(cmd)
        if c == "/pasta": self._salvar_cfg(); self._listar_projeto()
        self._escrever((txt if txt is not None else f"comando desconhecido: {cmd}") + "\n", "dim" if txt is not None else "warn")

    def _hist_up(self, ev):
        if not self._cmd_hist: return "break"
        if self._cmd_idx == -1: self._cmd_idx = len(self._cmd_hist)
        self._cmd_idx = max(0, self._cmd_idx - 1)
        self.entry.delete(0, "end"); self.entry.insert(0, self._cmd_hist[self._cmd_idx])
        return "break"

    def _hist_down(self, ev):
        if self._cmd_idx == -1: return "break"
        self._cmd_idx += 1
        self.entry.delete(0, "end")
        if self._cmd_idx < len(self._cmd_hist): self.entry.insert(0, self._cmd_hist[self._cmd_idx])
        else: self._cmd_idx = -1
        return "break"

    # ═══════ PERMISSÃO ═══════
    def _confirmar_da_thread(self, desc):
        req = {"texto": desc, "ev": threading.Event(), "box": {"ok": False}}
        self.queue.put(("confirmar", req))
        req["ev"].wait(timeout=180)
        return req["box"]["ok"]

    def _responder_confirm(self, ok):
        if self.confirm is None: return
        self.confirm["box"]["ok"] = ok
        self.confirm["ev"].set()
        self._escrever(f"permissão {'concedida' if ok else 'negada'}\n", "ok" if ok else "warn")
        self.confirm = None

    def _tecla_confirm(self, ev):
        """Com o pedido de permissão aberto, S/Enter aceita e N/Esc recusa (e não digita)."""
        if self.confirm is None: return None
        k = (ev.keysym or "").lower()
        if k in ("s", "y", "return"): self._responder_confirm(True)
        elif k in ("n", "escape"): self._responder_confirm(False)
        return "break"

    # ═══════ FILA DE EVENTOS ═══════
    def _poll(self):
        try:
            while True:
                tipo, dado = self.queue.get_nowait()
                if tipo == "pensando": self.pensando = dado
                elif tipo == "texto":
                    if not self.ai_aberto: self.ai_aberto = True; self._escrever("● ", "tool")
                    self._escrever(dado, "ai")
                elif tipo == "texto_fim":
                    if self.ai_aberto: self.ai_aberto = False; self._escrever("\n", "ai")
                elif tipo == "chamada":
                    nome, args = dado
                    if self.modo != "Summary": self._escrever(f"  ▸ {nome}({args[:90]})\n", "tool")
                elif tipo == "resultado":
                    if self.modo == "Verbose": self._escrever("    " + dado[:1200].replace("\n", "\n    ") + "\n", "dim")
                    elif self.modo == "Normal": self._escrever("    ⎿ " + (dado.splitlines() or [""])[0][:110] + "\n", "dim")
                    if "tarefas (" in dado[:30]: self.todos = core._carregar_todos()
                elif tipo == "aviso": self._escrever(f"⚠ {dado}\n", "warn")
                elif tipo == "erro": self._escrever(f"✗ {dado}\n", "err")
                elif tipo == "info": self._escrever(f"{dado}\n", "dim")
                elif tipo == "confirmar":
                    self.confirm = dado
                    self.root.focus_force(); self.entry.focus_set()
                elif tipo == "fim":
                    self.ai_aberto = False; self.processando = False; self.pensando = False
                    self.todos = core._carregar_todos()
        except queue.Empty:
            pass
        self.root.after(40, self._poll)

    def _fechar(self):
        if self.confirm is not None: self._responder_confirm(False)
        self._salvar_cfg()
        self.root.destroy()

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    AidenStark().run()
