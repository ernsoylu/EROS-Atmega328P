"""Entry point: uv run --extra gui python -m gui [path/to/app.yaml]"""
import sys

from PySide6.QtWidgets import QApplication

from .main_window import MainWindow
from .project import ProjectModel


def main(argv=None):
    argv = sys.argv if argv is None else argv
    app = QApplication(argv[:1])
    project = ProjectModel(argv[1]) if len(argv) > 1 else ProjectModel()
    win = MainWindow(project)
    win.resize(960, 640)
    win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
