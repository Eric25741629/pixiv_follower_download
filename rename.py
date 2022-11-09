import os
import numpy as np
import json
import shutil,time
import threading
import pixiv_api
import requests,json,time
import datetime,os
import zipfile
import loguru
import imageio
from tqdm import tqdm, trange
import concurrent.futures
from functools import partial
import tqdm as tqdm
from pathlib import Path
import numpy as np
import bs4
import tqdm as tqdm
from PIL import Image
from datetime import datetime
from os.path import getmtime
def get_filelist(path):
    Filelist = []
    homes=[]
    filenames=[]
    for home, dirs, files in os.walk(path):
        for filename in files:
            # 文件名列表，包含完整路徑
            homes.append(home)
            filenames.append(filename)
            # # 文件名列表，只包含文件名
            # Filelist.append( filename)
    return homes,filenames

def splitID(Filelist):
    i=0
    exist_pid=[]
    for file in Filelist :
        #print(Filelist[i])
        i+=1
        if (('jpg' not in file ) and ('png' not in file) and ('gif' not in file)):
            print(file+'jpg not in file')
            continue
        #if('PID'not in file and 'illust' not in file):
        #    print(file+'PID not in file')
        #    continue
        try:
            id=file.split('PID=')[1].split('_')[0]
            #print(id)
            if len(id)<12 and len(id)>6:
                #print(id)
                exist_pid.append(id)     
        except:
            try:
                id=file.split('PID')[1].split(' ')[0]
                if len(id)<12 and len(id)>6:
                    #print(id)
                    exist_pid.append(id)
            except:
                try:                    
                    id=file.split('_')[1]
                    #print(id)
                    if len(id)<12 and len(id)>6:
                        #print(id)
                        exist_pid.append(id)
                except:
                    try:      #illust_44773280_20220413_040534.jpg              
                        id=file.split('_')[1]
                        if len(id)<12 and len(id)>6:
                            #print(id)
                            exist_pid.append(id)
                    except:
                        print(file)
    #exist_pid=np.unique(exist_pid).tolist()
    print(len(exist_pid))
    return exist_pid 
def rename(path):#重新命名
    homes,filenames=get_filelist(path)
    pid=splitID(filenames)
    
    mydict = {}
    for i in range(0,len(pid)):
        if(filenames[i].rsplit(".",1)[1]!='jpg' and filenames[i].rsplit(".",1)[1]!='png' and filenames[i].rsplit(".",1)[1]!='gif'):
            continue
        mydict[pid[i]] = filenames[i]
        #mydict.append({pid[i]:homes[i]+filenames[i]})
    file = open(homes[0]+'fileName.json', "w")
    json.dump(mydict, file)
    file.close()
    for i in range(0,len(pid)):
        #print(homes[i]+filenames[i], homes[i]+pid[i]+"."+(filenames[i].rsplit(".",1)[1]))
        if(filenames[i].rsplit(".",1)[1]!='jpg' and filenames[i].rsplit(".",1)[1]!='png' and filenames[i].rsplit(".",1)[1]!='gif'):
            continue
        #print(homes[i]+filenames[i], homes[i]+pid[i]+"."+(filenames[i].rsplit(".",1)[1]))
        #time.sleep(0.1)
        shutil.move(homes[i]+filenames[i], homes[i]+pid[i]+"."+(filenames[i].rsplit(".",1)[1]))
def recover(path):#將命名還原
    homes,filenames=get_filelist(path)
    pid=splitID(filenames)    
    with open(path+'fileName.json') as f:
        data = json.load(f)
    for i in range(0,len(homes)):
        if(filenames[i].rsplit(".",1)[1]!='jpg' and filenames[i].rsplit(".",1)[1]!='png' and filenames[i].rsplit(".",1)[1]!='gif'):
            continue
        try:
            shutil.move(homes[i]+'/'+filenames[i],homes[i]+'/'+data[filenames[i].rsplit(".",1)[0]])
        except Exception as err:
            print(err)
            pass
def net_rename(info):
    try:
        pid=info[1].rsplit('/',1)[1]
        #tags,bookmark,n,n=pixiv_api.Pixiv_info(info[1])
        thetag=info[0]+'_PID'+pid+" "

        '''for i in range(0,len(tags)):
            if(i==(len(tags)-1)):
                thetag+="#"+tags[i]
            else:    
                thetag+="#"+tags[i]+" "
            if len(thetag)>230:
                break'''
        thetag=thetag+'.'+info[2].rsplit(".",1)[1]
        shutil.move(info[3]+'/'+info[2],info[3]+'/'+thetag)
        return 0
    except Exception as err:
        print(err)
    #print(info[3],thetag)
def net_recover(path):
    homes,filenames=get_filelist(path)
    #pid=splitID(filenames)   
    #print(filenames)
    url='https://www.pixiv.net/artworks/'+filenames[0].rsplit("p",2)[0]
    urls=[]
    for i in range(0,len(filenames)):
        exif_data =datetime.fromtimestamp(getmtime(homes[i]+filenames[i])).strftime('%Y%m%d_%H%M%S')
        urls.append([exif_data,'https://www.pixiv.net/artworks/'+filenames[i].rsplit("p",2)[0],filenames[i],homes[i]])
    
    #time.strftime('%Y%m%d_%H%M%S', os.path.getmtime)
    with concurrent.futures.ThreadPoolExecutor(max_workers=16) as executor:  
        results = list(tqdm.tqdm(executor.map(net_rename, urls), total=len(urls))) 
    #print(urls[0])

'''mydict = {}
for i in range(0,len(pid)):
    mydict[pid[i]] = homes[i]+filenames[i]
    #mydict.append({pid[i]:homes[i]+filenames[i]})
file = open(homes[0]+'fileName.json', "w")
json.dump(mydict, file)
file.close()
for i in range(0,len(pid)):

    shutil.move(homes[i]+filenames[i], homes[i]+pid[i]+"."+(filenames[i].rsplit(".",1)[1]))'''

#i,x=(get_filelist(r'E:\test/'))
#pid=splitID(x)

#print(len(x))
#print(splitID(['20220420_185009_PID69631583p0 #R-18 #アズールレーン #ピュリファイアー #スパッツ #セイレーン #ピュリファイアー.png']))
#rename(r'E:\png/')
#recover(r'E:\png/')

#net_recover(r'E:\test_re/')