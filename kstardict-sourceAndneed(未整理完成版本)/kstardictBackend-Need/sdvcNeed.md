/*
* kstardict -a krunner-based and qstardict-based dictionary
* author: xcatzix
* mailto: 343451012@qq.com
*/

需求:
1. 给'https://www.github.com/xcatzix/sdcv'的项目打上补丁:
	-- 增加org.kstardict.dbus接口
    -- 增加daemon运行,因krunner需要字典dbus服务接口.
    -- 在现有的浮窗界面上,再做一层活动浮窗,浮窗展开位于状态bar下层,字典查询返回值的上层; http API返回值显示在此层, 此层
	   单独接收http API接口数据, 避免用户网络延迟等待. 此浮窗默认位于状态Bar上方,右上角,显示9x9 px的可点击上浮指示器;
 	   若展开,则显示9x9 px的可点击下沉指示器.
	-- 增加字典词库读取配置文件,默认读取~/.local/share/kstardict/dict.config,
	   没有则读取/usr/share/kstardict/dict.config.
	-- 修改用户字典词库默认读取路径为:~/.local/share/kstardict/dict,
	   没有则读取/usr/share/kstardict/dict目录,
	   需支持遍历dict子目录查找字典.
	-- 修改字典库名称格式,严格按照如下格式:
	   EN-CN1.*,EN-CN2.*,即[A-Z][A-Z]-[A-Z][A-Z][0-9].*
	-- dict.config配置格式如下,查询时按用户排序从上往下依次检索:
		  [txt]         # qstardict默认格式
		  dicts=EN-CN1.* EN-CN2.* EN-CN3.*
		  [sql]   # sqlite格式,username和password默认为空,sqlite提供搜索字段为searchWord
		  username:xxxx
		  password:xxxxx
		  sqlitedict=SELECT *  FROM dictnAme WHERE searchWord={word}
		  # sqlite连接接口为sqlite,没有则由用户软链接sqlite3等生成(sqlite -readonly "EN-CN1.db")
		  [http]
		  htmlAPIs= htmlAPI1 htmlAPI2 htmlAPI3...
		  htmlAPI1=URL/%s={searchWord}/APIKEY=XXXXXXXXXXXXXX/HOSTkEY=xxxxxxxxx
		  htmlAPI2=URL/%s={searchWord}/APIKEY=XXXXXXXXXXXXXX/HOSTKEY=xxxxxxxxx
		  htmlAPI3=URL/%s={searchWord}/APIKEY=XXXXXXXXXXXXXX/HOSTKEY=xxxxxxxxx
 (* APIKEY是由服务网站生成的,HOSTKEY为用户自我生成;searchWord,APIKEY及HOSTKEY等字段需要封装,利于internet传输.)
 # =============HOSTKEY生成===============
用户必须获取如下信息:
# Windows
  获取MAC地址信息:
```cmd
   ipconfig /all
```

# linux/unix
   获取 MAC 地址：‌
```terminal
    ip link show
```

# macOS(MAC)系统
   获取 MAC 地址：
```terminal
    ifconfig en0 | grep ether
```
(
* 以上各系统MAC地址必须保留如下信息:
    link/ether ac:vc:8f:20:4f:a3 brd ff:ff:ff:ff:ff:ff  (* 即网卡硬件地址行.)
  ---用户可任意添加其他内容.---
)

将获取的各个地址保存在XX.txt文件内
```terminal
    sha512sum /pAth/to/Your/XX.txt
```
