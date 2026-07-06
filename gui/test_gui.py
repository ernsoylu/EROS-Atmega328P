"""GUI tests. Run headless with Qt's offscreen platform:

    QT_QPA_PLATFORM=offscreen uv run --extra gui python -m pytest gui/test_gui.py

Covers the pure ProjectModel bridge and an offscreen smoke of MainWindow (it
constructs, populates the tree, and mirrors the engine's diagnostics).
"""
import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt  # noqa: E402

from gui.project import ProjectModel  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
REF = REPO / "reference-demo" / "app.yaml"
MODEL_APP = REPO / "tools" / "fixtures" / "model_app" / "app.yaml"


def _app():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def test_projectmodel_reference_demo():
    p = ProjectModel(REF)
    assert p.name == "eros"
    assert p.mcu == "atmega328p"
    assert {t["name"] for t in p.tasks()} >= {"status", "ramp", "cmd", "button"}
    assert [d for d in p.diagnostics() if d.severity == "error"] == []


def test_projectmodel_model_app():
    p = ProjectModel(MODEL_APP)
    assert p.models()[0]["name"] == "appKnbSwt"
    assert p.models()[0]["rate_ms"] == 10
    assert [d for d in p.diagnostics() if d.severity == "error"] == []


def test_projectmodel_roundtrip_preserves_comments(tmp_path):
    src = tmp_path / "app.yaml"
    src.write_text("# a leading comment\nsystem: { name: t }  # inline\n")
    p = ProjectModel(src)
    p.save()
    text = src.read_text()
    assert "# a leading comment" in text and "# inline" in text


def test_projectmodel_set_mcu_and_budget():
    p = ProjectModel(REF)
    assert "atmega2560" in p.available_mcus()
    b = p.budget()
    # reference-demo: 4x8 pool arena, 128+64 uart rings
    assert b["arena"] == 32 and b["rings"] == 192
    p.set_mcu("attiny85")
    assert "UNKNOWN_MCU" in {d.code for d in p.diagnostics()}
    p.set_mcu("atmega328p")
    assert "UNKNOWN_MCU" not in {d.code for d in p.diagnostics()}


def test_projectmodel_schedule_unifies_tasks_and_models():
    # models become their own OS tasks; schedule() lists both, most-urgent first,
    # with engine-assigned priorities (SSOT).
    p = ProjectModel(MODEL_APP)
    sched = p.schedule()
    names = {r["name"] for r in sched}
    assert "appKnbSwt" in names
    model_row = next(r for r in sched if r["name"] == "appKnbSwt")
    assert model_row["is_model"] and model_row["period_ms"] == 10
    assert all(isinstance(r["priority"], int) for r in sched)
    # sorted by descending engine priority (most urgent first)
    prios = [r["priority"] for r in sched]
    assert prios == sorted(prios, reverse=True)


def test_projectmodel_update_task_and_facts():
    p = ProjectModel()
    p.new("t", "atmega328p")
    p.update_task("main", period_ms=0, wcet_ms=3, autostart=True)  # -> autostart
    row = next(r for r in p.schedule() if r["name"] == "main")
    assert row["autostart"] and row["wcet_ms"] == 3 and not row["period_ms"]
    facts = p.system_facts()
    assert facts["f_cpu"] == "16000000UL" and "adc" in facts["peripherals"]
    p.set_mcu("attiny85")
    assert p.system_facts() == {}        # unknown MCU -> empty, not a raise


def test_projectmodel_interfaces_and_unbind():
    p = ProjectModel()
    p.new("d", "atmega328p")
    cg = str(REPO / "codegen" / "appKnbSwt_ert_rtw")
    name, _s, runnable = p.model_signals(cg)
    p.add_model(name, cg, runnable, rate_ms=10)
    rows = p.model_interfaces(name)
    assert {r["signal"] for r in rows} == {"IN_KnbVal_Z", "OUT_Led1_B"}
    assert next(r for r in rows if r["signal"] == "IN_KnbVal_Z")["ctype"]
    drivers = p.available_drivers()
    assert drivers["adc"]["directions"] == ["in"]
    assert drivers["dio"]["required"] == ["port", "bit"]
    p.bind_port(name, "IN_KnbVal_Z", "adc", channel=0)
    assert p.port_binding(name, "IN_KnbVal_Z") == "adc channel=0"
    p.unbind_port(name, "IN_KnbVal_Z")
    assert p.port_binding(name, "IN_KnbVal_Z") == "unbound"


def test_mainwindow_smoke():
    from gui.main_window import MainWindow
    _app()
    p = ProjectModel(REF)
    w = MainWindow(p)
    # master-detail: two roots now - System and the unified Tasks schedule.
    assert w.tree.topLevelItemCount() == 2
    assert w.tree.topLevelItem(0).data(0, Qt.UserRole)[0] == "system"
    assert w.diag.rowCount() == len(p.diagnostics())
    # System is selected by default -> the right panel built its MCU combo.
    assert w.mcu_combo is not None and w.mcu_combo.count() >= 2
    w.close()


