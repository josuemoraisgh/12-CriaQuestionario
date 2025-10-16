# -*- coding: utf-8 -*-
"""
Janela principal da aplicação (Tk + ttk). Entrada via tabela (1 coluna).
Novidades:
- Editor com import robusto (corrige "attempted relative import beyond top-level package").
- Drag-and-drop opcional: se `tkinterdnd2` estiver instalado, soltar arquivos na tabela adiciona linhas.
- Tabela com altura fixa de 5 linhas (scroll só aparece quando >5).
"""
import io
import json
import shutil
import subprocess
from pathlib import Path
from datetime import datetime
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, colorchooser
import tkinter.font as tkfont
import sys
import threading
import importlib.util

# DnD opcional (gracioso)
try:
    from tkinterdnd2 import DND_FILES
    _HAS_DND = True
except Exception:
    _HAS_DND = False

from config.preferences import APP_NAME, get_ini_path, FONT_SIZES, DEFAULTS, load_prefs, save_prefs
from core.beamer_generator import json2beamer
from gui.scrollable_frame import ScrollableFrame

TREE_HEIGHT_ROWS = 5

class App(ttk.Frame):
    def __init__(self, master):
        super().__init__(master, padding=(10,10,10,6))
        self.master.title(APP_NAME)
        self.master.geometry("980x640")
        self.master.minsize(820, 540)

        self.grid(sticky="nsew")
        self.master.columnconfigure(0, weight=1)
        self.master.rowconfigure(0, weight=1)

        self._style()

        self.paned = tk.PanedWindow(self, orient="vertical", sashrelief="raised", sashwidth=6)
        self.paned.grid(row=0, column=0, sticky="nsew")
        self.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)

        self.top_container = ttk.Frame(self.paned)
        self.top_container.columnconfigure(0, weight=1)
        self.top_container.rowconfigure(0, weight=1)

        self.scroll = ScrollableFrame(self.top_container)
        self.scroll.grid(row=0, column=0, sticky="nsew")
        self.top = self.scroll.content
        self.top.columnconfigure(0, weight=1)

        self.bottom = ttk.Frame(self.paned, padding=(0,6,0,0))
        self.bottom.columnconfigure(0, weight=1)
        self.bottom.rowconfigure(1, weight=1)

        self.paned.add(self.top_container)
        self.paned.add(self.bottom)

        # Carrega prefs (agora buscando .ini local se existir)
        self.prefs = load_prefs()
        self.var_title = tk.StringVar(value=self.prefs["title"])
        self.var_fsq = tk.StringVar(value=self.prefs["fsq"])
        self.var_fsa = tk.StringVar(value=self.prefs["fsa"])
        self.var_alert = tk.StringVar(value=self.prefs["alert_color"])
        self.var_seed = tk.StringVar(value=self.prefs["shuffle_seed"])
        self.var_output = tk.StringVar(value="")
        self.var_status = tk.StringVar(value=f"Pronto. Config: {get_ini_path()}")

        self._build_top()
        self._build_bottom()
        self._bind_events()

        self.after_idle(self._apply_pane_constraints_and_place_sash)

    def _style(self):
        style = ttk.Style()
        for theme in ("vista", "clam", "alt", "default"):
            if theme in style.theme_names():
                style.theme_use(theme)
                break
        style.configure("TButton", padding=6)
        style.configure("Accent.TButton", padding=6)
        style.configure("TEntry", padding=4)
        style.configure("TLabel", padding=2)
        style.configure("TCombobox", padding=2)
        style.configure("Json.Treeview", rowheight=26)

    def _build_top(self):
        files = ttk.LabelFrame(self.top, text="Arquivos", padding=8)
        files.grid(row=0, column=0, sticky="nsew", padx=0, pady=(0,10))
        files.columnconfigure(0, weight=1)

        # Tabela (5 linhas visíveis)
        self.tbl = ttk.Treeview(files, columns=("path",), show="headings",
                                selectmode="browse", style="Json.Treeview", height=TREE_HEIGHT_ROWS)
        self.tbl.heading("path", text="JSON de questões (arraste e solte aqui)")
        self.tbl.column("path", width=600, anchor="w", stretch=True)
        self.vsb = ttk.Scrollbar(files, orient="vertical", command=self.tbl.yview)
        self.tbl.configure(yscrollcommand=self.vsb.set)
        self.tbl.grid(row=0, column=0, sticky="nsew", padx=(0,6))
        self._update_scrollbar_visibility()

        files.rowconfigure(0, weight=0)
        files.rowconfigure(1, weight=0)

        btns = ttk.Frame(files)
        btns.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(8,0))
        ttk.Button(btns, text="Adicionar…", command=self.browse_jsons).pack(side="left")
        self.btn_delete = ttk.Button(btns, text="Deletar", command=self.delete_selected, state="disabled")
        self.btn_delete.pack(side="left", padx=(6,0))

        out = ttk.LabelFrame(self.top, text="Saída", padding=8)
        out.grid(row=1, column=0, sticky="ew", pady=(0,10))
        out.columnconfigure(1, weight=1)
        ttk.Label(out, text="Arquivo .tex de saída:").grid(row=0, column=0, sticky="w", padx=(6,2), pady=6)
        ttk.Entry(out, textvariable=self.var_output, state="readonly").grid(row=0, column=1, sticky="ew", padx=(0,6), pady=6)
        ttk.Button(out, text="Abrir Pasta", command=self.open_output_dir).grid(row=0, column=2, padx=(0,6))

        opts = ttk.LabelFrame(self.top, text="Opções", padding=8)
        opts.grid(row=2, column=0, sticky="ew")
        for c in range(6):
            opts.columnconfigure(c, weight=(1 if c in (1,3,5) else 0))

        ttk.Label(opts, text="Título:").grid(row=0, column=0, sticky="w", padx=(6,2), pady=6)
        ttk.Entry(opts, textvariable=self.var_title).grid(row=0, column=1, columnspan=5, sticky="ew", padx=(0,6))

        ttk.Label(opts, text="Fonte Enunciado (fsq):").grid(row=1, column=0, sticky="w", padx=(6,2), pady=6)
        ttk.Combobox(opts, values=FONT_SIZES, textvariable=self.var_fsq, state="readonly")\
            .grid(row=1, column=1, sticky="ew", padx=(0,12))

        ttk.Label(opts, text="Fonte Alternativas (fsa):").grid(row=1, column=2, sticky="w", padx=(6,2))
        ttk.Combobox(opts, values=FONT_SIZES, textvariable=self.var_fsa, state="readonly")\
            .grid(row=1, column=3, sticky="ew", padx=(0,12))

        ttk.Label(opts, text="Cor do \\alert{…}:").grid(row=1, column=4, sticky="w", padx=(6,2))
        frm_color = ttk.Frame(opts)
        frm_color.grid(row=1, column=5, sticky="ew")
        ttk.Entry(frm_color, textvariable=self.var_alert).pack(side="left", fill="x", expand=True)
        ttk.Button(frm_color, text="🎨", width=3, command=self.pick_color).pack(side="left", padx=(6,0))

        ttk.Label(opts, text="Seed de Embaralhamento (opcional):").grid(row=2, column=0, sticky="w", padx=(6,2), pady=(0,8))
        ttk.Entry(opts, textvariable=self.var_seed).grid(row=2, column=1, sticky="ew", padx=(0,12), pady=(0,8))
        ttk.Label(opts, text="(vazio = aleatório a cada execução)").grid(row=2, column=2, columnspan=4, sticky="w", pady=(0,8))

        actions = ttk.Frame(self.top, padding=(0,8,0,0))
        actions.grid(row=3, column=0, sticky="ew")
        actions.columnconfigure(0, weight=1)
        ttk.Button(actions, text="Gerar .tex", command=self.on_run, style="Accent.TButton").grid(row=0, column=0, sticky="w")
        ttk.Button(actions, text="Gerar PDF", command=self.on_run_pdf, style="Accent.TButton").grid(row=0, column=0, padx=104, sticky="w")
        ttk.Button(actions, text="Salvar Preferências", command=self.on_save).grid(row=0, column=1, padx=8, sticky="w")
        self.btn_editor = ttk.Button(actions, text="Revisar/Editar Questões…", command=self.open_editor, state="disabled")
        self.btn_editor.grid(row=0, column=2, padx=8, sticky="w")

        # Seleção/duplo-clique
        self.tbl.bind("<<TreeviewSelect>>", lambda e: self._update_buttons_state())
        self.tbl.bind("<Double-1>", lambda e: self.open_editor())

        # Drag-and-drop (se disponível)
        if _HAS_DND and hasattr(self.tbl, "drop_target_register"):
            try:
                self.tbl.drop_target_register(DND_FILES)
                self.tbl.dnd_bind("<<Drop>>", self._on_drop_files)
            except Exception:
                pass

    def _build_bottom(self):
        ttk.Label(self.bottom, text="Log").grid(row=0, column=0, sticky="w")

        self.txt_log = tk.Text(self.bottom, height=5, wrap="word", undo=False)
        self.txt_log.grid(row=1, column=0, sticky="nsew", padx=(0,0), pady=6)
        vbar = ttk.Scrollbar(self.bottom, orient="vertical", command=self.txt_log.yview)
        vbar.grid(row=1, column=1, sticky="ns", pady=6)
        self.txt_log.configure(yscrollcommand=vbar.set)

        self.txt_log.configure(state="disabled")
        self.txt_log.bind("<Key>", lambda e: "break")
        self.txt_log.bind("<Control-v>", lambda e: "break")
        self.txt_log.bind("<Button-2>", lambda e: "break")

        self._log_menu = tk.Menu(self.txt_log, tearoff=0)
        self._log_menu.add_command(label="Copiar", command=lambda: self.txt_log.event_generate("<<Copy>>"))
        self._log_menu.add_command(label="Copiar tudo", command=self._copy_all_log)
        self.txt_log.bind("<Button-3>", self._show_log_menu)

        status = ttk.Frame(self.bottom)
        status.grid(row=2, column=0, columnspan=2, sticky="ew")
        ttk.Label(status, textvariable=self.var_status).grid(row=0, column=0, sticky="w")

    # ====== constraints e sash ======
    def _apply_pane_constraints_and_place_sash(self):
        try:
            f = tkfont.nametofont(self.txt_log.cget("font"))
            line_h = max(14, f.metrics("linespace"))
        except Exception:
            line_h = 16
        log_min_px = int(line_h * 5 + 24)
        top_min_px = 400
        try:
            self.paned.paneconfigure(self.bottom, minsize=log_min_px)
            self.paned.paneconfigure(self.top_container, minsize=top_min_px)
        except Exception:
            pass
        self.update_idletasks()
        total_h = max(0, self.winfo_height())
        top_req = self.top_container.winfo_reqheight()
        sash_y = max(top_min_px, min(top_req, total_h - log_min_px - 8))
        try:
            self.paned.sash_place(0, 0, sash_y)
        except Exception:
            pass
        def _on_resize(_evt=None):
            self.update_idletasks()
            total = max(0, self.winfo_height())
            top_req2 = self.top_container.winfo_reqheight()
            sash = max(top_min_px, min(top_req2, total - log_min_px - 8))
            try:
                self.paned.sash_place(0, 0, sash)
            except Exception:
                pass
        if not hasattr(self, "_resize_bound"):
            self.bind("<Configure>", _on_resize)
            self._resize_bound = True

    # ====== utils ======
    def _show_log_menu(self, event):
        try:
            self._log_menu.tk_popup(event.x_root, event.y_root)
        finally:
            self._log_menu.grab_release()

    def _copy_all_log(self):
        self.txt_log.configure(state="normal")
        try:
            self.txt_log.tag_add("sel", "1.0", "end-1c")
            self.txt_log.event_generate("<<Copy>>")
            self.txt_log.tag_remove("sel", "1.0", "end")
        finally:
            self.txt_log.configure(state="disabled")

    def _bind_events(self):
        for w in (self, self.top_container, self.bottom, self.top):
            w.grid_propagate(True)

    # ====== tabela / DnD ======
    def _on_drop_files(self, event):
        raw = event.data
        paths = []
        token = ""
        brace = False
        for ch in raw:
            if ch == "{":
                brace = True
                token = ""
            elif ch == "}":
                brace = False
                if token:
                    paths.append(token)
                    token = ""
            elif ch == " " and not brace:
                if token:
                    paths.append(token)
                    token = ""
            else:
                token += ch
        if token:
            paths.append(token)

        jsons = [p for p in paths if p.lower().endswith(".json")]
        if jsons:
            self._add_json_paths(jsons)

    def _get_json_paths(self):
        paths = []
        for iid in self.tbl.get_children():
            p = self.tbl.item(iid, "values")[0]
            if p and p not in paths:
                paths.append(p)
        return paths

    def _add_json_paths(self, paths):
        existing = set(self._get_json_paths())
        for p in paths:
            if p not in existing:
                self.tbl.insert("", "end", values=(p,))
        self.update_output_path()
        self._update_buttons_state()
        self._update_scrollbar_visibility()

    def browse_jsons(self):
        sel = filedialog.askopenfilenames(
            title="Escolher JSON de questões (múltiplos)",
            filetypes=[("JSON", "*.json"), ("Todos", "*.*")]
        )
        if not sel:
            return
        self._add_json_paths(list(sel))

    def delete_selected(self):
        sel = self.tbl.selection()
        if not sel:
            return
        self.tbl.delete(sel[0])
        self.update_output_path()
        self._update_buttons_state()
        self._update_scrollbar_visibility()

    def _update_buttons_state(self):
        sel = self.tbl.selection()
        state = "normal" if len(sel)==1 else "disabled"
        self.btn_editor.configure(state=state)
        self.btn_delete.configure(state=state)

    def _update_scrollbar_visibility(self):
        items = len(self.tbl.get_children())
        if items > TREE_HEIGHT_ROWS:
            self.vsb.grid(row=0, column=1, sticky="ns")
            self.tbl.configure(yscrollcommand=self.vsb.set)
        else:
            try:
                self.vsb.grid_remove()
            except Exception:
                pass
            self.tbl.configure(yscrollcommand=None)

    def update_output_path(self):
        paths = self._get_json_paths()
        if not paths:
            self.var_output.set("")
            return
        if len(paths)==1:
            jpath = Path(paths[0])
            out = jpath.with_name(jpath.stem + "_slides.tex")
        else:
            first = Path(paths[0])
            out = first.with_name(first.stem + "_combined_slides.tex")
        self.var_output.set(str(out))

    def open_output_dir(self):
        out = self.var_output.get().strip()
        if not out:
            return
        folder = str(Path(out).resolve().parent)
        try:
            if sys.platform.startswith("win"):
                import os
                os.startfile(folder)  # type: ignore
            elif sys.platform == "darwin":
                import os
                os.system(f'open "{folder}"')
            else:
                import os
                os.system(f'xdg-open "{folder}"')
        except Exception as e:
            messagebox.showerror("Abrir pasta", f"Não foi possível abrir a pasta:\n{e}")

    def pick_color(self):
        _, hexcolor = colorchooser.askcolor()
        if hexcolor:
            self.var_alert.set(hexcolor)

    def log(self, text):
        ts = datetime.now().strftime("%H:%M:%S")
        line = f"[{ts}] {text}\n"
        self.txt_log.configure(state="normal")
        try:
            self.txt_log.insert("end", line)
            self.txt_log.see("end")
        finally:
            self.txt_log.configure(state="disabled")

    # ====== validação e preparação ======
    def _load_and_merge_jsons(self, paths):
        all_qs = []
        for p in paths:
            if not Path(p).exists():
                raise FileNotFoundError(f"Arquivo não encontrado: {p}")
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, list):
                raise ValueError(f"O JSON precisa ser um array de questões: {p}")
            all_qs.extend(data)
        all_qs.sort(key=lambda q: q.get("id", 0))
        for i, q in enumerate(all_qs, start=1):
            q["id"] = int(i)
        return all_qs

    def _prepare_combined_json(self, paths):
        if len(paths)==1:
            return paths[0], False
        combined = self._load_and_merge_jsons(paths)
        first = Path(paths[0]).resolve()
        out_path = first.with_name(first.stem + "_combined.json")
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(combined, f, ensure_ascii=False, indent=2)
        return str(out_path), True

    def validate_inputs(self):
        paths = self._get_json_paths()
        if not paths:
            messagebox.showerror("Geração", "Adicione pelo menos um arquivo JSON.")
            return False
        try:
            self._load_and_merge_jsons(paths)
        except Exception as e:
            messagebox.showerror("Geração", f"Erro validando JSONs:\n{e}")
            return False
        return True

    # ====== ações ======
    def on_run(self):
        if not self.validate_inputs():
            return
        paths = self._get_json_paths()
        temp_json, is_temp = self._prepare_combined_json(paths)

        out = self.var_output.get().strip()
        title = self.var_title.get().strip() or DEFAULTS["title"]
        fsq = self.var_fsq.get().strip() or DEFAULTS["fsq"]
        fsa = self.var_fsa.get().strip() or DEFAULTS["fsa"]
        alert = self.var_alert.get().strip() or DEFAULTS["alert_color"]
        seed = self.var_seed.get().strip() or None
        self.var_status.set("Gerando .tex…")
        self.log(f"Iniciando geração para {len(paths)} JSON(s).")

        t = threading.Thread(
            target=self._run_json2beamer,
            args=(temp_json, out, seed, title, fsq, fsa, alert, is_temp),
            daemon=True
        )
        t.start()

    def on_save(self):
        values = {
            "title": self.var_title.get().strip(),
            "fsq": self.var_fsq.get().strip(),
            "fsa": self.var_fsa.get().strip(),
            "alert_color": self.var_alert.get().strip(),
            "shuffle_seed": self.var_seed.get().strip(),
        }
        save_prefs(values)
        self.log(f"Preferências salvas em {get_ini_path()}")
        self.var_status.set("Preferências salvas.")

    def _run_json2beamer(self, json_in, out, seed, title, fsq, fsa, alert, is_temp):
        old_stdout = sys.stdout
        buf = io.StringIO()
        sys.stdout = buf
        try:
            rc = json2beamer(
                input_json=json_in,
                output_tex=out,
                shuffle_seed=seed,
                title=title,
                fsq=fsq,
                fsa=fsa,
                alert_color=alert
            )
        except Exception as e:
            sys.stdout = old_stdout
            self.var_status.set("Erro.")
            self.log(f"❌ Erro: {e}")
            return
        finally:
            try:
                if is_temp:
                    Path(json_in).unlink(missing_ok=True)
            except Exception:
                pass
        sys.stdout = old_stdout
        out_text = buf.getvalue().strip()
        if out_text:
            self.log(out_text)
        if rc == 0:
            self.var_status.set("Concluído com sucesso.")
            self.log(f"✅ Arquivo gerado em: {out}")
        else:
            self.var_status.set("Falhou (veja o log).")
            self.log(f"❌ Retorno: {rc}")

    def on_run_pdf(self):
        if not self.validate_inputs():
            return
        if shutil.which("pdflatex") is None:
            messagebox.showerror("PDF", "pdflatex não encontrado no PATH. Verifique a instalação do LaTeX.")
            return
        paths = self._get_json_paths()
        temp_json, is_temp = self._prepare_combined_json(paths)

        out = self.var_output.get().strip()
        title = self.var_title.get().strip() or DEFAULTS["title"]
        fsq = self.var_fsq.get().strip() or DEFAULTS["fsq"]
        fsa = self.var_fsa.get().strip() or DEFAULTS["fsa"]
        alert = self.var_alert.get().strip() or DEFAULTS["alert_color"]
        seed = self.var_seed.get().strip() or None
        self.var_status.set("Gerando .tex e compilando PDF…")
        self.log(f"Iniciando geração e compilação para {len(paths)} JSON(s).")
        t = threading.Thread(
            target=self._run_json2beamer_and_pdflatex,
            args=(temp_json, out, seed, title, fsq, fsa, alert, is_temp),
            daemon=True
        )
        t.start()

    def _run_json2beamer_and_pdflatex(self, json_in, out, seed, title, fsq, fsa, alert, is_temp):
        old_stdout = sys.stdout
        buf = io.StringIO()
        sys.stdout = buf
        try:
            rc = json2beamer(
                input_json=json_in,
                output_tex=out,
                shuffle_seed=seed,
                title=title,
                fsq=fsq,
                fsa=fsa,
                alert_color=alert
            )
        except Exception as e:
            sys.stdout = old_stdout
            self.var_status.set("Erro.")
            self.log(f"❌ Erro gerando .tex: {e}")
            return
        finally:
            try:
                if is_temp:
                    Path(json_in).unlink(missing_ok=True)
            except Exception:
                pass
        sys.stdout = old_stdout
        out_text = buf.getvalue().strip()
        if out_text:
            self.log(out_text)
        if rc != 0:
            self.var_status.set("Falhou ao gerar .tex (veja o log).")
            self.log(f"❌ Retorno: {rc}")
            return

        self.log(f"✅ .tex gerado: {out}")
        try:
            tex_path = Path(out).resolve()
            workdir = tex_path.parent
            pdf_name = tex_path.with_suffix(".pdf").name
            cmd = ["pdflatex", "-interaction=nonstopmode", "-halt-on-error", tex_path.name]
            for i in range(2):
                self.log(f"Compilando (passagem {i+1}/2)…")
                proc = subprocess.run(cmd, cwd=str(workdir), capture_output=True, text=True)
                if proc.stdout:
                    self.log(proc.stdout.strip())
                if proc.returncode != 0:
                    if proc.stderr:
                        self.log(proc.stderr.strip())
                    raise RuntimeError(f"pdflatex retornou código {proc.returncode}")
            pdf_path = workdir / pdf_name
            if pdf_path.exists():
                self.var_status.set("PDF gerado com sucesso.")
                self.log(f"✅ PDF gerado em: {pdf_path}")
                try:
                    import os
                    if os.name == "nt":
                        os.startfile(str(pdf_path))
                    else:
                        opener = "open" if sys.platform == "darwin" else "xdg-open"
                        subprocess.Popen([opener, str(pdf_path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    self.log("📂 PDF aberto no visualizador padrão.")
                except Exception as e:
                    self.log(f"⚠️ Não foi possível abrir automaticamente o PDF: {e}")
            else:
                self.var_status.set("Compilação terminou, mas o PDF não foi localizado.")
                self.log("⚠️ pdflatex executou, mas o arquivo .pdf não foi encontrado.")
        except Exception as e:
            self.var_status.set("Erro na compilação do PDF.")
            self.log(f"❌ Erro no pdflatex: {e}")

    # ====== Editor com import robusto ======
    def _get_question_editor_class(self):
        # Tentativa 1: pacote instalado/nomeado
        try:
            from json2beamer_app.editor.question_editor import QuestionEditor  # type: ignore
            return QuestionEditor
        except Exception:
            pass
        # Tentativa 2: import relativo (quando executado via módulo)
        try:
            from ..editor.question_editor import QuestionEditor  # type: ignore
            return QuestionEditor
        except Exception:
            pass
        # Tentativa 3: adicionar raiz do projeto ao sys.path e tentar 'editor.question_editor'
        here = Path(__file__).resolve()
        project_root = here.parent.parent  # .../json2beamer_app
        parent_of_root = project_root.parent
        if str(parent_of_root) not in sys.path:
            sys.path.insert(0, str(parent_of_root))
        try:
            from editor.question_editor import QuestionEditor  # type: ignore
            return QuestionEditor
        except Exception:
            pass
        # Tentativa 4: carregar por caminho de arquivo
        editor_path = (project_root / "editor" / "question_editor.py").resolve()
        if editor_path.exists():
            spec = importlib.util.spec_from_file_location("json2beamer_editor_fallback", str(editor_path))
            if spec and spec.loader:
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)  # type: ignore
                if hasattr(mod, "QuestionEditor"):
                    return getattr(mod, "QuestionEditor")
        raise ImportError("Não foi possível localizar QuestionEditor. Verifique a estrutura do projeto.")

    def open_editor(self):
        sel = self.tbl.selection()
        if len(sel) != 1:
            messagebox.showwarning("Editor", "Selecione exatamente 1 linha para abrir o editor.")
            return
        path = self.tbl.item(sel[0], "values")[0]
        if not Path(path).exists():
            messagebox.showerror("Editor", "O arquivo JSON indicado não existe.")
            return
        try:
            QuestionEditor = self._get_question_editor_class()
            QuestionEditor(self.master, path, on_saved=lambda: self.log("JSON atualizado pelo editor."))
        except Exception as e:
            messagebox.showerror("Editor", f"Não foi possível abrir o editor:\n{e}")
