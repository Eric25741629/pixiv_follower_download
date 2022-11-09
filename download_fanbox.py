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
def user_(id):
    times=1
    urls=[]
    while(times):
        if(times==1):
            url='https://kemono.party/fanbox/user/'+id
        else:
            url='https://kemono.party/fanbox/user/'+id+'?o='+str(25*times)
        res = requests.get(url)
        res=bs4.BeautifulSoup(res.text, 'lxml')
        #print(res.text)
        
        count=0
        for a in res.find_all('a', href=True):
            if(('/'+str(id)+'/post')in  a['href']):
                #print("Found the URL:", a['href'])
                urls.append('https://kemono.party/'+a['href'])
                count+=1
        
        if(count==0 and times==1):
            return 'None'
        times+=1
        if(count==0):
            urls=set(urls)
            return urls
    '''for i in urls :
        print(i) 
    url='https://kemono.party//fanbox/user/21454965/post/1998201'  
    res = requests.get(url)

    res=bs4.BeautifulSoup(res.text, 'lxml')
    imgs=[]
    urls=(res.find_all('a',class_="fileThumb"))
    for url in urls:
        imgs.append(url['href'])
        print(url['href'])'''
si=(user_('22553630'))
print(si)
for i in range(0,len(si)):
    print(si[i])
#print(i)