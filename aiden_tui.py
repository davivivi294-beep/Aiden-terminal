"""
Aiden TUI — interface de terminal com painéis.
Barra de status + tarefas + chat com quebra de linha + entrada com acentos.
Roda: python aiden_tui.py        (Windows: pip install windows-curses)
"""

import sys, threading, queue, textwrap, time
from pathlib import Path

try:
    import curses
except ImportError:
    print("no Windows: pip install windows-curses")
    sys.exit(1)

sys.path.insert(0, str(Path(__file__).parent))
import aiden_core as core
core.INTERATIVO = False        # nunca chama input() com o curses aberto
core.set_plain()
try:
    import aiden_pc
except Exception as e:
    print(f"erro importando aiden_pc: {e}"); sys.exit(1)


class SaidaFila(core.Saida):
    def __init__(self, q): self.q = q
    def pensando(self, on, msg="pensando"): self.q.put(("pensando", on))
    def texto(self, s): self.q.put(("texto", s))
    def texto_fim(self): self.q.put(("texto_fim", None))
    def chamada(self, nome, args): self.q.put(("linha", (f"● {nome}({args[:100]})", "accent")))
    def resultado(self, res):
        primeira = (res.splitlines() or [""])[0][:200]
        tipo = "ok" if "✅" in res[:20] else ("err" if res.startswith(("Erro", "❌", "🚫")) else "dim")
        self.q.put(("linha", (f"  ⎿ {primeira}", tipo)))
        if "tarefas (" in res[:30]: self.q.put(("todos", None))
    def aviso(self, m): self.q.put(("linha", (f"⚠ {m}", "warn")))
    def erro(self, m): self.q.put(("linha", (f"❌ {m}", "err")))
    def info(self, m): self.q.put(("linha", (m, "dim")))


