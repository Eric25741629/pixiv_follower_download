import json
import os
import random
import shutil
import threading
import time
from logging import exception
from random import random
from pixiv_api import Test_cookies
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

import download_img
from requests import exceptions
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from tqdm import tqdm, trange
from selenium.webdriver.common.keys import Keys



from pathlib import Path

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
if __name__ == '__main__':
    cookies='__utmb=235335808.3.10.1668024700;_ga_75BBYNYN9J=GS1.1.1668024699.1.1.1668024704.0.0.0;QSI_S_ZN_5hF4My7Ad6VNNAi=v:0:0;_ga=GA1.1.387250321.1668024700;c_type=35;b_type=1;a_type=0;device_token=063b6b00e619988505ac99c2a83da071;PHPSESSID=27915696_549XZcEeskPkH8vGsyMLqwEhNTiUlR4K;_gat_gtag_UA_76252338_1=1;_gat_UA-1830249-3=1;_ga_MZ1NL4PHH0=GS1.1.1668024701.1.0.1668024703.0.0.0;privacy_policy_notification=0;_gid=GA1.2.1517130103.1668024701;_gcl_au=1.1.340013240.1668024700;p_ab_id=1;adr_id=LohaWhtY1OZ178AHJLv39PFrgAUxDNDCYfd3SxQAsPDNiTMe;__utmt=1;tag_view_ranking=0xsDLqCEW6~8qzNKXwVP9~P9jONkE5Ux~aMSPvw-ONW~49mOVaB3jw~JlM3GXVpyv;yuid_b=F4AIYmM;__utmz=235335808.1668024700.1.1.utmcsr=(direct)|utmccn=(direct)|utmcmd=(none);privacy_policy_agreement=0;first_visit_datetime_pc=2022-11-10+05%3A11%3A36;__utma=235335808.387250321.1668024700.1668024700.1668024700.1;__utmv=235335808.|2=login%20ever=no=1^3=plan=normal=1^5=gender=male=1^6=user_id=27915696=1^9=p_ab_id=1=1^10=p_ab_id_2=0=1^11=lang=zh_tw=1;__utmc=235335808;_im_vid=01GHF0A8K54BKG1EBBS30S4H7J;p_ab_id_2=0;__cf_bm=vMsAfOnBo552KD1Ed9JcujTnxkoS7FP0lENGGVq_LgY-1668024698-0-AWfVxATwWV1qAQjrW3VRNVhOMDMkTcNQfoklwu6JwZhD+wfeTIcyBlNRBqecUeQzkK02zJfPIi/MJcz48RxE79Gnw5iXabCto9nJXmw7ymIbRhKtDa2HGeU8eVZ1n2Y2ASXbJSz/TJW7SPpdTe4u3pwbmZC2Wv7AE9dzxIAXcPOEvyNLxkm3ne29luzKVguI017Mo+fG7mL2qjN1OlvjtswxxLoPcFRaVodU3pSfRkhM;_fbp=fb.1.1668024700119.433767091;p_ab_d_id=362323293;'
    #print(cookies)
    #cookies=auto_get_cookie('h321h21h1@gmail.com','kkid052330779')
    #cookies='_gat_gtag_UA_76252338_1=1;_gat_UA-76252338-4=1;_ga=GA1.3.1921962754.1667991451;_ga=GA1.2.1921962754.1667991451;_gat_UA-1830249-3=1;_gid=GA1.2.869284674.1667991452;_ga_75BBYNYN9J=GS1.1.1667991450.1.0.1667991451.0.0.0;_ga_MZ1NL4PHH0=GS1.1.1667991452.1.0.1667991455.0.0.0;__cf_bm=syi1YvEvrVk3.IuhZocStDrxQDB4N5sOtk7QU0BNB2g-1667991450-0-AcL278jygNWlkwarOr+x7Wve4/YNdRO7gDY1MZQY1yz1EMnnuxAtsFENJjutUIiGcpnI1h2KJPoIWP7wfpwcEu4nk0afX911xsI90ve7u5GlExfejk+8deda0sPzuXMaGFMaGv2fkubFen+cmvUeX1rY5PQJ0imOh4G0fRdT2StwEnPaoDLcAb8dWqXHwze+njCPboiECZVvposON8O0M6HN7jDOtAek9Hs9as4rrKR2;_fbp=fb.1.1667991451239.1915395752;__utmv=235335808.|2=login%20ever=no=1^9=p_ab_id=2=1^10=p_ab_id_2=3=1^11=lang=zh_tw=1;p_ab_id=2;__utmt=1;__utmz=235335808.1667991451.1.1.utmcsr=(direct)|utmccn=(direct)|utmcmd=(none);_gcl_au=1.1.1726668875.1667991451;__utmc=235335808;__utma=235335808.1921962754.1667991451.1667991451.1667991451.1;p_ab_id_2=3;__utmb=235335808.1.10.1667991451;p_ab_d_id=372183931;_gid=GA1.3.869284674.1667991452;PHPSESSID=e3q2nshm69557gj2vejcimgninnomq2g;'
    #cookies,agent=auto_get_cookie('f1213f6631@gmail.com','kkid052330779')
    #print(agent)

    print(cookies)
    print(Test_cookies([cookies,],
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/107.0.5304.89 Safari/537.36'))
