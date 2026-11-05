"""Local desktop registrar. Tk is optional and imported only for the GUI command."""

from __future__ import annotations

import json
import queue
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from . import __version__
from .canonical import loads_strict
from .desktop import DesktopSession, addresses_text, parse_addresses
from .errors import RALValidationError
from .models import validate_claim

BG, WHITE, INK, MUTED = "#f3f5f7", "#ffffff", "#18283b", "#56677b"
ACCENT, NAV = "#176b68", "#162c40"
STATUS = {
    "pending": "待審核",
    "accepted": "已核准",
    "rejected": "已駁回",
    "active": "有效",
    "suspended": "暫停",
    "revoked": "已撤銷",
}
ERRORS = {
    "head_mismatch": "帳本已被其他視窗更新。請重新整理並核對最新內容，再操作一次。",
    "address_collision": "地址已由另一位有效或暫停中的住民使用。請核對地址，不會覆寫原紀錄。",
    "application_not_pending": "這份申請已經處理。請重新整理查看結果。",
    "signer_locked": "請先解鎖管理金鑰，或使用示範模式。",
    "signer_not_current": "這把金鑰不是目前帳本的管理金鑰。",
    "claim_invalid": "申請欄位不符合公版格式，請檢查名稱、角色、地址與公開同意。",
    "duplicate_address": "同一份申請有重複地址，請移除重複項目。",
    "review_evidence_required": "至少填寫一項審核依據參照，每行一項。",
    "passphrase_too_short": "請使用至少 12 個字元的金鑰密碼。",
    "workspace_name_invalid": "工作區名稱不可空白，也不可包含路徑分隔符號。",
}


def lines(value):
    return [line.strip() for line in value.splitlines() if line.strip()]