class AidenTUI:
    COR = {"fg": 5, "accent": 1, "ok": 2, "err": 3, "dim": 4, "warn": 1}

    def __init__(self, stdscr):
        self.stdscr = stdscr
        self.queue = queue.Queue()
        core.set_saida(SaidaFila(self.queue))
        core.set_confirm_handler(self._confirmar_da_thread)
        mem = core.carregar_mem()
        self.hist = [{"role": "system", "content": aiden_pc.agente.build_sys()}] + mem
        self.raw = []              # [(texto, tipo)] linhas originais
        self.disp = []             # linhas já quebradas pra largura atual
        self.largura = 0
        self.parcial = ""          # texto do assistente chegando em streaming
        self.input_buf = ""
        self.hist_cmds = []; self.hist_idx = -1
        self.processando = False
        self.confirm_req = None    # {"texto", "ev", "ok"}
        self.pedindo_chave = False
        self.scroll = 0
        self.rodando = True
        self._init_curses()
        if mem: self._add(f"lembrei de {len(mem)} mensagens", "dim")

    # ═══════ CURSES ═══════
    def _init_curses(self):
        try: curses.curs_set(1)
        except curses.error: pass
        curses.start_color()
        try: curses.use_default_colors()
        except curses.error: pass
        for n, c in ((1, curses.COLOR_YELLOW), (2, curses.COLOR_GREEN), (3, curses.COLOR_RED),
                     (4, curses.COLOR_CYAN), (5, curses.COLOR_WHITE)):
            curses.init_pair(n, c, -1)
        curses.init_pair(6, curses.COLOR_BLACK, curses.COLOR_YELLOW)
        self.stdscr.keypad(True)
        self.stdscr.timeout(50)

    def _txt(self, y, x, texto, par=5, attr=0):
        try: self.stdscr.addstr(y, x, texto, curses.color_pair(par) | attr)
        except curses.error: pass

    # ═══════ LINHAS ═══════
    def _quebrar(self, texto, tipo):
        w = max(10, self.largura - 4)
        partes = textwrap.wrap(texto, w, replace_whitespace=False, drop_whitespace=False) or [""]
        return [(p, tipo) for p in partes]

    def _add(self, texto, tipo="fg"):
        for ln in str(texto).split("\n"):
            self.raw.append((ln, tipo))
            if self.largura: self.disp.extend(self._quebrar(ln, tipo))
        if len(self.raw) > 4000:
            self.raw = self.raw[-3000:]
            self.largura = 0           # força reconstruir

    def _reconstruir(self, w):
        self.largura = w
        self.disp = []
        for ln, tipo in self.raw: self.disp.extend(self._quebrar(ln, tipo))

    # ═══════ CONFIRMAÇÃO E COMANDOS ═══════
    def _confirmar_da_thread(self, desc):
        req = {"texto": desc, "ev": threading.Event(), "ok": False}
        self.confirm_req = req
        req["ev"].wait(timeout=180)
        self.confirm_req = None
        return req["ok"]

    def _comando(self, cmd):
        c = cmd.split()[0].lower()
        if c in ("/sair", "/quit", "/exit"): self.rodando = False; return
        if c == "/limpar":
            self.hist = [{"role": "system", "content": aiden_pc.agente.build_sys()}]
            self.raw = []; self.disp = []; self._add("histórico limpo", "dim"); return
        if c == "/esquecer":
            core.esquecer_mem(); self._add("histórico salvo apagado", "ok"); return
        if c == "/chave":
            self.pedindo_chave = True; self._add("cola a chave da Groq e aperta Enter (Esc cancela):", "warn"); return
        txt = aiden_pc.agente.comando(cmd)
        self._add(txt if txt is not None else f"comando desconhecido: {cmd}", "dim" if txt is not None else "warn")

    def _rodar(self, pergunta):
        try:
            self.hist = aiden_pc.agente.rodar(pergunta, self.hist)
        except Exception as e:
            self.queue.put(("linha", (f"❌ {e}", "err")))
        finally:
            self.queue.put(("fim", None))

    # ═══════ FILA ═══════
    def _processar_queue(self):
        try:
            while True:
                tipo, dado = self.queue.get_nowait()
                if tipo == "texto":
                    self.parcial += dado
                    while "\n" in self.parcial:
                        linha, self.parcial = self.parcial.split("\n", 1)
                        self._add(linha, "fg")
                elif tipo == "texto_fim":
                    if self.parcial: self._add(self.parcial, "fg"); self.parcial = ""
                elif tipo == "linha":
                    self._add(*dado)
                elif tipo == "fim":
                    if self.parcial: self._add(self.parcial, "fg"); self.parcial = ""
                    self.processando = False
        except queue.Empty:
            pass

    # ═══════ ENTRADA ═══════
    def _tecla(self):
        """Devolve ('str', ch) ou ('key', codigo) ou None."""
        try:
            ch = self.stdscr.get_wch()
        except curses.error:
            return None
        except AttributeError:               # curses sem get_wch
            c = self.stdscr.getch()
            if c == -1: return None
            return ("str", chr(c)) if 32 <= c <= 126 or c in (10, 13, 27, 8, 127) else ("key", c)
        return ("str", ch) if isinstance(ch, str) else ("key", ch)

    def _tratar(self, t):
        tipo, ch = t
        # resposta de confirmação
        if self.confirm_req is not None:
            if tipo == "str" and ch.lower() in ("s", "y"): self.confirm_req["ok"] = True
            elif tipo == "str" and ch.lower() in ("n", "\n", "\r", "\x1b"): self.confirm_req["ok"] = False
            else: return
            self.confirm_req["ev"].set(); return

        if tipo == "key":
            if ch == curses.KEY_RESIZE: self.largura = 0
            elif ch == curses.KEY_UP: self.scroll = min(self.scroll + 1, max(0, len(self.disp) - 5))
            elif ch == curses.KEY_DOWN: self.scroll = max(0, self.scroll - 1)
            elif ch == curses.KEY_PPAGE: self.scroll = min(self.scroll + 10, max(0, len(self.disp) - 5))
            elif ch == curses.KEY_NPAGE: self.scroll = max(0, self.scroll - 10)
            elif ch in (curses.KEY_BACKSPACE,): self.input_buf = self.input_buf[:-1]
            elif ch in (curses.KEY_ENTER,): self._enter()
            return

        if ch in ("\n", "\r"): self._enter()
        elif ch in ("\x7f", "\b"): self.input_buf = self.input_buf[:-1]
        elif ch == "\x1b":
            if self.pedindo_chave: self.pedindo_chave = False; self.input_buf = ""; self._add("cancelado", "warn")
            elif not self.input_buf and not self.processando: self.rodando = False
            else: self.input_buf = ""
        elif ch == "\x10" and self.hist_cmds:      # Ctrl+P: comando anterior
            self.hist_idx = (len(self.hist_cmds) if self.hist_idx == -1 else self.hist_idx) - 1
            self.hist_idx = max(0, self.hist_idx); self.input_buf = self.hist_cmds[self.hist_idx]
        elif ch.isprintable(): self.input_buf += ch

    def _enter(self):
        cmd = self.input_buf.strip()
        self.input_buf = ""
        if self.pedindo_chave:
            self.pedindo_chave = False
            msg = core.trocar_chave(cmd)
            self._add(msg, "ok" if msg.startswith("✅") else "err"); return
        if not cmd: return
        self.hist_cmds.append(cmd); self.hist_idx = -1
        self.scroll = 0
        self._add(f"> {cmd}", "accent")
        if cmd.startswith("/"):
            self._comando(cmd); self._add("", "fg"); return
        if self.processando:
            self._add("espera a resposta anterior terminar", "warn"); return
        self.processando = True
        threading.Thread(target=self._rodar, args=(cmd,), daemon=True).start()

    # ═══════ DESENHO ═══════
    def _render(self):
        self.stdscr.erase()
        h, w = self.stdscr.getmaxyx()
        if h < 15 or w < 40:
            self._txt(0, 0, "janela muito pequena"); self.stdscr.refresh(); return
        if w != self.largura: self._reconstruir(w)

        # barra de status
        plan = " [PLAN]" if core.ESTADO["plan"] else ""
        esq = f" AIDEN{plan} "
        meio = f" {core.ESTADO['modelo']} "
        dir_ = " ● pronto " if not self.processando else " ◐ pensando... "
        self._txt(0, 0, esq.ljust(w)[:w], 6, curses.A_BOLD)
        pos = max(len(esq) + 1, (w - len(meio)) // 2)
        if pos + len(meio) < w - len(dir_): self._txt(0, pos, meio, 6, curses.A_BOLD)
        self._txt(0, max(0, w - len(dir_)), dir_, 6, curses.A_BOLD)

        # tarefas
        self._txt(1, 1, "TAREFAS", 5, curses.A_BOLD)
        todos = core._carregar_todos()
        if not todos: self._txt(2, 2, "(nenhuma)", 4)
        for i, t in enumerate(todos[:4]):
            icone = {"pending": "○", "in_progress": "◐", "completed": "●"}.get(t["status"], "○")
            cor = 2 if t["status"] == "completed" else (1 if t["status"] == "in_progress" else 4)
            self._txt(2 + i, 2, f"{icone} {t['content']}"[:w - 4], cor)
        self._txt(6, 1, "─" * (w - 2), 4)

        # chat
        topo, base = 7, h - 4
        altura = max(1, base - topo)
        linhas = list(self.disp)
        if self.parcial: linhas.extend(self._quebrar(self.parcial, "fg"))
        fim = max(0, len(linhas) - self.scroll)
        for i, (texto, tipo) in enumerate(linhas[max(0, fim - altura):fim]):
            self._txt(topo + i, 2, texto[:w - 4], self.COR.get(tipo, 5))

        # entrada / confirmação
        self._txt(h - 3, 1, "─" * (w - 2), 4)
        if self.confirm_req is not None:
            desc = self.confirm_req["texto"].replace("\n", " ⏎ ")
            self._txt(h - 2, 2, f"? {desc}"[:w - 12] + "  [s/N]", 1, curses.A_BOLD)
        else:
            self._txt(h - 2, 2, "> ", 1, curses.A_BOLD)
            mostra = "*" * len(self.input_buf) if self.pedindo_chave else self.input_buf
            vis = mostra[-(w - 6):]
            self._txt(h - 2, 4, vis, 5)
            try: self.stdscr.move(h - 2, min(w - 2, 4 + len(vis)))
            except curses.error: pass
        self._txt(h - 1, 0, " ↑↓ PgUp/PgDn rola · /ajuda · Esc sai"[:w], 4, curses.A_DIM)
        self.stdscr.refresh()

    def loop(self):
        while self.rodando:
            t = self._tecla()
            if t is not None: self._tratar(t)
            self._processar_queue()
            self._render()
            if t is None: time.sleep(0.01)
        self.stdscr.erase()
        self._txt(0, 0, "flw"); self.stdscr.refresh()


def main(stdscr):
    tui = AidenTUI(stdscr)
    for l in core.BANNER.strip("\n").split("\n"): tui._add(l, "accent")
    tui._add("", "fg")
    tui._add(f"modelo: {core.ESTADO['modelo']}", "dim")
    tui._add("digite pra conversar · /ajuda pra comandos", "dim")
    fatos = core.carregar_fatos()
    if fatos: tui._add(f"sei {len(fatos)} coisas sobre você", "dim")
    tui._add("", "fg")
    if core.cliente is None:
        tui._add("sem chave da Groq: digite /chave", "warn")
    tui.loop()


if __name__ == "__main__":
    try:
        curses.wrapper(main)
    except KeyboardInterrupt:
        print("\nflw")
