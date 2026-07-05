"""GUI tests. Run headless with Qt's offscreen platform:

    QT_QPA_PLATFORM=offscreen uv run --extra gui python -m pytest gui/test_gui.py

Covers the pure ProjectModel bridge and an offscreen smoke of MainWindow (it
constructs, populates the tree, and mirrors the engine's diagnostics).
"""
import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

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


def test_mainwindow_smoke():
    from gui.main_window import MainWindow
    _app()
    p = ProjectModel(REF)
    w = MainWindow(p)
    # System + Tasks + Models + Memory roots
    assert w.tree.topLevelItemCount() == 4
    assert w.diag.rowCount() == len(p.diagnostics())
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
    assert w.tree.topLevelItemCount() == 4   # System, Tasks, Models, Memory
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
    models_root = next(r for r in roots if r.text(0) == "Models")
    assert models_root.childCount() == 1
    assert models_root.child(0).childCount() == 2  # IN_KnbVal_Z + OUT_Led1_B
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
