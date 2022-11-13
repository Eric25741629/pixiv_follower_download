import json
import os
import random
import shutil
import threading
import time
from logging import exception
from random import random

import bs4
import requests
import urllib3
from bs4 import BeautifulSoup

import tag_edit

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)                                                                                                                                                                                                                                                                                                             
# 子執行緒的工作函數
import re
from queue import Queue
from time import sleep
from urllib import request

from requests import exceptions
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from tqdm import tqdm, trange

import download_img

option = webdriver.ChromeOptions()
from pathlib import Path

# 防止打印一些无用的日志
#option.add_experimental_option("excludeSwitches", ['enable-automation', 'enable-logging'])
#options = Options()
option.add_experimental_option("debuggerAddress", "127.0.0.1:9527")
#https://www.pixiv.net/ajax/user/490219/profile/illustswork_category=illustManga&is_first_page=0&lang=zh_tw
def logging(address,password):
    url = 'https://pixiv.net/'
    driver = webdriver.Chrome(options=option)
    driver.get(url)
    driver.find_element(By.XPATH,'//*[@id="wrapper"]/div[3]/div[2]/a[2]').click()
    driver.find_element(By.XPATH,"//input[@autocomplete = 'username']").send_keys(address)
    passwd=driver.find_element(By.XPATH,"//input[@autocomplete = 'current-password']")
    passwd.send_keys(password)
    passwd.send_keys(Keys.RETURN)

#about_cookies
def auto_get_cookie(address,password):
    option = webdriver.ChromeOptions()
    option.add_experimental_option("excludeSwitches", ['enable-automation', 'enable-logging'])
    #option.add_argument("--headless")
    option.add_argument("--disable-backgrounding-occluded-windows")
    driver = webdriver.Chrome(options=option)
    url = 'https://pixiv.net/'
    driver.get(url)
    driver.find_element(By.XPATH,'//*[@id="wrapper"]/div[3]/div[2]/a[2]').click()
    driver.find_element(By.XPATH,"//input[@autocomplete = 'username']").send_keys(address)
    passwd=driver.find_element(By.XPATH,"//input[@autocomplete = 'current-password']")
    passwd.send_keys(password)
    passwd.send_keys(Keys.RETURN)
    sleep(2)
    url='https://www.pixiv.net/artworks/96509143'
    driver.get(url)
    sleep(2)
    def get_cookies():
        cookies = ""
        selenium_cookies = driver.get_cookies()
        for cookie in selenium_cookies:
            cookies+=str(cookie['name'])
            cookies+="="
            cookies+=str(cookie['value'])
            cookies+=";"
        return cookies
    agent=driver.execute_script("return navigator.userAgent")
    cookies=get_cookies()
    return str(cookies),agent

def Test_cookies(lists,agent):
    cookies=[]
    i=0
    for list1 in lists:
        try:
            print(list1,agent)
            pid='96509143'
            headers = {
                'User-Agent': agent,
                'Cookie':list1
                ,'Referer':('http://www.pixiv.net/'+str(pid)),        
                    } 
            url='https://www.pixiv.net/ajax/illust/'+pid+'/pages?lang=zh_tw'            

            htmlfile = requests.get(url,headers=headers)
            #print(htmlfile.text)
            htmlfile.raise_for_status() 
            #objSoup = bs4.BeautifulSoup(htmlfile.text, 'lxml')
            #print(objSoup.text)
            i=i+1
            cookies.append(list1) 
        except Exception as err:
            print(err)
            pass
    return i,cookies

def get_author_picture_ids(illust_ids,path,num,q,exist_pid):
    download_Pid=[]
    temp = 0
    total = len(illust_ids)     
    for illust_id in illust_ids:
        try:
            temp += 1
            print('\r' + '[線程%s]:[%s%s]%.2f%%' % (num,'█' * int(temp*20/total), ' ' * (20-int(temp*20/total)),float(temp/total*100)), end='')
            driver = webdriver.Chrome(options=option)
            time.sleep(5)
            
            url = ('https://pixiv.net/ajax/user/' + illust_id + '/profile/all?lang=zh')				#畫師id 輸入後可得到畫師所有的作品
            driver.switch_to.window(driver.window_handles[num])
            driver.get(url)
            #time.sleep(1)
            soup = BeautifulSoup(driver.page_source, 'html.parser')
            res=soup.find('pre')
            res=str(res)
            res=res.replace('<pre style="word-wrap: break-word; white-space: pre-wrap;">','')	#清除後才能夠轉為json
            res=res.replace('</pre>','')														#清除後才能夠轉為json
            #print(res)
            res=res.encode('UTF-8')
            resdict = json.loads(res)['body']['illusts']		  								# 將json轉化為python的字典后提取元素
            Pids=[key for key in resdict]                        #將元素放入陣列裡
            for Pid in Pids:
                if Pid in exist_pid:
                    break
                else :
                    download_Pid.append(Pid)
        except Exception as err:
            print(Pid+'獲取失敗',err)
            f = open((path+"get_download_author_err"+str(num)+".txt"), "a")
            f.write(id+'\n')
            f.close()
        time.sleep(random())
        #print(num)
    q.put(download_Pid)
def get_follow_illust(times,headers,queue,state):
    url = ('https://www.pixiv.net/ajax/user/27915696/following?offset='+str(times)+'&limit=100&rest='+state+'&tag=&lang=zh_tw')
    
    res = requests.get(url, headers=headers)
    resdicts = res.json()['body']['users']
    for resdict in resdicts:
            #print(resdict.get('userId'))
            queue.put(int(resdict.get('userId')))
