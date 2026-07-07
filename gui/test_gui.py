"""GUI tests. Run headless with Qt's offscreen platform:

    QT_QPA_PLATFORM=offscreen uv run --extra gui python -m pytest gui/test_gui.py

Covers the pure ProjectModel bridge and an offscreen smoke of MainWindow (it
constructs, populates the tree, and mirrors the engine's diagnostics).
"""
import math
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


def _find_by_kind(w, kind):
    """Walk the (now rate-grouped) tree for the first item of a given kind."""
    stack = [w.tree.topLevelItem(i) for i in range(w.tree.topLevelItemCount())]
    while stack:
        it = stack.pop()
        d = it.data(0, Qt.UserRole)
        if d and d[0] == kind:
            return it
        for i in range(it.childCount()):
            stack.append(it.child(i))
    return None


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


def test_projectmodel_board_labels_and_resources():
    p = ProjectModel()
    p.new("t", "atmega328p")
    # friendly board names (data-driven from the profile `board:` field)
    assert p.board_label("atmega328p") == "Arduino Nano"
    assert p.board_label("arduino_uno") == "Arduino Uno"
    assert p.board_label("atmega2560") == "Arduino Mega"
    assert dict(p.boards_for_chip("atmega328p")) == {
        "atmega328p": "Arduino Nano", "arduino_uno": "Arduino Uno"}
    # resources: the skeleton has one; removing it -> NO_RESOURCES; re-adding clears
    assert p.resources()[0]["name"] == "app"
    p.remove_resource("app")
    assert "NO_RESOURCES" in {d.code for d in p.diagnostics()}
    p.add_resource("shared", users=["main"])
    assert p.resources() == [{"name": "shared", "users": ["main"]}]
    assert "NO_RESOURCES" not in {d.code for d in p.diagnostics()}


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
    # master-detail: System first, then one node per task rate group.
    assert w.tree.topLevelItem(0).data(0, Qt.UserRole) == ("system",)
    assert w.tree.topLevelItemCount() >= 2
    assert w.diag.rowCount() == len(p.diagnostics())
    # System is selected by default -> the right panel built its MCU combo.
    assert w.mcu_combo is not None and w.mcu_combo.count() >= 2
    w.close()


def test_projectmodel_pinout():
    import yaml
    p = ProjectModel()
    p.doc = yaml.safe_load("""
system: { name: t, mcu: atmega328p, drivers_dir: ../drivers }
peripherals: { spi: {}, uart: {} }
gpio: [{ pin: D13, dir: out, name: LED }]
tasks: [{ name: a, period_ms: 10, wcet_ms: 1 }]
resources: [{ name: r, users: [a] }]
""")
    po = p.pinout()
    assert po["ports"] == ["B", "C", "D"]
    pb5 = po["cells"][("B", 5)]           # D13: SPI SCK vs the LED gpio
    assert pb5["conflict"] and set(pb5["owners"]) == {"spi", "LED"}
    assert po["cells"][("D", 0)]["kind"] == "periph"    # PD0 = UART RX
    assert po["cells"][("B", 1)]["kind"] == "free"      # D9, unused
    assert po["cells"][("B", 6)]["usable"] is False     # crystal, not broken out


def test_mainwindow_pinout_grid():
    from gui.main_window import MainWindow
    _app()
    w = MainWindow(ProjectModel(REF))
    assert w.tabs.tabText(2) == "Pinout"
    assert w.pinout.rowCount() == 3 and w.pinout.columnCount() == 8
    assert "1 kHz" in w.pin_note.text()
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
    # System + "100 ms" (main) + "aperiodic" (init) + Resources + Peripherals
    assert w.tree.topLevelItemCount() == 5
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
    # a codegen task lives under its rate group, marked ◆, and expands to ports.
    model_item = _find_by_kind(w, "model")
    assert model_item is not None
    assert "appKnbSwt" in model_item.text(0) and "◆" in model_item.text(0)
    assert model_item.childCount() == 2            # IN_KnbVal_Z + OUT_Led1_B
    assert model_item.child(0).data(0, Qt.UserRole)[0] == "signal"
    w.close()


