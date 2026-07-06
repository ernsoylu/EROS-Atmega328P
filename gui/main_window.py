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
                               QFileDialog, QFormLayout, QGroupBox, QLabel,
                               QLineEdit, QMainWindow, QMessageBox,
                               QPlainTextEdit, QPushButton, QScrollArea,
                               QSpinBox, QSplitter, QTabWidget, QTableWidget,
                               QTableWidgetItem, QTreeWidget, QTreeWidgetItem,
                               QVBoxLayout, QWidget)

from .project import ProjectModel

_SEV_COLOR = {"error": "#d64545", "warning": "#c98a1b", "info": "#3b73c4"}
_HOOKS = ("startup", "error", "shutdown")


# ---- small view helpers (module-level, no state) ------------------------
def _ro(text):
    return QTableWidgetItem(str(text))


def _params_to_text(params):
    return ", ".join(f"{k}={v}" for k, v in params.items())


def _parse_params(text):
    """'channel=0' / 'port=B, bit=5' -> {'channel': 0} / {'port': 'B', ...}.
    Ints stay ints; everything else is a string. Blank -> {} (e.g. pwm)."""
    kw = {}
    for kv in text.split(","):
        if "=" in kv:
            k, v = (x.strip() for x in kv.split("=", 1))
            if k:
                kw[k] = int(v) if v.lstrip("-").isdigit() else v
    return kw


