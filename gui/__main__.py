"""Entry point: uv run --extra gui python -m gui [path/to/app.yaml]"""
import faulthandler
import sys

from PySide6.QtWidgets import QApplication

from .main_window import MainWindow
from .project import ProjectModel

faulthandler.enable()   # dump a Python+C traceback if Qt ever hard-crashes


def main(argv=None):
    argv = sys.argv if argv is None else argv
    app = QApplication(argv[:1])
    project = ProjectModel(argv[1]) if len(argv) > 1 else ProjectModel()
    win = MainWindow(project)
    win.install_excepthook()   # keep the app alive + log on a slot exception
    win.confirm_close = True   # prompt to save unsaved edits on exit
    geo = win._settings.value("geometry")
    if geo is not None:
        win.restoreGeometry(geo)   # come back at last session's size/position
    else:
        win.resize(1200, 760)
    win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