def test_mainwindow_model_page_binds_inline():
    # Selecting a model builds the interface table; params are MCU-limited
    # dropdowns (channel / pin), so Apply commits a well-formed binding with no
    # parsing and no missing-key errors.
    from gui.main_window import MainWindow
    from PySide6.QtWidgets import QComboBox, QTableWidget
    _app()
    p = ProjectModel()
    p.new("swc_demo", "arduino_uno")
    cg = str(REPO / "codegen" / "appKnbSwt_ert_rtw")
    name, _sigs, runnable = p.model_signals(cg)
    p.add_model(name, cg, runnable, rate_ms=10)
    w = MainWindow(p)
    w._sel = ("model", name)
    w._show_inspector()                            # build the model page
    table = w.inspector.widget().findChild(QTableWidget)
    assert table.rowCount() == 2
    for r in range(table.rowCount()):
        driver = table.cellWidget(r, 3)
        if table.item(r, 0).text() == "IN_KnbVal_Z":
            assert {driver.itemText(i) for i in range(driver.count())} == {
                "(unbound)", "adc", "dio"}         # only in-capable drivers
            driver.setCurrentText("adc")           # rebuilds the params cell
            chan = table.cellWidget(r, 4).findChild(QComboBox)
            assert chan.count() == 6               # arduino_uno: A0..A5
            chan.setCurrentText("channel 0")
        if table.item(r, 0).text() == "OUT_Led1_B":
            driver.setCurrentText("dio")
            pin = table.cellWidget(r, 4).findChild(QComboBox)
            pin.setCurrentIndex(pin.findData("PB5"))       # PB5 (D13)
    w._apply_model()
    assert p.port_binding(name, "IN_KnbVal_Z") == "adc channel=0"
    assert p.port_binding(name, "OUT_Led1_B") == "dio port=B bit=5"
    assert [d for d in p.diagnostics() if d.severity == "error"] == []
    w.close()


def test_mainwindow_asw_task_page_authors_interface():
    # A hand ASW task's page exposes an editable interface table (Signal, Dir,
    # Type, Driver, Params, Description, remove) + a calibrations table; Apply
    # commits type/description/binding straight from the row widgets.
    from gui.main_window import MainWindow
    from PySide6.QtWidgets import QComboBox, QTableWidget
    _app()
    p = ProjectModel()
    p.new("demo", "arduino_uno")
    p.make_asw_task("main")                         # 100 ms task -> ASW
    p.add_port("main", "in", "IN_Knob", "uint16_T", "knob")
    p.add_calibration("main", "Kp", "uint16_T", 5, "gain")
    w = MainWindow(p)
    w._sel = ("asw", "main")
    w._show_inspector()
    tables = w.inspector.widget().findChildren(QTableWidget)
    iface, cals = tables[0], tables[1]
    assert iface.horizontalHeaderItem(5).text() == "Description"
    assert iface.item(0, 0).text() == "IN_Knob"
    iface.cellWidget(0, 3).setCurrentText("adc")    # driver -> adc, params dropdown
    iface.cellWidget(0, 4).findChild(QComboBox).setCurrentText("channel 0")
    assert cals.item(0, 0).text() == "Kp"
    w._apply_task()
    assert p.port_binding("main", "IN_Knob") == "adc channel=0"
    # a plain task instead offers to become an ASW task
    w._sel = ("task", "init")
    w._show_inspector()
    from PySide6.QtWidgets import QPushButton
    labels = [b.text() for b in w.inspector.widget().findChildren(QPushButton)]
    assert any("make this an ASW task" in t for t in labels)
    w.close()


def test_mainwindow_priority_dropdown_interleaves_kinds():
    # The within-rate priority dropdown places a hand task above a codegen task
    # at the same rate (the engine tie-breaks on the `order` it writes).
    from gui.main_window import MainWindow
    _app()
    p = ProjectModel()
    p.new("demo", "arduino_uno")
    p.add_task("ctrl", period_ms=100, wcet_ms=1)
    p.make_asw_task("ctrl")
    cg = str(REPO / "codegen" / "appKnbSwt_ert_rtw")
    name, _s, runnable = p.model_signals(cg)
    p.add_model(name, cg, runnable, rate_ms=100)
    order0 = [x["name"] for x in p.schedule() if x["period_ms"] == 100]
    assert order0[0] == "appKnbSwt"        # codegen task most urgent by default
    w = MainWindow(p)
    w._sel = ("asw", "ctrl")
    w._show_inspector()
    w._set_priority("ctrl", 0)             # dropdown -> position 0 (most urgent)
    order1 = [x["name"] for x in p.schedule() if x["period_ms"] == 100]
    assert order1[0] == "ctrl"             # hand task now outranks the codegen one
    w.close()


