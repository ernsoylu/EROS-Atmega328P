"""The main window: a thin two-pane view over a ProjectModel.

Left pane: project tree (MCU, tasks, models). Right pane: the live diagnostics
table (engine's collect_diagnostics). Bottom: a build console that streams
`make`. Menu: File (Open/Save/Generate/Build), Help (About). No domain logic -
every fact comes from the ProjectModel / the engine.
"""
from PySide6.QtCore import Qt, QProcess
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QFileDialog, QMainWindow, QMessageBox,
                               QPlainTextEdit, QSplitter, QTableWidget,
                               QTableWidgetItem, QTreeWidget, QTreeWidgetItem,
                               QVBoxLayout, QWidget)

from .project import ProjectModel

_SEV_COLOR = {"error": "#d64545", "warning": "#c98a1b", "info": "#3b73c4"}


class MainWindow(QMainWindow):
    def __init__(self, project=None):
        super().__init__()
        self.project = project or ProjectModel()
        self.proc = None
        self._build_ui()
        self._build_menu()
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

        self.console = QPlainTextEdit()
        self.console.setReadOnly(True)
        self.console.setMaximumBlockCount(5000)

        outer = QSplitter(Qt.Vertical)
        outer.addWidget(panes)
        outer.addWidget(self.console)
        outer.setStretchFactor(0, 3)
        self.setCentralWidget(outer)

    def _build_menu(self):
        filem = self.menuBar().addMenu("&File")
        for text, slot in (("&Open…", self.open_project),
                           ("&Save", self.save_project),
                           ("&Generate", self.generate),
                           ("&Build", self.build)):
            act = filem.addAction(text)
            act.triggered.connect(slot)
        filem.addSeparator()
        filem.addAction("E&xit").triggered.connect(self.close)
        self.menuBar().addMenu("&Help").addAction("&About").triggered.connect(
            self.about)

    # ---- view refresh ---------------------------------------------------
    def refresh(self):
        self._populate_tree()
        self._populate_diagnostics()
        name = self.project.name if self.project.path else "(no project)"
        self.setWindowTitle(f"EROS Configurator — {name}")

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
            QTreeWidgetItem(mroot, [m["name"], f"{m['rate_ms']} ms"])
        self.tree.expandAll()

    def _populate_diagnostics(self):
        diags = self.project.diagnostics() if self.project.path else []
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

    def generate(self):
        if not self.project.path:
            QMessageBox.warning(self, "Generate", "Open a project first.")
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
