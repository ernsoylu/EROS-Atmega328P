"""The main window: a thin two-pane view over a ProjectModel.

Left pane: project tree (MCU, tasks, models). Right pane: the live diagnostics
table (engine's collect_diagnostics). Bottom: a build console that streams
`make`. Menu: File (Open/Save/Generate/Build), Help (About). No domain logic -
every fact comes from the ProjectModel / the engine.
"""
from PySide6.QtCore import Qt, QProcess
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QComboBox, QFileDialog, QLabel, QMainWindow,
                               QMessageBox, QPlainTextEdit, QSplitter,
                               QTableWidget, QTableWidgetItem, QTreeWidget,
                               QTreeWidgetItem, QVBoxLayout, QWidget)

from .project import ProjectModel

_SEV_COLOR = {"error": "#d64545", "warning": "#c98a1b", "info": "#3b73c4"}


class MainWindow(QMainWindow):
    def __init__(self, project=None):
        super().__init__()
        self.project = project or ProjectModel()
        self.proc = None
        self._build_ui()
        self._build_menu()
        self._build_toolbar()
        self.refresh()

    # ---- construction ---------------------------------------------------
    def _build_ui(self):
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Project", "Value"])
        self.tree.setColumnWidth(0, 220)

        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        self.diag = QTableWidget(0, 4)
        self.diag.setHorizontalHeaderLabels(
            ["Severity", "Code", "Location", "Message"])
        self.diag.verticalHeader().setVisible(False)
        self.diag.setEditTriggers(QTableWidget.NoEditTriggers)
        rl.addWidget(self.diag)

        panes = QSplitter(Qt.Horizontal)
        panes.addWidget(self.tree)
        panes.addWidget(right)
        panes.setStretchFactor(1, 2)
        panes.setSizes([440, 540])

        self.console = QPlainTextEdit()
        self.console.setReadOnly(True)
        self.console.setMaximumBlockCount(5000)

        outer = QSplitter(Qt.Vertical)
        outer.addWidget(panes)
        outer.addWidget(self.console)
        outer.setStretchFactor(0, 3)
        outer.setSizes([460, 150])
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

        editm = self.menuBar().addMenu("&Edit")
        editm.addAction("Add &Task…").triggered.connect(self.add_task_dialog)
        editm.addAction("&Remove Selected Task").triggered.connect(
            self.remove_selected_task)

        modelm = self.menuBar().addMenu("&Model")
        modelm.addAction("&Add Model from codegen…").triggered.connect(
            self.add_model_dialog)
        modelm.addAction("&Bind Port…").triggered.connect(self.bind_port_dialog)

        self.menuBar().addMenu("&Help").addAction("&About").triggered.connect(
            self.about)

    def _build_toolbar(self):
        tb = self.addToolBar("Main")
        tb.addWidget(QLabel(" MCU: "))
        self.mcu_combo = QComboBox()
        self.mcu_combo.addItems(self.project.available_mcus())
        self.mcu_combo.currentTextChanged.connect(self._on_mcu_changed)
        tb.addWidget(self.mcu_combo)

    def _on_mcu_changed(self, name):
        # live editing: change the target, the diagnostics/budget re-derive.
        if self.project.path and name and name != self.project.mcu:
            self.project.set_mcu(name)
            self.refresh()

    # ---- view refresh ---------------------------------------------------
    def refresh(self):
        self._sync_mcu_combo()
        self._populate_tree()
        self._populate_diagnostics()
        name = self.project.name if self.project.path else "(no project)"
        self.setWindowTitle(f"EROS Configurator — {name}")

    def _sync_mcu_combo(self):
        self.mcu_combo.blockSignals(True)
        idx = self.mcu_combo.findText(self.project.mcu)
        if idx >= 0:
            self.mcu_combo.setCurrentIndex(idx)
        self.mcu_combo.blockSignals(False)

    def _populate_tree(self):
        self.tree.clear()
        p = self.project
        sysroot = QTreeWidgetItem(self.tree, [f"System: {p.name}", ""])
        QTreeWidgetItem(sysroot, ["MCU", p.mcu])
        tasks = p.tasks()
        troot = QTreeWidgetItem(self.tree, ["Tasks", str(len(tasks))])
        for t in tasks:
            QTreeWidgetItem(troot, [t["name"],
                                    f"{t['kind']}, wcet {t['wcet_ms']} ms"])
        models = p.models()
        mroot = QTreeWidgetItem(self.tree, ["Models", str(len(models))])
        for m in models:
            mi = QTreeWidgetItem(mroot, [m["name"], f"{m['rate_ms']} ms"])
            for sig, direction in p.model_port_signals(m["name"]):
                QTreeWidgetItem(mi, [f"{sig} ({direction})",
                                     p.port_binding(m["name"], sig)])

        b = p.budget()
        memroot = QTreeWidgetItem(self.tree, ["Memory (static RAM)", ""])
        if b is None:
            QTreeWidgetItem(memroot, ["(config invalid)", "—"])
        else:
            used = b["kernel"] + b["arena"] + b["rings"]
            QTreeWidgetItem(memroot, ["kernel state", f"~{b['kernel']} B"])
            QTreeWidgetItem(memroot, ["pool arena", f"{b['arena']} B"])
            QTreeWidgetItem(memroot, ["uart rings", f"{b['rings']} B"])
            QTreeWidgetItem(memroot, ["stack + idle (est.)",
                                      f"~{b['sram_total'] - used} B "
                                      f"of {b['sram_total']}"])
        self.tree.expandAll()

    def _populate_diagnostics(self):
        # gate on the doc (not the path) so a new, unsaved project shows its
        # diagnostics live too.
        diags = self.project.diagnostics() if self.project.doc else []
        self.diag.setRowCount(len(diags))
        for row, d in enumerate(diags):
            for col, val in enumerate((d.severity, d.code, d.location,
                                       d.message)):
                item = QTableWidgetItem(str(val))
                if col == 0 and d.severity in _SEV_COLOR:
                    item.setForeground(QColor(_SEV_COLOR[d.severity]))
                self.diag.setItem(row, col, item)
        self.diag.resizeColumnsToContents()

    # ---- actions --------------------------------------------------------
    def open_project(self):
        fn, _ = QFileDialog.getOpenFileName(
            self, "Open app.yaml", "", "YAML (*.yaml *.yml)")
        if fn:
            self.project.load(fn)
            self.console.appendPlainText(f"opened {fn}")
            self.refresh()

    def save_project(self):
        if self.project.path:
            self.project.save()
            self.console.appendPlainText(f"saved {self.project.path}")
        else:
            self.save_as()

    def new_project(self):
        from PySide6.QtWidgets import QInputDialog
        name, ok = QInputDialog.getText(self, "New Project", "Project name:",
                                        text="app")
        if ok and name:
            self.project.new(name, self.mcu_combo.currentText() or "atmega328p")
            self.console.appendPlainText(f"new project '{name}' (unsaved — "
                                         "edit, then File → Save As)")
            self.refresh()

    def save_as(self):
        fn, _ = QFileDialog.getSaveFileName(self, "Save app.yaml", "app.yaml",
                                            "YAML (*.yaml *.yml)")
        if fn:
            self.project.save(fn)
            self.console.appendPlainText(f"saved {fn}")
            self.refresh()

    def add_task_dialog(self):
        from PySide6.QtWidgets import QInputDialog
        spec, ok = QInputDialog.getText(
            self, "Add Task",
            "name, period_ms (blank = aperiodic), wcet_ms:", text="ctrl, 10, 1")
        if ok and spec.strip():
            parts = [p.strip() for p in spec.split(",")]
            period = int(parts[1]) if len(parts) > 1 and parts[1] else None
            wcet = int(parts[2]) if len(parts) > 2 and parts[2] else 1
            self.project.add_task(parts[0], period, wcet)
            self.refresh()

    def remove_selected_task(self):
        it = self.tree.currentItem()
        if it and it.parent() and it.parent().text(0) == "Tasks":
            self.project.remove_task(it.text(0))
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
        self.console.appendPlainText(
            f"added model {name}: {len(sigs)} signals — now bind their ports")
        self.refresh()

    def bind_port_dialog(self):
        from PySide6.QtWidgets import QInputDialog
        models = [m["name"] for m in self.project.models()]
        if not models:
            QMessageBox.information(self, "Bind Port", "Add a model first.")
            return
        model, ok = QInputDialog.getItem(self, "Bind Port", "Model:", models,
                                         0, False)
        if not ok:
            return
        sigs = [f"{s} ({d})" for s, d in self.project.model_port_signals(model)]
        if not sigs:
            QMessageBox.information(self, "Bind Port", "That model has no ports.")
            return
        choice, ok = QInputDialog.getItem(self, "Bind Port", "Signal:", sigs,
                                          0, False)
        if not ok:
            return
        signal = choice.split(" ")[0]
        driver, ok = QInputDialog.getItem(self, "Bind Port",
                                          f"Driver for {signal}:",
                                          ["adc", "dio", "pwm"], 0, False)
        if not ok:
            return
        params, ok = QInputDialog.getText(
            self, "Bind Port",
            "Params (e.g. 'channel=0' or 'port=B, bit=5'; blank for pwm):")
        if not ok:
            return
        kw = {}
        for kv in params.split(","):
            if "=" in kv:
                k, v = (x.strip() for x in kv.split("=", 1))
                kw[k] = int(v) if v.lstrip("-").isdigit() else v
        self.project.bind_port(model, signal, driver, **kw)
        self.console.appendPlainText(f"bound {model}.{signal} → {driver} {kw}")
        self.refresh()

    def generate(self):
        if not self.project.path:
            self.save_as()  # a new project must be saved before generating
        if not self.project.path:
            return
        ok, report = self.project.generate()
        self.console.appendPlainText(report.rstrip())
        self.console.appendPlainText("generate: OK" if ok else "generate: FAILED")
        self.refresh()

    def build(self):
        if not self.project.path:
            QMessageBox.warning(self, "Build", "Open a project first.")
            return
        workdir = str(self.project.path.parent)
        self.console.appendPlainText(f"$ make -C {workdir}")
        self.proc = QProcess(self)
        self.proc.setWorkingDirectory(workdir)
        self.proc.setProcessChannelMode(QProcess.MergedChannels)
        self.proc.readyReadStandardOutput.connect(self._drain_build)
        self.proc.errorOccurred.connect(
            lambda e: self.console.appendPlainText(f"make: {e}"))
        self.proc.start("make", [])

    def _drain_build(self):
        text = bytes(self.proc.readAllStandardOutput()).decode(errors="replace")
        self.console.appendPlainText(text.rstrip())

    def about(self):
        QMessageBox.about(
            self, "About EROS Configurator",
            "EROS Configurator\n\nA thin PySide6 view over the erosgen engine "
            "(validation, diagnostics, RTE generation, MCU profiles).")
