"""
Aiden ARC — interface HUD estilo Stark/Jarvis, toda em azul.

Reator animado que reage ao que o Aiden está fazendo, relógio, tira de dias do mês,
CPU/RAM/bateria/disco/rede ao vivo, clima (Open-Meteo, sem chave), tarefas ao vivo,
atalhos de apps, permissões em janela HUD, voz (microfone + resposta falada) e tela cheia.

Roda sozinho:  python aiden_arc.py      (ou /arc dentro do aiden_pc)
Atalhos:       F11 tela cheia · Ctrl+L foca o texto · Esc sai da tela cheia
Comandos:      /clima Cidade · /atalho nome · /atalho -nome · /voz · /mic · /tela · /ajuda
"""

import sys, os, math, time, json, queue, shutil, threading, subprocess, calendar, array
import urllib.request, urllib.parse, io, wave
import tkinter as tk
from tkinter import simpledialog
from collections import deque
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import aiden_core as core
core.INTERATIVO = False        # nunca chama input()
core.set_plain()
try:
    import aiden_pc
except Exception as e:
    print(f"erro importando aiden_pc: {e}"); sys.exit(1)


def _garantir_pkg(modulo, pip_nome, instalar=True):
    """Importa o módulo; se faltar e instalar=True, tenta instalar com pip."""
    try:
        return __import__(modulo)
    except Exception:                 # ImportError, ou OSError quando falta a biblioteca nativa
        if not instalar: return None
    try:
        subprocess.run([sys.executable, "-m", "pip", "install", "--quiet", pip_nome],
                       timeout=180, capture_output=True)
        return __import__(modulo)
    except Exception:
        return None

psutil = _garantir_pkg("psutil", "psutil")

# ═══════ PALETA (tudo azul) ═══════
BG = "#01050b"; PAINEL = "#031422"
AZUL = "#1ec8ff"; AZUL2 = "#0a8fd0"; AZUL3 = "#0b4a6e"; ESCURO = "#0a2a3f"
TXT = "#bfeaff"; DIM = "#5f8aa3"; ERRO = "#ff6b7a"; AVISO = "#ffd25a"; OK = "#5bd8ff"


