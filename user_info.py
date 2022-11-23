from PyQt5 import QtCore
from PyQt5.QtGui import QImage,QPixmap
import os,json
from PyQt5.QtWidgets import QFileDialog
class Userdata_controller(object):
    def __init__(self,path,exist_pid,Author_list,download_path,start,stop,user_path1):
        self.path =path
        self.exist_pid =exist_pid
        self.author_list =Author_list
        self.download_path =download_path    
        self.start=start
        self.stop=stop
        self.user_path1=user_path1
    def load_data(self):
        self.path=os.getenv('APPDATA')+r'/pixiv_download/'
        if not os.path.exists(self.path):
            os.mkdir(self.path)
        if not os.path.exists(self.path+r"/existPID.txt"):
            print("找不到existPID文件")
        else:
            with open((self.path+r"/existPID.txt")) as file:     #讀取寫入的文檔
                self.exist_pid = [line.rstrip().replace("p0","") for line in file]
                self.exist_pid = set(self.exist_pid)
        #self.pixiv_pid(self.exist_pid)
        if not os.path.exists(self.path+r"/following.txt"):
            print("找不到following文件")
        else:
            with open((self.path+r"/following.txt")) as file:     #讀取寫入的文檔
                self.Author_list = [line.rstrip() for line in file]
        if os.path.isfile(self.path+'data.json'):
            try:
                with open(self.path+'data.json') as f:
                    data = json.load(f)
                    self.download_path=data['user_download_path']
                    self.user_path1.setText(self.download_path)
            except Exception as err:
                print(err)
                print("加載user_data文件失敗\n重新選擇資料夾")
                self.download_path=self.open_folder()+'/'
                self.user_path1.setText(self.path)
                self.write_data(self)
            
        else :
            print("找不到user_data文件")
            self.download_path=self.open_folder()+'/'
            self.write_data(self)
        return self.path,self.download_path,self.exist_pid,self.user_path1,self.Author_list ,self.start,self.stop
    def open_folder(self):
        folder_path = QFileDialog.getExistingDirectory(self,
                  "Open folder",
                  "./")                 # start path
        print(folder_path)
        path=folder_path
        return path
    def set_downloaa_path(self):
        self.download_path=self.open_folder()+'/'
        self.user_path1.setText(self.download_path)
    def write_data(self):
        jsonObject = {
        "start": self.start,
        "stop": self.stop,
        "user_download_path": self.download_path,    
        }
        user_data=self.path
        fileName = user_data+"data.json"
        file = open(fileName, "w")
        json.dump(jsonObject, file, indent = 4)
        file.close()        
    