import sys
import time
from pathlib import Path

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QTimer
from PIL import ImageGrab

sys.path.insert(0, '/Super_ADB/Super_ADB_Main')
from about_dialog import AboutDialog

app = QApplication(sys.argv)
app.setQuitOnLastWindowClosed(False)

dlg = AboutDialog()
dlg.show()

def capture():
    time.sleep(0.5)
    img = ImageGrab.grab()
    img.save('G:/Python/jcspy/Super_ADB/shot_about_standalone.png')
    print('captured')
    app.quit()

QTimer.singleShot(1000, capture)
app.exec()
