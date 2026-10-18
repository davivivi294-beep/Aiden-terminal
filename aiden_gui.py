"""
Aiden GUI — Desktop.
Sessões na lateral, chat + terminal, modos Verbose/Normal/Summary, lista de tarefas e
confirmação de ações em janela. Roda sozinho: python aiden_gui.py (ou /gui no aiden_pc).
"""

import sys, threading, queue
from pathlib import Path
import tkinter as tk
from tkinter import scrolledtext, simpledialog, messagebox

sys.path.insert(0, str(Path(__file__).parent))
import aiden_core as core
core.INTERATIVO = False        # nunca chama input() (pythonw não tem terminal)
core.set_plain()               # sem códigos de cor
try:
    import aiden_pc
except Exception as e:
    print(f"erro importando aiden_pc: {e}"); sys.exit(1)


class SaidaFila(core.Saida):
    """Troca os print do núcleo por eventos que a janela consome (sempre na thread principal)."""
    def __init__(self, q):
        self.q = q; self.sessao = 0
    def _p(self, tipo, dado=None): self.q.put((tipo, self.sessao, dado))
    def pensando(self, on, msg="pensando"): self._p("pensando", on)
    def texto(self, s): self._p("texto", s)
    def texto_fim(self): self._p("texto_fim")
    def chamada(self, nome, args): self._p("chamada", (nome, args))
    def resultado(self, res): self._p("resultado", res)
    def aviso(self, m): self._p("aviso", m)
    def erro(self, m): self._p("erro", m)
    def info(self, m): self._p("info", m)


class Sessao:
    def __init__(self, nome, hist=None):
        self.nome = nome
        self.hist = hist or [{"role": "system", "content": aiden_pc.agente.build_sys()}]
        self.msgs = []            # [(tag, texto)] pra redesenhar ao trocar de sessão
        self.ai_aberto = False