def illusts(id,cookie,Agent):				#輸入你的id得到你所有關注的P站畫師
    headers = {
        'User-Agent': Agent,
        'Cookie':cookie
        ,'referer': 'https://www.pixiv.net/users/'+id+'/following',        
    }
    times=0
    pixiv_author_id=[]
    limit=1
    url = ('https://www.pixiv.net/ajax/user/27915696/following?offset='+str(times)+'&limit=1&rest=show&tag=&lang=zh_tw') # 访问存有画师所有作品
    print(url)
    res = requests.get(url, headers=headers)
    show_total_num=(res.json()['body']['total'])
    url = ('https://www.pixiv.net/ajax/user/27915696/following?offset='+str(times)+'&limit=1&rest=hide&tag=&lang=zh_tw')
    res = requests.get(url, headers=headers)
    hide_total_num=(res.json()['body']['total'])
    print(show_total_num,hide_total_num)
    threads=[]
    queue=Queue() 
    for i in range(0,show_total_num+100,100):
        threads.append(threading.Thread(target =get_follow_illust, args =(i,headers,queue,'show') ))
        #print(i)
    for i in range(0,len(threads)):
        threads[i].start()
        while(threading.activeCount()>=7):
            sleep(0.01)
    for i in range(0,len(threads)):
        threads[i].join()
    
    threads.clear()
    for i in range(0,hide_total_num+100,100):
        threads.append(threading.Thread(target =get_follow_illust, args =(i,headers,queue,'hide') ))
        #print(i)
    for i in range(0,len(threads)):
        threads[i].start()
        while(threading.activeCount()>=7):
            sleep(0.01)
    for i in range(0,len(threads)):
        threads[i].join()
    while not(queue.empty()):
        pixiv_author_id.append(queue.get())
    return (pixiv_author_id)
def thread_no_use_seleium_get_pid(cookie,Agent,path,num,author_pids):
    #author_pids=str(author_pids)
    pid=[]
    try:
        url='https://www.pixiv.net/ajax/user/'+author_pids+'/profile/all?lang=zh%27'
        headers = {
        'User-Agent': Agent,
        'Cookie':cookie
        ,'referer': 'https://www.pixiv.net/users/'+author_pids,        
        }
        res = requests.get(url, headers=headers)
        resdicts = res.json()['body']['illusts']
        #print(resdicts)
        for key in resdicts:
            pid.append(key)
    except Exception as err:
        print(err)
        f = open((path+"authorPids_err"+str(num)+".txt"), "a+")
        f.write(author_pids+'\n')
        f.close() 
    return pid

def random_Agent():
    USER_AGENTS=[
                "Mozilla/5.0 (Linux; U; Android 4.0.4; en-gb; GT-I9300 Build/IMM76D) AppleWebKit/534.30 (KHTML, like Gecko) Version/4.0 Mobile Safari/534.30",
                "Mozilla/5.0 (Linux; U; Android 8.0; en-gb; GT-P1000 Build/FROYO) AppleWebKit/533.1 (KHTML, like Gecko) Version/4.0 Mobile Safari/533.1",
                "Mozilla/5.0 (Windows NT 6.2; WOW64; rv:21.0) Gecko/20100101 Firefox/21.0",
                "Mozilla/5.0 (Android; Mobile; rv:14.0) Gecko/14.0 Firefox/14.0",
                "Mozilla/5.0 (Windows NT 6.2; WOW64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/27.0.1453.94 Safari/537.36",
                "Mozilla/5.0 (Linux; Android 4.0.4; Galaxy Nexus Build/IMM76B) AppleWebKit/535.19 (KHTML, like Gecko) Chrome/18.0.1025.133 Mobile Safari/535.19",            
                "Mozilla/4.0 (compatible; MSIE 6.0; Windows NT 5.1; SV1; AcooBrowser; .NET CLR 1.1.4322; .NET CLR 2.0.50727)",
                "Mozilla/4.0 (compatible; MSIE 7.0; Windows NT 6.0; Acoo Browser; SLCC1; .NET CLR 2.0.50727; Media Center PC 5.0; .NET CLR 3.0.04506)",
                "Mozilla/4.0 (compatible; MSIE 7.0; AOL 9.5; AOLBuild 4337.35; Windows NT 5.1; .NET CLR 1.1.4322; .NET CLR 2.0.50727)",
                "Mozilla/5.0 (Windows; U; MSIE 9.0; Windows NT 9.0; en-US)",
                "Mozilla/5.0 (compatible; MSIE 9.0; Windows NT 6.1; Win64; x64; Trident/5.0; .NET CLR 3.5.30729; .NET CLR 3.0.30729; .NET CLR 2.0.50727; Media Center PC 6.0)",
                "Mozilla/5.0 (compatible; MSIE 8.0; Windows NT 6.0; Trident/4.0; WOW64; Trident/4.0; SLCC2; .NET CLR 2.0.50727; .NET CLR 3.5.30729; .NET CLR 3.0.30729; .NET CLR 1.0.3705; .NET CLR 1.1.4322)",
                "Mozilla/4.0 (compatible; MSIE 7.0b; Windows NT 5.2; .NET CLR 1.1.4322; .NET CLR 2.0.50727; InfoPath.2; .NET CLR 3.0.04506.30)",
                "Mozilla/5.0 (Windows; U; Windows NT 5.1; zh-CN) AppleWebKit/523.15 (KHTML, like Gecko, Safari/419.3) Arora/0.3 (Change: 287 c9dfb30)",
                "Mozilla/5.0 (X11; U; Linux; en-US) AppleWebKit/527+ (KHTML, like Gecko, Safari/419.3) Arora/0.6",
                "Mozilla/5.0 (Windows; U; Windows NT 5.1; en-US; rv:1.8.1.2pre) Gecko/20070215 K-Ninja/2.1.1",
                "Mozilla/5.0 (Windows; U; Windows NT 5.1; zh-CN; rv:1.9) Gecko/20080705 Firefox/3.0 Kapiko/3.0",
                "Mozilla/5.0 (X11; Linux i686; U;) Gecko/20070322 Kazehakase/0.4.5",
                "Mozilla/5.0 (X11; U; Linux i686; en-US; rv:1.9.0.8) Gecko Fedora/1.9.0.8-1.fc10 Kazehakase/0.5.6",
                "Mozilla/5.0 (Windows NT 6.1; WOW64) AppleWebKit/535.11 (KHTML, like Gecko) Chrome/17.0.963.56 Safari/535.11",
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_7_3) AppleWebKit/535.20 (KHTML, like Gecko) Chrome/19.0.1036.7 Safari/535.20",
                "Opera/9.80 (Macintosh; Intel Mac OS X 10.6.8; U; fr) Presto/2.9.168 Version/11.52",
                ] 
    return USER_AGENTS[random.randint(0,len(USER_AGENTS)-1)]
