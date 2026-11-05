"""Tk callback integration (machine channel, not a human/visual usability claim)."""

import time

import pytest

tk = pytest.importorskip("tkinter", reason="Optional desktop Tk runtime unavailable")
from tkinter import ttk

from sedb_ral_public.desktop import DesktopSession, example_claim
from sedb_ral_public.gui import RegistrarApp


def settle(app):
    deadline = time.monotonic() + 10
    while app.busy and time.monotonic() < deadline:
        app.root.update()
        time.sleep(0.01)
    app.root.update()
    assert not app.busy


@pytest.fixture
def app(monkeypatch):
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("Tk display unavailable; callback tests require a desktop")
    root.withdraw()
    instance = RegistrarApp(root)
    monkeypatch.setattr(
        "sedb_ral_public.gui.messagebox.showerror", lambda *a, **k: None
    )
    monkeypatch.setattr("sedb_ral_public.gui.messagebox.showinfo", lambda *a, **k: None)
    try:
        yield instance
    finally:
        settle(instance)
        instance.close()


def descendants(widget):
    for child in widget.winfo_children():
        yield child
        yield from descendants(child)


def button(widget, text):
    return next(
        w
        for w in descendants(widget)
        if isinstance(w, ttk.Button) and w.cget("text") == text
    )


def test_demo_admission_roster_and_lock_callbacks(app):
    app.demo()
    settle(app)
    assert app.snapshot["sequence"] == 3
    tree = next(w for w in descendants(app.content) if isinstance(w, ttk.Treeview))
    pending = tree.get_children()
    assert len(pending) == 1
    tree.selection_set(pending[0])
    tree.event_generate("<<TreeviewSelect>>")
    app.root.update()
    button(app.content, "審核並核准…").invoke()
    dialog = next(w for w in app.root.winfo_children() if isinstance(w, tk.Toplevel))
    text = next(w for w in descendants(dialog) if isinstance(w, tk.Text))
    text.insert("1.0", "urn:example:review:callback")
    button(dialog, "確認核准登記").invoke()
    settle(app)
    assert len(app.snapshot["residents"]) == 2
    assert app.snapshot["sequence"] == 4
    app.show_page("residents")
    tree = next(w for w in descendants(app.content) if isinstance(w, ttk.Treeview))
    assert len(tree.get_children()) == 2
    app.key_action()
    assert app.session.signer is None
    assert app.new_button.instate(["disabled"])


def test_form_validation_keeps_form_and_never_writes(app):
    app.demo()
    settle(app)
    app.new_application()
    dialog = next(w for w in app.root.winfo_children() if isinstance(w, tk.Toplevel))
    button(dialog, "送交審核").invoke()
    app.root.update()
    assert dialog.winfo_exists()
    assert app.snapshot["sequence"] == 3
    dialog.destroy()


def test_stale_display_and_lost_ack_recovery_callbacks(app):
    app.demo()
    settle(app)
    head = app.snapshot["head"]
    app.session.write(
        "submit",
        {"claim": example_claim("Parallel", "parallel")},
        expected_head=head,
        request_id="test:parallel",
    )
    app.commit("submit", {"claim": example_claim("Stale", "stale")}, head)
    settle(app)
    assert "head_mismatch" in app.notice.get()
    assert app.session.snapshot()["sequence"] == 4
    app.refresh()
    settle(app)
    original = app.session.write

    def lose_ack(*args, **kwargs):
        original(*args, **kwargs)
        raise OSError("synthetic lost acknowledgement")

    app.session.write = lose_ack
    app.commit(
        "submit", {"claim": example_claim("Saved", "saved")}, app.snapshot["head"]
    )
    settle(app)
    assert app.uncertain
    assert app.page == "history"
    assert app.new_button.instate(["disabled"])
    app.check_receipt()
    settle(app)
    assert not app.uncertain
    assert app.snapshot["sequence"] == 5


def test_failed_workspace_open_does_not_replace_current_session(app, tmp_path):
    app.demo()
    settle(app)
    original = app.session
    app._run("test", lambda: DesktopSession.open(tmp_path / "absent"), app._use)
    settle(app)
    assert app.session is original
    assert app.session.snapshot()["sequence"] == 3
