from PyQt5 import QtWidgets, QtGui, QtCore
from PyQt5.QtWidgets import QFileDialog
from PyQt5.QtCore import *
from PyQt5.QtGui import *
from PyQt5.QtWidgets import *
from tqdm import trange
from ui import Ui_MainWindow
import pixiv_api
from queue import Queue
import numpy as np
import time,random
import threading
import os
from multiprocessing import Pool
import requests
from tqdm import tqdm, trange
from multiprocessing import Pool
import multiprocessing as MP
from queue import Queue
import os
from functools import partial
import tqdm
import download_url
global cookies
global Agent
global path
import concurrent.futures
class get_pixiv_author_imgID_Thread(QThread):
        def __init__(self):
            super(get_pixiv_author_imgID_Thread,self).__init__()
        def run(self):
            print(len(self.Author_list))
            #Author_list = np.array_split(self.Author_list,700)   #將獲取的文檔分成100等分
            #print(len(self.Author_list))
            func=partial(pixiv_api.thread_no_use_seleium_get_pid,self.cookies[0],self.Agent,self.path,'1')
            results = []
            with concurrent.futures.ThreadPoolExecutor(max_workers=16) as executor:  
                results = list(tqdm.tqdm(executor.map(func, self.Author_list), total=len(self.Author_list))) 
            
            results=([i for item in results for i in item]) 
            print(len(results))
            end=[]
            for i in trange(0,len(results)):
                if(results[i] not in self.exist_pid):
                    #print(results[i])
                    end.append(results[i])
            
            '''p=Pool(processes= 16)
            
            for result in tqdm.tqdm(p.imap_unordered(MainWindow_controller.in_the_pid, results), total=len(results)):
                if(result!=0):
                    end.append(result)
            p.close()
            p.join()'''
            #results=[x for x in results if not MainWindow_controller.in_the_pid(x)]
            print(len(end))
            f = open((self.path+"/pictures_id.txt"), "w+")     #讀取寫入的文檔
            for text in end:
                f.write(str(text)+'\n')
            f.close()