def Pixiv_Tag(url):                                                 #回傳標籤
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/98.0.4758.80 Safari/537.36 Edg/98.0.1108.50',
        'Cookie':'p_ab_id=5; p_ab_id_2=5; p_ab_d_id=1125130963; first_visit_datetime_pc=2021-02-23+02%3A32%3A11; yuid_b=OJRESQg; a_type=0; b_type=1; login_ever=yes; privacy_policy_notification=0; c_type=34; PHPSESSID=27915696_60l9nve4HS7Z2Y0bngGXRZQwV4W4DvJ0; privacy_policy_agreement=3; tag_view_ranking=0xsDLqCEW6~lH5YZxnbfC~Lt-oEicbBr~kGYw4gQ11Z~Ie2c51_4Sp~RTJMXD26Ak~eVxus64GZU~HLWLeyYOUF~qiO14cZMBI~RVRPe90CVr~oCR2Pbz1ly~qWFESUmfEs~kP7msdIeEU~OT4SuGenFI~FySY6ZVB78~tgP8r-gOe_~5RvyKm3yea~kqu7T68WD3~v3nOtgG77A~bopfpc8En6~mCYugqjYJX~JXmGXDx4tL~qcYo_5oqVP~jfnUZgnpFl~J_YijUi2Xg~F8u6sord4r~3gc3uGrU1V~MM6RXH_rlN~TcgCqYbydo~Hry6GxyqEm~_giyO1uU9O~zyKU3Q5L4C~dUhrZMpRPB~aKhT3n4RHZ~KN7uxuR89w~BU9SQkS-zU~5oPIfUbtd6~y8GNntYHsi~EGefOqA6KB~05tD6f663z~Hjx7wJwsUT~h9r9YX0n2U~R-EFi7fMtD~w8ffkPoJ_S~jEoxuA2PIS~TOd0tpUry5~hRUnVPuHhQ~JtHr1OyMVc~Bd2L9ZBE8q~C9_ZtBtMWU~_EOd7bsGyl~TaUYlgH_jM~LVSDGaCAdn~iFcW6hPGPU~d-u0duThlB~MsF32uM-vh~GNcgbuT3T-~XDEWeW9f9i~_bee-JX46i~q303ip6Ui5~tlXeaI4KBb~LMpjieSVIv~ZXFMxANDG_~nRp2ZLPLbj~uKsA-LcJvn~qBVGbZbpq5~G-44hwuIPi~xa5-CDAPro~0j_zFcQpTM~YX72Y3LbXY~Txs9grkeRc~4ZEPYJhfGu~zASPXsXKdt~DADQycFGB0~HBlflqJjBZ~Gcv5xjGZY3~5Rf_nE4tAW~9wN-K8_crj~D4hLr_YmAD~bbZFcn8nQh~T40wdiG5yy~wlJLIPQpdd~5v2pI9_gGE~X4sPgKUWBs~hebOixBpSV~qIDsnltE2o~cxmbAHgoTk~mv-jOivdpn~f8pnWEIf9Z~ngUJxbZ4-R~ay54Q_G6oX~ziiAzr_h04~JWOyXSsjO2~HY55MqmzzQ~EUwzYuPRbU~KOnmT1ndWG~QKeXYK2oSR~cbmDKjZf9z~4qWlGrZbSE~iVTmZJMGJj; QSI_S_ZN_5hF4My7Ad6VNNAi=r:10:69; __cf_bm=k.dJJM7WQ45APSabaxdmzFCWQnBtPJzcg00Hbj4GxqQ-1644941038-0-ASQvUxnLsV4Q6uD6v4xA5kiW4NqFrLJ6ldhirpyqbEkQhANLCj2WurCFAnUYKvPZ+OmOXbJkpdoEJvJ7Rjf8HRIMzEgsQeWEO2NSD0jfhK5a'
        ,'referer': 'https://www.pixiv.net/users/27915696/following',        
    }
    id=url.rsplit('/',1)[1]
    res = requests.get(url, headers=headers)
    obj = str(bs4.BeautifulSoup(res.text, 'lxml').find_all('meta')[25])
    obj=obj.replace('<meta content=\'','')
    obj=obj.replace('id="meta-preload-data" name="preload-data"/>','') 
    o=obj.rsplit('\'',1)[0] 
    o=o.encode('UTF-8')
    resdicts = str(json.loads(o)['illust'][str(id)]['tags']['tags'])
    #print(resdicts)
    #resdicts = str(json.loads(o)['illust'][str(id)]['tags']['tags'])
    return resdicts
class tagErr(Exception):
    pass 
class Err(Exception):
    pass