def test_projectmodel_build_dirs():
    p = ProjectModel()
    p.new("t", "atmega328p")
    # new() auto-detects the EROS kernel/drivers (running from the repo tree)
    assert p.kernel_dir.endswith("/kernel") and p.drivers_dir.endswith("/drivers")
    p.set_dir("drivers_dir", "../drivers")  # still explicitly settable
    assert p.drivers_dir == "../drivers"
    p.set_dir("drivers_dir", "")            # blank clears it
    assert p.drivers_dir == ""


def test_projectmodel_detect_dirs():
    p = ProjectModel()
    d = p.detect_dirs()
    assert d, "erosgen runs from the repo, so kernel/drivers are detectable"
    assert (Path(d["kernel_dir"]) / "eros.h").is_file()
    assert Path(d["drivers_dir"]).is_dir()
    # autodetect fills an otherwise-empty project
    p2 = ProjectModel()
    assert p2.autodetect_dirs()
    assert p2.kernel_dir.endswith("/kernel")


def test_mainwindow_add_port_preserves_edits():
    # Regression: adding a port used to rebuild the page from the saved doc and
    # discard un-Applied edits to the other rows. _commit_task now runs first.
    from unittest.mock import patch

    from gui.main_window import MainWindow
    from PySide6.QtWidgets import QTableWidget
    _app()
    p = ProjectModel()
    p.new("t", "arduino_uno")
    p.make_asw_task("main")
    p.add_port("main", "in", "IN_A", "uint16_T", "")
    w = MainWindow(p)
    w._sel = ("asw", "main")
    w._show_inspector()
    table = w.inspector.widget().findChild(QTableWidget)
    table.cellWidget(0, 3).setCurrentText("adc")     # edit driver, NOT applied
    with patch("PySide6.QtWidgets.QInputDialog.getText",
               return_value=("IN_B", True)):
        w._add_port("main", "in")                     # add a second input
    # the first row's in-progress driver survived the structural add
    assert p.port_binding("main", "IN_A") == "adc channel=0"
    assert {s for s, _d in p.model_port_signals("main")} == {"IN_A", "IN_B"}
    w.close()


def test_projectmodel_internal_signal_wiring():
    p = ProjectModel()
    p.new("d", "atmega328p")
    p.add_task("App1", period_ms=10)
    p.make_asw_task("App1")
    p.add_port("App1", "out", "OUT_A1_B", "boolean_T", "")
    p.bind_port("App1", "OUT_A1_B", "internal")
    p.add_task("App2", period_ms=10)
    p.make_asw_task("App2")
    p.add_port("App2", "in", "IN_A2_B", "boolean_T", "")
    # App2's input can source App1's output (another SWC's output)
    assert "App1.OUT_A1_B" in p.available_sources("App2")
    assert "App2.IN_A2_B" not in p.available_sources("App2")   # not its own/inputs
    p.set_port_source("App2", "IN_A2_B", "App1.OUT_A1_B")
    assert p.port_binding("App2", "IN_A2_B") == "← App1.OUT_A1_B"
    assert p.port_binding("App1", "OUT_A1_B") == "internal"
    # re-binding to a driver clears the source
    p.bind_port("App2", "IN_A2_B", "dio", port="C", bit=0)
    assert p.port_binding("App2", "IN_A2_B") == "dio port=C bit=0"