class MainWindow_controller(QtWidgets.QMainWindow):
    path='none'
    exist_pid=''
    Author_list=[]
    cookies=['p_ab_id=5; p_ab_id_2=5; p_ab_d_id=1125130963; first_visit_datetime_pc=2021-02-23+02%3A32%3A11; yuid_b=OJRESQg; a_type=0; b_type=1; login_ever=yes; privacy_policy_notification=0; c_type=34; PHPSESSID=27915696_60l9nve4HS7Z2Y0bngGXRZQwV4W4DvJ0; privacy_policy_agreement=3; QSI_S_ZN_5hF4My7Ad6VNNAi=v:0:0; tag_view_ranking=0xsDLqCEW6~qWFESUmfEs~LVSDGaCAdn~QKeXYK2oSR~Txs9grkeRc~RTJMXD26Ak~kGYw4gQ11Z~lH5YZxnbfC~Lt-oEicbBr~_EOd7bsGyl~yS_WrRrWFi~G-44hwuIPi~LLyDB5xskQ~Ie2c51_4Sp~HLWLeyYOUF~DADQycFGB0~sqGkVxMuMR~jk9IzfjZ6n~uvBGOtCzqF~MM6RXH_rlN~aKhT3n4RHZ~HY55MqmzzQ~Ti1gvrVQFO~bXMh6mBhl8~RokSaRBUGr~aC55Umcfh1~zsm1ECW5Wb~5f1R8PG9ra~xa5-CDAPro~G_f4j5NH8i~v3nOtgG77A~0RGtdYkK6L~abNIEh2zTB~Bd2L9ZBE8q~0jyux9PxkH~QaiOjmwQnI~n39RQWfHku~vxqZQOR3t2~hk_QPyZfi8~Tg1PbOMGRv~qXzcci65nj~ZTBAtZUDtQ~1VgdMhBiax~dUhrZMpRPB~tgP8r-gOe_~YTKjYV1RQx~Je_lQPk0GY~m3EJRa33xU~iVTmZJMGJj~rMC0CLW0cf~mHukPa9Swj~GuK7T6aGv6~T6NhuB95ST~CLTDpOEHJL~gpglyfLkWs~NGpDowiVmM~MnGbHeuS94~mZurA-1CO-~Am8pyjYCcZ~Riqeg_qBGT~jfnUZgnpFl~BtXd1-LPRH~ujS7cIBGO-~zZZn32I7eS~CrFcrMFJzz~ZN5DR5ie1W~AZ1ov2QNRs~N7rBHi7ijr~QzKFCsGzn-~PBxKNk7VAD~zyKU3Q5L4C~vAwbTkrP0I~P5-w_IbJrm~Ltbk6w58aR~l2rugVKl6u~ajFGI2BXvo~R0DtApn-IB~W4_X_Af3yY~OUF2gvwPef~D4hLr_YmAD~QIa7PLv7ZL~EQ_o6ZyXFg~lf-Uj4GKzU~2FO_ideA5k~18j5-cWRq2~FPCeANM2Bm~TWrozby2UO~9Gbahmahac~2QTW_H5tVX~bplY14maDo~jjVAJCBCtW~B2kc8vAuXw~m3sqCXWo7m~k39B1CkQWC~muA8Dd9eL4~I-ST5EF_lI~wbvCWCYbkM~mVhi1hBMit~Hry6GxyqEm~i8u6Dgt7ao; __cf_bm=cqoyzD4i.qO0s1sUnjhOf9p5ytamrWA2qApQNhhiIKE-1656319872-0-AaXwpJas6wECDAH0caPNgFN5+Y5wjvrFlFzdxBuyzQz6oQGTN8qILCJhy4DeWPqBE9H8Msy1ymtWXbBqLJ6dRm160hdvQQHr56qP0p3ZdhTI'
            ,'first_visit_datetime_pc=2022-02-21+04%3A19%3A54; yuid_b=MTUTVkk; p_ab_id=4; p_ab_id_2=6; p_ab_d_id=1662998805; __utmz=235335808.1645384795.1.1.utmcsr=(direct)|utmccn=(direct)|utmcmd=(none); __utmc=235335808; _fbp=fb.1.1645384796047.1404905860; tags_sended=1; categorized_tags=qiO14cZMBI; _ga=GA1.2.1345214711.1645384795; _gid=GA1.2.1304250070.1645458740; _gcl_au=1.1.1013665307.1645458745; __utma=235335808.1345214711.1645384795.1645384795.1645458746.2; device_token=890c12fe1ae502d096903938be8c227e; privacy_policy_agreement=3; c_type=23; privacy_policy_notification=0; a_type=0; b_type=1; _im_vid=01FWEFWFG9GRN8DKJXAZT84BYM; _im_uid.3929=b.c59ccf7d5d50d431; login_ever=yes; user_language=zh_tw; adr_id=BmFidFAu5OmMs4oXuHo61ZJLtzGcfXPXY1CthCRCZhiXhOjN; __utmv=235335808.|2=login%20ever=yes=1^3=plan=normal=1^5=gender=male=1^6=user_id=78672485=1^9=p_ab_id=4=1^10=p_ab_id_2=6=1^11=lang=zh_tw=1; __cf_bm=u3f.4UbqFzh27jI.9kiV6Oek8ybKUiapTb6Iug3OrYw-1645460067-0-AWgfr/W0kUyjWwGTlZ5yOHXeVyyTC6Nbt/OpfQ+2DlBmCcCXhBaPZZ6mucGHJb/3t1q2oy05xdEgTIivZXBisI8SeMo6hkVkh2mXNM15kk+ZPXFsj8JRrWB2Hci3lD9oCaUcxsMdsgwB4ddFjiT6y/QhczacXUzVCcvzK1kq17/Cgd9v6IaYOwUrfhrTU7ZHNw==; __utmt=1; _gat=1; PHPSESSID=78672485_uQHjP5oN2l5xMuDphuPP4l4w7tu6hi8s; _gat_UA-76252338-1=1; QSI_S_ZN_5hF4My7Ad6VNNAi=r:10:12; _gat_UA-1830249-3=1; tag_view_ranking=0xsDLqCEW6~kGYw4gQ11Z~Lt-oEicbBr~yPNaP3JSNF~hZ2t3qh_DW~4sFVZzGkwH~3QA_JYWfyk~iVi-M2JKqi~sAwDH104z0~7-W__ytBZr~lH5YZxnbfC~QZh6FpjOHZ~BSkdEJ73Ii~MSNRmMUDgC~4ZEPYJhfGu~Mqchq_6wKi~yTeYwgP1QD~CCSovzMpr1~MoxKbn2Dre~dqqWNpq7ul~k712URXStf~Ck8wmf-5yb~yM9dlBJ8Hp~l015P5ziIS~qiO14cZMBI~RVRPe90CVr~oCR2Pbz1ly~eVxus64GZU~uKsA-LcJvn; __utmb=235335808.37.9.1645460132433',
    'first_visit_datetime_pc=2022-02-22+01%3A16%3A54; p_ab_id=4; p_ab_id_2=7; p_ab_d_id=1650471887; __utma=235335808.1075363582.1645460214.1645460214.1645460214.1; __utmc=235335808; __utmz=235335808.1645460214.1.1.utmcsr=(direct)|utmccn=(direct)|utmcmd=(none); __cf_bm=0a88D.DNolnjql11bA2YlSX0AcrLNbOeH4e01.n9Bxo-1645460214-0-AZBMYe8wLu85sKJgxlsiLyTfyRirq9PImai1YQW6aqaElwzgwilYvqTg1yArIG9dhNAWfXUnstyJaUnA7KjsC4hCuS2VpDjkwIxqiWZjNpEfds/gw+Fti9Xi7WNGm60A275E+delw5z8UbPR+KvvWIsu+gzXXX+bNS+iKfwixI8fUjhytmLnoA2qeosMkwT2EA==; _fbp=fb.1.1645460216607.810010051; __utmt=1; yuid_b=ExaEQEI; _ga=GA1.2.1075363582.1645460214; _gid=GA1.2.1775348523.1645460337; _gat=1; PHPSESSID=78672220_Guho8rq4QaKdQmhrizUuq9XBanNhtU5S; device_token=9fed18d4053fdd2dd3c14b6a8b9487f6; privacy_policy_agreement=3; c_type=23; privacy_policy_notification=0; a_type=0; b_type=1; __utmv=235335808.|3=plan=normal=1^5=gender=male=1^6=user_id=78672220=1^11=lang=zh_tw=1; QSI_S_ZN_5hF4My7Ad6VNNAi=r:10:1; tag_view_ranking=qiO14cZMBI~RVRPe90CVr~oCR2Pbz1ly~Lt-oEicbBr~eVxus64GZU~uKsA-LcJvn; __utmb=235335808.4.10.1645460214'
    ,'first_visit_datetime_pc=2022-03-17+23%3A05%3A16; p_ab_id=4; p_ab_id_2=4; p_ab_d_id=398323197; yuid_b=JXAwggA; _gcl_au=1.1.376831798.1647525922; __cf_bm=1bEVYJcWhK3aD7_yMqeN.9aF4ly.x_IsZQU.WMkJmV0-1647525921-0-AS6pMRGvMfXgPGqtV8X/oOqaXsE4DEyDJwYTT7O6ry9zqEZvXD0Vb0daRPCbSD4aqTK0KGaKKNnF1dw/f/+ImVyyPZdK3AyET1BfehP8gyBNDoZxtot4aIeMOJ7lFsgL00bPzGgrfl2MlwPYIrVXScH21y9klIlZTohcTSPrXcltbngkI4qF2gZ1kTn9QbfymQ==; __utmz=235335808.1647525924.1.1.utmcsr=(direct)|utmccn=(direct)|utmcmd=(none); __utmc=235335808; __utma=235335808.2118747505.1647525924.1647525924.1647525924.1; __utmt=1; _ga=GA1.2.2118747505.1647525924; _gid=GA1.2.746009525.1647525924; _fbp=fb.1.1647525924803.1181043735; PHPSESSID=79496792_JOPvYmDmXskwVt5ZyrZdpPfS1LX1N4a9; device_token=34806b359e78fe0cde0c57865819ef64; privacy_policy_agreement=3; c_type=42; privacy_policy_notification=0; a_type=0; b_type=0; _gat_UA-1830249-3=1; login_ever=yes; __utmv=235335808.|2=login%20ever=yes=1^3=plan=normal=1^6=user_id=79496792=1^9=p_ab_id=4=1^10=p_ab_id_2=4=1^11=lang=zh_tw=1; user_language=zh_tw; QSI_S_ZN_5hF4My7Ad6VNNAi=r:10:2; tag_view_ranking=RTJMXD26Ak~I8PKmJXPGb~HLnjco0RwG~cvz2GKHWJW~ETjPkL0e6r; __utmb=235335808.15.9.1647526108186',
    ]
    Agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/96.0.4664.110 Safari/537.36 Edg/96.0.1054.62'
    picture_ids=Queue()
    path='unknown'
    i=-1
    exist_pid='unknown'
    def __init__(self):
        super().__init__() # in python3, super(Class, self).xxx = super().xxx
        self.ui = Ui_MainWindow()
        self.ui.setupUi(self)
        i,self.coookies=pixiv_api.Test_cookies(self.cookies)
        print(i)
        self.setup_control()
    def setup_control(self):
        # TODO
        #self.ui.actionfile.triggered.connect(self.open_folder) 
        #self.path=self.open_folder()
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
        self.ui.getpixiv_author.clicked.connect(lambda:self.get_pixiv_author(self.path))
        self.ui.getpixiv_author_imgID.clicked.connect(lambda:get_pixiv_author_imgID_Thread.run(self))
        self.ui.get_url.clicked.connect(lambda:download_url.main(self))
        self.ui.download_img.clicked.connect(lambda:download_url.main(self))
        #self.ui.getpixiv_author.clicked.connect(lambda:self.get_pixiv_author(self.path))
        #self.ui.label.setText('Happy World!')
        pass
    def open_folder(self):
        folder_path = QFileDialog.getExistingDirectory(self,
                  "Open folder",
                  "./")                 # start path
        print(folder_path)
        path=folder_path
        return path
        #self.ui.show_folder_path.setText(folder_path)
    def set_bar(self, i):
        self.progressBar.setValue(i)
    def get_pixiv_author(self,path):
        all_pixiv_ids = pixiv_api.illusts('21971914'
        ,'p_ab_id=5; p_ab_id_2=5; p_ab_d_id=1125130963; first_visit_datetime_pc=2021-02-23+02%3A32%3A11; yuid_b=OJRESQg; a_type=0; b_type=1; login_ever=yes; privacy_policy_notification=0; c_type=34; PHPSESSID=27915696_60l9nve4HS7Z2Y0bngGXRZQwV4W4DvJ0; privacy_policy_agreement=3; QSI_S_ZN_5hF4My7Ad6VNNAi=v:0:0; tag_view_ranking=0xsDLqCEW6~qWFESUmfEs~LVSDGaCAdn~QKeXYK2oSR~Txs9grkeRc~RTJMXD26Ak~kGYw4gQ11Z~lH5YZxnbfC~Lt-oEicbBr~_EOd7bsGyl~yS_WrRrWFi~G-44hwuIPi~LLyDB5xskQ~Ie2c51_4Sp~HLWLeyYOUF~DADQycFGB0~sqGkVxMuMR~jk9IzfjZ6n~uvBGOtCzqF~MM6RXH_rlN~aKhT3n4RHZ~HY55MqmzzQ~Ti1gvrVQFO~bXMh6mBhl8~RokSaRBUGr~aC55Umcfh1~zsm1ECW5Wb~5f1R8PG9ra~xa5-CDAPro~G_f4j5NH8i~v3nOtgG77A~0RGtdYkK6L~abNIEh2zTB~Bd2L9ZBE8q~0jyux9PxkH~QaiOjmwQnI~n39RQWfHku~vxqZQOR3t2~hk_QPyZfi8~Tg1PbOMGRv~qXzcci65nj~ZTBAtZUDtQ~1VgdMhBiax~dUhrZMpRPB~tgP8r-gOe_~YTKjYV1RQx~Je_lQPk0GY~m3EJRa33xU~iVTmZJMGJj~rMC0CLW0cf~mHukPa9Swj~GuK7T6aGv6~T6NhuB95ST~CLTDpOEHJL~gpglyfLkWs~NGpDowiVmM~MnGbHeuS94~mZurA-1CO-~Am8pyjYCcZ~Riqeg_qBGT~jfnUZgnpFl~BtXd1-LPRH~ujS7cIBGO-~zZZn32I7eS~CrFcrMFJzz~ZN5DR5ie1W~AZ1ov2QNRs~N7rBHi7ijr~QzKFCsGzn-~PBxKNk7VAD~zyKU3Q5L4C~vAwbTkrP0I~P5-w_IbJrm~Ltbk6w58aR~l2rugVKl6u~ajFGI2BXvo~R0DtApn-IB~W4_X_Af3yY~OUF2gvwPef~D4hLr_YmAD~QIa7PLv7ZL~EQ_o6ZyXFg~lf-Uj4GKzU~2FO_ideA5k~18j5-cWRq2~FPCeANM2Bm~TWrozby2UO~9Gbahmahac~2QTW_H5tVX~bplY14maDo~jjVAJCBCtW~B2kc8vAuXw~m3sqCXWo7m~k39B1CkQWC~muA8Dd9eL4~I-ST5EF_lI~wbvCWCYbkM~mVhi1hBMit~Hry6GxyqEm~i8u6Dgt7ao; __cf_bm=cqoyzD4i.qO0s1sUnjhOf9p5ytamrWA2qApQNhhiIKE-1656319872-0-AaXwpJas6wECDAH0caPNgFN5+Y5wjvrFlFzdxBuyzQz6oQGTN8qILCJhy4DeWPqBE9H8Msy1ymtWXbBqLJ6dRm160hdvQQHr56qP0p3ZdhTI'
        ,'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/102.0.5005.124 Safari/537.36 Edg/102.0.1245.41')
        f = open((path+"/following.txt"), "w+")
        texts = np.unique(all_pixiv_ids).tolist()
        for text in texts:
            f.write(str(text)+'\n')
        f.close()
    
    def in_the_pid(id):
        if 'p' in id:
            pid=id.split('p')[0]
        else:
            pid=id
        if(len(pid)==6):
            if id in globals()['exist_pid'+str(pid[0])+"_"+str(6)]:
                return 0
            else:
                return 1
        elif(len(pid)==7):
            if id in globals()['exist_pid'+str(pid[0])+"_"+str(7)]:
                return 0
            else:
                return 1
        elif(len(pid)==8):
            if id in globals()['exist_pid'+str(pid[0])+"_"+str(8)]:
                return 0
            else:
                return 1
        elif(len(pid)==9):
            if id in globals()['exist_pid'+str(pid[0])+"_"+str(9)]:
                return 0
            else:
                return 1
    def pixiv_pid(self,exist_pid):
        for i in range(1,10):    
            for j in range(6,10):
                globals()['exist_pid'+str(i)+"_"+str(j)]=[]   
        for i in trange (0,len(exist_pid)):
            if 'p' in exist_pid[i]:
                pid=exist_pid[i].split('p')[0]
            else:
                pid=exist_pid[i]
            if(len(pid)==6):
                globals()['exist_pid'+str(exist_pid[i][0])+"_"+str(6)].append(exist_pid[i])
            elif(len(pid)==7):
                globals()['exist_pid'+str(exist_pid[i][0])+"_"+str(7)].append(exist_pid[i])
            elif(len(pid)==8):
                globals()['exist_pid'+str(exist_pid[i][0])+"_"+str(8)].append(exist_pid[i])
            else:
                globals()['exist_pid'+str(exist_pid[i][0])+"_"+str(9)].append(exist_pid[i])
        for i in range(1,10):    
            for j in range(6,10):
                globals()['exist_pid'+str(i)+"_"+str(j)]=str(globals()['exist_pid'+str(i)+"_"+str(j)])   
    def get_pixiv_author_imgID(self,path):
        with open((path+r"/following.txt")) as file:     #讀取寫入的文檔
            lines = [line.rstrip() for line in file]
        Author_list = np.array_split(lines,10)   #將獲取的文檔分成100等分
        for i in range(0,len(Author_list)):    
            locals()['picture_ids'+str(i)]=Queue()   
        threads=[]
        for i in range(0,len(Author_list)):
        #print(len(Author_list))
            threads.append(threading.Thread(target = pixiv_api.no_use_seleium_get_pid, 
            args = (Author_list[i],cookies[random.randint(0,len(cookies)-1)],Agent,locals()['picture_ids'+str(i)],self.path,i,str(self.exist_pid))))
            threads[i].start()
            while (threading.active_count())>5:
                    time.sleep(0.01)
        for i in range(8,len(Author_list)):
            threads[i].join()    
            f = open((path+r"/picture_ids"+str(i)+".txt"), "w+")
            texts=locals()['picture_ids'+str(i)].get()
            for text in texts:
                f.write(str(text)+'\n')
            f.close()