def get_download_url(path,cookie,Agent,num,pid):    #回傳下載連結
    download_url=[]
    for x in range(0,2):
        try:
            #print('檢測tag')
            url='https://www.pixiv.net/artworks/'+pid
            #print(url)
            j=1
            while(j):
                tag,like,pagecount,img_url=Pixiv_info(url,Agent=Agent)
                j=j+1
                if tag!=[] or like!=404:
                    break
                if j==3:
                    raise Err()
                if tag==404 and like==404:
                    break
            if tag ==404 and like==404:
                break
            tag=str(tag) 
            if ('R-18G'in tag) and (('死姦'in tag) or ('脫腸'in tag) or ('斬首' in tag) or ('屍姦'in tag) or ('necrophilia'in tag) or('割脖'in tag) or ('砍頭'in tag)or('食糞'in tag)or('眼孔姦'in tag)):
                #print('跳過'+url+tag)        
                break
                raise tagErr()
            if ( ('gay'in tag)or ('原創BL'in tag)):
                #print('跳過'+url+tag)   
                return pid
                raise tagErr()
            #print('檢測愛心')
            if like <300:
                #print('愛心太少了'+url+' '+str(like))
                return pid
                raise Exception()
            img_url=img_url.rsplit(".",1)
            for count in range(0,pagecount):
                download_url.append(img_url[0]+str(count)+"."+img_url[1])
            time.sleep(random()/5)
            return (download_url)   
            '''try:
                url='https://www.pixiv.net/ajax/illust/'+pid+'/pages?lang=zh_tw'            
                headers = { 'User-Agent':Agent,
                        'Cookie':cookie
                        ,'Referer':('http://www.pixiv.net/'+str(pid))}
                htmlfile = requests.get(url,headers=headers,timeout=20)
                if htmlfile.status_code == 404:
                    break
                get_url=htmlfile.json()['body']
                try:
                    for urls in get_url:
                        url=urls['urls']['original']
                        download_url.append(url)
                except Exception as err:
                    print(err)
                break       
            except Exception as err:
                if x==9:
                    print(pid+'獲取失敗',err) 
                    myfile = Path(path+"network_err"+str(num%20)+".txt")
                    myfile.touch(exist_ok=True)
                    f = open((path+"network_err"+str(num%20)+".txt"), "r")           
                    exist=f.read()
                    f.close()
                    if str(pid) not in exist:
                        f = open((path+"network_err"+str(num%20)+".txt"), "a+")  
                        f.write(str(pid)+'\n')
                        f.close() '''
        except Exception as err:
            print(pid+'獲取失敗',err) 
            if x==9:
                    print(pid+'獲取失敗',err) 
                    myfile = Path(path+"network_err"+str(num%20)+".txt")
                    myfile.touch(exist_ok=True)
                    f = open((path+"network_err"+str(num%20)+".txt"), "r")           
                    exist=f.read()
                    f.close()
                    if str(pid) not in exist:
                        f = open((path+"network_err"+str(num%20)+".txt"), "a+")  
                        f.write(str(pid)+'\n')
                        f.close() 
        time.sleep(random())
    #print(download_url)
    return(download_url)
