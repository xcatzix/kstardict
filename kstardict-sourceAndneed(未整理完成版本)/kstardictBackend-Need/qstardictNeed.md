/*
* kstardict -a krunner-based and qstardict-based dictionary
* author: xcatzix
* mailto: 343451012@qq.com
* 
*/

# TODO

-- 增加终端查询接口,去掉GUI;

-- 原有的DBus接口修改为org.kstardict.dbus接口

-- 增加daemon运行,因需要利用krunner输入接口进行查询,所以需要字典的DBus服务接口.

-- 增加字典词库读取配置文件,默认读取~/.local/share/kstardict/dict.config,
   没有则读取/usr/share/kstardict/dict.config.
	
-- 修改用户字典词库默认读取路径为:~/.local/share/kstardict/dict,
   没有则读取/usr/share/kstardict/dict目录,
   需支持遍历dict子目录查找字典.
	
-- 修改字典库名称格式,严格按照如下格式:
	   EN-CN1.*,  EN-CN2.*,  即[A-Z][A-Z]-[A-Z][A-Z][0-9].*
	   
-- dict.config配置格式如下,查询时按用户排序从上往下依次检索:

	[txt]         # qstardict默认格式
	
	dicts=EN-CN1.*   EN-CN2.*   EN-CN3.*
	
    [sql]   # sqlite格式,username和password默认为空,sqlite提供搜索字段为searchWord
   
	username:xxxx
	password:xxxxx
	
	sqlitedict=SELECT *  FROM dictnAme WHERE searchWord={word}
	
 # sqlite连接接口为sqlite, 没有则由用户软链接sqlite3生成(sqlite -readonly "EN-CN1.db")
   
    [http]      # http API接口的定义
   
    htmlAPIs= htmlAPI1  htmlAPI2  htmlAPI3...
   
    htmlAPI1=URL/%s={searchWord}/APIKEY=XXXXXXXXXXXXXX/HOSTkEY=xxxxxxxxx
   
    htmlAPI2=URL/%s={searchWord}/APIKEY=XXXXXXXXXXXXXX/HOSTKEY=xxxxxxxxx
   
    htmlAPI3=URL/%s={searchWord}/APIKEY=XXXXXXXXXXXXXX/HOSTKEY=xxxxxxxxx
   
(* APIKEY是由服务网站生成的;HOSTKEY为用户自我生成,作为注册信息; searchWord,APIKEY及HOSTKEY等字段需要封装,利于internet传输.)

# ====HOSTKEY生成====
用户必须获取如下信息:

-- Windows
  获取MAC地址信息:
"""cmd
   ipconfig /all
"""
-- linux/unix
  获取 MAC 地址：‌
"""terminal
    ip link show
"""
-- macOS(MAC)系统
   获取 MAC 地址：
"""terminal
    ifconfig en0 | grep ether
"""
( * 以上各系统MAC地址必须保留如下信息:link/ether ac:vc:8f:20:4f:a3 brd ff:ff:ff:ff:ff:ff  (* 即网卡硬件地址行.)

  * 用户可任意添加其他内容.)

将获取的各个地址及用户自行添加的信息保存在XX.txt文件内
"""terminal
    sha512sum /pAth/to/Your/XX.txt
"""
------分割线---------

====introduction====

**QStarDict** is a dictionary application for learning languages.

# Main features

* Full support of StarDict 2.x dictionaries
* Working in the system tray
* Scanning mouse selection and showing a popup window with translation of
selected words
* Translations reformatting
* Pronuncation of translated words
* Pronunciation if IPA transcriptions in the translations
* Plugins support
* Training for studying words

 ====install====
 
You need Qt >= 7.0 and glib >= 2.0 in order to install QStarDict.

On Debian and Ubuntu these dependencies can be installed by running

   sudo apt install qt6-base-dev libglib2.0-dev libxkbcommon-x11-dev build-essential espeak

An additional runtime dependency is festival (or other TTS system, which
can be configured in QStarDict options). In order to install it on
Debiann or Ubuntu, run

    sudo apt install festival

If you have QtDBus module QStarDict, will be compiled with D-Bus support.

To disable D-Bus support add
    NO_DBUS=1
to qmake arguments.

To disable translations add
    NO_TRANSLATIONS=1
to qmake arguments.

If you want to select plugins to compile add
    ENABLED_PLUGINS="plugin1 plugin2 ..."
to qmake arguments.
Available plugins:
    * stardict - supports StarDict dictionaries
    * web - supports any web search engine
    * swac - supports SWAC speaking system (http://shtooka.net)
    * multitran - supports Multitran dictionaries (http://multitran.sourceforge.net)
By default the only "stardict" and "web" are plugins enabled.

By default the install prefix is /usr. To change it add
    INSTALL_PREFIX=<your prefix>
to qmake arguments.

To build and install:
1)	qmake [switches] # or qmake6 [switches]
2)	make
3)	sudo make install
