"""The main window: a master-detail configurator over a ProjectModel.

Left: the project tree - System, and every runnable the kernel schedules
(declared tasks plus one synthesized OS task per model), ordered by priority.
Right: a context panel that shows the *selected* node's config - System -> MCU +
memory budget; a task -> its timing; a model -> its in/out interfaces with inline
peripheral binding. Bottom: tabs for the live problem list and the build/message
console. No domain logic - every fact comes from the ProjectModel / the engine.
"""
import sys
import traceback

from PySide6.QtCore import QProcess, Qt, QTimer, QUrl
from PySide6.QtGui import QColor, QDesktopServices
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox,
                               QFileDialog, QFormLayout, QGroupBox, QHBoxLayout,
                               QLabel, QLineEdit, QMainWindow, QMessageBox,
                               QPlainTextEdit, QPushButton, QScrollArea,
                               QSpinBox, QSplitter, QTabWidget, QTableWidget,
                               QTableWidgetItem, QTreeWidget, QTreeWidgetItem,
                               QVBoxLayout, QWidget)

from .project import ProjectModel

_SEV_COLOR = {"error": "#d64545", "warning": "#c98a1b", "info": "#3b73c4"}
_HOOKS = ("startup", "error", "shutdown")


def _ro(text):
    return QTableWidgetItem(str(text))