def test_mainwindow_input_offers_and_wires_internal_source():
    from gui.main_window import MainWindow
    from PySide6.QtWidgets import QTableWidget
    _app()
    p = ProjectModel()
    p.new("d", "atmega328p")
    p.add_task("App1", period_ms=10)
    p.make_asw_task("App1")
    p.add_port("App1", "out", "OUT_A1_B", "boolean_T", "")
    p.bind_port("App1", "OUT_A1_B", "internal")
    p.add_task("App2", period_ms=10)
    p.make_asw_task("App2")
    p.add_port("App2", "in", "IN_A2_B", "boolean_T", "")
    w = MainWindow(p)
    w._sel = ("asw", "App2")
    w._show_inspector()
    table = w.inspector.widget().findChild(QTableWidget)
    combo = table.cellWidget(0, 3)                 # driver combo for IN_A2_B
    labels = {combo.itemText(i) for i in range(combo.count())}
    assert "← App1.OUT_A1_B" in labels             # the internal source is offered
    combo.setCurrentText("← App1.OUT_A1_B")
    w._apply_task()
    assert p.port_binding("App2", "IN_A2_B") == "← App1.OUT_A1_B"
    # reopening the page must preselect the wired source (str item data so
    # QComboBox.findData matches - a tuple would silently fall back to unbound)
    w._show_inspector()
    table2 = w.inspector.widget().findChild(QTableWidget)
    assert table2.cellWidget(0, 3).currentText() == "← App1.OUT_A1_B"
    w.close()


def test_projectmodel_peripherals_and_pwm():
    p = ProjectModel()
    p.new("t", "atmega328p")
    names = {r["name"] for r in p.known_peripherals()}
    assert {"pwm", "uart", "adc", "spi", "i2c"} <= names   # MCU's peripherals
    assert not p.peripheral_active("pwm")
    p.activate_peripheral("pwm", True)
    assert p.peripheral_active("pwm")
    p.set_peripheral_prop("pwm", "freq_hz", 2000)
    assert p.peripheral_config("pwm")["freq_hz"] == 2000
    assert math.isclose(p.pwm_achieved(2000)[0], 2000.0) and p.pwm_achieved(2000)[1] == "timer1"
    p.activate_peripheral("pwm", False)
    assert not p.peripheral_active("pwm")


def test_projectmodel_conflict_aware_pins():
    p = ProjectModel()
    p.new("t", "arduino_uno")
    p.add_task("T", period_ms=10)
    p.make_asw_task("T")
    p.add_port("T", "out", "OUT_a", "boolean_T", "")
    p.add_port("T", "out", "OUT_b", "boolean_T", "")
    assert {"PB2", "PB3", "PB4", "PB5"} <= {d["pin"] for d in p.dio_pins()}
    # activating SPI (owns PB2..PB5) hides those from the dio picker
    p.activate_peripheral("spi", True)
    avail = {d["pin"] for d in p.available_dio_pins("T", "OUT_a")}
    assert not ({"PB2", "PB3", "PB4", "PB5"} & avail)
    # a dio-bound pin is hidden from other ports but kept for its own row
    p.bind_port("T", "OUT_b", "dio", port="B", bit=0)
    assert "PB0" not in {d["pin"] for d in p.available_dio_pins("T", "OUT_a")}
    assert "PB0" in {d["pin"] for d in p.available_dio_pins("T", "OUT_b")}
    # an ADC channel in use hides its A-pin from dio, and vice-versa (A0 = PC0)
    p.add_port("T", "in", "IN_x", "uint16_T", "")
    p.bind_port("T", "IN_x", "adc", channel=0)
    assert "PC0" not in {d["pin"] for d in p.available_dio_pins("T", "OUT_a")}
    assert 0 in p.available_adc_channels("T", "IN_x")     # its own channel kept


def test_mainwindow_spi_config_page():
    from gui.main_window import MainWindow
    from PySide6.QtWidgets import QComboBox
    _app()
    p = ProjectModel()
    p.new("t", "atmega328p")
    p.activate_peripheral("spi", True)
    w = MainWindow(p)
    w._sel = ("peripheral", "spi")
    w._show_inspector()
    assert len(w.inspector.widget().findChildren(QComboBox)) >= 2  # mode + clock
    w._set_peripheral_prop("spi", "mode", 2)
    w._set_peripheral_prop("spi", "clock", 8)
    assert p.peripheral_config("spi") == {"mode": 2, "clock": 8}
    w.close()