def _params_hint(direction, drivers):
    """Placeholder text listing what each valid driver for this direction wants,
    e.g. 'adc: channel=…  |  dio: port=…, bit=…'."""
    parts = []
    for d in sorted(drivers):
        if direction in drivers[d]["directions"]:
            req = drivers[d]["required"]
            parts.append(f"{d}: " + (", ".join(f"{k}=…" for k in req)
                                     if req else "no params"))
    return "  |  ".join(parts)


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
        editm.addAction("Add &Model from codegen…").triggered.connect(
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

        sched = p.schedule()
        troot = QTreeWidgetItem(self.tree, ["Tasks · by priority", str(len(sched))])
        troot.setData(0, Qt.UserRole, ("section",))
        for r in sched:
            self._task_row_item(troot, r)
        self.tree.expandAll()
        self.tree.blockSignals(False)
        self._reselect()

    def _task_row_item(self, parent, r):
        """One schedule row: a declared task, or a model (◆) that becomes its own
        OS task. Models expand to their bound/unbound signals."""
        mark = " ◆" if r["is_model"] else ""
        if r["autostart"]:
            timing = "autostart"
        elif r["period_ms"]:
            timing = f"{r['period_ms']} ms"
        else:
            timing = "aperiodic"
        prio = "" if r["priority"] is None else f"P{r['priority']} · "
        item = QTreeWidgetItem(parent, [r["name"] + mark, f"{prio}{timing}"])
        kind = "model" if r["is_model"] else "task"
        item.setData(0, Qt.UserRole, (kind, r["name"]))
        if r["is_model"]:
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
        elif kind == "task":
            page = self._page_task(self._sel[1])
        elif kind in ("model", "signal"):
            page = self._page_model(self._sel[1])
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
        self.mcu_combo = QComboBox()
        self.mcu_combo.addItems(p.available_mcus())
        i = self.mcu_combo.findText(p.mcu)
        if i >= 0:
            self.mcu_combo.setCurrentIndex(i)
        self.mcu_combo.currentTextChanged.connect(self._on_mcu_changed)
        form.addRow("MCU", self.mcu_combo)
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
        p = self.project
        row = next((r for r in p.schedule()
                    if not r["is_model"] and r["name"] == tname), None)
        w = QWidget()
        lay = QVBoxLayout(w)
        if row is None:
            lay.addWidget(QLabel(f"Task '{tname}' not found."))
            return w
        gb = QGroupBox(f"Task: {tname}")
        form = QFormLayout(gb)
        prio = "—" if row["priority"] is None else f"P{row['priority']}"
        form.addRow("Priority",
                    QLabel(f"{prio}  (rate-monotonic, engine-assigned)"))
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

        apply = QPushButton("Apply")
        apply.clicked.connect(lambda: self._apply_task(
            tname, period.value(), wcet.value(), autostart.isChecked()))
        lay.addWidget(gb)
        lay.addWidget(apply)
        lay.addStretch(1)
        return w

    def _page_model(self, mname):
        """The model's in/out interfaces as an editable binding table: pick a
        driver per signal and give its params, then Apply. This is the interface
        binding the user asked for - all in/out signals in one place, each
        assignable to a peripheral."""
        p = self.project
        meta = p.model_meta(mname)
        w = QWidget()
        lay = QVBoxLayout(w)
        if not meta:
            lay.addWidget(QLabel(f"Model '{mname}' not found."))
            return w

        gb = QGroupBox(f"Model: {mname}")
        form = QFormLayout(gb)
        form.addRow("codegen", QLabel(str(meta.get("codegen_dir") or "—")))
        form.addRow("runnable", QLabel(str(meta.get("runnable") or "—")))
        rate = QSpinBox()
        rate.setRange(1, 32767)
        rate.setValue(int(meta.get("rate_ms") or 10))
        form.addRow("rate ms", rate)
        lay.addWidget(gb)

        lay.addWidget(QLabel(
            "Interfaces — bind each in/out signal to a peripheral driver:"))
        rows = p.model_interfaces(mname)
        drivers = p.available_drivers()
        table = QTableWidget(len(rows), 5)
        table.setHorizontalHeaderLabels(
            ["Signal", "Dir", "Type", "Driver", "Params"])
        table.verticalHeader().setVisible(False)
        table.setEditTriggers(QTableWidget.NoEditTriggers)
        editors = []
        for i, r in enumerate(rows):
            table.setItem(i, 0, _ro(r["signal"]))
            table.setItem(i, 1, _ro(r["direction"]))
            table.setItem(i, 2, _ro(r["ctype"]))
            combo = QComboBox()
            combo.addItem("(unbound)")
            combo.addItems([d for d in sorted(drivers)
                            if r["direction"] in drivers[d]["directions"]])
            if r["driver"]:
                j = combo.findText(r["driver"])
                if j >= 0:
                    combo.setCurrentIndex(j)
            table.setCellWidget(i, 3, combo)
            pedit = QLineEdit(_params_to_text(r["params"]))
            pedit.setPlaceholderText(_params_hint(r["direction"], drivers))
            table.setCellWidget(i, 4, pedit)
            editors.append((r["signal"], combo, pedit))
        table.resizeColumnsToContents()
        table.horizontalHeader().setStretchLastSection(True)
        lay.addWidget(table, 1)

        apply = QPushButton("Apply bindings")
        apply.clicked.connect(
            lambda: self._apply_model(mname, rate.value(), editors))
        lay.addWidget(apply)
        return w

    # ---- inspector edit handlers ----------------------------------------
    def _set_name(self, text):
        text = text.strip()
        if text and text != self.project.name:
            self.project.set_name(text)
            self._defer_refresh()

    def _set_hook(self, name, on):
        self.project.set_hook(name, on)
        self._defer_refresh()

    def _on_mcu_changed(self, name):
        # Gate on the doc, not the saved path, so it also works on a new,
        # not-yet-saved project (matching the live diagnostics behaviour).
        if self.project.doc and name and name != self.project.mcu:
            self.project.set_mcu(name)
            self._defer_refresh()

    def _apply_task(self, name, period_ms, wcet_ms, autostart):
        self.project.update_task(name, period_ms, wcet_ms, autostart)
        self._defer_refresh()

    def _apply_model(self, mname, rate_ms, editors):
        self.project.set_model_rate(mname, rate_ms)
        for signal, combo, pedit in editors:
            drv = combo.currentText()
            if drv == "(unbound)":
                self.project.unbind_port(mname, signal)
            else:
                self.project.bind_port(mname, signal, drv,
                                       **_parse_params(pedit.text()))
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
        # numbers are now impossible rather than caught after the fact.
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
        form.addRow("Name", name)
        form.addRow("Period ms", period)
        form.addRow("WCET ms", wcet)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        form.addRow(bb)
        if dlg.exec() != QDialog.Accepted or not name.text().strip():
            return
        self.project.add_task(name.text().strip(), period.value() or None,
                              wcet.value())
        self._sel = ("task", name.text().strip())
        self.refresh()

    def add_model_dialog(self):
        from PySide6.QtWidgets import QInputDialog
        d = QFileDialog.getExistingDirectory(self, "Select a <model>_ert_rtw dir")
        if not d:
            return
        try:
            name, sigs, runnable = self.project.model_signals(d)
        except Exception as e:
            QMessageBox.warning(self, "Add Model", f"Could not parse: {e}")
            return
        rate, ok = QInputDialog.getInt(self, "Add Model",
                                       f"{name}: runnable rate (ms)", 10, 1, 32767)
        if not ok:
            return
        self.project.add_model(name, d, runnable, rate)
        self._sel = ("model", name)
        self._log(f"added model {name}: {len(sigs)} signals — bind their ports "
                  "on the right")
        self.refresh()

    def remove_selected(self):
        kind = self._sel[0]
        if kind == "task":
            self.project.remove_task(self._sel[1])
        elif kind in ("model", "signal"):
            self.project.remove_model(self._sel[1])
        else:
            QMessageBox.information(self, "Remove",
                                    "Select a task or model to remove.")
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
