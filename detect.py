import os,threading
import shutil
from PIL import Image
def is_valid_jpg(jpg_file):
    with open(jpg_file, 'rb') as f:
        f.seek(-2, 2)
        buf = f.read()
        
        return buf.endswith(b'\xff\xd9')


def is_valid_png(png_file):
    with open(png_file, 'rb') as f:
        f.seek(-3, 2)
        buf = f.read()
        if buf == b'\x60\x82\x00':
            return True
        elif buf[1:] == b'\x60\x82':
            return True
        else:
            return False


def is_valid_pic(pic_file):
    if pic_file.endswith('jpg'):
        return is_valid_jpg(pic_file)

    elif pic_file.endswith('png'):
        return is_valid_png(pic_file)
    else:
        return False

def trymain(file,path):
    #print(file)
    error=[]
    pic_file = os.path.join(path, file)
    if not is_valid_pic(pic_file):
        try:
            img = Image.open(pic_file)
            img.load()
        except Exception as e:
            #print(pic_file)
            error.append(file)
            #shutil.copy(pic_file, '/home/king/Desktop/')
            
    return error
import os
import numpy as np
def get_filelist(path):
    Filelist = []
    for home, dirs, files in os.walk(path):
        for filename in files:
            # 文件名列表，包含完整路徑
            Filelist.append(os.path.join(home, filename))
            # # 文件名列表，只包含文件名
            # Filelist.append( filename)
    return Filelist
def splitID(Filelist):
    exist_pid=[]
    for file in  Filelist :
        if (('jpg' not in file ) and ('png' not in file) and ('gif'not in file)):
            #print(file)
            continue
        try:
            id=file.split('PID=')[1].split('_')[0]
            #print(id)
            if len(id)<10 and len(id)>6:
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
                    if len(id)<12 and len(id)>6:
                        #print(id)
                        exist_pid.append(id)
                except:
                    pass
    exist_pid=np.unique(exist_pid).tolist()
    #exist_pid=str(exist_pid)
    return exist_pid 

def loading(path,read_path):
    Filelist = get_filelist(read_path)+get_filelist(path)
    exist_pid=splitID(Filelist)               
    f = open((path+"existPID.txt"), "a+")
    texts=exist_pid
    for text in texts:
        print(text)
        f.write(str(text)+'\n')
    f.close()


if __name__ =="__main__":
    path =r"D:\圖片/暫存區"
    downloadpath=r'D:\圖片\下載/'
    Filelist = get_filelist(path)
    readpid=splitID(Filelist)
    os.mkdir(path+'/找不到/')
    File=get_filelist(downloadpath)
    download_pid=splitID(File)
    delete=[]
    delpath=[]
    for i in range(len(download_pid)):
        if download_pid[i].split('p')[0] in readpid :
            delete.append(download_pid[i])
    for i in range(0,len(File)):
        for j in range(0,len(delete)):
            if delete[j] in File[i]:
                delpath.append(File[i])
                
                shutil.move((File[i]), path+'/找不到/')