class AidenGUI:
    BG = "#0d0d0d"; BG2 = "#1a1a1a"; BG3 = "#252525"; BG4 = "#2f2f2f"
    FG = "#dddddd"; DIM = "#888888"; DIM2 = "#555555"
    ACCENT = "#d77757"; GREEN = "#7dbe7d"; RED = "#dc5a5a"; YELLOW = "#e6b450"; BLUE = "#78a0dc"

    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Aiden · Desktop")
        self.root.geometry("1100x760")
        self.root.configure(bg=self.BG)
        self.root.minsize(760, 500)
        self.root.protocol("WM_DELETE_WINDOW", self._fechar)

        self.queue = queue.Queue()
        self.saida = SaidaFila(self.queue)
        core.set_saida(self.saida)
        core.set_confirm_handler(self._confirmar_da_thread)

        mem = core.carregar_mem()
        self.sessoes = [Sessao("Chat principal", [{"role": "system", "content": aiden_pc.agente.build_sys()}] + mem)]
        self.sessao_atual = 0
        self.proc_idx = 0
        self.processando = False
        self.modo = "Normal"
        self._cmd_hist = []; self._cmd_idx = -1

        self._build()
        self._render_sessao()
        self._banner_inicial(len(mem))
        self._atualizar_todos()
        self.root.after(40, self._poll)
        if core.cliente is None:
            self.root.after(400, self._pedir_chave)

    # ═══════ CONSTRUÇÃO ═══════
    def _build(self):
        head = tk.Frame(self.root, bg=self.BG2, height=48)
        head.pack(fill="x"); head.pack_propagate(False)
        tk.Label(head, text="AIDEN", bg=self.BG2, fg=self.ACCENT, font=("Consolas", 15, "bold")).pack(side="left", padx=(16, 4))
        tk.Label(head, text="· desktop", bg=self.BG2, fg=self.DIM, font=("Consolas", 10)).pack(side="left")
        self.status_lbl = tk.Label(head, text="● pronto", bg=self.BG2, fg=self.GREEN, font=("Consolas", 10))
        self.status_lbl.pack(side="right", padx=16)
        self.plan_lbl = tk.Label(head, text="", bg=self.BG2, fg=self.YELLOW, font=("Consolas", 10, "bold"))
        self.plan_lbl.pack(side="right", padx=(0, 8))
        self.modelo_lbl = tk.Label(head, text=core.ESTADO["modelo"], bg=self.BG2, fg=self.DIM, font=("Consolas", 9))
        self.modelo_lbl.pack(side="right", padx=(0, 8))

        body = tk.Frame(self.root, bg=self.BG)
        body.pack(fill="both", expand=True)
        self.sidebar = tk.Frame(body, bg=self.BG2, width=220)
        self.sidebar.pack(side="left", fill="y"); self.sidebar.pack_propagate(False)
        self._build_sidebar()

        main = tk.Frame(body, bg=self.BG)
        main.pack(side="left", fill="both", expand=True)

        modos_bar = tk.Frame(main, bg=self.BG2, height=34)
        modos_bar.pack(fill="x"); modos_bar.pack_propagate(False)
        self.modo_btns = {}
        for m in ("Verbose", "Normal", "Summary"):
            b = tk.Label(modos_bar, text=m, bg=self.BG2, fg=self.DIM, font=("Consolas", 10),
                         padx=14, pady=6, cursor="hand2")
            b.pack(side="left", padx=(8 if m == "Verbose" else 2, 0), pady=4)
            b.bind("<Button-1>", lambda e, mm=m: self._mudar_modo(mm))
            self.modo_btns[m] = b
        self._mudar_modo("Normal")

        self.panes = tk.PanedWindow(main, orient="horizontal", bg=self.BG3, sashwidth=4, sashrelief="flat")
        self.panes.pack(fill="both", expand=True)

        self.chat = self._painel("CHAT", self.ACCENT, bg=self.BG, fg=self.FG, fonte=11)
        for tag, cor in (("user", self.ACCENT), ("ai", self.FG), ("dim", self.DIM), ("ok", self.GREEN),
                         ("err", self.RED), ("warn", self.YELLOW), ("blue", self.BLUE)):
            self.chat.tag_config(tag, foreground=cor)
        self.chat.tag_config("user", font=("Consolas", 11, "bold"))
        self.term = self._painel("TERMINAL", self.GREEN, bg="#0a0a0a", fg="#b8d8b8", fonte=10)
        for tag, cor in (("prompt", self.ACCENT), ("out", self.DIM), ("ok", self.GREEN), ("err", self.RED)):
            self.term.tag_config(tag, foreground=cor)

        foot = tk.Frame(main, bg=self.BG2, height=64)
        foot.pack(fill="x"); foot.pack_propagate(False)
        self.entry = tk.Entry(foot, bg=self.BG, fg=self.FG, insertbackground=self.ACCENT,
                              font=("Consolas", 12), bd=0, highlightthickness=1,
                              highlightbackground=self.BG4, highlightcolor=self.ACCENT)
        self.entry.pack(side="left", fill="x", expand=True, padx=(16, 8), pady=14, ipady=9)
        self.entry.bind("<Return>", lambda e: self._enviar())
        self.entry.bind("<Up>", self._hist_up)
        self.entry.bind("<Down>", self._hist_down)
        self.entry.focus()
        tk.Button(foot, text="enviar", bg=self.ACCENT, fg="white", activebackground="#b86545",
                  activeforeground="white", bd=0, font=("Consolas", 11, "bold"), padx=22, pady=8,
                  cursor="hand2", command=self._enviar).pack(side="right", padx=(0, 16), pady=14)

    def _painel(self, titulo, cor, bg, fg, fonte):
        frame = tk.Frame(self.panes, bg=self.BG2)
        head = tk.Frame(frame, bg=self.BG3, height=30)
        head.pack(fill="x"); head.pack_propagate(False)
        tk.Canvas(head, width=8, height=8, bg=cor, highlightthickness=0).pack(side="left", padx=(12, 6), pady=11)
        tk.Label(head, text=titulo, bg=self.BG3, fg=self.DIM, font=("Consolas", 10, "bold")).pack(side="left")
        txt = scrolledtext.ScrolledText(frame, wrap="word", bg=bg, fg=fg, insertbackground=self.ACCENT,
                                        font=("Consolas", fonte), bd=0, highlightthickness=0,
                                        padx=14, pady=12, spacing3=3)
        txt.pack(fill="both", expand=True)
        txt.configure(state="disabled")
        self.panes.add(frame, minsize=260 if titulo == "CHAT" else 200)
        return txt

    def _build_sidebar(self):
        tk.Label(self.sidebar, text="SESSÕES", bg=self.BG2, fg=self.DIM2, font=("Consolas", 9, "bold"),
                 anchor="w").pack(fill="x", padx=16, pady=(14, 6))
        self.sessoes_frame = tk.Frame(self.sidebar, bg=self.BG2)
        self.sessoes_frame.pack(fill="x")
        add = tk.Label(self.sidebar, text="+ nova sessão", bg=self.BG2, fg=self.DIM, font=("Consolas", 10),
                       anchor="w", padx=16, pady=8, cursor="hand2")
        add.pack(fill="x", pady=(6, 0))
        add.bind("<Button-1>", lambda e: self._nova_sessao())

        tk.Label(self.sidebar, text="TAREFAS", bg=self.BG2, fg=self.DIM2, font=("Consolas", 9, "bold"),
                 anchor="w").pack(fill="x", padx=16, pady=(16, 4))
        self.todos_frame = tk.Frame(self.sidebar, bg=self.BG2)
        self.todos_frame.pack(fill="x")

        rodape = tk.Frame(self.sidebar, bg=self.BG2)
        rodape.pack(side="bottom", fill="x", pady=12)
        for cmd, desc in (("/plan", "plan mode"), ("/fatos", "o que sei"), ("/todos", "tarefas"),
                          ("/chave", "trocar chave"), ("/ajuda", "comandos")):
            lbl = tk.Label(rodape, text=f"  {cmd}  ·  {desc}", bg=self.BG2, fg=self.DIM2, anchor="w",
                           font=("Consolas", 9), padx=16, pady=3, cursor="hand2")
            lbl.pack(fill="x")
            lbl.bind("<Button-1>", lambda e, c=cmd: self._enviar_cmd(c))

    # ═══════ SESSÕES ═══════
    def _render_sessoes(self):
        for w in self.sessoes_frame.winfo_children(): w.destroy()
        for i, s in enumerate(self.sessoes):
            ativo = (i == self.sessao_atual)
            bg = self.BG3 if ativo else self.BG2
            fr = tk.Frame(self.sessoes_frame, bg=bg, cursor="hand2")
            fr.pack(fill="x", padx=8, pady=1)
            fr.bind("<Button-1>", lambda e, idx=i: self._trocar_sessao(idx))
            for txt, cor, fonte in (("💬", self.ACCENT if ativo else self.DIM, ("Consolas", 11)),
                                    (s.nome[:20], self.ACCENT if ativo else self.FG,
                                     ("Consolas", 10, "bold" if ativo else "normal"))):
                l = tk.Label(fr, text=txt, bg=bg, fg=cor, font=fonte, padx=8, pady=6, anchor="w", cursor="hand2")
                l.pack(side="left")
                l.bind("<Button-1>", lambda e, idx=i: self._trocar_sessao(idx))

    def _render_sessao(self):
        self.chat.configure(state="normal"); self.chat.delete("1.0", "end"); self.chat.configure(state="disabled")
        for tag, texto in self.sessoes[self.sessao_atual].msgs:
            self._escrever(self.chat, texto, tag)
        self._render_sessoes()

    def _trocar_sessao(self, idx):
        if idx == self.sessao_atual: return
        self.sessao_atual = idx
        self._render_sessao()

    def _nova_sessao(self):
        self.sessoes.append(Sessao(f"Sessão {len(self.sessoes) + 1}"))
        self.sessao_atual = len(self.sessoes) - 1
        self._render_sessao()

    # ═══════ ESCRITA NA TELA ═══════
    def _escrever(self, widget, texto, tag=None):
        widget.configure(state="normal")
        widget.insert("end", texto, tag or "")
        widget.see("end")
        widget.configure(state="disabled")

    def _chat(self, idx, texto, tag="ai"):
        """Guarda na sessão que pediu e só desenha se ela for a que está na tela."""
        self.sessoes[idx].msgs.append((tag, texto))
        if idx == self.sessao_atual: self._escrever(self.chat, texto, tag)

    def _term(self, texto, tag="out"):
        self._escrever(self.term, texto, tag)

    def _banner_inicial(self, n_mem):
        s = self.sessoes[0]
        for l in core.BANNER.strip("\n").split("\n"):
            s.msgs.append(("user", l + "\n"))
        s.msgs.append(("dim", f"\n  modelo: {core.ESTADO['modelo']}\n  digite pra conversar · /ajuda pra comandos\n"))
        if n_mem: s.msgs.append(("dim", f"  lembrei de {n_mem} mensagens\n"))
        fatos = core.carregar_fatos()
        if fatos: s.msgs.append(("dim", f"  sei {len(fatos)} coisas sobre você\n"))
        s.msgs.append(("dim", "\n"))
        self._render_sessao()
        self._term("$ python aiden_gui.py\n", "prompt")
        self._term(f"AIDEN · desktop\nmodelo: {core.ESTADO['modelo']}\n\n", "out")

    def _mudar_modo(self, m):
        self.modo = m
        for k, b in self.modo_btns.items():
            ativo = k == m
            b.config(bg=self.BG3 if ativo else self.BG2, fg=self.ACCENT if ativo else self.DIM,
                     font=("Consolas", 10, "bold" if ativo else "normal"))

    def _atualizar_todos(self):
        for w in self.todos_frame.winfo_children(): w.destroy()
        todos = core._carregar_todos()
        if not todos:
            tk.Label(self.todos_frame, text="(nenhuma)", bg=self.BG2, fg=self.DIM2,
                     font=("Consolas", 9), anchor="w", padx=16).pack(fill="x")
            return
        icone = {"pending": "○", "in_progress": "◐", "completed": "●"}
        cor = {"pending": self.DIM, "in_progress": self.YELLOW, "completed": self.GREEN}
        for t in todos[:8]:
            tk.Label(self.todos_frame, text=f"{icone.get(t['status'], '○')} {t['content'][:24]}",
                     bg=self.BG2, fg=cor.get(t["status"], self.DIM), font=("Consolas", 9),
                     anchor="w", padx=16).pack(fill="x")

    def _atualizar_status(self):
        self.modelo_lbl.config(text=core.ESTADO["modelo"])
        self.plan_lbl.config(text="[PLAN]" if core.ESTADO["plan"] else "")

    # ═══════ ENVIO ═══════
    def _enviar_cmd(self, cmd):
        self.entry.delete(0, "end"); self.entry.insert(0, cmd); self._enviar()

    def _enviar(self):
        if self.processando: return
        txt = self.entry.get().strip()
        if not txt: return
        self.entry.delete(0, "end")
        self._cmd_hist.append(txt); self._cmd_idx = -1
        idx = self.sessao_atual
        self._chat(idx, f"\n> {txt}\n", "user")
        self._term(f"\n$ {txt}\n", "prompt")
        if txt.startswith("/"):
            self._comando(txt, idx); return
        self.processando = True
        self.proc_idx = idx
        self.saida.sessao = idx
        self.status_lbl.config(text="● pensando...", fg=self.ACCENT)
        threading.Thread(target=self._rodar, args=(idx, txt), daemon=True).start()

    def _rodar(self, idx, pergunta):
        s = self.sessoes[idx]
        try:
            s.hist = aiden_pc.agente.rodar(pergunta, s.hist, persistir=(idx == 0))
        except Exception as e:
            self.queue.put(("erro", idx, str(e)))
        finally:
            self.queue.put(("fim", idx, None))

    def _comando(self, cmd, idx):
        c = cmd.split()[0].lower()
        if c in ("/sair", "/quit", "/exit"): self._fechar(); return
        if c == "/limpar":
            nome = self.sessoes[idx].nome
            self.sessoes[idx] = Sessao(nome)
            self._render_sessao(); self._chat(idx, "histórico limpo\n", "dim"); return
        if c == "/esquecer":
            core.esquecer_mem(); self._chat(idx, "histórico salvo apagado\n", "ok"); return
        if c == "/chave": self._pedir_chave(); return
        if c == "/gui": self._chat(idx, "você já está na GUI\n", "dim"); return
        txt = aiden_pc.agente.comando(cmd)
        self._atualizar_status()
        if txt is None: self._chat(idx, f"comando desconhecido: {cmd}\n", "warn")
        else:
            self._chat(idx, txt + "\n", "dim"); self._term(txt + "\n", "out")

    # ═══════ FILA DE EVENTOS (thread principal) ═══════
    def _poll(self):
        try:
            while True:
                tipo, idx, dado = self.queue.get_nowait()
                if idx >= len(self.sessoes): idx = self.sessao_atual
                s = self.sessoes[idx]
                if tipo == "pensando":
                    pass
                elif tipo == "texto":
                    if not s.ai_aberto:
                        s.ai_aberto = True; self._chat(idx, "\n● ", "user")
                    self._chat(idx, dado, "ai"); self._term(dado, "out")
                elif tipo == "texto_fim":
                    if s.ai_aberto:
                        s.ai_aberto = False; self._chat(idx, "\n", "ai"); self._term("\n", "out")
                elif tipo == "chamada":
                    nome, args = dado
                    self._term(f"● {nome}({args})\n", "prompt")
                    if self.modo != "Summary": self._chat(idx, f"  ● {nome}({args[:80]})\n", "blue")
                elif tipo == "resultado":
                    self._term(dado[:3000] + "\n", "ok" if "✅" in dado[:20] else (
                        "err" if dado.startswith(("Erro", "❌", "🚫")) else "out"))
                    if self.modo == "Verbose": self._chat(idx, "    " + dado[:1500].replace("\n", "\n    ") + "\n", "dim")
                    elif self.modo == "Normal":
                        self._chat(idx, "    ⎿ " + (dado.splitlines() or [""])[0][:100] + "\n", "dim")
                    if "tarefas (" in dado[:30]: self._atualizar_todos()
                elif tipo == "aviso":
                    self._chat(idx, f"⚠ {dado}\n", "warn"); self._term(f"⚠ {dado}\n", "out")
                elif tipo == "erro":
                    self._chat(idx, f"❌ {dado}\n", "err"); self._term(f"❌ {dado}\n", "err")
                    self.status_lbl.config(text="● erro", fg=self.RED)
                elif tipo == "info":
                    self._chat(idx, f"{dado}\n", "dim"); self._term(f"{dado}\n", "out")
                elif tipo == "confirmar":
                    desc, ev, box = dado
                    box["ok"] = messagebox.askyesno("Aiden pede permissão", desc, parent=self.root)
                    ev.set()
                elif tipo == "fim":
                    s.ai_aberto = False
                    self.processando = False
                    self.status_lbl.config(text="● pronto", fg=self.GREEN)
                    self._atualizar_status(); self._atualizar_todos()
        except queue.Empty:
            pass
        self.root.after(40, self._poll)

    def _confirmar_da_thread(self, desc):
        """Chamada pela thread do agente: pergunta na janela e espera a resposta."""
        ev = threading.Event(); box = {"ok": False}
        self.queue.put(("confirmar", self.proc_idx, (desc, ev, box)))
        ev.wait(timeout=180)
        return box["ok"]

    # ═══════ CHAVE, HISTÓRICO, FECHAR ═══════
    def _pedir_chave(self):
        nova = simpledialog.askstring(
            "Chave da Groq", "Cola a chave da Groq:\n(pega em https://console.groq.com/keys)",
            parent=self.root, show="*")
        if not nova:
            self._chat(self.sessao_atual, "chave não trocada\n", "warn"); return
        msg = core.trocar_chave(nova)
        self._chat(self.sessao_atual, msg + "\n", "ok" if msg.startswith("✅") else "err")

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

    def _fechar(self):
        if self.processando and not messagebox.askyesno("Aiden", "Ainda tem uma tarefa rodando. Fechar mesmo assim?"):
            return
        self.root.destroy()

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    AidenGUI().run()