class MainWindow(QMainWindow):
    def __init__(self, project=None):
        super().__init__()
        self.project = project or ProjectModel()
        self.proc = None
        self._sel = ("system",)      # which node the right panel is showing
        self.mcu_combo = None        # (re)created by the System page
        self.workspace = None   # a WorkspaceModel when an erosproject.yaml is open
        self._build_ui()
        self._build_menu()
        self._build_toolbar()
        self._build_workspace_bar()
        self.refresh()

    def install_excepthook(self):
        """Route unhandled slot exceptions to the Console pane instead of letting
        PySide6 (>=6.5) terminate the whole app. Called by the app entry point
        (not in __init__, so importing/testing the window leaves sys.excepthook
        alone)."""
        sys.excepthook = self._excepthook

    def _excepthook(self, etype, value, tb):
        text = "".join(traceback.format_exception(etype, value, tb))
        sys.stderr.write(text)
        try:
            self.tabs.setCurrentWidget(self.console)
            self._log("⚠ internal error — the action was aborted, the app "
                      "is still running. Please report this trace:\n"
                      + text.rstrip())
        except Exception:
            pass

    # ---- construction ---------------------------------------------------
    def _build_ui(self):
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Project", "Value"])
        self.tree.setColumnWidth(0, 240)
        self.tree.currentItemChanged.connect(self._on_select)

        # right: a context panel that we swap out per selection.
        self.inspector = QScrollArea()
        self.inspector.setWidgetResizable(True)

        panes = QSplitter(Qt.Horizontal)
        panes.addWidget(self.tree)
        panes.addWidget(self.inspector)
        panes.setStretchFactor(1, 2)
        panes.setSizes([360, 600])

        # bottom: the live problem list + the build/message console, tabbed.
        self.diag = QTableWidget(0, 4)
        self.diag.setHorizontalHeaderLabels(
            ["Severity", "Code", "Location", "Message"])
        self.diag.verticalHeader().setVisible(False)
        self.diag.setEditTriggers(QTableWidget.NoEditTriggers)
        self.diag.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.diag.itemDoubleClicked.connect(self._open_diagnostic)

        self.console = QPlainTextEdit()
        self.console.setReadOnly(True)
        self.console.setMaximumBlockCount(5000)

        # Pinout: a whole-chip pin map (CubeMX-style) - ports x bits, coloured by
        # owner with conflicts in red. Read-only; the conflict-aware pickers still
        # do the editing. Facts come from ProjectModel.pinout() (engine-backed).
        self.pinout = QTableWidget(0, 8)
        self.pinout.setHorizontalHeaderLabels([str(b) for b in range(8)])
        self.pinout.setEditTriggers(QTableWidget.NoEditTriggers)
        self.pinout.setSelectionMode(QAbstractItemView.NoSelection)
        self.pin_note = QLabel()
        self.pin_note.setWordWrap(True)
        pin_tab = QWidget()
        pin_l = QVBoxLayout(pin_tab)
        pin_l.setContentsMargins(0, 0, 0, 0)
        pin_l.addWidget(self.pinout)
        pin_l.addWidget(self.pin_note)

        self.tabs = QTabWidget()
        self.tabs.addTab(self.diag, "Problems")
        self.tabs.addTab(self.console, "Console")
        self.tabs.addTab(pin_tab, "Pinout")

        outer = QSplitter(Qt.Vertical)
        outer.addWidget(panes)
        outer.addWidget(self.tabs)
        outer.setStretchFactor(0, 3)
        outer.setSizes([460, 180])
        self.setCentralWidget(outer)

    def _build_menu(self):
        filem = self.menuBar().addMenu("&File")
        for text, slot in (("&New Project…", self.new_project),
                           ("&Open…", self.open_project),
                           ("Open &Workspace…", self.open_workspace),
                           ("&Save", self.save_project),
                           ("Save &As…", self.save_as),
                           ("&Generate", self.generate),
                           ("&Build", self.build)):
            filem.addAction(text).triggered.connect(slot)
        filem.addSeparator()
        filem.addAction("E&xit").triggered.connect(self.close)

        # One "Edit" menu for structure: add a task, add a model, remove the
        # selected one. Port binding is no longer a modal - it's inline on the
        # model's page (select a model on the left).
        editm = self.menuBar().addMenu("&Edit")
        editm.addAction("Add &Task…").triggered.connect(self.add_task_dialog)
        editm.addAction("Add &Codegen Task…").triggered.connect(
            self.add_model_dialog)
        editm.addAction("Add &Resource…").triggered.connect(
            self.add_resource_dialog)
        editm.addSeparator()
        editm.addAction("&Remove Selected").triggered.connect(
            self.remove_selected)

        self.menuBar().addMenu("&Help").addAction("&About").triggered.connect(
            self.about)

    def _build_toolbar(self):
        tb = self.addToolBar("Main")
        tb.setMovable(False)
        for text, slot in (("Generate", self.generate), ("Build", self.build)):
            tb.addAction(text).triggered.connect(slot)

    def _build_workspace_bar(self):
        """A second toolbar shown only when an erosproject.yaml is open: the app
        picker (each opens into the normal editor), the variant selector, and
        Generate All (runs the whole workspace through the engine)."""
        self.addToolBarBreak()
        self.ws_bar = self.addToolBar("Workspace")
        self.ws_bar.setMovable(False)
        self.ws_name = QLabel("")
        self.ws_apps = QComboBox()
        self.ws_variant = QComboBox()
        self.ws_bar.addWidget(QLabel(" Workspace: "))
        self.ws_bar.addWidget(self.ws_name)
        self.ws_bar.addWidget(QLabel("   App: "))
        self.ws_bar.addWidget(self.ws_apps)
        self.ws_bar.addWidget(QLabel("   Variant: "))
        self.ws_bar.addWidget(self.ws_variant)
        self.ws_bar.addAction("Generate All").triggered.connect(
            self.generate_workspace)
        self.ws_apps.activated.connect(self._on_ws_app)
        self.ws_bar.setVisible(False)

    # ---- view refresh ---------------------------------------------------
    def refresh(self):
        self._populate_tree()
        self._populate_diagnostics()
        self._populate_pinout()
        self._show_inspector()
        if not self.project.doc:
            title = "(no project)"
        else:
            title = self.project.name + ("" if self.project.path else " — unsaved")
        self.setWindowTitle(f"EROS Configurator — {title}")

    def _defer_refresh(self):
        # A full refresh rebuilds the right panel, deleting the very widget whose
        # signal we're handling (e.g. the MCU combo). Defer to the event loop so
        # the emitting widget survives the call that scheduled its replacement.
        QTimer.singleShot(0, self.refresh)

    # ---- left: the project tree -----------------------------------------
    def _populate_tree(self):
        self.tree.blockSignals(True)
        self.tree.clear()
        p = self.project
        sysitem = QTreeWidgetItem(
            self.tree, [f"System: {p.name}", p.board_label(p.mcu)])
        sysitem.setData(0, Qt.UserRole, ("system",))

        # Tasks (plain + hand ASW + codegen) grouped by rate; within a rate,
        # ordered by priority (most-urgent first). ◆ = codegen task, ⬡ = hand
        # ASW task (has an interface).
        for label, rows in p.rate_groups():
            grp = QTreeWidgetItem(self.tree, [f"@ {label}", f"{len(rows)}"])
            grp.setData(0, Qt.UserRole, ("section",))
            for r in rows:
                self._task_row_item(grp, r)

        # Resources (OSEK shared sections) - the kernel needs at least one.
        resources = p.resources()
        rroot = QTreeWidgetItem(self.tree, ["Resources", str(len(resources))])
        rroot.setData(0, Qt.UserRole, ("section",))
        for r in resources:
            users = ", ".join(r["users"]) if r["users"] else "(no users)"
            ri = QTreeWidgetItem(rroot, [r["name"], users])
            ri.setData(0, Qt.UserRole, ("resource", r["name"]))

        # Peripherals the MCU offers - activate + configure; ● marks active.
        periphs = p.known_peripherals()
        n_active = sum(1 for r in periphs if r["active"])
        proot = QTreeWidgetItem(self.tree, ["Peripherals",
                                            f"{n_active}/{len(periphs)}"])
        proot.setData(0, Qt.UserRole, ("section",))
        for r in periphs:
            mark = "● " if r["active"] else "○ "
            pins = ", ".join(r["pins"]) if r["pins"] else ""
            pi = QTreeWidgetItem(proot, [mark + r["name"], pins])
            pi.setData(0, Qt.UserRole, ("peripheral", r["name"]))

        self.tree.expandAll()
        self.tree.blockSignals(False)
        self._reselect()

    _MARK = {"model": " ◆", "asw": " ⬡", "task": ""}

    def _task_row_item(self, parent, r):
        """One schedule row within its rate group. Codegen tasks (◆) and hand ASW
        tasks (⬡) expand to their bound/unbound signals; the value column shows
        the engine priority."""
        mark = self._MARK.get(r["kind"], "")
        prio = "—" if r["priority"] is None else f"P{r['priority']}"
        item = QTreeWidgetItem(parent, [r["name"] + mark, prio])
        kind = "model" if r["kind"] == "model" else (
            "asw" if r["kind"] == "asw" else "task")
        item.setData(0, Qt.UserRole, (kind, r["name"]))
        if r["kind"] in ("model", "asw"):
            for sig, direction in self.project.model_port_signals(r["name"]):
                binding = self.project.port_binding(r["name"], sig)
                si = QTreeWidgetItem(item, [sig, f"{direction} · {binding}"])
                si.setData(0, Qt.UserRole, ("signal", r["name"], sig))
        return item

    def _iter_items(self):
        stack = [self.tree.topLevelItem(i)
                 for i in range(self.tree.topLevelItemCount())]
        while stack:
            it = stack.pop()
            yield it
            for i in range(it.childCount()):
                stack.append(it.child(i))

    def _find_item(self, key):
        for it in self._iter_items():
            if it.data(0, Qt.UserRole) == key:
                return it
        return None

    def _reselect(self):
        """Restore the previous selection after a rebuild; fall back to System if
        the node is gone (e.g. its task was just removed)."""
        target = self._find_item(self._sel)
        if target is None:
            self._sel = ("system",)
            target = self._find_item(self._sel)
        if target:
            self.tree.blockSignals(True)
            self.tree.setCurrentItem(target)
            self.tree.blockSignals(False)

    def _on_select(self, cur, _prev):
        key = cur.data(0, Qt.UserRole) if cur else None
        if not key or key[0] == "section":
            return
        self._sel = key
        self._show_inspector()

    # ---- right: the context panel ---------------------------------------
    def _show_inspector(self):
        kind = self._sel[0]
        if kind == "system":
            page = self._page_system()
        elif kind in ("task", "asw"):
            page = self._page_task(self._sel[1])
        elif kind == "model":
            page = self._page_model(self._sel[1])
        elif kind == "signal":               # a port under a codegen or ASW task
            name = self._sel[1]
            page = (self._page_task(name) if self.project.is_asw(name)
                    else self._page_model(name))
        elif kind == "resource":
            page = self._page_resource(self._sel[1])
        elif kind == "peripheral":
            page = self._page_peripheral(self._sel[1])
        else:
            page = QLabel("Select a node on the left.")
        # Swap via takeWidget + deleteLater rather than letting setWidget delete
        # the old page synchronously: a page is never freed while one of its
        # child widgets is still mid-signal (which would use-after-free / crash).
        old = self.inspector.takeWidget()
        self.inspector.setWidget(page)
        if old is not None:
            old.deleteLater()

    def _page_system(self):
        """MCU target + its facts + the static-RAM budget. Changing the MCU here
        re-derives the facts, the budget, and the whole problem list - the live
        edit the toolbar combo used to hide."""
        p = self.project
        w = QWidget()
        lay = QVBoxLayout(w)

        cfg = QGroupBox("System")
        form = QFormLayout(cfg)
        name = QLineEdit(p.name)
        name.editingFinished.connect(lambda: self._set_name(name.text()))
        form.addRow("Project name", name)

        # MCU (chip) then Board: one chip can carry several board configs (an
        # atmega328p runs as a bare chip, an arduino_uno, ...). The board profile
        # is what the engine actually targets (system.mcu).
        targets = p.available_targets()
        chip = p.current_chip()
        self.mcu_combo = QComboBox()
        self.mcu_combo.addItems(list(targets))
        j = self.mcu_combo.findText(chip)
        if j >= 0:
            self.mcu_combo.setCurrentIndex(j)
        self.mcu_combo.currentTextChanged.connect(self._on_mcu_changed)
        form.addRow("MCU", self.mcu_combo)

        # Board picker shows friendly names (Arduino Nano/Uno/Mega), not the ECU
        # stem; the profile stem it targets rides along as item data.
        self.board_combo = QComboBox()
        for stem, label in p.boards_for_chip(chip):
            self.board_combo.addItem(label, stem)
        k = self.board_combo.findData(p.mcu)
        if k >= 0:
            self.board_combo.setCurrentIndex(k)
        self.board_combo.currentIndexChanged.connect(self._on_board_changed)
        form.addRow("Board", self.board_combo)

        # Build paths Generate needs: where the EROS kernel + peripheral driver
        # sources live (relative to the saved app.yaml, or absolute). Without
        # drivers_dir a project that binds a driver can't generate a Makefile.
        form.addRow("kernel dir", self._dir_field("kernel_dir", "…/kernel"))
        form.addRow("drivers dir", self._dir_field("drivers_dir", "…/drivers"))
        detect = QPushButton("Auto-detect kernel + drivers")
        detect.clicked.connect(self._autodetect_dirs)
        form.addRow("", detect)
        lay.addWidget(cfg)

        facts = p.system_facts()
        if facts:
            fb = QGroupBox("MCU facts")
            ff = QFormLayout(fb)
            ff.addRow("F_CPU", QLabel(str(facts["f_cpu"])))
            ff.addRow("Programmer",
                      QLabel(f"{facts['programmer']} @ {facts['baud']} baud"))
            ff.addRow("avrdude part", QLabel(str(facts["part"])))
            ff.addRow("Peripherals",
                      QLabel(", ".join(facts["peripherals"]) or "—"))
            lay.addWidget(fb)
        else:
            lay.addWidget(QLabel(f"Unknown MCU '{p.mcu}' — no profile found."))

        hooks = p.hooks()
        hb = QGroupBox("OS hooks")
        hl = QVBoxLayout(hb)
        for h in _HOOKS:
            cb = QCheckBox(h)
            cb.setChecked(bool(hooks.get(h, False)))
            cb.toggled.connect(lambda on, hh=h: self._set_hook(hh, on))
            hl.addWidget(cb)
        lay.addWidget(hb)

        # Build config: kernel-image options that flow to -D flags.
        cb_box = QGroupBox("Build config")
        cl = QVBoxLayout(cb_box)
        sleep_cb = QCheckBox("Suppress SLEEP instruction (busy-wait idle)")
        sleep_cb.setChecked(p.idle() == "busy")
        sleep_cb.setToolTip(
            "Idle by spinning instead of executing SLEEP. Enable for simulators "
            "/ debuggers that don't implement SLEEP (e.g. SimulIDE); costs power "
            "on real hardware. Emits -DEROS_IDLE_BUSY.")
        sleep_cb.toggled.connect(self._set_idle_busy)
        cl.addWidget(sleep_cb)
        lay.addWidget(cb_box)

        b = p.budget()
        mb = QGroupBox("Memory (static RAM)")
        ml = QFormLayout(mb)
        if b is None:
            ml.addRow(QLabel("config invalid — fix the problems below"))
        else:
            used = b["kernel"] + b["arena"] + b["rings"]
            ml.addRow("kernel state", QLabel(f"~{b['kernel']} B"))
            ml.addRow("pool arena", QLabel(f"{b['arena']} B"))
            ml.addRow("uart rings", QLabel(f"{b['rings']} B"))
            ml.addRow("stack + idle (est.)",
                      QLabel(f"~{b['sram_total'] - used} B of {b['sram_total']}"))
        lay.addWidget(mb)
        lay.addStretch(1)
        return w

    def _page_task(self, tname):
        """A task's config: timing + within-rate priority, and - for a hand ASW
        task - its editable interface (in/out ports bound to peripherals) and
        calibrations. A plain task offers 'Add interface' to become one."""
        p = self.project
        row = next((r for r in p.schedule()
                    if not r["is_model"] and r["name"] == tname), None)
        w = QWidget()
        lay = QVBoxLayout(w)
        if row is None:
            lay.addWidget(QLabel(f"Task '{tname}' not found."))
            return w
        is_asw = p.is_asw(tname)
        kind = "ASW task" if is_asw else "task"
        gb = QGroupBox(f"{kind}: {tname}")
        form = QFormLayout(gb)

        form.addRow("Priority (within rate)", self._priority_combo(tname))

        autostart = QCheckBox("autostart (runs once at boot)")
        autostart.setChecked(row["autostart"])
        form.addRow("", autostart)
        period = QSpinBox()
        period.setRange(0, 32767)
        period.setSpecialValueText("aperiodic")
        period.setValue(row["period_ms"] or 0)
        form.addRow("Period ms", period)
        wcet = QSpinBox()
        wcet.setRange(1, 32767)
        wcet.setValue(row["wcet_ms"])
        form.addRow("WCET ms", wcet)

        def sync():
            period.setDisabled(autostart.isChecked())
        autostart.toggled.connect(lambda _=None: sync())
        sync()
        lay.addWidget(gb)

        iface_states, cal_states = [], []
        if is_asw:
            lay.addWidget(QLabel("Interfaces — in/out ports (erosgen emits "
                                 f"{tname}_Intfc.* and binds these to drivers):"))
            table, iface_states = self._interface_table(tname, editable=True)
            lay.addWidget(table, 1)
            btns = QHBoxLayout()
            for text, d in (("+ Input", "in"), ("+ Output", "out")):
                b = QPushButton(text)
                b.clicked.connect(lambda _=None, dd=d: self._add_port(tname, dd))
                btns.addWidget(b)
            btns.addStretch(1)
            bw = QWidget()
            bw.setLayout(btns)
            lay.addWidget(bw)

            lay.addWidget(QLabel(f"Calibrations — tunables in {tname}_Param.*:"))
            ctable, cal_states = self._calibration_table(tname)
            lay.addWidget(ctable)
            addc = QPushButton("+ Calibration")
            addc.clicked.connect(lambda: self._add_calibration(tname))
            lay.addWidget(addc)
        else:
            hint = QPushButton("Add interface (make this an ASW task)")
            hint.clicked.connect(lambda: self._make_asw(tname))
            lay.addWidget(hint)

        self._task_page = {"name": tname, "period": period, "wcet": wcet,
                           "autostart": autostart, "iface": iface_states,
                           "cals": cal_states}
        apply = QPushButton("Apply")
        apply.clicked.connect(lambda: self._apply_task())
        lay.addWidget(apply)
        lay.addStretch(1)
        return w

    def _interface_table(self, name, editable):
        """Shared in/out binding table for an SWC (codegen model or hand ASW
        task). Columns: Signal, Dir, Type, Driver, Params[, Description, ✕].
        `editable` (hand task) makes Type/Description editable and adds a remove
        button; a codegen model's Signal/Type are read-only (parsed from C).
        Returns (table, states) where each state commits its row on Apply."""
        p = self.project
        rows = p.model_interfaces(name)
        drivers = p.available_drivers()
        cols = (["Signal", "Dir", "Type", "Driver", "Params", "Description", ""]
                if editable else ["Signal", "Dir", "Type", "Driver", "Params"])
        table = QTableWidget(len(rows), len(cols))
        table.setHorizontalHeaderLabels(cols)
        table.verticalHeader().setVisible(False)
        table.setEditTriggers(QTableWidget.NoEditTriggers)
        states = []
        for i, r in enumerate(rows):
            table.setItem(i, 0, _ro(r["signal"]))
            table.setItem(i, 1, _ro(r["direction"]))
            st = {"signal": r["signal"], "get": (lambda: {})}
            if editable:
                tcombo = QComboBox()
                tcombo.addItems(p.available_signal_types())
                j = tcombo.findText(r["ctype"])
                if j >= 0:
                    tcombo.setCurrentIndex(j)
                table.setCellWidget(i, 2, tcombo)
                st["type"] = tcombo
            else:
                table.setItem(i, 2, _ro(r["ctype"]))
            combo = QComboBox()
            combo.addItem("(unbound)")
            combo.addItems([d for d in sorted(drivers)
                            if r["direction"] in drivers[d]["directions"]])
            if r["direction"] == "out":
                combo.addItem("internal")            # ASW<->ASW: exported, no HW
            for src in (p.available_sources(name)
                        if r["direction"] == "in" else []):
                combo.addItem(f"← {src}", f"src:{src}")   # read a producer output
            if r["source"]:                          # preselect source > driver
                j = combo.findData(f"src:{r['source']}")   # str data: findData ok
                combo.setCurrentIndex(j if j >= 0 else 0)
            elif r["driver"]:
                k = combo.findText(r["driver"])
                if k >= 0:
                    combo.setCurrentIndex(k)
            table.setCellWidget(i, 3, combo)
            st["combo"] = combo
            self._fill_params_cell(table, i, r["driver"], r["params"], st, name)
            combo.currentTextChanged.connect(
                lambda drv, row=i, s=st: self._fill_params_cell(table, row, drv,
                                                                {}, s, name))
            if editable:
                desc = QLineEdit(r["description"])
                table.setCellWidget(i, 5, desc)
                st["desc"] = desc
                rm = QPushButton("✕")
                rm.clicked.connect(lambda _=None, sig=r["signal"]:
                                   self._remove_port(name, sig))
                table.setCellWidget(i, 6, rm)
            states.append(st)
        table.resizeColumnsToContents()
        table.horizontalHeader().setStretchLastSection(True)
        return table, states

    def _calibration_table(self, name):
        p = self.project
        cals = p.calibrations(name)
        table = QTableWidget(len(cals), 5)
        table.setHorizontalHeaderLabels(["Name", "Type", "Value", "Description",
                                         ""])
        table.verticalHeader().setVisible(False)
        table.setEditTriggers(QTableWidget.NoEditTriggers)
        states = []
        for i, c in enumerate(cals):
            table.setItem(i, 0, _ro(c["name"]))
            tcombo = QComboBox()
            tcombo.addItems(p.available_signal_types())
            j = tcombo.findText(c["type"])
            if j >= 0:
                tcombo.setCurrentIndex(j)
            table.setCellWidget(i, 1, tcombo)
            val = QLineEdit(str(c["value"]))
            table.setCellWidget(i, 2, val)
            desc = QLineEdit(c["description"])
            table.setCellWidget(i, 3, desc)
            rm = QPushButton("✕")
            rm.clicked.connect(lambda _=None, cn=c["name"]:
                               self._remove_calibration(name, cn))
            table.setCellWidget(i, 4, rm)
            states.append({"name": c["name"], "type": tcombo, "value": val,
                           "desc": desc})
        table.resizeColumnsToContents()
        table.horizontalHeader().setStretchLastSection(True)
        return table, states

    def _page_model(self, mname):
        """A Codegen Task (Simulink SWC): its interface is parsed from the codegen
        dir (Signal/Type read-only); bind each in/out signal to a peripheral."""
        p = self.project
        meta = p.model_meta(mname)
        w = QWidget()
        lay = QVBoxLayout(w)
        if not meta:
            lay.addWidget(QLabel(f"Codegen Task '{mname}' not found."))
            return w

        gb = QGroupBox(f"Codegen Task: {mname}")
        form = QFormLayout(gb)
        form.addRow("codegen", QLabel(str(meta.get("codegen_dir") or "—")))
        form.addRow("runnable", QLabel(str(meta.get("runnable") or "—")))
        rate = QSpinBox()
        rate.setRange(1, 32767)
        rate.setValue(int(meta.get("rate_ms") or 10))
        form.addRow("rate ms", rate)
        form.addRow("Priority (within rate)", self._priority_combo(mname))
        lay.addWidget(gb)

        lay.addWidget(QLabel(
            "Interfaces — bind each in/out signal to a peripheral driver:"))
        table, states = self._interface_table(mname, editable=False)
        lay.addWidget(table, 1)

        self._model_page = {"name": mname, "rate": rate, "states": states}
        apply = QPushButton("Apply bindings")
        apply.clicked.connect(lambda: self._apply_model())
        lay.addWidget(apply)
        return w

    def _page_resource(self, rname):
        """A resource (OSEK shared section): pick which tasks use it. The kernel
        needs >= 1 resource, each with >= 1 user (its priority ceiling is the
        highest-priority user)."""
        p = self.project
        row = next((r for r in p.resources() if r["name"] == rname), None)
        w = QWidget()
        lay = QVBoxLayout(w)
        if row is None:
            lay.addWidget(QLabel(f"Resource '{rname}' not found."))
            return w
        gb = QGroupBox(f"Resource: {rname}")
        gl = QVBoxLayout(gb)
        gl.addWidget(QLabel("Used by (tasks that share this section — the "
                            "priority ceiling is their highest priority):"))
        boxes = []
        for name in p.runnable_names():
            cb = QCheckBox(name)
            cb.setChecked(name in row["users"])
            gl.addWidget(cb)
            boxes.append((name, cb))
        if not boxes:
            gl.addWidget(QLabel("(no tasks yet — add a task first)"))
        lay.addWidget(gb)

        self._resource_page = {"name": rname, "boxes": boxes}
        apply = QPushButton("Apply")
        apply.clicked.connect(lambda: self._apply_resource())
        lay.addWidget(apply)
        lay.addStretch(1)
        return w

    def _page_peripheral(self, name):
        """Activate + configure one peripheral. Activating compiles its driver
        and claims its pins (so overlaps show live in Problems). Config forms
        exist for pwm (frequency) and uart (baud/rings); the rest are toggle
        only for now."""
        p = self.project
        w = QWidget()
        lay = QVBoxLayout(w)
        active = p.peripheral_active(name)
        pins = next((r["pins"] for r in p.known_peripherals()
                     if r["name"] == name), [])
        gb = QGroupBox(f"Peripheral: {name}")
        form = QFormLayout(gb)
        act = QCheckBox("active (compiled in; claims its pins)")
        act.setChecked(active)
        act.toggled.connect(lambda on: self._activate_peripheral(name, on))
        form.addRow("", act)
        form.addRow("pins", QLabel(", ".join(pins) or "—"))
        lay.addWidget(gb)

        if active:
            cfg = p.peripheral_config(name)
            if name == "pwm":
                lay.addWidget(self._pwm_config_group(cfg))
            elif name == "uart":
                lay.addWidget(self._uart_config_group(cfg))
            elif name == "spi":
                lay.addWidget(self._spi_config_group(cfg))
            elif name == "adc":
                lay.addWidget(self._adc_config_group(cfg))
            elif name == "i2c":
                lay.addWidget(self._i2c_config_group(cfg))
            elif name == "timer0_pwm":
                lay.addWidget(self._timer0_pwm_config_group(cfg))
            else:
                lay.addWidget(QLabel("No configurable properties yet — it's "
                                     "compiled in and ready to call."))
        lay.addStretch(1)
        return w

    def _pwm_config_group(self, cfg):
        gb = QGroupBox("PWM (Timer1 fast-PWM)")
        form = QFormLayout(gb)
        freq = QSpinBox()
        freq.setRange(1, 2000000)
        freq.setSuffix(" Hz")
        freq.setValue(int(cfg.get("freq_hz", 1000)))
        note = QLabel()

        def refresh_note(v):
            got = self.project.pwm_achieved(v)
            note.setText(f"→ actual {got[0]:.0f} Hz on {got[1]}"
                         if got else "→ unreachable at this F_CPU")
        freq.valueChanged.connect(refresh_note)
        refresh_note(freq.value())
        form.addRow("frequency", freq)
        form.addRow("", note)
        apply = QPushButton("Apply frequency")
        apply.clicked.connect(
            lambda: self._set_peripheral_prop("pwm", "freq_hz", freq.value()))
        form.addRow(apply)
        return gb

    def _uart_config_group(self, cfg):
        gb = QGroupBox("UART")
        form = QFormLayout(gb)
        baud = QComboBox()
        baud.setEditable(True)
        for b in (9600, 19200, 38400, 57600, 115200):
            baud.addItem(str(b))
        baud.setCurrentText(str(cfg.get("baud", 9600)))
        form.addRow("baud", baud)
        rings = {}
        for key in ("tx_ring", "rx_ring"):
            box = QComboBox()
            for n in (16, 32, 64, 128, 256):
                box.addItem(str(n))
            box.setCurrentText(str(cfg.get(key, 128 if key == "tx_ring" else 64)))
            form.addRow(key, box)
            rings[key] = box
        apply = QPushButton("Apply UART")

        def commit():
            self.project.set_peripheral_prop("uart", "baud", int(baud.currentText()))
            for key, box in rings.items():
                self.project.set_peripheral_prop("uart", key, int(box.currentText()))
            self._defer_refresh()
        apply.clicked.connect(commit)
        form.addRow(apply)
        return gb

    def _spi_config_group(self, cfg):
        gb = QGroupBox("SPI (master)")
        form = QFormLayout(gb)
        mode = QComboBox()
        for m in range(4):
            mode.addItem(f"mode {m}", m)
        mode.setCurrentIndex(int(cfg.get("mode", 0)))
        form.addRow("mode", mode)
        clock = QComboBox()
        # divider -> approximate SCK at 16 MHz, most-common (/16 = 1 MHz) default
        for div, hz in ((2, "8 MHz"), (4, "4 MHz"), (8, "2 MHz"), (16, "1 MHz"),
                        (32, "500 kHz"), (64, "250 kHz"), (128, "125 kHz")):
            clock.addItem(f"/{div}  (~{hz})", div)
        ci = clock.findData(int(cfg.get("clock", 16)))
        clock.setCurrentIndex(ci if ci >= 0 else 3)
        form.addRow("clock", clock)
        apply = QPushButton("Apply SPI")

        def commit():
            self.project.set_peripheral_prop("spi", "mode", mode.currentData())
            self.project.set_peripheral_prop("spi", "clock", clock.currentData())
            self._defer_refresh()
        apply.clicked.connect(commit)
        form.addRow(apply)
        return gb

    def _adc_config_group(self, cfg):
        gb = QGroupBox("ADC")
        form = QFormLayout(gb)
        ref = QComboBox()
        for label, val in (("AVcc", "avcc"), ("internal 1.1 V", "internal"),
                           ("external AREF", "aref")):
            ref.addItem(label, val)
        ri = ref.findData(cfg.get("reference", "avcc"))
        ref.setCurrentIndex(ri if ri >= 0 else 0)
        form.addRow("reference", ref)
        presc = QComboBox()
        for d in (2, 4, 8, 16, 32, 64, 128):
            presc.addItem(f"/{d}", d)
        pi = presc.findData(int(cfg.get("prescaler", 128)))
        presc.setCurrentIndex(pi if pi >= 0 else 6)
        form.addRow("prescaler", presc)
        form.addRow("", QLabel("ADC clock = F_CPU / prescaler; keep it "
                               "50–200 kHz for full 10-bit accuracy."))
        apply = QPushButton("Apply ADC")

        def commit():
            self.project.set_peripheral_prop("adc", "reference", ref.currentData())
            self.project.set_peripheral_prop("adc", "prescaler",
                                             presc.currentData())
            self._defer_refresh()
        apply.clicked.connect(commit)
        form.addRow(apply)
        return gb

    def _i2c_config_group(self, cfg):
        gb = QGroupBox("I2C (TWI master)")
        form = QFormLayout(gb)
        speed = QComboBox()
        for hz, label in ((100000, "100 kHz (standard)"),
                          (400000, "400 kHz (fast)")):
            speed.addItem(label, hz)
        si = speed.findData(int(cfg.get("speed_hz", 100000)))
        speed.setCurrentIndex(si if si >= 0 else 0)
        form.addRow("bus speed", speed)
        apply = QPushButton("Apply I2C")

        def commit():
            self.project.set_peripheral_prop("i2c", "speed_hz",
                                             speed.currentData())
            self._defer_refresh()
        apply.clicked.connect(commit)
        form.addRow(apply)
        return gb

    def _timer0_pwm_config_group(self, cfg):
        gb = QGroupBox("Timer0 PWM (8-bit — OC0A/PD6 + OC0B/PD5)")
        form = QFormLayout(gb)
        freq = QSpinBox()
        freq.setRange(1, 2000000)
        freq.setSuffix(" Hz")
        freq.setValue(int(cfg.get("freq_hz", 977)))
        note = QLabel()

        def refresh_note(v):
            got = self.project.timer0_pwm_achieved(v)
            note.setText(f"→ actual {got:.0f} Hz (8-bit: only the prescaler sets "
                         "it, so it snaps to a few values)"
                         if got else "→ unreachable")
        freq.valueChanged.connect(refresh_note)
        refresh_note(freq.value())
        form.addRow("frequency", freq)
        form.addRow("", note)
        apply = QPushButton("Apply frequency")
        apply.clicked.connect(lambda: self._set_peripheral_prop(
            "timer0_pwm", "freq_hz", freq.value()))
        form.addRow(apply)
        return gb

    def _activate_peripheral(self, name, on):
        self.project.activate_peripheral(name, on)
        self._defer_refresh()

    def _set_peripheral_prop(self, name, key, value):
        self.project.set_peripheral_prop(name, key, value)
        self._defer_refresh()

    def _fill_params_cell(self, table, row, driver, params, state, swc=None):
        """Put the right param picker in the Params cell for `driver`: adc -> a
        channel dropdown, dio -> a pin dropdown, pwm/unbound -> nothing. The pin
        and channel lists hide anything already owned by an active peripheral,
        gpio, or another port (conflict-aware), so a clash can't be picked. Sets
        state['get'] to a callable returning the chosen params dict."""
        sig = state.get("signal")
        cell = QWidget()
        h = QHBoxLayout(cell)
        h.setContentsMargins(4, 0, 4, 0)
        if driver == "adc":
            box = QComboBox()
            for ch in self.project.available_adc_channels(swc, sig):
                box.addItem(f"channel {ch}", ch)
            idx = box.findData(params.get("channel"))
            box.setCurrentIndex(idx if idx >= 0 else 0)
            h.addWidget(box)
            state["get"] = lambda: {"channel": box.currentData()}
        elif driver == "dio":
            box = QComboBox()
            for pin in self.project.available_dio_pins(swc, sig):
                box.addItem(pin["label"], pin["pin"])     # userData = "PB5"
            port, bit = params.get("port"), params.get("bit")
            want = f"P{port}{bit}" if port and bit is not None else None
            idx = box.findData(want) if want else -1
            box.setCurrentIndex(idx if idx >= 0 else 0)
            h.addWidget(box)

            def get_dio():
                pin = box.currentData()                    # "PB5" -> port B, bit 5
                return ({"port": pin[1], "bit": int(pin[2:])}
                        if pin and len(pin) >= 3 else {})
            state["get"] = get_dio
        elif driver == "timer0_pwm":            # Timer0 8-bit PWM: 2 channels
            box = QComboBox()
            for ch, pin in ((0, "PD5/OC0B"), (1, "PD6/OC0A")):
                box.addItem(f"channel {ch} ({pin})", ch)
            idx = box.findData(params.get("channel"))
            box.setCurrentIndex(idx if idx >= 0 else 0)
            h.addWidget(box)
            state["get"] = lambda: {"channel": box.currentData()}
        else:                                   # pwm / (unbound): no params
            h.addWidget(QLabel("—"))
            state["get"] = lambda: {}
        table.setCellWidget(row, 4, cell)

    # ---- inspector edit handlers ----------------------------------------
    def _set_name(self, text):
        text = text.strip()
        if text and text != self.project.name:
            self.project.set_name(text)
            self._defer_refresh()

    def _set_hook(self, name, on):
        self.project.set_hook(name, on)
        self._defer_refresh()

    def _set_idle_busy(self, on):
        self.project.set_idle("busy" if on else "sleep")
        self._log("idle: busy (SLEEP suppressed)" if on else "idle: sleep")
        self._defer_refresh()

    def _autodetect_dirs(self):
        if self.project.autodetect_dirs():
            self._log(f"auto-detected kernel dir: {self.project.kernel_dir}\n"
                      f"auto-detected drivers dir: {self.project.drivers_dir}")
        else:
            self._log("auto-detect: could not find kernel/ and drivers/ near "
                      "erosgen — set them manually")
        self._defer_refresh()

    def _dir_field(self, key, hint):
        """An editable path field + Browse for kernel_dir / drivers_dir. Edits
        don't refresh (paths don't affect live diagnostics), so typing a path is
        never interrupted."""
        edit = QLineEdit(getattr(self.project, key))
        edit.setPlaceholderText(hint)
        edit.editingFinished.connect(
            lambda: self.project.set_dir(key, edit.text().strip()))
        browse = QPushButton("Browse…")

        def pick():
            d = QFileDialog.getExistingDirectory(self, f"Select {key}")
            if d:
                edit.setText(d)
                self.project.set_dir(key, d)
        browse.clicked.connect(pick)
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(edit, 1)
        row.addWidget(browse)
        holder = QWidget()
        holder.setLayout(row)
        return holder

    def _on_mcu_changed(self, chip):
        # Picking a chip selects that chip's default board (its bare-chip profile,
        # or first board). Do NOT gate on self.project.doc: on a fresh launch the
        # doc is empty ({}), and gating there is exactly what made the MCU combo
        # look dead - set_mcu on an empty doc just creates system.mcu.
        if not chip or chip == self.project.current_chip():
            return
        board = self.project.available_targets().get(chip, [chip])[0]
        self.project.set_mcu(board)
        self._defer_refresh()

    def _on_board_changed(self, _idx=None):
        board = self.board_combo.currentData()   # the profile stem, not the label
        if board and board != self.project.mcu:
            self.project.set_mcu(board)
            self._defer_refresh()

    def _commit_task(self):
        """Write the task page's current widgets (timing + every interface and
        calibration row) into the document, without refreshing. Called by Apply
        and, crucially, before Add/Remove Port/Calibration so a structural edit
        never discards the in-progress table values (which live only in the
        widgets until they're written back here)."""
        page = getattr(self, "_task_page", None)
        if not page:
            return
        name = page["name"]
        self.project.update_task(name, page["period"].value(),
                                 page["wcet"].value(),
                                 page["autostart"].isChecked())
        for st in page["iface"]:               # ports: type/description + binding
            if "type" in st:
                self.project.set_port_meta(name, st["signal"],
                                           ctype=st["type"].currentText(),
                                           description=st["desc"].text())
            self._apply_port(name, st)
        for st in page["cals"]:                # calibration type/value/description
            val = st["value"].text().strip()
            self.project.set_calibration(
                name, st["name"], ctype=st["type"].currentText(),
                value=int(val) if val.lstrip("-").isdigit() else val,
                description=st["desc"].text())

    def _apply_task(self):
        page = getattr(self, "_task_page", None)
        if not page:
            return
        self._commit_task()
        self._log(f"applied {page['name']}")
        self._defer_refresh()

    def _priority_combo(self, name):
        """A dropdown that places `name` among its same-rate peers (both hand and
        codegen tasks), most-urgent first. Aperiodic tasks have no rate to order
        within, so it's disabled there."""
        combo = QComboBox()
        peers = self.project.rate_peers(name)
        if len(peers) < 2:
            combo.addItem("— (only runnable at this rate)")
            combo.setEnabled(False)
            return combo
        n = len(peers)
        for i in range(n):
            tag = " (most urgent)" if i == 0 else (" (least)" if i == n - 1 else "")
            combo.addItem(f"{i + 1} of {n}{tag}")
        combo.setCurrentIndex(peers.index(name))     # set before connecting
        combo.currentIndexChanged.connect(
            lambda pos, nm=name: self._set_priority(nm, pos))
        return combo

    def _set_priority(self, name, tree_pos):
        if self.project.set_rate_position(name, tree_pos):
            self._defer_refresh()

    def _make_asw(self, name):
        self.project.make_asw_task(name)
        self._defer_refresh()

    def _add_port(self, name, direction):
        from PySide6.QtWidgets import QInputDialog
        pfx = "IN_" if direction == "in" else "OUT_"
        sig, ok = QInputDialog.getText(self, f"Add {direction} port",
                                       "Signal name:", text=pfx)
        if ok and sig.strip():
            self._commit_task()            # keep the other rows' in-progress edits
            self.project.add_port(name, direction, sig.strip())
            self._defer_refresh()

    def _remove_port(self, name, signal):
        self._commit_task()
        self.project.remove_port(name, signal)
        self._defer_refresh()

    def _add_calibration(self, name):
        from PySide6.QtWidgets import QInputDialog
        cal, ok = QInputDialog.getText(self, "Add calibration", "Name:")
        if ok and cal.strip():
            self._commit_task()
            self.project.add_calibration(name, cal.strip())
            self._defer_refresh()

    def _remove_calibration(self, name, cal):
        self._commit_task()
        self.project.remove_calibration(name, cal)
        self._defer_refresh()

    def _apply_port(self, name, st):
        """Commit one interface row's binding: an internal source (← SWC.OUT),
        an internal-only output, unbound, or a peripheral driver + params."""
        data = st["combo"].currentData()
        drv = st["combo"].currentText()
        if isinstance(data, str) and data.startswith("src:"):
            self.project.set_port_source(name, st["signal"], data[len("src:"):])
        elif drv == "(unbound)":
            self.project.unbind_port(name, st["signal"])
        elif drv == "internal":
            self.project.bind_port(name, st["signal"], "internal")
        else:
            self.project.bind_port(name, st["signal"], drv, **st["get"]())

    def _apply_model(self):
        page = getattr(self, "_model_page", None)
        if not page:
            return
        mname = page["name"]
        self.project.set_model_rate(mname, page["rate"].value())
        for st in page["states"]:
            self._apply_port(mname, st)
        self._log(f"applied bindings for {mname}")
        self._defer_refresh()

    def _apply_resource(self):
        page = getattr(self, "_resource_page", None)
        if not page:
            return
        users = [name for name, cb in page["boxes"] if cb.isChecked()]
        self.project.set_resource_users(page["name"], users)
        self._log(f"resource {page['name']} used by {users or '(none)'}")
        self._defer_refresh()

    # ---- bottom: diagnostics + console ----------------------------------
    _PIN_COLORS = {"conflict": "#d64545", "periph": "#3b73c4",
                   "gpio": "#2e8b57", "port": "#8a5cc9", "na": "#3a3a3a"}

    def _populate_pinout(self):
        """Render the whole-chip pin map from ProjectModel.pinout(): ports as
        rows, bits 0..7 as columns, each cell coloured by owner (conflicts red)."""
        po = self.project.pinout()
        ports = po["ports"]
        self.pinout.setRowCount(len(ports))
        self.pinout.setVerticalHeaderLabels([f"Port {p}" for p in ports])
        for r, port in enumerate(ports):
            for bit in range(8):
                c = po["cells"][(port, bit)]
                text = c["aliases"][0] if c["aliases"] else c["pin"]
                if c["owners"]:
                    text += "\n" + c["owners"][0] + (" ⚠" if c["conflict"]
                                                     else "")
                item = QTableWidgetItem(text)
                tip = c["pin"] + (f" ({', '.join(c['aliases'])})"
                                  if c["aliases"] else "")
                tip += ("\nowners: " + ", ".join(c["owners"]) if c["owners"]
                        else ("\nfree" if c["usable"] else "\nnot broken out"))
                item.setToolTip(tip)
                key = "conflict" if c["conflict"] else c["kind"]
                col = self._PIN_COLORS.get(key)
                if col:
                    item.setBackground(QColor(col))
                    item.setForeground(QColor("#dddddd" if key == "na"
                                              else "#ffffff"))
                self.pinout.setItem(r, bit, item)
        self.pinout.resizeColumnsToContents()
        self.pinout.resizeRowsToContents()
        self.pin_note.setText(
            "Clock: Timer2 CTC, /64, OCR2A=249 → 16 MHz/64/250 = 1 kHz OS "
            "tick (fixed — Timer2 is the kernel tick, never repurpose it).  "
            "Legend: blue=peripheral, green=gpio, purple=port binding, "
            "red=conflict, grey=not broken out.")

    def _populate_diagnostics(self):
        # gate on the doc (not the path) so a new, unsaved project shows its
        # diagnostics live too.
        diags = self.project.diagnostics() if self.project.doc else []
        self._diags = diags          # row -> Diagnostic, for jump-to-source
        self.diag.setRowCount(len(diags))
        for row, d in enumerate(diags):
            for col, val in enumerate((d.severity, d.code, d.location,
                                       d.message)):
                item = QTableWidgetItem(str(val))
                if col == 0 and d.severity in _SEV_COLOR:
                    item.setForeground(QColor(_SEV_COLOR[d.severity]))
                self.diag.setItem(row, col, item)
        self.diag.resizeColumnsToContents()
        errs = sum(1 for d in diags if d.severity == "error")
        if not diags:
            self.tabs.setTabText(0, "Problems")
        else:
            tail = f", {errs} err" if errs else ""
            self.tabs.setTabText(0, f"Problems ({len(diags)}{tail})")

    def _open_diagnostic(self, item):
        """Double-click a problem row -> open its source (jump to source). The
        Diagnostic.location resolves to a line in app.yaml where it can; the file
        opens in the OS default editor either way."""
        diags = getattr(self, "_diags", [])
        row = item.row()
        if not 0 <= row < len(diags):
            return
        path, line = self.project.locate(diags[row])
        if path is None:
            self._log("jump to source: save the project first")
            return
        where = f"{path}:{line}" if line else str(path)
        self._log(f"opening {where}  ({diags[row].location})")
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def _log(self, text):
        self.console.appendPlainText(text)

    # ---- actions --------------------------------------------------------
    def open_project(self):
        fn, _ = QFileDialog.getOpenFileName(
            self, "Open app.yaml", "", "YAML (*.yaml *.yml)")
        if fn:
            self.project.load(fn)
            self._sel = ("system",)
            self._log(f"opened {fn}")
            self.refresh()

    def open_workspace(self, fn=None):
        """Open an erosproject.yaml: populate the workspace bar and load its first
        app into the editor. `fn` is the path (a dialog is shown when omitted)."""
        from gui.project import WorkspaceModel
        if not fn:
            fn, _ = QFileDialog.getOpenFileName(
                self, "Open erosproject.yaml", "", "YAML (*.yaml *.yml)")
        if not fn:
            return
        try:
            ws = WorkspaceModel(fn)
        except (ValueError, OSError) as e:
            QMessageBox.warning(self, "Open Workspace", str(e))
            return
        self.workspace = ws
        self.ws_name.setText(ws.name)
        self.ws_apps.clear()
        for rel, ap in ws.apps():
            self.ws_apps.addItem(rel, str(ap))
        self.ws_variant.clear()
        self.ws_variant.addItem("(none)", "")
        for v in ws.variants():
            self.ws_variant.addItem(v, v)
        self.ws_bar.setVisible(True)
        self._log(f"opened workspace '{ws.name}' — {len(ws.apps())} app(s)")
        if ws.apps():
            self._on_ws_app(0)

    def _on_ws_app(self, idx):
        """Workspace app picker changed: load that app.yaml into the editor."""
        ap = self.ws_apps.itemData(idx)
        if ap:
            self.project.load(ap)
            self._sel = ("system",)
            self._log(f"editing {ap}")
            self.refresh()

    def generate_workspace(self):
        """Generate every app in the workspace with the selected variant overlay."""
        if not self.workspace:
            return
        if self.project.path:
            self.project.save()   # flush edits to the app currently open
        variant = self.ws_variant.currentData() or None
        self.tabs.setCurrentWidget(self.console)
        self._log(f"$ erosgen {self.workspace.path.name}"
                  + (f" --variant {variant}" if variant else ""))
        ok, report = self.workspace.generate(variant)
        self._log(report.rstrip())
        self._log("generate all: OK" if ok else "generate all: FAILED")
        self.refresh()

    def save_project(self):
        if self.project.path:
            self.project.save()
            self._log(f"saved {self.project.path}")
        else:
            self.save_as()

    def new_project(self):
        from PySide6.QtWidgets import QInputDialog
        name, ok = QInputDialog.getText(self, "New Project", "Project name:",
                                        text="app")
        if ok and name:
            self.project.new(name, "atmega328p")
            self._sel = ("system",)
            self._log(f"new project '{name}' — set the MCU on the System page, "
                      "then File → Save As")
            self.refresh()

    def save_as(self):
        fn, _ = QFileDialog.getSaveFileName(self, "Save app.yaml", "app.yaml",
                                            "YAML (*.yaml *.yml)")
        if fn:
            self.project.save(fn)
            self._log(f"saved {fn}")
            self.refresh()

    def add_task_dialog(self):
        # A typed form (QSpinBox) instead of comma-parsed free text - bad
        # numbers are now impossible rather than caught after the fact. An ASW
        # task also gets an interface + the <name>{,_Intfc,_Param} skeletons.
        from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QFormLayout,
                                       QLineEdit, QSpinBox)
        dlg = QDialog(self)
        dlg.setWindowTitle("Add Task")
        form = QFormLayout(dlg)
        name = QLineEdit("ctrl")
        period = QSpinBox()
        period.setRange(0, 32767)
        period.setValue(10)
        period.setSpecialValueText("aperiodic")   # 0 reads as "aperiodic"
        wcet = QSpinBox()
        wcet.setRange(1, 32767)
        wcet.setValue(1)
        asw = QCheckBox("ASW task (add an interface + generate skeleton files)")
        asw.setChecked(True)
        form.addRow("Name", name)
        form.addRow("Period ms", period)
        form.addRow("WCET ms", wcet)
        form.addRow("", asw)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        form.addRow(bb)
        if dlg.exec() != QDialog.Accepted or not name.text().strip():
            return
        nm = name.text().strip()
        self.project.add_task(nm, period.value() or None, wcet.value())
        if asw.isChecked():
            self.project.make_asw_task(nm)
        self._sel = ("asw" if asw.isChecked() else "task", nm)
        self.refresh()

    def add_model_dialog(self):
        from PySide6.QtWidgets import QInputDialog
        d = QFileDialog.getExistingDirectory(self, "Select a <model>_ert_rtw dir")
        if not d:
            return
        try:
            name, sigs, runnable = self.project.model_signals(d)
        except Exception as e:
            QMessageBox.warning(self, "Add Codegen Task", f"Could not parse: {e}")
            return
        # Instance name: the same SWC can be added multiple times (different rate
        # / pins), each a distinct instance. Default to the model name; if already
        # used, suggest <model>_2, <model>_3, ... so the second add just works.
        existing = {m.get("name") for m in self.project.plain.get("models", [])
                    if isinstance(m, dict)}
        inst_default = name
        if name in existing:
            i = 2
            while f"{name}_{i}" in existing:
                i += 1
            inst_default = f"{name}_{i}"
        inst, ok = QInputDialog.getText(
            self, "Add Codegen Task",
            f"Instance name (SWC '{name}'; use distinct names to add it more "
            "than once):", text=inst_default)
        if not ok or not inst:
            return
        rate, ok = QInputDialog.getInt(self, "Add Codegen Task",
                                       f"{inst}: runnable rate (ms)", 10, 1, 32767)
        if not ok:
            return
        self.project.add_model(inst, d, runnable, rate,
                               model=name if inst != name else None)
        self._sel = ("model", inst)
        self._log(f"added codegen task {inst} (SWC {name}): {len(sigs)} signals "
                  "— bind their ports on the right")
        self.refresh()

    def add_resource_dialog(self):
        from PySide6.QtWidgets import QInputDialog
        name, ok = QInputDialog.getText(self, "Add Resource", "Resource name:",
                                        text="app")
        if not ok or not name.strip():
            return
        # default to the first task so the resource is valid immediately
        # (a resource needs >= 1 user); adjust on its page.
        tasks = self.project.runnable_names()
        self.project.add_resource(name.strip(), users=tasks[:1])
        self._sel = ("resource", name.strip())
        self._log(f"added resource {name.strip()} — pick its user tasks on the "
                  "right")
        self.refresh()

    def remove_selected(self):
        kind, name = self._sel[0], (self._sel[1] if len(self._sel) > 1 else None)
        if kind == "signal":                    # route by what owns the port
            kind = "asw" if self.project.is_asw(name) else "model"
        if kind in ("task", "asw"):
            self.project.remove_task(name)
        elif kind == "model":
            self.project.remove_model(name)
        elif kind == "resource":
            self.project.remove_resource(name)
        else:
            QMessageBox.information(
                self, "Remove", "Select a task, codegen task or resource.")
            return
        self._sel = ("system",)
        self.refresh()

    def generate(self):
        if not self.project.path:
            self.save_as()  # a new project must be saved before generating
        if not self.project.path:
            return
        self.tabs.setCurrentWidget(self.console)
        ok, report = self.project.generate()
        self._log(report.rstrip())
        self._log("generate: OK" if ok else "generate: FAILED")
        self.refresh()

    def build(self):
        if not self.project.path:
            QMessageBox.warning(self, "Build", "Open a project first.")
            return
        self.tabs.setCurrentWidget(self.console)
        workdir = str(self.project.path.parent)
        self._log(f"$ make -C {workdir}")
        self.proc = QProcess(self)
        self.proc.setWorkingDirectory(workdir)
        self.proc.setProcessChannelMode(QProcess.MergedChannels)
        self.proc.readyReadStandardOutput.connect(self._drain_build)
        self.proc.errorOccurred.connect(
            lambda e: self._log(f"make: {e}"))
        self.proc.start("make", [])

    def _drain_build(self):
        text = bytes(self.proc.readAllStandardOutput()).decode(errors="replace")
        self._log(text.rstrip())

    def about(self):
        QMessageBox.about(
            self, "About EROS Configurator",
            "EROS Configurator\n\nA thin PySide6 view over the erosgen engine "
            "(validation, diagnostics, RTE generation, MCU profiles).")