def test_projectmodel_new_and_edit(tmp_path):
    p = ProjectModel()
    p.new("blinky", "atmega2560")
    assert p.name == "blinky" and p.mcu == "atmega2560"
    assert {t["name"] for t in p.tasks()} == {"init", "main"}
    assert [d for d in p.diagnostics() if d.severity == "error"] == []
    p.add_task("fast", period_ms=5, wcet_ms=1)
    assert "fast" in {t["name"] for t in p.tasks()}
    p.remove_task("main")
    assert "main" not in {t["name"] for t in p.tasks()}
    dst = tmp_path / "app.yaml"
    p.save(dst)                       # new project persists and reloads
    assert ProjectModel(dst).name == "blinky"


def test_mainwindow_new_project():
    from gui.main_window import MainWindow
    _app()
    w = MainWindow(ProjectModel())    # start empty (no project)
    w.project.new("demo", "atmega328p")
    w.refresh()
    assert w.tree.topLevelItemCount() == 2   # System + Tasks·by priority
    assert w.diag.rowCount() == 0            # the skeleton is valid -> no problems
    w.close()


def test_projectmodel_add_model_and_bind():
    p = ProjectModel()
    p.new("swc_demo", "atmega328p")
    cg = str(REPO / "codegen" / "appKnbSwt_ert_rtw")
    name, sigs, runnable = p.model_signals(cg)
    assert name == "appKnbSwt" and runnable == "appKnbSwt_Runnable"
    assert ("IN_KnbVal_Z", "uint16_T", "in") in sigs
    p.add_model(name, cg, runnable, rate_ms=10)
    # ports listed but unbound -> the live problem list flags missing drivers
    assert "PORT_NO_DRIVER" in {d.code for d in p.diagnostics()}
    assert p.port_binding(name, "IN_KnbVal_Z") == "unbound"
    p.bind_port(name, "IN_KnbVal_Z", "adc", channel=0)
    p.bind_port(name, "OUT_Led1_B", "dio", port="B", bit=5)
    assert p.port_binding(name, "IN_KnbVal_Z") == "adc channel=0"
    # bound -> resolves type-clean
    errs = [d for d in p.diagnostics() if d.severity == "error"]
    assert errs == [], [e.message for e in errs]


def test_mainwindow_shows_model_ports():
    from gui.main_window import MainWindow
    _app()
    w = MainWindow(ProjectModel(MODEL_APP))
    roots = [w.tree.topLevelItem(i) for i in range(w.tree.topLevelItemCount())]
    # models are scheduled tasks now, so they live under the unified Tasks root,
    # marked ◆, and expand to their in/out signals.
    tasks_root = next(r for r in roots if r.text(0).startswith("Tasks"))
    model_item = next(tasks_root.child(i) for i in range(tasks_root.childCount())
                      if tasks_root.child(i).data(0, Qt.UserRole)[0] == "model")
    assert "appKnbSwt" in model_item.text(0) and "◆" in model_item.text(0)
    assert model_item.childCount() == 2            # IN_KnbVal_Z + OUT_Led1_B
    assert model_item.child(0).data(0, Qt.UserRole)[0] == "signal"
    w.close()


def test_mainwindow_model_page_binds_inline():
    # Selecting a model builds the interface table; Apply commits the bindings
    # straight from the row editors - no modal.
    from gui.main_window import MainWindow
    from PySide6.QtWidgets import QComboBox, QTableWidget
    _app()
    p = ProjectModel()
    p.new("swc_demo", "atmega328p")
    cg = str(REPO / "codegen" / "appKnbSwt_ert_rtw")
    name, _sigs, runnable = p.model_signals(cg)
    p.add_model(name, cg, runnable, rate_ms=10)
    w = MainWindow(p)
    w._sel = ("model", name)
    w._show_inspector()                            # build the model page
    page = w.inspector.widget()
    table = page.findChild(QTableWidget)
    assert table.rowCount() == 2
    # drive the IN_ row: pick adc, params channel=0
    for r in range(table.rowCount()):
        if table.item(r, 0).text() == "IN_KnbVal_Z":
            combo = table.cellWidget(r, 3)
            assert isinstance(combo, QComboBox)
            assert {combo.itemText(i) for i in range(combo.count())} == {
                "(unbound)", "adc", "dio"}   # only in-capable drivers offered
            combo.setCurrentText("adc")
            table.cellWidget(r, 4).setText("channel=0")
        if table.item(r, 0).text() == "OUT_Led1_B":
            table.cellWidget(r, 3).setCurrentText("dio")
            table.cellWidget(r, 4).setText("port=B, bit=5")
    editors = [(table.item(r, 0).text(), table.cellWidget(r, 3),
                table.cellWidget(r, 4)) for r in range(table.rowCount())]
    w._apply_model(name, 10, editors)
    assert p.port_binding(name, "IN_KnbVal_Z") == "adc channel=0"
    assert [d for d in p.diagnostics() if d.severity == "error"] == []
    w.close()