def Pixiv_info(url,
    Agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/98.0.4758.80 Safari/537.36 Edg/98.0.1108.50'):                                                 #回傳標籤
    try:
        headers = {
            'User-Agent': Agent,
            'referer': 'https://www.pixiv.net/',        
        }
        id=url.rsplit('/',1)[1]
        res = requests.get(url, headers=headers)
        if res.status_code == 404:
            return 404,404,404,404
        #print(res.json())
        obj = str(bs4.BeautifulSoup(res.text, 'lxml').select_one('meta[name="preload-data"]'))
        obj=obj.replace('<meta content=\'','')
        obj=obj.replace('id="meta-preload-data" name="preload-data"/>','') 
        o=obj.rsplit('\'',1)[0] 
        #print(o)
        o=o.encode('UTF-8')
        o=json.loads(o)['illust'][id]
        bookmarkCount = o['bookmarkCount']
        pageCount=o['userIllusts'][id]["pageCount"]
        resdicts =o['userIllusts'][id]["tags"]
        resdicts=tag_edit.Tag(resdicts)
        img_url=o['urls']['original'].replace("p0","p",1).replace("ugoira0","ugoira",1)  
        return resdicts,int(bookmarkCount),int(pageCount),img_url
    except Exception as err:
        print(err)
        try:
            obj = str(bs4.BeautifulSoup(res.text, 'lxml').find_all('meta')[26])
            obj=obj.replace('<meta content=\'','')
            
            obj=obj.replace('id="meta-preload-data" name="preload-data"/>','') 
            o=obj.rsplit('\'',1)[0] 
            
            o=o.encode('UTF-8')
            bookmarkCount = str(json.loads(o)['illust'][str(id)]['bookmarkCount'])
            resdicts =json.loads(o)['illust'][str(id)]['tags']['tags']
            resdicts=tag_edit.Tag(resdicts)
            return resdicts,int(bookmarkCount)
        except:
            print('error')
            return [],[]
def pixiv_following_count(id,cookie,Agent):
    url = ("https://www.pixiv.net/ajax/user/extra?lang=zh_tw") # 访问存有画师所有作品
    print(url)
    headers = {
        'User-Agent': Agent,
        'Cookie':cookie
        ,'referer': 'https://www.pixiv.net/users/'+id+'/following',        
    }
    res = requests.get(url,headers=headers)
    return res.json()['body']['following']

    #objSoup = bs4.BeautifulSoup(res.content, 'lxml')
    #print(objSoup)

def no_use_seleium_get_pid(author_pids,cookie,Agent,q,path,num,exist_pid):
    pids=[]
    for i in trange(0,len(author_pids)):
        try:
            url='https://www.pixiv.net/ajax/user/'+author_pids[i]+'/profile/all?lang=zh%27'
            headers = {
            'User-Agent': Agent,
            'Cookie':cookie
            ,'referer': 'https://www.pixiv.net/users/'+author_pids[i],        
            }
            res = requests.get(url, headers=headers)
            resdicts = res.json()['body']['illusts']
            for key in resdicts:
                if key not in exist_pid:
                    q.put(key) 
        except:
            f = open((path+"authorPids_err"+str(num)+".txt"), "a+")
            f.write(author_pids[i]+'\n')
            f.close() 

       
if __name__ == '__main__':
    '''all_pixiv_ids = illusts('21971914'
            ,'first_visit_datetime_pc=2021-10-28+00%3A33%3A54; yuid_b=IgaEcIM; p_ab_id=9; p_ab_id_2=2; p_ab_d_id=1655378609; c_type=34; privacy_policy_notification=0; a_type=0; b_type=1; privacy_policy_agreement=3; login_ever=yes; PHPSESSID=27915696_PjlaOdEHhwZxxwFR6QhmWdmUAguAJ05n; device_token=b0e8ae7f1085345fd97205929a8c801f; QSI_S_ZN_5hF4My7Ad6VNNAi=r:10:16; tag_view_ranking=0xsDLqCEW6~qWFESUmfEs~RTJMXD26Ak~lH5YZxnbfC~Bd2L9ZBE8q~kGYw4gQ11Z~Lt-oEicbBr~HLWLeyYOUF~xa5-CDAPro~Txs9grkeRc~jH0uD88V6F~5oPIfUbtd6~Avyrt8Dl6U~Ie2c51_4Sp~KN7uxuR89w~iFcW6hPGPU~LVSDGaCAdn~aKhT3n4RHZ~HY55MqmzzQ~QKeXYK2oSR~s1DI4r3R9d~-StjcwdYwv~_hSAdpN9rx~Zw76BPYnQY~LLyDB5xskQ~Je_lQPk0GY~At-5ulc3K-~MM6RXH_rlN~PKOnf9fn03~kWRbcAGDa9~_pwIgrV8TB~HBlflqJjBZ~rIovsiOt91~kqu7T68WD3~_EOd7bsGyl~ziiAzr_h04~wKl4cqK7Gl~YXsA4N8tVW~uGQeWvelyQ~EGefOqA6KB~yS_WrRrWFi~y0H0q1mN2T~bbZFcn8nQh~7eQw69bujS~hfCvniImMk~0M0zAeslDb~Ti1gvrVQFO~Hjx7wJwsUT~gpglyfLkWs~cbmDKjZf9z~t_MXrQdcbG~v3nOtgG77A~hRUnVPuHhQ~Cj_Gcw9KR1~txZ9z5ByU7~vdbd7LdFLQ~BtXd1-LPRH~q303ip6Ui5~faHcYIP1U0~jsuXqE_4cM~yPNaP3JSNF~4QveACRzn3~T40wdiG5yy~sqGkVxMuMR~y8GNntYHsi~TWrozby2UO~n39RQWfHku~w04oCbou_K~EWR7JDW6jH~qsesP1OhVb~O_HW-VFJqw~tzIoUMzCb7~q3eUobDMJW~qiO14cZMBI~zJ9HPr_eGC~py0hn8jqar~RokSaRBUGr~KavbyZsaB1~rgxOsa3XtV~phyAxUXrUB~o1uJiiK9Pb~3gc3uGrU1V~m3EJRa33xU~zIv0cf5VVk~JBqkgBEhOH~bhGHO52dlK~_Rh3LLrBkn~Sp679VBWVz~PNmj47oZlB~5ObVqT-Fku~svKogfYWcS~2bq8SNVWly~j7DYHEocqe~VP5Nfk8taA~mxDE3obNef~bkSTvfrPKL~Peat8vFmO1~pYlUxeIoeg~zASPXsXKdt~DADQycFGB0; __cf_bm=amkOyDjW98gUXXLbSGYlOQO2yEr_dO3dsGfCSusktuQ-1647280343-0-AdCW/o0Ks2IM0BaHV5g6N6BeBELcwf82kJgll/tiqKrCBt9+JZIka7Ipba1bqJqV+sjZK6c8unyuAxUFXKv0qmKT3NSzntjWOFBvjLeo0wCY'
            ,'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/99.0.4844.51 Safari/537.36 Edg/99.0.1150.36')
    '''
    cookie=['p_ab_id=5; p_ab_id_2=5; p_ab_d_id=1125130963; first_visit_datetime_pc=2021-02-23+02%3A32%3A11; yuid_b=OJRESQg; a_type=0; b_type=1; login_ever=yes; privacy_policy_notification=0; c_type=34; PHPSESSID=27915696_60l9nve4HS7Z2Y0bngGXRZQwV4W4DvJ0; privacy_policy_agreement=3; QSI_S_ZN_5hF4My7Ad6VNNAi=v:0:0; tag_view_ranking=0xsDLqCEW6~qWFESUmfEs~LVSDGaCAdn~QKeXYK2oSR~Txs9grkeRc~RTJMXD26Ak~kGYw4gQ11Z~lH5YZxnbfC~Lt-oEicbBr~_EOd7bsGyl~yS_WrRrWFi~G-44hwuIPi~LLyDB5xskQ~Ie2c51_4Sp~HLWLeyYOUF~DADQycFGB0~sqGkVxMuMR~jk9IzfjZ6n~uvBGOtCzqF~MM6RXH_rlN~aKhT3n4RHZ~HY55MqmzzQ~Ti1gvrVQFO~bXMh6mBhl8~RokSaRBUGr~aC55Umcfh1~zsm1ECW5Wb~5f1R8PG9ra~xa5-CDAPro~G_f4j5NH8i~v3nOtgG77A~0RGtdYkK6L~abNIEh2zTB~Bd2L9ZBE8q~0jyux9PxkH~QaiOjmwQnI~n39RQWfHku~vxqZQOR3t2~hk_QPyZfi8~Tg1PbOMGRv~qXzcci65nj~ZTBAtZUDtQ~1VgdMhBiax~dUhrZMpRPB~tgP8r-gOe_~YTKjYV1RQx~Je_lQPk0GY~m3EJRa33xU~iVTmZJMGJj~rMC0CLW0cf~mHukPa9Swj~GuK7T6aGv6~T6NhuB95ST~CLTDpOEHJL~gpglyfLkWs~NGpDowiVmM~MnGbHeuS94~mZurA-1CO-~Am8pyjYCcZ~Riqeg_qBGT~jfnUZgnpFl~BtXd1-LPRH~ujS7cIBGO-~zZZn32I7eS~CrFcrMFJzz~ZN5DR5ie1W~AZ1ov2QNRs~N7rBHi7ijr~QzKFCsGzn-~PBxKNk7VAD~zyKU3Q5L4C~vAwbTkrP0I~P5-w_IbJrm~Ltbk6w58aR~l2rugVKl6u~ajFGI2BXvo~R0DtApn-IB~W4_X_Af3yY~OUF2gvwPef~D4hLr_YmAD~QIa7PLv7ZL~EQ_o6ZyXFg~lf-Uj4GKzU~2FO_ideA5k~18j5-cWRq2~FPCeANM2Bm~TWrozby2UO~9Gbahmahac~2QTW_H5tVX~bplY14maDo~jjVAJCBCtW~B2kc8vAuXw~m3sqCXWo7m~k39B1CkQWC~muA8Dd9eL4~I-ST5EF_lI~wbvCWCYbkM~mVhi1hBMit~Hry6GxyqEm~i8u6Dgt7ao; __cf_bm=cqoyzD4i.qO0s1sUnjhOf9p5ytamrWA2qApQNhhiIKE-1656319872-0-AaXwpJas6wECDAH0caPNgFN5+Y5wjvrFlFzdxBuyzQz6oQGTN8qILCJhy4DeWPqBE9H8Msy1ymtWXbBqLJ6dRm160hdvQQHr56qP0p3ZdhTI',
        'first_visit_datetime_pc=2022-02-22+01%3A16%3A54; p_ab_id=4; p_ab_id_2=7; p_ab_d_id=1650471887; __utma=235335808.1075363582.1645460214.1645460214.1645460214.1; __utmc=235335808; __utmz=235335808.1645460214.1.1.utmcsr=(direct)|utmccn=(direct)|utmcmd=(none); __cf_bm=0a88D.DNolnjql11bA2YlSX0AcrLNbOeH4e01.n9Bxo-1645460214-0-AZBMYe8wLu85sKJgxlsiLyTfyRirq9PImai1YQW6aqaElwzgwilYvqTg1yArIG9dhNAWfXUnstyJaUnA7KjsC4hCuS2VpDjkwIxqiWZjNpEfds/gw+Fti9Xi7WNGm60A275E+delw5z8UbPR+KvvWIsu+gzXXX+bNS+iKfwixI8fUjhytmLnoA2qeosMkwT2EA==; _fbp=fb.1.1645460216607.810010051; __utmt=1; yuid_b=ExaEQEI; _ga=GA1.2.1075363582.1645460214; _gid=GA1.2.1775348523.1645460337; _gat=1; PHPSESSID=78672220_Guho8rq4QaKdQmhrizUuq9XBanNhtU5S; device_token=9fed18d4053fdd2dd3c14b6a8b9487f6; privacy_policy_agreement=3; c_type=23; privacy_policy_notification=0; a_type=0; b_type=1; __utmv=235335808.|3=plan=normal=1^5=gender=male=1^6=user_id=78672220=1^11=lang=zh_tw=1; QSI_S_ZN_5hF4My7Ad6VNNAi=r:10:1; tag_view_ranking=qiO14cZMBI~RVRPe90CVr~oCR2Pbz1ly~Lt-oEicbBr~eVxus64GZU~uKsA-LcJvn; __utmb=235335808.4.10.1645460214'
        ]
        #Test_cookies(cookie)
    '''url='https://www.pixiv.net/artworks/96429430'
    tag=Pixiv_Tag(url) ''' 
    '''q=Queue()'''
    #print(Pixiv_info('https://www.pixiv.net/artworks/98019845'))  
    start=time.time() 
    Agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/99.0.4844.74 Safari/537.36 Edg/99.0.1150.55'
    #print(illusts('27915696',cookie[0],'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/102.0.5005.124 Safari/537.36 Edg/102.0.1245.41'))
    #illusts('59115126',cookie[0],'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/102.0.5005.124 Safari/537.36 Edg/102.0.1245.41')
    #pixiv_following_count('27915696',cookie[0],'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/102.0.5005.124 Safari/537.36 Edg/102.0.1245.41')
    path=r'D:/pixiv/'

    print(thread_no_use_seleium_get_pid(cookie[0],Agent,path,'1','59115126'))
    stop=time.time()
    print(stop-start)
    '''i,cookies=Test_cookies(['p_ab_id=5; p_ab_id_2=5; p_ab_d_id=1125130963; first_visit_datetime_pc=2021-02-23+02%3A32%3A11; yuid_b=OJRESQg; a_type=0; b_type=1; login_ever=yes; privacy_policy_notification=0; c_type=34; PHPSESSID=27915696_60l9nve4HS7Z2Y0bngGXRZQwV4W4DvJ0; privacy_policy_agreement=3; tag_view_ranking=0xsDLqCEW6~RTJMXD26Ak~lH5YZxnbfC~kGYw4gQ11Z~Ie2c51_4Sp~Lt-oEicbBr~HLWLeyYOUF~6lAZFEHdIG~tgP8r-gOe_~v3nOtgG77A~q303ip6Ui5~SnoEe7upUJ~VbPCYJXdEP~4QveACRzn3~hRUnVPuHhQ~fkptjjF31f~liM64qjhwQ~LcFnY5KMB3~R8jL-NaEv1~5oPIfUbtd6~HY55MqmzzQ~EZQqoW9r8g~rIovsiOt91~xZMlo13i1B~jXyAkKG_r6~fwLb-f-Cyw~1KCppYVBZi~xS1IEbYkDC~D2Z9yqNh4C~q3eUobDMJW~LLyDB5xskQ~qWFESUmfEs~PwDMGzD6xn~UCT8y2nU0w~Txs9grkeRc~9aCtrIRNdF~8zydy1kf22~zvcWye7PPU~QkN-eEgwBf~BRoQO5EgS6~DlBi_h7Pbj~Syl9NQhE_u~H0KKRBjKCB~oLKtJ-caOt~jH0uD88V6F~Yw6zHqltKg~ltbsxp8yio~bX7kls1wXg~LVSDGaCAdn~qkC-JF_MXY~yJr9CrS0uL~JXmGXDx4tL~8qzNKXwVP9~P9jONkE5Ux~aMSPvw-ONW~49mOVaB3jw~77cKnr2WaY~y9_ytVF_KY~lLoGT15boh~CO8L4b7n_7~dUhrZMpRPB~reR7DUAWuG~sHj972WME6~1VgdMhBiax~jFLb4HjoWf~bXMh6mBhl8~3Q3HW-78l_~_EOd7bsGyl~ziiAzr_h04~9Gbahmahac~6vriIwKZAv~u3EAZmzDcl~2R7RYffVfj~SapL8yQw4Y~Ed_W9RQRe_~QaiOjmwQnI~o8a--Qa5of~cHpSJQiKeZ~KexWqtgzW1~wLSj8MDOo8~mLrrjwTHBm~WVrsHleeCL~OUsYoX1-GT~_vCZ2RLsY2~_3oeEue7S7~FrNQVCB8yi~YHRjLHL-7q~4sdiKNzOsj~hMzrji99a1~PrND0ipqBX~Uw_mm-h1Wo~FgYArp6riX~MM6RXH_rlN~EsPictrypp~MA6EUZYaNt~ZEYMFD786k~RcahSSzeRf~ePN3h1AXKX~R97S29V8Qw~YXsA4N8tVW; QSI_S_ZN_5hF4My7Ad6VNNAi=r:10:19; __cf_bm=0m8DlJrBTwZ.UkA95H91jwWjQYvzJ9v53XH1_hDO.tg-1648960602-0-AeDEXETjg7BWesTTPlyV2jRCeYr/S60VaHO7W/8JyG0+ycRcVcHsn2T3uBQjbBzkpXVgf7VMxxf53z8IeftahY/hNQEmRpLmUBzLfObJxdCO']
    )'''
    '''with open((r"R:/picture_ids0.txt")) as file:     #讀取寫入的文檔
            lines = [line.rstrip() for line in file]
    URL=get_download_url(lines,r'D:/pyedge/','p_ab_id=5; p_ab_id_2=5; p_ab_d_id=1125130963; first_visit_datetime_pc=2021-02-23+02%3A32%3A11; yuid_b=OJRESQg; a_type=0; b_type=1; login_ever=yes; privacy_policy_notification=0; c_type=34; PHPSESSID=27915696_60l9nve4HS7Z2Y0bngGXRZQwV4W4DvJ0; privacy_policy_agreement=3; tag_view_ranking=0xsDLqCEW6~RTJMXD26Ak~lH5YZxnbfC~kGYw4gQ11Z~Ie2c51_4Sp~Lt-oEicbBr~HLWLeyYOUF~6lAZFEHdIG~tgP8r-gOe_~v3nOtgG77A~q303ip6Ui5~SnoEe7upUJ~VbPCYJXdEP~4QveACRzn3~hRUnVPuHhQ~fkptjjF31f~liM64qjhwQ~LcFnY5KMB3~R8jL-NaEv1~5oPIfUbtd6~HY55MqmzzQ~EZQqoW9r8g~rIovsiOt91~xZMlo13i1B~jXyAkKG_r6~fwLb-f-Cyw~1KCppYVBZi~xS1IEbYkDC~D2Z9yqNh4C~q3eUobDMJW~LLyDB5xskQ~qWFESUmfEs~PwDMGzD6xn~UCT8y2nU0w~Txs9grkeRc~9aCtrIRNdF~8zydy1kf22~zvcWye7PPU~QkN-eEgwBf~BRoQO5EgS6~DlBi_h7Pbj~Syl9NQhE_u~H0KKRBjKCB~oLKtJ-caOt~jH0uD88V6F~Yw6zHqltKg~ltbsxp8yio~bX7kls1wXg~LVSDGaCAdn~qkC-JF_MXY~yJr9CrS0uL~JXmGXDx4tL~8qzNKXwVP9~P9jONkE5Ux~aMSPvw-ONW~49mOVaB3jw~77cKnr2WaY~y9_ytVF_KY~lLoGT15boh~CO8L4b7n_7~dUhrZMpRPB~reR7DUAWuG~sHj972WME6~1VgdMhBiax~jFLb4HjoWf~bXMh6mBhl8~3Q3HW-78l_~_EOd7bsGyl~ziiAzr_h04~9Gbahmahac~6vriIwKZAv~u3EAZmzDcl~2R7RYffVfj~SapL8yQw4Y~Ed_W9RQRe_~QaiOjmwQnI~o8a--Qa5of~cHpSJQiKeZ~KexWqtgzW1~wLSj8MDOo8~mLrrjwTHBm~WVrsHleeCL~OUsYoX1-GT~_vCZ2RLsY2~_3oeEue7S7~FrNQVCB8yi~YHRjLHL-7q~4sdiKNzOsj~hMzrji99a1~PrND0ipqBX~Uw_mm-h1Wo~FgYArp6riX~MM6RXH_rlN~EsPictrypp~MA6EUZYaNt~ZEYMFD786k~RcahSSzeRf~ePN3h1AXKX~R97S29V8Qw~YXsA4N8tVW; QSI_S_ZN_5hF4My7Ad6VNNAi=r:10:19; __cf_bm=0m8DlJrBTwZ.UkA95H91jwWjQYvzJ9v53XH1_hDO.tg-1648960602-0-AeDEXETjg7BWesTTPlyV2jRCeYr/S60VaHO7W/8JyG0+ycRcVcHsn2T3uBQjbBzkpXVgf7VMxxf53z8IeftahY/hNQEmRpLmUBzLfObJxdCO'
    ,'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/99.0.4844.74 Safari/537.36 Edg/99.0.1150.55',1,q)
    x=q.get()
    print(x)'''

    '''if 'R-18G'and ('死姦'or '脫腸' or'斬首'or '屍姦'or 'necrophilia'or'割脖'or '砍頭') in tag:
                    print('跳過'+url+tag) 
                                                         #回傳標籤
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/98.0.4758.80 Safari/537.36 Edg/98.0.1108.50',
        'Cookie':'p_ab_id=5; p_ab_id_2=5; p_ab_d_id=1125130963; first_visit_datetime_pc=2021-02-23+02%3A32%3A11; yuid_b=OJRESQg; a_type=0; b_type=1; login_ever=yes; privacy_policy_notification=0; c_type=34; PHPSESSID=27915696_60l9nve4HS7Z2Y0bngGXRZQwV4W4DvJ0; privacy_policy_agreement=3; tag_view_ranking=0xsDLqCEW6~lH5YZxnbfC~Lt-oEicbBr~kGYw4gQ11Z~Ie2c51_4Sp~RTJMXD26Ak~eVxus64GZU~HLWLeyYOUF~qiO14cZMBI~RVRPe90CVr~oCR2Pbz1ly~qWFESUmfEs~kP7msdIeEU~OT4SuGenFI~FySY6ZVB78~tgP8r-gOe_~5RvyKm3yea~kqu7T68WD3~v3nOtgG77A~bopfpc8En6~mCYugqjYJX~JXmGXDx4tL~qcYo_5oqVP~jfnUZgnpFl~J_YijUi2Xg~F8u6sord4r~3gc3uGrU1V~MM6RXH_rlN~TcgCqYbydo~Hry6GxyqEm~_giyO1uU9O~zyKU3Q5L4C~dUhrZMpRPB~aKhT3n4RHZ~KN7uxuR89w~BU9SQkS-zU~5oPIfUbtd6~y8GNntYHsi~EGefOqA6KB~05tD6f663z~Hjx7wJwsUT~h9r9YX0n2U~R-EFi7fMtD~w8ffkPoJ_S~jEoxuA2PIS~TOd0tpUry5~hRUnVPuHhQ~JtHr1OyMVc~Bd2L9ZBE8q~C9_ZtBtMWU~_EOd7bsGyl~TaUYlgH_jM~LVSDGaCAdn~iFcW6hPGPU~d-u0duThlB~MsF32uM-vh~GNcgbuT3T-~XDEWeW9f9i~_bee-JX46i~q303ip6Ui5~tlXeaI4KBb~LMpjieSVIv~ZXFMxANDG_~nRp2ZLPLbj~uKsA-LcJvn~qBVGbZbpq5~G-44hwuIPi~xa5-CDAPro~0j_zFcQpTM~YX72Y3LbXY~Txs9grkeRc~4ZEPYJhfGu~zASPXsXKdt~DADQycFGB0~HBlflqJjBZ~Gcv5xjGZY3~5Rf_nE4tAW~9wN-K8_crj~D4hLr_YmAD~bbZFcn8nQh~T40wdiG5yy~wlJLIPQpdd~5v2pI9_gGE~X4sPgKUWBs~hebOixBpSV~qIDsnltE2o~cxmbAHgoTk~mv-jOivdpn~f8pnWEIf9Z~ngUJxbZ4-R~ay54Q_G6oX~ziiAzr_h04~JWOyXSsjO2~HY55MqmzzQ~EUwzYuPRbU~KOnmT1ndWG~QKeXYK2oSR~cbmDKjZf9z~4qWlGrZbSE~iVTmZJMGJj; QSI_S_ZN_5hF4My7Ad6VNNAi=r:10:69; __cf_bm=k.dJJM7WQ45APSabaxdmzFCWQnBtPJzcg00Hbj4GxqQ-1644941038-0-ASQvUxnLsV4Q6uD6v4xA5kiW4NqFrLJ6ldhirpyqbEkQhANLCj2WurCFAnUYKvPZ+OmOXbJkpdoEJvJ7Rjf8HRIMzEgsQeWEO2NSD0jfhK5a'
        ,        
    }'''
    '''
    Agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/98.0.4758.80 Safari/537.36 Edg/98.0.1108.50'
    cookie='first_visit_datetime_pc=2022-02-22+01%3A16%3A54; p_ab_id=4; p_ab_id_2=7; p_ab_d_id=1650471887; __utma=235335808.1075363582.1645460214.1645460214.1645460214.1; __utmc=235335808; __utmz=235335808.1645460214.1.1.utmcsr=(direct)|utmccn=(direct)|utmcmd=(none); __cf_bm=0a88D.DNolnjql11bA2YlSX0AcrLNbOeH4e01.n9Bxo-1645460214-0-AZBMYe8wLu85sKJgxlsiLyTfyRirq9PImai1YQW6aqaElwzgwilYvqTg1yArIG9dhNAWfXUnstyJaUnA7KjsC4hCuS2VpDjkwIxqiWZjNpEfds/gw+Fti9Xi7WNGm60A275E+delw5z8UbPR+KvvWIsu+gzXXX+bNS+iKfwixI8fUjhytmLnoA2qeosMkwT2EA==; _fbp=fb.1.1645460216607.810010051; __utmt=1; yuid_b=ExaEQEI; _ga=GA1.2.1075363582.1645460214; _gid=GA1.2.1775348523.1645460337; _gat=1; PHPSESSID=78672220_Guho8rq4QaKdQmhrizUuq9XBanNhtU5S; device_token=9fed18d4053fdd2dd3c14b6a8b9487f6; privacy_policy_agreement=3; c_type=23; privacy_policy_notification=0; a_type=0; b_type=1; __utmv=235335808.|3=plan=normal=1^5=gender=male=1^6=user_id=78672220=1^11=lang=zh_tw=1; QSI_S_ZN_5hF4My7Ad6VNNAi=r:10:1; tag_view_ranking=qiO14cZMBI~RVRPe90CVr~oCR2Pbz1ly~Lt-oEicbBr~eVxus64GZU~uKsA-LcJvn; __utmb=235335808.4.10.1645460214'
    author_pids=['21971914','16976384']      
    q1=Queue()
    path=r'D:\圖片\下載\測試/'
    num=0
    no_use_seleium_get_pid(author_pids,cookie,Agent,q1,path,num)
    text=q1.get()
    print(len(text))'''
    
    #print(resdicts)
    #resdicts = str(json.loads(o)['illust'][str(id)]['tags']['tags'])
                                                                      

