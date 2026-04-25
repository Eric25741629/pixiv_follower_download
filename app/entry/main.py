from PyQt5 import QtGui, QtWidgets
from app.gui.controller import MainWindow_controller

try:
    from qfluentwidgets import Theme, setTheme, setThemeColor
    HAS_FLUENT = True
except Exception:
    HAS_FLUENT = False


def main():
    import sys
    app = QtWidgets.QApplication(sys.argv)
    app_font = QtGui.QFont("Microsoft JhengHei UI", 12)
    app.setFont(app_font)
    if HAS_FLUENT:
        # Force light theme to avoid low-contrast text on translucent backgrounds.
        setTheme(Theme.LIGHT)
        setThemeColor("#4A7BFF")
    window = MainWindow_controller()
    window.show()
    window.setup_control()
    sys.exit(app.exec_())


if __name__ == '__main__':
    main()