def test_mainwindow_adc_i2c_timer0_pages():
    from gui.main_window import MainWindow
    from PySide6.QtWidgets import QSpinBox
    _app()
    p = ProjectModel()
    p.new("t", "atmega328p")
    for name in ("adc", "i2c", "timer0_pwm"):
        p.activate_peripheral(name, True)
    w = MainWindow(p)
    w._sel = ("peripheral", "adc")
    w._show_inspector()
    w._set_peripheral_prop("adc", "reference", "internal")
    w._set_peripheral_prop("adc", "prescaler", 64)
    assert p.peripheral_config("adc") == {"reference": "internal",
                                          "prescaler": 64}
    w._sel = ("peripheral", "i2c")
    w._show_inspector()
    w._set_peripheral_prop("i2c", "speed_hz", 400000)
    assert p.peripheral_config("i2c")["speed_hz"] == 400000
    w._sel = ("peripheral", "timer0_pwm")
    w._show_inspector()
    assert w.inspector.widget().findChild(QSpinBox) is not None
    assert math.isclose(p.timer0_pwm_achieved(7812), 7812.5)     # /8 at 16 MHz
    w.close()


def test_mainwindow_peripherals_page():
    from gui.main_window import MainWindow
    from PySide6.QtWidgets import QSpinBox
    _app()
    p = ProjectModel()
    p.new("t", "atmega328p")
    w = MainWindow(p)
    # a Peripherals node exists with each MCU peripheral as a child
    per_item = _find_by_kind(w, "peripheral")
    assert per_item is not None
    # open the pwm page, activate, set frequency via the widgets
    w._sel = ("peripheral", "pwm")
    w._show_inspector()
    w._activate_peripheral("pwm", True)
    w._sel = ("peripheral", "pwm")
    w._show_inspector()                       # rebuild -> now shows the config
    spin = w.inspector.widget().findChild(QSpinBox)
    assert spin is not None                   # frequency spinbox present
    spin.setValue(2000)
    w._set_peripheral_prop("pwm", "freq_hz", spin.value())
    assert p.peripheral_config("pwm")["freq_hz"] == 2000
    w.close()


def test_mainwindow_excepthook_logs_and_survives():
    # An unhandled slot exception must be logged to the Console (and the app
    # kept alive) rather than terminating PySide6 (>=6.5 aborts by default).
    from gui.main_window import MainWindow
    _app()
    w = MainWindow(ProjectModel())
    w.project.new("t", "atmega328p")
    w.refresh()
    try:
        raise ValueError("boom-under-test")
    except ValueError:
        import sys
        w._excepthook(*sys.exc_info())      # must not re-raise
    log = w.console.toPlainText()
    assert "boom-under-test" in log and "internal error" in log
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


def test_mainwindow_mcu_change_on_empty_launch():
    # Regression: on a fresh launch the doc is {} (title "(no project)"). The old
    # `if self.project.doc` guard made the MCU combo a no-op there.
    from gui.main_window import MainWindow
    _app()
    w = MainWindow(ProjectModel())         # empty doc, no project
    assert not w.project.doc
    w._on_mcu_changed("atmega2560")        # used to do nothing
    assert w.project.mcu == "atmega2560"   # now creates system.mcu
    w.close()


def test_mainwindow_board_selector():
    # MCU picks the chip; Board picks a concrete profile on it, shown by its
    # friendly name (Arduino Uno/Nano) with the profile stem as item data.
    from gui.main_window import MainWindow
    _app()
    p = ProjectModel()
    p.new("t", "arduino_uno")
    w = MainWindow(p)                       # System page built the two combos
    assert w.mcu_combo.currentText() == "atmega328p"        # chip (ECU)
    assert w.board_combo.currentText() == "Arduino Uno"     # friendly board name
    labels = {w.board_combo.itemText(i) for i in range(w.board_combo.count())}
    stems = {w.board_combo.itemData(i) for i in range(w.board_combo.count())}
    assert labels == {"Arduino Nano", "Arduino Uno"}        # not the ECU names
    assert stems == {"atmega328p", "arduino_uno"}
    # switch board within the same chip via the dropdown (data drives the target)
    w.board_combo.setCurrentIndex(w.board_combo.findData("atmega328p"))
    assert p.mcu == "atmega328p"            # bare 328p = Arduino Nano
    w.close()