def mix(c1, c2, t):
    a = [int(c1[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(c2[i:i + 2], 16) for i in (1, 3, 5)]
    t = max(0.0, min(1.0, t))
    return "#%02x%02x%02x" % tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


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


WMO = [((0,), "Céu limpo"), ((1, 2, 3), "Parcialmente nublado"), ((45, 48), "Neblina"),
       ((51, 53, 55, 56, 57), "Garoa"), ((61, 63, 65, 66, 67), "Chuva"),
       ((71, 73, 75, 77, 85, 86), "Neve"), ((80, 81, 82), "Pancadas de chuva"),
       ((95, 96, 99), "Tempestade")]

def descricao_clima(codigo):
    for codigos, txt in WMO:
        if codigo in codigos: return txt
    return "—"

DIAS = ["segunda", "terça", "quarta", "quinta", "sexta", "sábado", "domingo"]
MESES = ["", "JAN", "FEV", "MAR", "ABR", "MAI", "JUN", "JUL", "AGO", "SET", "OUT", "NOV", "DEZ"]


class ARC:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("AIDEN · ARC")
        self.root.geometry("1280x760")
        self.root.minsize(1000, 640)
        self.root.configure(bg=BG)
        self.root.protocol("WM_DELETE_WINDOW", self._fechar)

        self.queue = queue.Queue()
        self.saida = SaidaFila(self.queue)
        core.set_saida(self.saida)
        core.set_confirm_handler(self._confirmar_da_thread)

        mem = core.carregar_mem()
        self.hist = [{"role": "system", "content": aiden_pc.agente.build_sys()}] + mem
        self.cfg = core.carregar_cfg()
        padrao = ["chrome", "notepad", "calc", "explorer"] if aiden_pc.SO == "Windows" else ["firefox", "gedit"]
        self.atalhos = list(self.cfg.get("arc_atalhos") or padrao)
        self.clima = None
        self.estado = "pronto"
        self.processando = False
        self.fase = 0.0
        self.nivel = 0.0
        self.voz_ligada = False
        self.gravando = False
        self._stream = None; self._pcm = bytearray(); self._fala = None
        self.net_down = deque([0.0] * 60, maxlen=60)
        self.net_up = deque([0.0] * 60, maxlen=60)
        self._net_ant = None
        self._cmd_hist = []; self._cmd_idx = -1
        self._poll_rodando = False
        self._rz = None
        self.ids = {}; self.rx = {}
        self._vivo = True
        self.tela_cheia = False

        self.c = tk.Canvas(self.root, bg=BG, highlightthickness=0)
        self.c.pack(fill="both", expand=True)
        self._criar_widgets()
        self.c.bind("<Configure>", self._on_resize)
        self.root.bind("<F11>", lambda e: self._tela_cheia())
        self.root.bind("<Escape>", lambda e: self._tela_cheia(False))
        self.root.bind("<Control-l>", lambda e: self.entry.focus_set())

        if mem: self._log(f"memória: {len(mem)} mensagens carregadas", "dim")
        self.root.after(150, self._boot)
        self.root.after(33, self._anim)
        self.root.after(40, self._poll)
        self.root.after(1000, self._tick)
        self.root.after(800, self._clima_inicial)
        if core.cliente is None: self.root.after(1500, self._pedir_chave)

    # ═══════ WIDGETS ═══════
    def _criar_widgets(self):
        self.console = tk.Text(self.c, wrap="word", bg=PAINEL, fg=TXT, insertbackground=AZUL,
                               font=("Consolas", 10), bd=0, highlightthickness=0, padx=10, pady=6,
                               spacing3=2, cursor="arrow")
        for tag, cor in (("user", AZUL), ("ai", TXT), ("dim", DIM), ("ok", OK),
                         ("err", ERRO), ("warn", AVISO), ("tool", AZUL2)):
            self.console.tag_config(tag, foreground=cor)
        self.console.tag_config("user", font=("Consolas", 10, "bold"))
        self.console.configure(state="disabled")
        self.entry = tk.Entry(self.c, bg=PAINEL, fg=TXT, insertbackground=AZUL, font=("Consolas", 11),
                              bd=0, highlightthickness=1, highlightbackground=AZUL3, highlightcolor=AZUL)
        self.entry.bind("<Return>", lambda e: self._enviar())
        self.entry.bind("<Up>", self._hist_up); self.entry.bind("<Down>", self._hist_down)
        self.btn_mic = tk.Button(self.c, text="MIC", bg=PAINEL, fg=AZUL, activebackground=AZUL3,
                                 activeforeground=TXT, bd=0, highlightthickness=1,
                                 highlightbackground=AZUL3, font=("Consolas", 9, "bold"),
                                 cursor="hand2", command=self._mic_toggle)
        self.entry.focus_set()

    # ═══════ DESENHO ═══════
    def _on_resize(self, e):
        if self._rz: self.root.after_cancel(self._rz)
        self._rz = self.root.after(120, self._desenhar)

    def _t(self, x, y, texto, tam=10, cor=TXT, bold=False, anchor="center", tags=()):
        return self.c.create_text(x, y, text=texto, fill=cor, anchor=anchor,
                                  font=("Consolas", tam, "bold" if bold else "normal"), tags=tags)

    def _painel(self, x1, y1, x2, y2, titulo):
        c = self.c; L = 12
        c.create_rectangle(x1, y1, x2, y2, outline=ESCURO)
        for (x, y, dx, dy) in ((x1, y1, 1, 1), (x2, y1, -1, 1), (x1, y2, 1, -1), (x2, y2, -1, -1)):
            c.create_line(x, y, x + dx * L, y, fill=AZUL2, width=2)
            c.create_line(x, y, x, y + dy * L, fill=AZUL2, width=2)
        if titulo:
            self._t(x1 + 14, y1 + 11, titulo, 8, AZUL2, True, "w")
            c.create_line(x1 + 14 + len(titulo) * 7 + 8, y1 + 11, x2 - 14, y1 + 11, fill=ESCURO)

    def _gauge(self, chave, cx, cy, r, rotulo):
        c = self.c
        c.create_oval(cx - r, cy - r, cx + r, cy + r, outline=ESCURO, width=10)
        arco = c.create_arc(cx - r, cy - r, cx + r, cy + r, start=90, extent=-0.1, style="arc",
                            outline=AZUL, width=10)
        c.create_oval(cx - r + 13, cy - r + 13, cx + r - 13, cy + r - 13, outline=AZUL3)
        val = self._t(cx, cy - 4, "--", 13, TXT, True)
        self._t(cx, cy + 14, rotulo, 8, DIM)
        self.ids[chave] = (arco, val)

    def _set_gauge(self, chave, pct, texto):
        if chave not in self.ids: return
        arco, val = self.ids[chave]
        self.c.itemconfigure(arco, extent=-max(0.1, min(100, pct) * 3.59))
        self.c.itemconfigure(val, text=texto)

    def _desenhar(self):
        c = self.c
        w, h = c.winfo_width(), c.winfo_height()
        if w < 300 or h < 300: return
        c.delete("all"); self.ids = {}
        m = 16; top = 46
        lw = int(w * 0.20); rw = int(w * 0.23)
        lx0, lx1 = m, lw
        rx0, rx1 = w - rw, w - m
        cx0, cx1 = lx1 + m, rx0 - m

        # cabeçalho: título + tira de dias do mês
        self._t(m, 24, "AIDEN · ARC", 14, AZUL, True, "w")
        c.create_line(m, 38, w - m, 38, fill=ESCURO)
        hoje = time.localtime()
        dias = calendar.monthrange(hoje.tm_year, hoje.tm_mon)[1]
        passo = (cx1 - cx0) / max(1, dias)
        for d in range(1, dias + 1):
            x = cx0 + passo * (d - 0.5)
            if d == hoje.tm_mday:
                c.create_rectangle(x - 11, 14, x + 11, 32, fill=AZUL, outline="")
                self._t(x, 23, f"{d:02d}", 9, BG, True)
            else:
                self._t(x, 23, f"{d:02d}", 9, AZUL3)

        # chips (clicáveis)
        self.ids["chip_status"] = self._t(rx1, 24, "● ONLINE", 8, AZUL, True, "e")
        self.ids["chip_voz"] = self._t(rx1 - 74, 24, "", 8, DIM, True, "e", ("chip_voz",))
        self.ids["chip_plan"] = self._t(rx1 - 140, 24, "", 8, AVISO, True, "e", ("chip_plan",))
        self.ids["chip_modelo"] = self._t(cx1, h - 16, "", 8, DIM, False, "e", ("chip_modelo",))
        c.tag_bind("chip_plan", "<Button-1>", lambda e: self._enviar_cmd("/plan"))
        c.tag_bind("chip_modelo", "<Button-1>", lambda e: self._cicla_modelo())
        c.tag_bind("chip_voz", "<Button-1>", lambda e: self._alterna_voz())

        # ── coluna esquerda: data + medidores ──
        dcx = (lx0 + lx1) // 2; dcy = top + 74
        c.create_oval(dcx - 52, dcy - 52, dcx + 52, dcy + 52, outline=AZUL, width=4)
        c.create_oval(dcx - 42, dcy - 42, dcx + 42, dcy + 42, outline=AZUL3)
        self.ids["mes"] = self._t(dcx, dcy - 26, "", 9, DIM)
        self.ids["dia"] = self._t(dcx, dcy, "", 30, TXT, True)
        self.ids["semana"] = self._t(dcx, dcy + 28, "", 8, DIM)
        esp = min(112, (h - top - 250) // 3)
        gr = 38
        g0 = top + 190
        self._gauge("cpu", dcx, g0, gr, "CPU")
        self._gauge("ram", dcx, g0 + esp, gr, "MEMÓRIA")
        self._gauge("bat", dcx, g0 + 2 * esp, gr, "BATERIA")
        self._gauge("disco", dcx, g0 + 3 * esp, gr, "DISCO")

        # ── coluna direita ──
        px1, px2 = rx0 + 4, rx1
        self.ids["hora"] = self._t((px1 + px2) // 2 - 22, top + 30, "", 36, TXT, True)
        self.ids["seg"] = self._t((px1 + px2) // 2 + 70, top + 40, "", 12, AZUL2, True)
        self.ids["data"] = self._t((px1 + px2) // 2, top + 66, "", 9, DIM)
        y = top + 86
        self._painel(px1, y, px2, y + 168, "CLIMA")
        self.ids["clima_t"] = self._t(px1 + 18, y + 44, "—", 26, AZUL, True, "w")
        self.ids["clima_d"] = self._t(px1 + 18, y + 76, "use /clima Cidade", 9, TXT, False, "w")
        self.ids["clima_x"] = self._t(px1 + 18, y + 94, "", 8, DIM, False, "w")
        self.ids["clima_p"] = self._t(px1 + 18, y + 128, "", 8, TXT, False, "w")
        y2 = y + 178
        self._painel(px1, y2, px2, y2 + 150, "TAREFAS")
        self.ids["tarefas"] = [self._t(px1 + 18, y2 + 34 + i * 18, "", 9, TXT, False, "w") for i in range(6)]
        y3 = y2 + 160
        y3b = h - m
        if y3b - y3 > 70:
            self._painel(px1, y3, px2, y3b, "SISTEMA")
            grande = (y3b - y3) >= 150
            self.ids["rede_info"] = c.create_text(px1 + 14, y3 + 30, text="", fill=TXT, anchor="nw",
                                                  font=("Consolas", 8), width=px2 - px1 - 24) if grande else None
            gy1 = y3 + 112 if grande else y3 + 40
            self.ids["rede_t"] = self._t(px1 + 14, gy1 - 10, "", 8, DIM, False, "w")
            self.ids["rede_d"] = c.create_line(px1 + 10, y3b - 10, px1 + 11, y3b - 10, fill=AZUL, width=2)
            self.ids["rede_u"] = c.create_line(px1 + 10, y3b - 10, px1 + 11, y3b - 10, fill=AZUL3, width=2)
            self.rede_box = (px1 + 10, gy1 + 4, px2 - 10, y3b - 10)

        # ── centro: reator ──
        cwid = cx1 - cx0
        R = min(cwid * 0.5, h * 0.25)
        rcx = cx0 + cwid / 2; rcy = top + R + 20
        self.rx = {"cx": rcx, "cy": rcy, "R": R, "arcs": [], "bars": []}
        for f, cor in ((1.0, AZUL3), (0.93, ESCURO), (0.60, AZUL3), (0.42, ESCURO)):
            c.create_oval(rcx - R * f, rcy - R * f, rcx + R * f, rcy + R * f, outline=cor)
        for i in range(72):                                 # marcas de escala
            a = math.radians(i * 5)
            r1 = R * (0.80 if i % 6 == 0 else 0.84); r2 = R * 0.88
            c.create_line(rcx + math.cos(a) * r1, rcy + math.sin(a) * r1,
                          rcx + math.cos(a) * r2, rcy + math.sin(a) * r2,
                          fill=AZUL2 if i % 6 == 0 else AZUL3)
        for f, wd, cor, n, ext, vel in ((0.97, 3, AZUL, 3, 70, 1.0), (0.72, 6, AZUL2, 2, 110, -1.5),
                                         (0.88, 1, AZUL, 4, 40, 2.2), (0.52, 4, AZUL, 2, 80, -0.8)):
            for k in range(n):
                aid = c.create_arc(rcx - R * f, rcy - R * f, rcx + R * f, rcy + R * f,
                                   start=k * 360 / n, extent=ext, style="arc", outline=cor, width=wd)
                self.rx["arcs"].append((aid, k * 360 / n, vel))
        for i in range(48):
            self.rx["bars"].append(c.create_line(rcx, rcy, rcx, rcy, fill=AZUL, width=2))
        self.rx["orb"] = []
        for f in (0.34, 0.27, 0.20, 0.13, 0.07):
            self.rx["orb"].append(c.create_oval(rcx - R * f, rcy - R * f, rcx + R * f, rcy + R * f,
                                                fill=AZUL3, outline=AZUL2))
        self.ids["estado"] = self._t(rcx, rcy + R + 14, "PRONTO", 10, AZUL, True)

        # ── centro: console + entrada + atalhos ──
        dock_y1 = h - 66
        py1 = rcy + R + 30
        py2 = dock_y1 - 8
        self._painel(cx0, py1, cx1, py2, "CONSOLE")
        ent_h = 28
        c.create_window(cx0 + 8, py1 + 20, window=self.console, anchor="nw",
                        width=cx1 - cx0 - 16, height=max(40, py2 - py1 - 20 - ent_h - 14))
        ey = py2 - ent_h - 6
        c.create_window(cx0 + 8, ey, window=self.entry, anchor="nw", width=cx1 - cx0 - 84, height=ent_h)
        c.create_window(cx1 - 68, ey, window=self.btn_mic, anchor="nw", width=60, height=ent_h)
        n = max(1, len(self.atalhos))
        larg = min(130, (cx1 - cx0 - 10 * (n - 1)) / n)
        x = cx0
        for i, nome in enumerate(self.atalhos):
            tag = f"atalho{i}"
            c.create_rectangle(x, dock_y1, x + larg, dock_y1 + 32, outline=AZUL3, fill=PAINEL, tags=(tag,))
            c.create_line(x, dock_y1, x + 10, dock_y1, fill=AZUL, width=2, tags=(tag,))
            self._t(x + larg / 2, dock_y1 + 16, nome[:14].upper(), 9, TXT, True, "center", (tag,))
            c.tag_bind(tag, "<Button-1>", lambda e, nm=nome: self._abrir_atalho(nm))
            x += larg + 10
        self.ids["rodape"] = self._t(cx0, h - 16, "", 8, DIM, False, "w")
        self._atualiza_tudo()

    # ═══════ ANIMAÇÃO ═══════
    def _anim(self):
        if not self._vivo: return
        try:
            self._anim_reator()
        except tk.TclError:
            return
        self.root.after(33, self._anim)

    def _anim_reator(self):
        r = self.rx
        if not r: return
        c = self.c
        t = time.time()
        vel = {"pronto": 1.0, "pensando": 4.5, "ouvindo": 2.2, "falando": 2.4}[self.estado]
        self.fase += 0.033 * vel
        for aid, base, v in r["arcs"]:
            c.itemconfigure(aid, start=(base + v * self.fase * 57.3) % 360)
        nbar = len(r["bars"]); R0 = r["R"] * 0.40
        if not self.gravando: self.nivel *= 0.85
        for i, bid in enumerate(r["bars"]):
            a = 2 * math.pi * i / nbar
            if self.estado == "ouvindo":
                amp = (0.12 + self.nivel * 0.9) * (0.5 + 0.5 * math.sin(i * 0.9 + t * 11))
            elif self.estado == "falando":
                amp = 0.35 * (0.5 + 0.5 * math.sin(i * 0.55 + t * 8)) * (0.6 + 0.4 * math.sin(t * 3))
            elif self.estado == "pensando":
                amp = 0.30 * max(0.0, math.sin(i * 0.25 - t * 9))
            else:
                amp = 0.05 + 0.04 * math.sin(i * 0.4 + t * 1.5)
            L = R0 * (0.06 + 0.5 * amp)
            c.coords(bid, r["cx"] + math.cos(a) * R0, r["cy"] + math.sin(a) * R0,
                     r["cx"] + math.cos(a) * (R0 + L), r["cy"] + math.sin(a) * (R0 + L))
        pulso = 0.5 + 0.5 * math.sin(t * (7 if self.estado == "pensando" else 2.2))
        for k, oid in enumerate(r["orb"]):
            cor = mix(AZUL3, "#d8f6ff", (k / (len(r["orb"]) - 1)) * 0.85 + pulso * 0.15)
            c.itemconfigure(oid, fill=cor, outline=mix(AZUL2, AZUL, pulso))

    def _set_estado(self, e):
        self.estado = e
        nomes = {"pronto": "PRONTO", "pensando": "PENSANDO...", "ouvindo": "OUVINDO", "falando": "RESPONDENDO"}
        if "estado" in self.ids: self.c.itemconfigure(self.ids["estado"], text=nomes[e])
        if "chip_status" in self.ids:
            self.c.itemconfigure(self.ids["chip_status"], text="● ONLINE" if e == "pronto" else "● ATIVO")

    # ═══════ RELÓGIO E SENSORES (1 s) ═══════
    def _tick(self):
        if not self._vivo: return
        try:
            self._atualiza_tudo()
        except tk.TclError:
            return
        self.root.after(1000, self._tick)

    def _atualiza_tudo(self):
        if "hora" not in self.ids: return
        c = self.c; agora = time.localtime()
        c.itemconfigure(self.ids["hora"], text=time.strftime("%H:%M", agora))
        c.itemconfigure(self.ids["seg"], text=time.strftime("%S", agora))
        c.itemconfigure(self.ids["data"], text=f"{DIAS[agora.tm_wday]}, {agora.tm_mday:02d}/{agora.tm_mon:02d}/{agora.tm_year}")
        c.itemconfigure(self.ids["dia"], text=f"{agora.tm_mday:02d}")
        c.itemconfigure(self.ids["mes"], text=MESES[agora.tm_mon])
        c.itemconfigure(self.ids["semana"], text=DIAS[agora.tm_wday][:3].upper())
        if psutil:
            try:
                cpu = psutil.cpu_percent(interval=None)
                self._set_gauge("cpu", cpu, f"{cpu:.0f}%")
                mem = psutil.virtual_memory()
                self._set_gauge("ram", mem.percent, f"{mem.percent:.0f}%")
                bat = psutil.sensors_battery()
                if bat is None: self._set_gauge("bat", 100, "AC")
                else: self._set_gauge("bat", bat.percent, f"{bat.percent:.0f}%" + ("+" if bat.power_plugged else ""))
                disco = psutil.disk_usage(str(Path.home().anchor or "/"))
                self._set_gauge("disco", disco.percent, f"{disco.percent:.0f}%")
                self._atualiza_rede()
            except Exception:
                pass
        if self.ids.get("rede_info"):
            c.itemconfigure(self.ids["rede_info"], text=self._info_sistema())
        # tarefas
        todos = core._carregar_todos()
        icone = {"pending": "○", "in_progress": "◐", "completed": "●"}
        cor = {"pending": DIM, "in_progress": AZUL, "completed": AZUL3}
        for i, tid in enumerate(self.ids.get("tarefas", [])):
            if i < len(todos):
                t = todos[i]
                c.itemconfigure(tid, text=f"{icone.get(t['status'], '○')} {t['content'][:28]}", fill=cor.get(t["status"], DIM))
            else:
                c.itemconfigure(tid, text="(nenhuma)" if (i == 0 and not todos) else "", fill=DIM)
        # chips e rodapé
        c.itemconfigure(self.ids["chip_plan"], text="PLAN on" if core.ESTADO["plan"] else "PLAN off",
                        fill=AVISO if core.ESTADO["plan"] else AZUL3)
        c.itemconfigure(self.ids["chip_modelo"], text=core.ESTADO["modelo"][:26])
        c.itemconfigure(self.ids["chip_voz"], text="VOZ on" if self.voz_ligada else "VOZ off",
                        fill=AZUL if self.voz_ligada else AZUL3)
        n_fatos = len(core.carregar_fatos())
        c.itemconfigure(self.ids["rodape"],
                        text=f"F11 tela cheia · /ajuda · {n_fatos} fatos")

    def _info_sistema(self):
        import platform, socket
        user = os.getenv("USER") or os.getenv("USERNAME") or "?"
        try:
            sk = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); sk.connect(("10.255.255.255", 1))
            ip = sk.getsockname()[0]; sk.close()
        except Exception:
            ip = "sem rede"
        up = ""
        if psutil:
            try:
                seg = int(time.time() - psutil.boot_time()); up = f"{seg // 3600}h {seg % 3600 // 60:02d}min"
            except Exception: up = "?"
        return f"SO      {platform.system()} {platform.release()[:14]}\nUSUÁRIO {user}\nLIGADO  {up or '?'}\nIP      {ip}"

    def _atualiza_rede(self):
        io_ = psutil.net_io_counters()
        agora = time.time()
        if self._net_ant:
            dt = max(0.2, agora - self._net_ant[0])
            self.net_down.append((io_.bytes_recv - self._net_ant[1]) / dt / 1024)
            self.net_up.append((io_.bytes_sent - self._net_ant[2]) / dt / 1024)
        self._net_ant = (agora, io_.bytes_recv, io_.bytes_sent)
        if "rede_d" not in self.ids or not hasattr(self, "rede_box"): return
        x1, y1, x2, y2 = self.rede_box
        pico = max(10.0, max(self.net_down), max(self.net_up))
        for chave, serie in (("rede_d", self.net_down), ("rede_u", self.net_up)):
            pts = []
            for i, v in enumerate(serie):
                pts += [x1 + (x2 - x1) * i / (len(serie) - 1), y2 - (y2 - y1) * min(1, v / pico)]
            self.c.coords(self.ids[chave], *pts)
        self.c.itemconfigure(self.ids["rede_t"], text=f"↓ {self.net_down[-1]:.0f} KB/s   ↑ {self.net_up[-1]:.0f} KB/s")

    # ═══════ CLIMA (Open-Meteo, sem chave) ═══════
    def _http_json(self, url):
        req = urllib.request.Request(url, headers={"User-Agent": "aiden-arc"})
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read().decode("utf-8"))

    def _clima_inicial(self):
        loc = self.cfg.get("arc_clima")
        if loc: threading.Thread(target=self._buscar_clima, args=(loc,), daemon=True).start()
        self.root.after(20 * 60 * 1000, self._clima_inicial)

    def _buscar_clima(self, loc):
        try:
            url = ("https://api.open-meteo.com/v1/forecast?" + urllib.parse.urlencode({
                "latitude": loc["lat"], "longitude": loc["lon"], "timezone": "auto", "forecast_days": 4,
                "current": "temperature_2m,apparent_temperature,relative_humidity_2m,weather_code,wind_speed_10m",
                "daily": "weather_code,temperature_2m_max,temperature_2m_min"}))
            d = self._http_json(url)
            self.queue.put(("clima", {"nome": loc.get("nome", ""), "dados": d}))
        except Exception as e:
            self.queue.put(("aviso", f"clima indisponível: {e}"))

    def _definir_cidade(self, nome):
        try:
            url = "https://geocoding-api.open-meteo.com/v1/search?" + urllib.parse.urlencode(
                {"name": nome, "count": 1, "language": "pt", "format": "json"})
            res = self._http_json(url).get("results") or []
            if not res:
                self.queue.put(("aviso", f"não achei a cidade '{nome}'")); return
            r = res[0]
            loc = {"nome": r.get("name", nome), "lat": r["latitude"], "lon": r["longitude"]}
            cfg = core.carregar_cfg(); cfg["arc_clima"] = loc; core.salvar_cfg(cfg)
            self.cfg = cfg
            self.queue.put(("info", f"cidade do clima: {loc['nome']}"))
            self._buscar_clima(loc)
        except Exception as e:
            self.queue.put(("aviso", f"clima indisponível: {e}"))

    def _pintar_clima(self, info):
        d = info["dados"]; c = self.c
        if "clima_t" not in self.ids: return
        try:
            atual = d["current"]; diario = d["daily"]
            c.itemconfigure(self.ids["clima_t"], text=f"{atual['temperature_2m']:.0f}°C")
            c.itemconfigure(self.ids["clima_d"], text=f"{info['nome']} · {descricao_clima(atual['weather_code'])}")
            c.itemconfigure(self.ids["clima_x"], text=f"sensação {atual['apparent_temperature']:.0f}° · umid. {atual['relative_humidity_2m']:.0f}% · vento {atual['wind_speed_10m']:.0f} km/h")
            linhas = []
            for i in range(1, min(4, len(diario["time"]))):
                dia = time.strptime(diario["time"][i], "%Y-%m-%d")
                linhas.append(f"{DIAS[dia.tm_wday][:3].upper()} {diario['temperature_2m_min'][i]:.0f}°/{diario['temperature_2m_max'][i]:.0f}°  {descricao_clima(diario['weather_code'][i])}")
            c.itemconfigure(self.ids["clima_p"], text="\n".join(linhas))
        except Exception as e:
            self._log(f"clima: resposta inesperada ({e})", "warn")

    # ═══════ CONSOLE ═══════
    def _log(self, texto, tag="ai"):
        w = self.console
        w.configure(state="normal")
        w.insert("end", texto, tag)
        if int(w.index("end-1c").split(".")[0]) > 1500: w.delete("1.0", "300.0")
        w.see("end"); w.configure(state="disabled")

    def _boot(self):
        linhas = ["INICIALIZANDO NÚCLEO ARC ........ ok", f"MODELO ........................ {core.ESTADO['modelo']}",
                  f"SENSORES ..................... {'ok' if psutil else 'instale psutil'}",
                  f"MEMÓRIA ...................... {len(core.carregar_fatos())} fatos",
                  "PRONTO. Digite abaixo ou use /ajuda.\n"]
        def passo(i=0):
            if i < len(linhas):
                self._log(linhas[i] + "\n", "dim" if i < len(linhas) - 1 else "ok")
                self.root.after(180, passo, i + 1)
        passo()
        if core.cliente is None: self._log("sem chave da Groq: use /chave\n", "warn")

    # ═══════ ENVIO ═══════
    def _enviar_cmd(self, cmd):
        self.entry.delete(0, "end"); self.entry.insert(0, cmd); self._enviar()

    def _enviar(self):
        txt = self.entry.get().strip()
        if not txt: return
        self.entry.delete(0, "end")
        self._cmd_hist.append(txt); self._cmd_idx = -1
        self._log(f"\n> {txt}\n", "user")
        if txt.startswith("/"): self._comando(txt); return
        self._perguntar(txt)

    def _perguntar(self, txt):
        if self.processando:
            self._log("espera a resposta anterior terminar\n", "warn"); return
        self._parar_fala()
        self.processando = True
        threading.Thread(target=self._rodar, args=(txt,), daemon=True).start()

    def _rodar(self, pergunta):
        try:
            self.hist = aiden_pc.agente.rodar(pergunta, self.hist)
        except Exception as e:
            self.queue.put(("erro", str(e)))
        finally:
            self.queue.put(("fim", None))

    def _comando(self, cmd):
        partes = cmd.split(maxsplit=1); c = partes[0].lower(); arg = partes[1].strip() if len(partes) > 1 else ""
        if c in ("/sair", "/quit", "/exit"): self._fechar(); return
        if c == "/limpar":
            self.hist = [{"role": "system", "content": aiden_pc.agente.build_sys()}]
            self.console.configure(state="normal"); self.console.delete("1.0", "end"); self.console.configure(state="disabled")
            self._log("histórico limpo\n", "dim"); return
        if c == "/esquecer": core.esquecer_mem(); self._log("histórico salvo apagado\n", "ok"); return
        if c == "/chave": self._pedir_chave(); return
        if c == "/tela": self._tela_cheia(); return
        if c == "/voz": self._alterna_voz(); return
        if c == "/mic": self._mic_toggle(); return
        if c == "/arc": self._log("você já está no ARC\n", "dim"); return
        if c == "/clima":
            if not arg: self._log("use: /clima Nome da Cidade\n", "warn"); return
            self._log(f"buscando clima de {arg}...\n", "dim")
            threading.Thread(target=self._definir_cidade, args=(arg,), daemon=True).start(); return
        if c == "/atalho":
            if not arg: self._log("atalhos: " + ", ".join(self.atalhos) + "\nuse /atalho nome ou /atalho -nome\n", "dim"); return
            if arg.startswith("-"):
                nome = arg[1:].strip()
                if nome in self.atalhos: self.atalhos.remove(nome)
            elif arg not in self.atalhos and len(self.atalhos) < 8: self.atalhos.append(arg)
            cfg = core.carregar_cfg(); cfg["arc_atalhos"] = self.atalhos; core.salvar_cfg(cfg)
            self._desenhar(); self._log("atalhos: " + ", ".join(self.atalhos) + "\n", "ok"); return
        if c == "/ajuda":
            extra = ("  /clima Cidade  define a cidade do clima\n  /atalho nome   adiciona (ou -nome remove) atalho\n"
                     "  /voz           liga/desliga a resposta falada\n  /mic           grava sua voz (clique de novo pra enviar)\n"
                     "  /tela          tela cheia (F11)\n")
            self._log(aiden_pc.agente.texto_ajuda() + "\n" + extra, "dim"); return
        txt = aiden_pc.agente.comando(cmd)
        self._log((txt if txt is not None else f"comando desconhecido: {cmd}") + "\n", "dim" if txt is not None else "warn")

    def _cicla_modelo(self):
        n = len(core.MODELOS)
        core.trocar_modelo(str(2 if n > 1 else 1))
        self._atualiza_tudo(); self._log(f"modelo: {core.ESTADO['modelo']}\n", "dim")

    def _abrir_atalho(self, nome):
        self._log(f"abrindo {nome}...\n", "dim")
        def go():
            res = aiden_pc.abrir_app(nome)
            self.queue.put(("info", res))
        threading.Thread(target=go, daemon=True).start()

    def _tela_cheia(self, ligar=None):
        self.tela_cheia = (not self.tela_cheia) if ligar is None else ligar
        self.root.attributes("-fullscreen", self.tela_cheia)

    # ═══════ FILA DE EVENTOS (thread principal) ═══════
    def _poll(self):
        if not self._vivo: return
        if self._poll_rodando: return          # reentrada durante a janela de permissão
        self._poll_rodando = True
        try:
            while True:
                try: tipo, dado = self.queue.get_nowait()
                except queue.Empty: break
                self._evento(tipo, dado)
        finally:
            self._poll_rodando = False
        self.root.after(40, self._poll)

    def _evento(self, tipo, dado):
        if tipo == "pensando":
            if dado and self.estado != "ouvindo": self._set_estado("pensando")
        elif tipo == "texto":
            if not getattr(self, "_ai_aberto", False):
                self._ai_aberto = True; self._log("\n● ", "user"); self._set_estado("falando")
            self._log(dado, "ai"); self._resp_acum = getattr(self, "_resp_acum", "") + dado
        elif tipo == "texto_fim":
            if getattr(self, "_ai_aberto", False): self._ai_aberto = False; self._log("\n", "ai")
        elif tipo == "chamada":
            nome, args = dado; self._log(f"  ▸ {nome}({args[:70]})\n", "tool"); self._set_estado("pensando")
        elif tipo == "resultado":
            self._log("    ⎿ " + (dado.splitlines() or [""])[0][:90] + "\n", "dim")
        elif tipo == "aviso": self._log(f"⚠ {dado}\n", "warn")
        elif tipo == "erro": self._log(f"❌ {dado}\n", "err")
        elif tipo == "info": self._log(f"{dado}\n", "dim")
        elif tipo == "clima": self._pintar_clima(dado)
        elif tipo == "mic_texto":
            self._log(f"\n🎤 {dado}\n", "user"); self._perguntar(dado)
        elif tipo == "confirmar": self._dialogo_confirmar(*dado)
        elif tipo == "fim":
            self._ai_aberto = False; self.processando = False
            resp = getattr(self, "_resp_acum", ""); self._resp_acum = ""
            self._set_estado("pronto"); self._atualiza_tudo()
            if self.voz_ligada and resp.strip(): self._falar(resp)

    # ═══════ PERMISSÕES (janela HUD) ═══════
    def _confirmar_da_thread(self, desc):
        ev = threading.Event(); box = {"ok": False}
        self.queue.put(("confirmar", (desc, ev, box)))
        ev.wait(timeout=180)
        return box["ok"]

    def _dialogo_confirmar(self, desc, ev, box):
        top = tk.Toplevel(self.root); top.configure(bg=AZUL); top.title("AIDEN · permissão")
        top.transient(self.root); top.resizable(False, False)
        corpo = tk.Frame(top, bg=BG); corpo.pack(padx=2, pady=2)
        tk.Label(corpo, text="⟡ PERMISSÃO", bg=BG, fg=AZUL, font=("Consolas", 12, "bold")).pack(anchor="w", padx=18, pady=(14, 4))
        tk.Label(corpo, text=desc[:700], bg=BG, fg=TXT, font=("Consolas", 10), justify="left",
                 wraplength=460).pack(anchor="w", padx=18, pady=6)
        linha = tk.Frame(corpo, bg=BG); linha.pack(fill="x", padx=18, pady=(8, 16))
        def fechar(v):
            box["ok"] = v; ev.set(); top.destroy()
        for texto, v, cor in (("SIM  [S]", True, AZUL), ("NÃO  [N]", False, AZUL3)):
            tk.Button(linha, text=texto, bg=PAINEL, fg=cor, activebackground=AZUL3, activeforeground=TXT, bd=0,
                      highlightthickness=1, highlightbackground=cor, font=("Consolas", 10, "bold"), padx=18, pady=6,
                      cursor="hand2", command=lambda vv=v: fechar(vv)).pack(side="left", padx=(0, 10))
        top.bind("<Key-s>", lambda e: fechar(True)); top.bind("<Key-n>", lambda e: fechar(False))
        top.bind("<Escape>", lambda e: fechar(False)); top.protocol("WM_DELETE_WINDOW", lambda: fechar(False))
        top.update_idletasks()
        x = self.root.winfo_rootx() + (self.root.winfo_width() - top.winfo_width()) // 2
        y = self.root.winfo_rooty() + (self.root.winfo_height() - top.winfo_height()) // 3
        top.geometry(f"+{max(0, x)}+{max(0, y)}")
        top.grab_set(); top.focus_force()
        self.root.wait_window(top)

    # ═══════ VOZ ═══════
    def _alterna_voz(self):
        self.voz_ligada = not self.voz_ligada
        if self.voz_ligada:
            self._log("voz ligada (precisa de edge-tts e ffplay ou mpv)\n", "dim")
            threading.Thread(target=lambda: _garantir_pkg("edge_tts", "edge-tts"), daemon=True).start()
        else:
            self._parar_fala()
        self._atualiza_tudo()

    def _parar_fala(self):
        p = self._fala
        if p is not None and p.poll() is None:
            try: p.terminate()
            except Exception: pass

    def _falar(self, texto):
        import re, asyncio, tempfile
        limpo = re.sub(r"https?://\S+", "link", texto)
        limpo = re.sub(r"[*_`#>~|]", "", limpo)
        limpo = re.sub(r"[\U00010000-\U0010ffff\u2600-\u27bf\ufe0f]", "", limpo)
        limpo = re.sub(r"\s+", " ", limpo).strip()[:700]
        if not limpo: return
        def go():
            edge = _garantir_pkg("edge_tts", "edge-tts", instalar=False)
            if edge is None:
                self.queue.put(("aviso", "voz: instale com pip install edge-tts")); return
            try:
                arq = str(Path(tempfile.gettempdir()) / f"aiden_arc_{int(time.time())}.mp3")
                asyncio.run(edge.Communicate(limpo, "pt-BR-AntonioNeural", rate="+8%").save(arq))
                for cmd in (["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", arq],
                            ["mpv", "--no-video", "--really-quiet", arq]):
                    if shutil.which(cmd[0]):
                        self._fala = subprocess.Popen(cmd); self._fala.wait(); return
                if hasattr(os, "startfile"): os.startfile(arq)
                else: self.queue.put(("aviso", "voz: instale ffplay ou mpv pra ouvir"))
            except Exception as e:
                self.queue.put(("aviso", f"voz falhou: {e}"))
        threading.Thread(target=go, daemon=True).start()

    def _mic_toggle(self):
        if self.gravando: self._parar_gravacao(); return
        if self.processando:
            self._log("espera a resposta anterior terminar\n", "warn"); return
        sd = _garantir_pkg("sounddevice", "sounddevice", instalar=False)
        if sd is None:
            self._log("microfone: instalando sounddevice...\n", "dim")
            def inst():
                m = _garantir_pkg("sounddevice", "sounddevice")
                self.queue.put(("info", "sounddevice instalado, clique em MIC de novo" if m
                                else "não consegui instalar o sounddevice (pip install sounddevice)"))
            threading.Thread(target=inst, daemon=True).start(); return
        try:
            self._pcm = bytearray()
            def cb(indata, frames, tempo, status):
                b = bytes(indata); self._pcm.extend(b)
                a = array.array("h", b)
                if a: self.nivel = min(1.0, (sum(x * x for x in a) / len(a)) ** 0.5 / 6000.0)
            self._stream = sd.RawInputStream(samplerate=16000, channels=1, dtype="int16", callback=cb)
            self._stream.start()
        except Exception as e:
            self._log(f"microfone indisponível: {e}\n", "err"); return
        self._parar_fala()
        self.gravando = True; self._set_estado("ouvindo")
        self.btn_mic.config(text="PARAR", fg=ERRO)
        self.root.after(30000, lambda: self.gravando and self._parar_gravacao())

    def _parar_gravacao(self):
        self.gravando = False
        self.btn_mic.config(text="MIC", fg=AZUL)
        try:
            self._stream.stop(); self._stream.close()
        except Exception: pass
        pcm = bytes(self._pcm)
        self._set_estado("pronto")
        if len(pcm) < 16000 * 2 * 0.5:
            self._log("áudio curto demais\n", "warn"); return
        self._log("transcrevendo...\n", "dim")
        def go():
            try:
                b = io.BytesIO()
                with wave.open(b, "wb") as w:
                    w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000); w.writeframes(pcm)
                r = core.cliente.audio.transcriptions.create(
                    file=("fala.wav", b.getvalue()), model="whisper-large-v3-turbo",
                    language="pt", temperature=0, prompt="Aiden, Jarvis.")
                texto = (getattr(r, "text", "") or "").strip()
                if len(texto) < 2: self.queue.put(("aviso", "não entendi o áudio"))
                else: self.queue.put(("mic_texto", texto))
            except Exception as e:
                self.queue.put(("erro", f"whisper: {e}"))
        threading.Thread(target=go, daemon=True).start()

    # ═══════ CHAVE, HISTÓRICO, FECHAR ═══════
    def _pedir_chave(self):
        nova = simpledialog.askstring("Chave da Groq", "Cola a chave da Groq:\n(pega em https://console.groq.com/keys)",
                                      parent=self.root, show="*")
        if not nova: self._log("chave não trocada\n", "warn"); return
        msg = core.trocar_chave(nova)
        self._log(msg + "\n", "ok" if msg.startswith("✅") else "err")

    def _hist_up(self, ev):
        if not self._cmd_hist: return "break"
        if self._cmd_idx == -1: self._cmd_idx = len(self._cmd_hist)
        self._cmd_idx = max(0, self._cmd_idx - 1)
        self.entry.delete(0, "end"); self.entry.insert(0, self._cmd_hist[self._cmd_idx]); return "break"

    def _hist_down(self, ev):
        if self._cmd_idx == -1: return "break"
        self._cmd_idx += 1
        self.entry.delete(0, "end")
        if self._cmd_idx < len(self._cmd_hist): self.entry.insert(0, self._cmd_hist[self._cmd_idx])
        else: self._cmd_idx = -1
        return "break"

    def _fechar(self):
        self._vivo = False
        self._parar_fala()
        try:
            if self._stream: self._stream.stop(); self._stream.close()
        except Exception: pass
        self.root.destroy()

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    ARC().run()
