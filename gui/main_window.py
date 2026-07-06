"""The main window: a master-detail configurator over a ProjectModel.

Left: the project tree - System, and every runnable the kernel schedules
(declared tasks plus one synthesized OS task per model), ordered by priority.
Right: a context panel that shows the *selected* node's config - System -> MCU +
memory budget; a task -> its timing; a model -> its in/out interfaces with inline
peripheral binding. Bottom: tabs for the live problem list and the build/message
console. No domain logic - every fact comes from the ProjectModel / the engine.
"""
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
        self._build_ui()
        self._build_menu()
        self._build_toolbar()
        self.refresh()

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

        self.tabs = QTabWidget()
        self.tabs.addTab(self.diag, "Problems")
        self.tabs.addTab(self.console, "Console")

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

    # ---- view refresh ---------------------------------------------------
    def refresh(self):
        self._populate_tree()
        self._populate_diagnostics()
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
        sysitem = QTreeWidgetItem(self.tree, [f"System: {p.name}", p.mcu])
        sysitem.setData(0, Qt.UserRole, ("system",))

        # Tasks (plain + hand ASW + codegen) grouped by rate; within a rate,
        # ordered by priority (most-urgent first). ◆ = codegen task, ⬡ = hand
        # ASW task (has an interface).
        for label, rows in p.rate_groups():
            grp = QTreeWidgetItem(self.tree, [f"@ {label}", f"{len(rows)}"])
            grp.setData(0, Qt.UserRole, ("section",))
            for r in rows:
                self._task_row_item(grp, r)
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
        else:
            page = QLabel("Select a node on the left.")
        self.inspector.setWidget(page)

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

        self.board_combo = QComboBox()
        self.board_combo.addItems(targets.get(chip, [p.mcu]))
        k = self.board_combo.findText(p.mcu)
        if k >= 0:
            self.board_combo.setCurrentIndex(k)
        self.board_combo.currentTextChanged.connect(self._on_board_changed)
        form.addRow("Board", self.board_combo)
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
            if r["driver"]:
                k = combo.findText(r["driver"])
                if k >= 0:
                    combo.setCurrentIndex(k)
            table.setCellWidget(i, 3, combo)
            st["combo"] = combo
            self._fill_params_cell(table, i, r["driver"], r["params"], st)
            combo.currentTextChanged.connect(
                lambda drv, row=i, s=st: self._fill_params_cell(table, row, drv,
                                                                {}, s))
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

    def _fill_params_cell(self, table, row, driver, params, state):
        """Put the right param picker in the Params cell for `driver`: adc -> a
        channel dropdown, dio -> a pin dropdown, pwm/unbound -> nothing. Sets
        state['get'] to a callable returning the chosen params dict."""
        cell = QWidget()
        h = QHBoxLayout(cell)
        h.setContentsMargins(4, 0, 4, 0)
        if driver == "adc":
            box = QComboBox()
            for ch in self.project.adc_channels():
                box.addItem(f"channel {ch}", ch)
            idx = box.findData(params.get("channel"))
            box.setCurrentIndex(idx if idx >= 0 else 0)
            h.addWidget(box)
            state["get"] = lambda: {"channel": box.currentData()}
        elif driver == "dio":
            box = QComboBox()
            for pin in self.project.dio_pins():
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

    def _on_board_changed(self, board):
        if board and board != self.project.mcu:
            self.project.set_mcu(board)
            self._defer_refresh()

    def _apply_task(self):
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
            drv = st["combo"].currentText()
            if drv == "(unbound)":
                self.project.unbind_port(name, st["signal"])
            else:
                self.project.bind_port(name, st["signal"], drv, **st["get"]())
        for st in page["cals"]:                # calibration type/value/description
            val = st["value"].text().strip()
            self.project.set_calibration(
                name, st["name"], ctype=st["type"].currentText(),
                value=int(val) if val.lstrip("-").isdigit() else val,
                description=st["desc"].text())
        self._log(f"applied {name}")
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
            self.project.add_port(name, direction, sig.strip())
            self._defer_refresh()

    def _remove_port(self, name, signal):
        self.project.remove_port(name, signal)
        self._defer_refresh()

    def _add_calibration(self, name):
        from PySide6.QtWidgets import QInputDialog
        cal, ok = QInputDialog.getText(self, "Add calibration", "Name:")
        if ok and cal.strip():
            self.project.add_calibration(name, cal.strip())
            self._defer_refresh()

    def _remove_calibration(self, name, cal):
        self.project.remove_calibration(name, cal)
        self._defer_refresh()

    def _apply_model(self):
        page = getattr(self, "_model_page", None)
        if not page:
            return
        mname = page["name"]
        self.project.set_model_rate(mname, page["rate"].value())
        for st in page["states"]:
            drv = st["combo"].currentText()
            if drv == "(unbound)":
                self.project.unbind_port(mname, st["signal"])
            else:
                self.project.bind_port(mname, st["signal"], drv, **st["get"]())
        self._log(f"applied bindings for {mname}")
        self._defer_refresh()

    # ---- bottom: diagnostics + console ----------------------------------
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
        rate, ok = QInputDialog.getInt(self, "Add Codegen Task",
                                       f"{name}: runnable rate (ms)", 10, 1, 32767)
        if not ok:
            return
        self.project.add_model(name, d, runnable, rate)
        self._sel = ("model", name)
        self._log(f"added codegen task {name}: {len(sigs)} signals — bind their "
                  "ports on the right")
        self.refresh()

    def remove_selected(self):
        kind, name = self._sel[0], (self._sel[1] if len(self._sel) > 1 else None)
        if kind == "signal":                    # route by what owns the port
            kind = "asw" if self.project.is_asw(name) else "model"
        if kind in ("task", "asw"):
            self.project.remove_task(name)
        elif kind == "model":
            self.project.remove_model(name)
        else:
            QMessageBox.information(self, "Remove",
                                    "Select a task or codegen task to remove.")
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