def test_mainwindow_resource_page_fixes_no_resources():
    # The Resources section + page let you clear NO_RESOURCES in-app: add a
    # resource, tick a task on its page, Apply.
    from gui.main_window import MainWindow
    from PySide6.QtWidgets import QCheckBox
    _app()
    p = ProjectModel()
    p.new("t", "atmega328p")
    p.remove_resource("app")                     # -> NO_RESOURCES
    w = MainWindow(p)
    assert "NO_RESOURCES" in {w.diag.item(r, 1).text()
                              for r in range(w.diag.rowCount())}
    # a Resources root exists in the tree
    assert _find_by_kind(w, "resource") is None  # none yet
    p.add_resource("rte", users=[])              # invalid until a user is picked
    w._sel = ("resource", "rte")
    w._show_inspector()
    boxes = w.inspector.widget().findChildren(QCheckBox)
    assert boxes                                  # one checkbox per task
    boxes[0].setChecked(True)
    w._apply_resource()
    assert p.resources()[0]["users"]              # a user got assigned
    codes = {d.code for d in p.diagnostics()}
    assert "NO_RESOURCES" not in codes and "RES_NO_USERS" not in codes
    w.close()


def test_mainwindow_mcu_combo_live():
    from gui.main_window import MainWindow
    _app()
    p = ProjectModel(REF)
    w = MainWindow(p)
    assert w.mcu_combo.count() >= 2  # atmega328p, atmega2560 (chips)
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


def _make_workspace(tmp_path, variants=True):
    """Write a temp erosproject.yaml + two copied app.yamls (so a Generate All
    that saves the open app never touches a committed fixture)."""
    import shutil
    apps = []
    for i, src in enumerate((REF, MODEL_APP)):
        sub = tmp_path / f"app{i}"
        shutil.copytree(src.parent, sub)
        apps.append(sub / "app.yaml")
    wf = tmp_path / "erosproject.yaml"
    v = ("variants:\n  release: { system: { hooks: { error: true } } }\n"
         if variants else "")
    wf.write_text("name: prod\n" + v + "apps:\n"
                  + "".join(f"  - {a}\n" for a in apps))
    return wf, apps


def test_workspacemodel_load_and_apps(tmp_path):
    from gui.project import WorkspaceModel
    wf, apps = _make_workspace(tmp_path)
    assert WorkspaceModel.is_workspace_file(wf)
    assert not WorkspaceModel.is_workspace_file(REF)      # a plain app.yaml
    ws = WorkspaceModel(wf)
    assert ws.name == "prod"
    assert ws.variants() == ["release"]
    assert [str(p) for _, p in ws.apps()] == [str(a.resolve()) for a in apps]


def test_workspacemodel_rejects_plain_app():
    from gui.project import WorkspaceModel
    try:
        WorkspaceModel(REF)
        raise AssertionError("expected ValueError for a non-workspace file")
    except ValueError as e:
        assert "apps" in str(e)


def test_mainwindow_open_workspace_and_generate(tmp_path):
    from gui.main_window import MainWindow
    _app()
    wf, _ = _make_workspace(tmp_path)
    w = MainWindow(ProjectModel())
    w.open_workspace(str(wf))                              # path passed (no dialog)
    assert not w.ws_bar.isHidden()
    assert w.ws_apps.count() == 2
    assert w.ws_variant.count() == 2                       # "(none)" + release
    assert w.project.name == "eros"                        # first app loaded
    w._on_ws_app(1)
    assert w.project.name == "knbdemo"                     # switched to second
    w.ws_variant.setCurrentIndex(1)                        # select release
    w.generate_workspace()                                 # runs whole workspace
    # both apps generated their config.h under the temp copies
    assert (tmp_path / "app0" / "config.h").exists()
    assert (tmp_path / "app1" / "config.h").exists()
    w.close()


def test_mainwindow_suppress_sleep_toggle():
    """System page 'Build config' toggle flips system.idle sleep<->busy and the
    engine turns busy into -DEROS_IDLE_BUSY."""
    from gui.main_window import MainWindow
    from erosgen.emit.makefile import idle_busy_def
    import erosgen
    _app()
    p = ProjectModel()
    p.new("demo", "atmega328p")
    p.add_task("ctrl", period_ms=10, wcet_ms=1)
    w = MainWindow(p)
    assert p.idle() == "sleep"                        # default
    w._set_idle_busy(True)
    assert p.idle() == "busy"
    s = erosgen.System(p.plain, Path("app.yaml"))
    assert idle_busy_def(s) == " -DEROS_IDLE_BUSY"
    w._set_idle_busy(False)                           # back to default, key dropped
    assert p.idle() == "sleep" and "idle" not in p._system()
    w.close()
