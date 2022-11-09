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

import download_img
from requests import exceptions
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from tqdm import tqdm, trange
from selenium.webdriver.common.keys import Keys
option = webdriver.ChromeOptions()
option.add_experimental_option("excludeSwitches", ['enable-automation', 'enable-logging'])
from pathlib import Path
def logging(address,password):
    #options=webdriver.ChromeOptions()
 
    # 忽略无用的日志
    url = 'https://pixiv.net/'
    driver = webdriver.Chrome(options=option)
    driver.get(url)
    driver.find_element(By.XPATH,'//*[@id="wrapper"]/div[3]/div[2]/a[2]').click()
    time.sleep(3)
    driver.find_element(By.XPATH,"//input[@autocomplete = 'username']").send_keys(address)
    time.sleep(3)
    passwd=driver.find_element(By.XPATH,"//input[@autocomplete = 'current-password']")
    passwd.send_keys(password)
    passwd.send_keys(Keys.RETURN)
    time.sleep(3)
    driver.find_element(By.XPATH,'//*[@id="LoginComponent"]/form/button').click()
logging('f1213f6631@gmail.com','kkid052330779')