class RegistrarApp:
    def __init__(self, root):
        self.root = root
        self.session = None
        self.snapshot = None
        self.busy = False
        self.last_request = None
        self.uncertain = False
        self.page = "applications"
        self.jobs = ThreadPoolExecutor(max_workers=1, thread_name_prefix="registrar")
        self.results = queue.Queue()
        self.root.title("SEDB-RAL · 公開戶籍管理")
        self.root.geometry("1220x820")
        self.root.minsize(1000, 720)
        self.root.configure(bg=BG)
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self._style()
        self._shell()
        self.show_welcome()
        self.root.after(70, self._poll)

    def _style(self):
        s = ttk.Style(self.root)
        s.theme_use("clam")
        s.configure(
            ".", font=("Microsoft JhengHei UI", 10), background=BG, foreground=INK
        )
        s.configure("TButton", padding=(13, 8), background=WHITE, borderwidth=1)
        s.map("TButton", background=[("active", "#e3eeed"), ("disabled", "#edf0f3")])
        s.configure("Primary.TButton", background=ACCENT, foreground=WHITE)
        s.map(
            "Primary.TButton",
            background=[("active", "#105955"), ("disabled", "#a9bbb9")],
        )
        s.configure("Danger.TButton", foreground="#a12d3b")
        s.configure("White.TFrame", background=WHITE)
        s.configure("Title.TLabel", font=("Microsoft JhengHei UI", 22, "bold"))
        s.configure("Muted.TLabel", foreground=MUTED)
        s.configure(
            "Treeview",
            background=WHITE,
            fieldbackground=WHITE,
            rowheight=40,
            borderwidth=0,
        )
        s.configure(
            "Treeview.Heading",
            background="#e9eef2",
            padding=9,
            font=("Microsoft JhengHei UI", 10, "bold"),
        )
        s.map(
            "Treeview",
            background=[("selected", "#d8eeea")],
            foreground=[("selected", INK)],
        )
        s.configure("TEntry", padding=6)
        s.configure("TCombobox", padding=6)

    def _shell(self):
        sidebar = tk.Frame(self.root, bg=NAV, width=190)
        sidebar.pack(side="left", fill="y")
        sidebar.pack_propagate(False)
        tk.Label(
            sidebar, text="SEDB / RAL", fg=WHITE, bg=NAV, font=("Segoe UI", 18, "bold")
        ).pack(anchor="w", padx=20, pady=(28, 4))
        tk.Label(
            sidebar,
            text="公開戶籍管理\nPUBLIC REGISTRAR",
            fg="#a8c4cd",
            bg=NAV,
            justify="left",
            font=("Microsoft JhengHei UI", 10),
        ).pack(anchor="w", padx=20, pady=(0, 28))
        for text, key in (
            ("01   申請與審核", "applications"),
            ("02   住民名冊", "residents"),
            ("03   操作紀錄", "history"),
        ):
            tk.Button(
                sidebar,
                text=text,
                bg=NAV,
                fg=WHITE,
                relief="flat",
                anchor="w",
                activebackground="#284b62",
                activeforeground=WHITE,
                cursor="hand2",
                font=("Microsoft JhengHei UI", 11),
                padx=18,
                pady=13,
                command=lambda k=key: self.show_page(k),
            ).pack(fill="x", pady=2)
        tk.Frame(sidebar, height=1, bg="#365366").pack(fill="x", padx=18, pady=24)
        for text, command in (
            ("開啟帳本…", self.open_workspace),
            ("建立新工作區…", self.new_workspace),
            ("試用示範資料", self.demo),
        ):
            tk.Button(
                sidebar,
                text=text,
                command=command,
                bg=NAV,
                fg="#d3e4e9",
                activebackground="#284b62",
                activeforeground=WHITE,
                relief="flat",
                anchor="w",
                padx=20,
                pady=10,
                font=("Microsoft JhengHei UI", 10),
            ).pack(fill="x")
        tk.Label(
            sidebar,
            text=f"MIT · Preview {__version__}\n本機檔案 · 無雲端連線",
            justify="left",
            bg=NAV,
            fg="#a8c4cd",
            font=("Microsoft JhengHei UI", 9),
        ).pack(side="bottom", anchor="w", padx=20, pady=24)
        self.main = ttk.Frame(self.root, padding=24)
        self.main.pack(side="left", fill="both", expand=True)
        self.context = tk.StringVar(value="尚未開啟帳本")
        ttk.Label(
            self.main, textvariable=self.context, style="Muted.TLabel", wraplength=850
        ).pack(anchor="w")
        toolbar = ttk.Frame(self.main)
        toolbar.pack(fill="x", pady=(12, 15))
        self.heading = ttk.Label(toolbar, text="開始管理公開戶籍", style="Title.TLabel")
        self.heading.pack(side="left")
        self.new_button = ttk.Button(
            toolbar,
            text="＋ 新增申請",
            style="Primary.TButton",
            command=self.new_application,
        )
        self.new_button.pack(side="right")
        self.refresh_button = ttk.Button(toolbar, text="重新整理", command=self.refresh)
        self.refresh_button.pack(side="right", padx=8)
        self.key_button = ttk.Button(toolbar, text="解鎖金鑰", command=self.key_action)
        self.key_button.pack(side="right")
        self.summary = ttk.Label(
            self.main, text="只管理公開登記資料。", style="Muted.TLabel"
        )
        self.summary.pack(anchor="w", pady=(0, 12))
        self.content = ttk.Frame(self.main)
        self.content.pack(fill="both", expand=True)
        self.progress = ttk.Progressbar(self.main, mode="indeterminate")
        self.progress.pack(fill="x", pady=(12, 6))
        self.notice = tk.StringVar(value="準備就緒")
        ttk.Label(self.main, textvariable=self.notice, wraplength=900).pack(anchor="w")
        self._controls()

    def _controls(self):
        ready = self.session is not None and not self.busy
        writable = ready and self.session.signer is not None and not self.uncertain
        self.new_button.configure(state="normal" if writable else "disabled")
        self.refresh_button.configure(state="normal" if ready else "disabled")
        self.key_button.configure(
            state="normal" if ready else "disabled",
            text="鎖定金鑰" if self.session and self.session.signer else "解鎖金鑰",
        )

    def _clear(self):
        for widget in self.content.winfo_children():
            widget.destroy()

    def show_welcome(self):
        self._clear()
        card = ttk.Frame(self.content, style="White.TFrame", padding=32)
        card.pack(fill="both", expand=True)
        tk.Label(
            card,
            text="一個清楚、可追溯的戶籍工作台",
            font=("Microsoft JhengHei UI", 22, "bold"),
            bg=WHITE,
            fg=INK,
        ).pack(anchor="w", pady=(25, 15))
        tk.Label(
            card,
            text="收件、審核、建立名冊，並持續管理住民狀態。\n申請是聲明；核准是管理人的登記認證。",
            bg=WHITE,
            fg=MUTED,
            justify="left",
            font=("Microsoft JhengHei UI", 13),
        ).pack(anchor="w", pady=(0, 28))
        ttk.Button(
            card, text="試用示範資料 →", style="Primary.TButton", command=self.demo
        ).pack(anchor="w", pady=8)
        ttk.Button(card, text="建立我的工作區…", command=self.new_workspace).pack(
            anchor="w", pady=8
        )
        ttk.Button(card, text="開啟既有公版帳本…", command=self.open_workspace).pack(
            anchor="w", pady=8
        )
        tk.Label(
            card,
            text="示範資料使用暫存帳本與記憶體金鑰，關閉後清除。\n正式工作區保存加密金鑰與公開帳本；只讀查閱不需解鎖。\n\n請只輸入有意公開的資料；歷史更正與撤銷不會刪除舊紀錄。",
            bg=WHITE,
            fg=MUTED,
            justify="left",
            font=("Microsoft JhengHei UI", 10),
        ).pack(anchor="w", pady=28)

    def _run(self, label, work, done, failed=None):
        if self.busy:
            return
        self.busy = True
        self._controls()
        self.notice.set(label)
        self.progress.start(12)
        future = self.jobs.submit(work)
        future.add_done_callback(lambda f: self.results.put((f, done, failed)))

    def _poll(self):
        try:
            future, done, failed = self.results.get_nowait()
        except queue.Empty:
            pass
        else:
            self.busy = False
            self.progress.stop()
            try:
                result = future.result()
            except Exception as error:
                if failed:
                    failed(error)
                else:
                    self.error(error)
            else:
                done(result)
            self._controls()
        self.root.after(70, self._poll)

    def error(self, error, parent=None):
        if isinstance(error, RALValidationError):
            code = error.code
            message = (
                ERRORS.get(
                    code, "操作未完成，原有資料未被自動修正。請核對欄位與帳本狀態。"
                )
                + f"\n\n代碼：{code}"
            )
        elif isinstance(error, ValueError):
            message = (
                str(error) if type(error) is ValueError else "資料或金鑰密碼無法驗證。"
            )
        else:
            message = "檔案操作未完成。請檢查指定目錄、檔案權限與可用空間。\n若剛建立工作區，請保留已產生的檔案，勿覆蓋重建。"
        self.notice.set(message.replace("\n", " "))
        messagebox.showerror("操作未完成", message, parent=parent or self.root)

    def _can_switch(self):
        if self.busy:
            return False
        if self.uncertain:
            messagebox.showinfo(
                "請先核對回執",
                "上次操作結果尚未確認。請先在操作紀錄查詢回執，再切換工作區。",
                parent=self.root,
            )
            return False
        return True

    def _use(self, session):
        if self.session:
            self.session.close()
        self.session = session
        self.last_request = None
        self.uncertain = False
        self.refresh()

    def demo(self):
        if self._can_switch():
            self._run("建立合成示範資料…", DesktopSession.demo, self._use)

    def open_workspace(self):
        if not self._can_switch():
            return
        path = filedialog.askdirectory(
            title="選擇包含 registry.json 的公版帳本目錄", parent=self.root
        )
        if path:
            self._run("驗證並開啟帳本…", lambda: DesktopSession.open(path), self._use)

    def dialog(self, title, width=650, height=560):
        d = tk.Toplevel(self.root)
        d.title(title)
        d.geometry(f"{width}x{height}")
        d.minsize(width, height)
        d.configure(bg=BG)
        d.transient(self.root)
        d.grab_set()
        frame = ttk.Frame(d, padding=24)
        frame.pack(fill="both", expand=True)
        return d, frame

    @staticmethod
    def entry(frame, label, value="", secret=False):
        ttk.Label(frame, text=label).pack(anchor="w", pady=(10, 4))
        variable = tk.StringVar(value=value)
        entry = ttk.Entry(frame, textvariable=variable, show="•" if secret else "")
        entry.pack(fill="x")
        return variable, entry

    @staticmethod
    def text_field(frame, label, value="", height=3):
        ttk.Label(frame, text=label).pack(anchor="w", pady=(10, 4))
        text = tk.Text(
            frame,
            height=height,
            font=("Microsoft JhengHei UI", 10),
            wrap="word",
            relief="solid",
            borderwidth=1,
            padx=8,
            pady=6,
            undo=True,
        )
        text.insert("1.0", value)
        text.pack(fill="x")
        return text

    def new_workspace(self):
        if not self._can_switch():
            return
        parent = filedialog.askdirectory(
            title="選擇新工作區的上層目錄", parent=self.root
        )
        if not parent:
            return
        d, f = self.dialog("建立公版工作區", 650, 480)
        ttk.Label(f, text="建立新的本機工作區", style="Title.TLabel").pack(anchor="w")
        ttk.Label(
            f,
            text="新建獨立資料夾；不會開啟或搬移其他系統的資料。",
            style="Muted.TLabel",
        ).pack(anchor="w", pady=8)
        name, first = self.entry(f, "資料夾名稱", "public-registry")
        password, _ = self.entry(
            f, "管理金鑰密碼（至少 12 字元，請自行備份）", secret=True
        )
        confirmation, _ = self.entry(f, "再次輸入密碼", secret=True)
        ttk.Label(
            f,
            text="將保存：registrar.pem（加密）、registrar.pub、registry/\n沒有遺失密碼復原功能。這不是 Windows PIN。",
            style="Muted.TLabel",
        ).pack(anchor="w", pady=15)

        def create():
            if password.get() != confirmation.get():
                messagebox.showerror("密碼不同", "兩次密碼不一致。", parent=d)
                return
            n, p = name.get().strip(), password.get()
            password.set("")
            confirmation.set("")
            d.destroy()
            self._run(
                "建立加密金鑰與帳本…",
                lambda: DesktopSession.create(parent, n, p),
                self._use,
            )

        ttk.Button(f, text="建立並開啟", style="Primary.TButton", command=create).pack(
            anchor="e", pady=10
        )
        first.focus_set()

    def key_action(self):
        if self.busy or not self.session:
            return
        if self.session.signer:
            self.session.lock()
            self.notice.set("管理金鑰已鎖定；可繼續只讀查閱。")
            self.show_page(self.page)
            self._controls()
            return
        path = filedialog.askopenfilename(
            title="選擇加密管理金鑰",
            filetypes=[("Encrypted PEM", "*.pem"), ("All", "*")],
            parent=self.root,
        )
        if not path:
            return
        d, f = self.dialog("解鎖管理金鑰", 600, 300)
        ttk.Label(f, text="只在本次程序記憶體中解鎖", style="Title.TLabel").pack(
            anchor="w"
        )
        password, first = self.entry(f, "金鑰密碼", secret=True)
        ttk.Label(
            f,
            text="關閉或鎖定後需重新解鎖。密碼不寫入帳本或設定。",
            style="Muted.TLabel",
        ).pack(anchor="w", pady=15)

        def unlock():
            p = password.get()
            password.set("")
            d.destroy()
            self._run(
                "解鎖並核對目前管理公鑰…",
                lambda: self.session.unlock(path, p),
                lambda _: self.refresh(),
            )

        ttk.Button(f, text="解鎖", style="Primary.TButton", command=unlock).pack(
            anchor="e"
        )
        first.focus_set()

    def refresh(self):
        if self.session and not self.busy:
            self._run("讀取並驗證簽章帳本…", self.session.snapshot, self._loaded)

    def _loaded(self, snapshot):
        self.snapshot = snapshot
        mode = "示範資料（關閉即清除）" if self.session.temporary else "本機公版帳本"
        self.context.set(f"{mode}   /   {snapshot['registry_id']}")
        pending = sum(a["status"] == "pending" for a in snapshot["applications"])
        active = sum(r["status"] == "active" for r in snapshot["residents"])
        self.summary.configure(
            text=f"待審核  {pending}     有效住民  {active}     全部住民  {len(snapshot['residents'])}     紀錄  {snapshot['sequence']}     · 簽章與帳本鏈已核對"
        )
        self.notice.set("已載入。帳本內部一致；未提供外部信任根或防回退檢查點。")
        self.show_page(self.page)

    def show_page(self, page):
        if self.busy or not self.snapshot:
            return
        self.page = page
        self._clear()
        self.heading.configure(
            text={
                "applications": "申請與審核",
                "residents": "住民名冊",
                "history": "操作紀錄",
            }[page]
        )
        if page == "history":
            self.history()
            return
        toolbar = ttk.Frame(self.content)
        toolbar.pack(fill="x", pady=(0, 12))
        ttk.Label(toolbar, text="搜尋").pack(side="left", padx=(0, 8))
        search = tk.StringVar()
        ttk.Entry(toolbar, textvariable=search, width=28).pack(side="left")
        ttk.Label(toolbar, text="狀態").pack(side="left", padx=(18, 8))
        options = ["全部"] + [
            STATUS[s]
            for s in (
                ("pending", "accepted", "rejected")
                if page == "applications"
                else ("active", "suspended", "revoked")
            )
        ]
        status = tk.StringVar(value="待審核" if page == "applications" else "全部")
        ttk.Combobox(
            toolbar, textvariable=status, values=options, state="readonly", width=12
        ).pack(side="left")
        panels = ttk.Panedwindow(self.content, orient="vertical")
        panels.pack(fill="both", expand=True)
        upper, lower = (
            ttk.Frame(panels),
            ttk.Frame(panels, padding=(16, 10), style="White.TFrame"),
        )
        panels.add(upper, weight=3)
        panels.add(lower, weight=2)
        columns = ("name", "status", "role", "address")
        tree = ttk.Treeview(
            upper, columns=columns, show="headings", selectmode="browse", height=6
        )
        for key, label, width in zip(
            columns, ("名稱", "狀態", "角色", "公開地址"), (170, 90, 260, 300)
        ):
            tree.heading(
                key, text=label, command=lambda k=key: self.sort_table(tree, k)
            )
            tree.column(key, width=width, minwidth=75)
        scroll = ttk.Scrollbar(upper, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        tree.pack(fill="both", expand=True)
        empty = ttk.Label(upper, text="", style="Muted.TLabel")
        empty.pack(anchor="w", pady=6)
        detail = tk.Text(
            lower,
            height=5,
            wrap="word",
            bg=WHITE,
            fg=INK,
            relief="flat",
            font=("Microsoft JhengHei UI", 10),
            padx=2,
            pady=4,
        )
        detail.pack(fill="both", expand=True)
        actions = ttk.Frame(lower)
        actions.pack(fill="x", pady=(6, 0))
        records = {
            a["application_id"] if page == "applications" else a["resident_id"]: a
            for a in self.snapshot[page]
        }

        def selected(_event=None):
            for child in actions.winfo_children():
                child.destroy()
            ids = tree.selection()
            item = records.get(ids[0]) if ids else None
            detail.configure(state="normal")
            detail.delete("1.0", "end")
            if not item:
                detail.insert("1.0", "選擇一筆紀錄以檢視詳細內容與可執行操作。")
            else:
                claim = item["claim"] if page == "applications" else item
                identifier = (
                    item.get("application_id")
                    if page == "applications"
                    else item["resident_id"]
                )
                text = (
                    f"{claim['display_label']}  ·  {STATUS[item['status']]}\n{identifier}\n角色：{claim['role_description']}\n地址：\n{addresses_text(claim['addresses'])}\n依據參照："
                    + ", ".join(
                        item.get("evidence_refs", claim.get("evidence_refs", []))
                    )
                )
                if item.get("reason"):
                    text += "\n駁回原因：" + item["reason"]
                if page == "residents":
                    text += f"\n認證基礎：管理人登記認證；連續身分未建立\nInstance：{item['instance_id']}"
                detail.insert("1.0", text)
                writable = self.session.signer is not None and not self.uncertain
                if page == "applications" and item["status"] == "pending":
                    for title, kind in (
                        ("審核並核准…", "admit"),
                        ("駁回申請…", "reject"),
                    ):
                        ttk.Button(
                            actions,
                            text=title,
                            command=lambda k=kind: self.review(item, k),
                            state="normal" if writable else "disabled",
                        ).pack(side="left", padx=(0, 8))
                if page == "residents" and item["status"] != "revoked":
                    ttk.Button(
                        actions,
                        text="變更狀態…",
                        command=lambda: self.change_status(item),
                        state="normal" if writable else "disabled",
                    ).pack(side="left", padx=(0, 8))
                    ttk.Button(
                        actions,
                        text="更正公開資料…",
                        command=lambda: self.new_application(item),
                        state="normal" if writable else "disabled",
                    ).pack(side="left")
                if not writable:
                    ttk.Label(
                        actions,
                        text="唯讀模式；寫入前請解鎖管理金鑰。",
                        style="Muted.TLabel",
                    ).pack(side="right")
            detail.configure(state="disabled")

        def filter_rows(*_):
            previous = tree.selection()
            tree.delete(*tree.get_children())
            query = search.get().casefold().strip()
            for identifier, item in records.items():
                claim = item["claim"] if page == "applications" else item
                if status.get() != "全部" and STATUS[item["status"]] != status.get():
                    continue
                if (
                    query
                    and query not in json.dumps(item, ensure_ascii=False).casefold()
                ):
                    continue
                tree.insert(
                    "",
                    "end",
                    iid=identifier,
                    values=(
                        claim["display_label"],
                        STATUS[item["status"]],
                        claim["role_description"],
                        claim["addresses"][0]["locator"],
                    ),
                )
            count = len(tree.get_children())
            empty.configure(
                text=f"顯示 {count} 筆"
                if count
                else "沒有符合的紀錄。可調整篩選，或按「新增申請」開始。"
            )
            if previous and tree.exists(previous[0]):
                tree.selection_set(previous)
            selected()

        search.trace_add("write", filter_rows)
        status.trace_add("write", filter_rows)
        tree.bind("<<TreeviewSelect>>", selected)
        filter_rows()

    @staticmethod
    def sort_table(tree, column):
        rows = sorted(
            (tree.set(row, column).casefold(), row) for row in tree.get_children()
        )
        reverse = getattr(tree, "_sort", None) == (column, False)
        if reverse:
            rows.reverse()
        for i, (_, row) in enumerate(rows):
            tree.move(row, "", i)
        tree._sort = (column, reverse)

    def new_application(self, resident=None):
        if self.busy or not self.session or not self.session.signer or self.uncertain:
            return
        head = self.snapshot["head"]
        d, f = self.dialog("更正公開資料" if resident else "新增公開申請", 760, 750)
        ttk.Label(
            f, text="更正公開資料" if resident else "新增公開申請", style="Title.TLabel"
        ).pack(anchor="w")
        ttk.Label(
            f, text="只填有意公開的內容。申請不會自動成為住民。", style="Muted.TLabel"
        ).pack(anchor="w", pady=5)
        claim = resident or {}
        label, first = self.entry(f, "名稱 *", claim.get("display_label", ""))
        role, _ = self.entry(f, "角色說明 *", claim.get("role_description", ""))
        addresses = self.text_field(
            f,
            "公開地址 *（每行：命名空間 | 類型 | 地址）",
            addresses_text(claim.get("addresses", [])),
            3,
        )
        ttk.Label(
            f,
            text="例：example | agent | urn:example:agent:alpha",
            style="Muted.TLabel",
        ).pack(anchor="w")
        evidence = self.text_field(
            f,
            "更正原因 *" if resident else "申請依據參照（每行一項，可留空）",
            height=2,
        )
        continuity = tk.StringVar(value="new")
        consent = tk.BooleanVar(value=False)
        if not resident:
            ttk.Label(f, text="連續性聲明（公版不建立歷史身分合併）").pack(
                anchor="w", pady=(12, 4)
            )
            ttk.Combobox(
                f,
                textvariable=continuity,
                values=("new", "uncertain"),
                state="readonly",
            ).pack(fill="x")
            ttk.Checkbutton(
                f, text="已收取申請人對上述公開資料的登記同意 *", variable=consent
            ).pack(anchor="w", pady=14)
        error = tk.StringVar()
        ttk.Label(f, textvariable=error, foreground="#a12d3b", wraplength=690).pack(
            anchor="w", pady=8
        )

        def save():
            try:
                changes = {
                    "display_label": label.get().strip(),
                    "role_description": role.get().strip(),
                    "addresses": parse_addresses(addresses.get("1.0", "end")),
                }
                if resident:
                    reason = evidence.get("1.0", "end").strip()
                    if not reason:
                        raise ValueError("請填寫更正原因。")
                    data = {
                        "resident_id": resident["resident_id"],
                        "changes": changes,
                        "reason": reason,
                    }
                else:
                    value = {
                        **changes,
                        "schema": "sedb-ral.public-claim/0.1",
                        "continuity_claim": continuity.get(),
                        "public_registration_opt_in": consent.get(),
                        "evidence_refs": lines(evidence.get("1.0", "end")),
                    }
                    validate_claim(value)
                    data = {"claim": value}
            except (ValueError, RALValidationError) as exc:
                error.set(ERRORS.get(getattr(exc, "code", ""), str(exc)))
                return
            d.destroy()
            self.commit("correct" if resident else "submit", data, head)

        buttons = ttk.Frame(f)
        buttons.pack(side="bottom", fill="x", pady=8)
        ttk.Button(buttons, text="取消", command=d.destroy).pack(side="right")
        ttk.Button(
            buttons,
            text="確認更正並保存" if resident else "送交審核",
            style="Primary.TButton",
            command=save,
        ).pack(side="right", padx=8)
        if not resident:

            def import_claim():
                path = filedialog.askopenfilename(
                    title="匯入公版申請 JSON", filetypes=[("JSON", "*.json")], parent=d
                )
                if path:
                    try:
                        value = validate_claim(
                            loads_strict(Path(path).read_text(encoding="utf-8"))
                        )
                        label.set(value["display_label"])
                        role.set(value["role_description"])
                        continuity.set(value["continuity_claim"])
                        addresses.delete("1.0", "end")
                        addresses.insert("1.0", addresses_text(value["addresses"]))
                        evidence.delete("1.0", "end")
                        evidence.insert("1.0", "\n".join(value["evidence_refs"]))
                        consent.set(False)
                        error.set("已匯入；請自行核對內容與公開同意。")
                    except Exception as exc:
                        self.error(exc, d)

            ttk.Button(buttons, text="匯入 JSON…", command=import_claim).pack(
                side="left"
            )
        first.focus_set()

    def review(self, item, kind):
        if self.busy:
            return
        head = self.snapshot["head"]
        d, f = self.dialog("審核申請", 690, 510)
        title = "核准登記" if kind == "admit" else "駁回申請"
        ttk.Label(
            f, text=title + " · " + item["claim"]["display_label"], style="Title.TLabel"
        ).pack(anchor="w")
        ttk.Label(
            f,
            text=f"申請：{item['application_id']}\n核准只代表管理人登記認證，不代表供應商或原生身分證明。",
            wraplength=640,
            style="Muted.TLabel",
        ).pack(anchor="w", pady=15)
        text = self.text_field(
            f,
            "審核依據參照 *（每行一項）" if kind == "admit" else "駁回原因 *",
            height=4,
        )
        error = tk.StringVar()
        ttk.Label(f, textvariable=error, foreground="#a12d3b").pack(anchor="w", pady=8)

        def save():
            value = text.get("1.0", "end").strip()
            if not value:
                error.set("請填寫審核依據或駁回原因。")
                return
            data = {"application_id": item["application_id"]}
            data.update(
                {"evidence_refs": lines(value)}
                if kind == "admit"
                else {"reason": value}
            )
            d.destroy()
            self.commit(kind, data, head)

        ttk.Button(f, text="確認" + title, style="Primary.TButton", command=save).pack(
            side="bottom", anchor="e", pady=8
        )
        text.focus_set()

    def change_status(self, item):
        if self.busy:
            return
        head = self.snapshot["head"]
        d, f = self.dialog("管理住民狀態", 650, 480)
        ttk.Label(
            f, text=item["display_label"] + " · 狀態管理", style="Title.TLabel"
        ).pack(anchor="w")
        ttk.Label(
            f,
            text="目前："
            + STATUS[item["status"]]
            + "\n暫停保留地址；撤銷是終止狀態，無法恢復。紀錄永遠保留。",
            style="Muted.TLabel",
        ).pack(anchor="w", pady=16)
        options = {
            STATUS[k]: k
            for k in ("active", "suspended", "revoked")
            if k != item["status"]
        }
        status = tk.StringVar(value=next(iter(options)))
        ttk.Combobox(
            f, textvariable=status, values=list(options), state="readonly"
        ).pack(fill="x")
        reason = self.text_field(f, "變更原因 *", height=3)
        error = tk.StringVar()
        ttk.Label(f, textvariable=error, foreground="#a12d3b").pack(anchor="w", pady=10)

        def save():
            value = reason.get("1.0", "end").strip()
            if not value:
                error.set("請填寫變更原因。")
                return
            if options[status.get()] == "revoked" and not messagebox.askyesno(
                "確認不可恢復的撤銷",
                "撤銷後無法恢復此住民。確定保存這筆終止紀錄？",
                parent=d,
            ):
                return
            data = {
                "resident_id": item["resident_id"],
                "status": options[status.get()],
                "reason": value,
            }
            d.destroy()
            self.commit("status", data, head)

        ttk.Button(f, text="保存狀態變更", style="Primary.TButton", command=save).pack(
            side="bottom", anchor="e"
        )
        reason.focus_set()

    def commit(self, operation, data, head):
        request_id = DesktopSession.request_id()
        self.last_request = {
            "request_id": request_id,
            "operation": operation,
            "data": data,
            "head": head,
        }

        def work():
            receipt = self.session.write(
                operation, data, expected_head=head, request_id=request_id
            )
            return receipt, self.session.snapshot()

        def done(result):
            receipt, snapshot = result
            self._loaded(snapshot)
            self.notice.set(
                f"已保存並讀回 · {receipt['operation']} · 第 {receipt['sequence']} 筆。可至「操作紀錄」查看回執。"
            )

        def failed(error):
            if not isinstance(error, RALValidationError):
                self.uncertain = True
                self.show_page("history")
            self.error(error)

        self._run("保存簽章紀錄並讀回…", work, done, failed)

    def history(self):
        ttk.Label(
            self.content,
            text="每次管理操作保存一筆簽章事件；不刪改歷史。",
            style="Muted.TLabel",
        ).pack(anchor="w", pady=(0, 10))
        tree = ttk.Treeview(
            self.content,
            columns=("seq", "time", "op", "request"),
            show="headings",
            height=7,
        )
        for key, title, width in zip(
            ("seq", "time", "op", "request"),
            ("序號", "時間（UTC）", "操作", "請求 ID"),
            (60, 170, 220, 330),
        ):
            tree.heading(key, text=title)
            tree.column(key, width=width, minwidth=55)
        tree.pack(fill="both", expand=True)
        for event in reversed(self.snapshot["events"]):
            tree.insert(
                "",
                "end",
                values=(
                    event["sequence"],
                    event["recorded_at"],
                    event["operation"]["kind"],
                    event["request_id"],
                ),
            )
        detail = tk.Text(
            self.content,
            height=8,
            wrap="word",
            font=("Consolas", 10),
            relief="flat",
            padx=10,
            pady=10,
        )
        detail.pack(fill="both", expand=True, pady=(10, 0))
        detail.insert(
            "1.0",
            f"目前 head\n{self.snapshot['head']}\n\n目前管理公鑰指紋\n{self.snapshot['current_registrar_key_id']}",
        )
        detail.configure(state="disabled")
        if self.last_request:
            ttk.Label(
                self.content,
                text="最近請求：" + self.last_request["request_id"],
                wraplength=850,
            ).pack(anchor="w", pady=8)
            ttk.Button(
                self.content, text="查詢最近操作回執", command=self.check_receipt
            ).pack(anchor="w")
        else:
            ttk.Label(
                self.content, text="選取一列可查看事件內容。", style="Muted.TLabel"
            ).pack(anchor="w", pady=8)

        def select(_):
            selected = tree.selection()
            if not selected:
                return
            index = tree.index(selected[0])
            event = list(reversed(self.snapshot["events"]))[index]
            detail.configure(state="normal")
            detail.delete("1.0", "end")
            detail.insert("1.0", json.dumps(event, ensure_ascii=False, indent=2))
            detail.configure(state="disabled")

        tree.bind("<<TreeviewSelect>>", select)

    def check_receipt(self):
        if self.busy or not self.last_request:
            return

        def done(receipt):
            self.uncertain = False
            messagebox.showinfo(
                "已找到保存回執",
                json.dumps(receipt, ensure_ascii=False, indent=2),
                parent=self.root,
            )
            self.refresh()

        def failed(error):
            if (
                isinstance(error, RALValidationError)
                and error.code == "request_missing"
            ):
                self.uncertain = False
                self.notice.set(
                    "目前帳本未找到這筆請求。請核對目錄與檔案後重新整理，勿在其他帳本盲目重做。"
                )
            else:
                self.error(error)

        self._run(
            "查詢已保存回執（不重送）…",
            lambda: self.session.registry.receipt(self.last_request["request_id"]),
            done,
            failed,
        )

    def close(self):
        if self.busy:
            messagebox.showinfo(
                "仍在處理",
                "請等目前檔案操作完成後再關閉。沒有倒數或自動重送。",
                parent=self.root,
            )
            return
        self.jobs.shutdown(wait=False)
        if self.session:
            self.session.close()
        self.root.destroy()


def main():
    root = tk.Tk()
    RegistrarApp(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