def test_mainwindow_system_page_mcu_change_rerenders():
    # The MCU dropdown now lives on the System page; changing it re-derives the
    # facts panel and the problem list (the fix for "MCU is not changing").
    from gui.main_window import MainWindow
    _app()
    p = ProjectModel(REF)
    w = MainWindow(p)                      # System selected by default
    assert w.mcu_combo.currentText() == "atmega328p"
    w._on_mcu_changed("attiny85")          # unknown -> UNKNOWN_MCU appears live
    assert p.mcu == "attiny85"
    w.refresh()
    codes = {w.diag.item(r, 1).text() for r in range(w.diag.rowCount())}
    assert "UNKNOWN_MCU" in codes
    w.close()


def test_mainwindow_mcu_combo_live():
    from gui.main_window import MainWindow
    _app()
    p = ProjectModel(REF)
    w = MainWindow(p)
    assert w.mcu_combo.count() >= 2  # atmega328p, atmega2560
    w._on_mcu_changed("atmega2560")
    assert p.mcu == "atmega2560"
    w.close()


def test_add_task_dialog_adds_typed_task(monkeypatch):
    # The Add Task form uses typed QSpinBoxes, so bad numbers are impossible;
    # accepting the dialog adds the task with its (default) field values.
    from gui.main_window import MainWindow
    from PySide6.QtWidgets import QDialog
    _app()
    p = ProjectModel()
    p.new("t", "atmega328p")
    w = MainWindow(p)
    before = len(p.tasks())
    monkeypatch.setattr(QDialog, "exec", lambda self: QDialog.Accepted)
    w.add_task_dialog()                 # default fields: name "ctrl", 10 ms, 1 ms
    assert len(p.tasks()) == before + 1
    assert "ctrl" in {t["name"] for t in p.tasks()}
    w.close()


def test_mainwindow_surfaces_errors():
    from gui.main_window import MainWindow
    _app()
    import tempfile
    with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as f:
        f.write("system: { name: t, mcu: attiny85 }\n"
                "tasks: [{ name: a, period_ms: 10, wcet_ms: 1 }]\n"
                "resources: [{ name: r, users: [a] }]\n")
        bad = f.name
    w = MainWindow(ProjectModel(bad))
    codes = {w.diag.item(r, 1).text() for r in range(w.diag.rowCount())}
    assert "UNKNOWN_MCU" in codes  # the engine's diagnostic reaches the table
    w.close()
    os.unlink(bad)


def test_projectmodel_locate_resolves_lines():
    from erosgen import Diagnostic
    p = ProjectModel(REF)
    # an indexed location resolves to that item's 1-based line in app.yaml
    path, line = p.locate(Diagnostic("error", "X", "msg", "tasks[1]"))
    assert path == p.path and isinstance(line, int) and line > 0
    assert "name:" in REF.read_text().splitlines()[line - 1]   # a task item
    # an unresolvable location still returns the file, no line
    upath, uline = p.locate(Diagnostic("error", "Z", "msg", "pin PB5"))
    assert upath == p.path and uline is None
    # an unsaved project has no jump target
    fresh = ProjectModel()
    fresh.new("x")
    assert fresh.locate(Diagnostic("error", "W", "msg", "tasks[0]")) == (None, None)


def test_mainwindow_mcu_change_on_unsaved_project():
    from gui.main_window import MainWindow
    _app()
    w = MainWindow(ProjectModel())
    w.project.new("demo", "atmega328p")   # unsaved: path is None
    w.refresh()
    assert w.project.path is None
    w._on_mcu_changed("atmega2560")       # used to be a no-op without a saved path
    assert w.project.mcu == "atmega2560"
    w.close()


def test_mainwindow_diagnostic_double_click_opens_source(monkeypatch):
    import tempfile

    from gui.main_window import MainWindow
    from PySide6.QtGui import QDesktopServices
    _app()
    with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as f:
        f.write("system: { name: t, mcu: attiny85 }\n"
                "tasks: [{ name: a, period_ms: 10, wcet_ms: 1 }]\n"
                "resources: [{ name: r, users: [a] }]\n")
        bad = f.name
    w = MainWindow(ProjectModel(bad))
    assert w.diag.rowCount() >= 1
    opened = {}
    monkeypatch.setattr(QDesktopServices, "openUrl",
                        lambda url: opened.setdefault("path", url.toLocalFile()))
    w._open_diagnostic(w.diag.item(0, 0))          # simulate double-click on row 0
    assert opened.get("path", "").endswith(".yaml")
    w.close()
    os.unlink(bad)
