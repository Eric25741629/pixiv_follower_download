from PyQt5.QtCore import *
import time
from pixiv_api import *

class test_thread(QThread):
    valueChange = pyqtSignal(int,int)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._isPause = False
        self._value = 0
        self.cond = QWaitCondition()

    def pause(self):
        #print(1)
        self._isPause = True

    def resume(self):
        #print(12)
        self._isPause = False
        

    def run(self):
        while 1:
            print(1)
            while self._isPause:
                print(10)
                time.sleep(1)
            time.sleep(1)








