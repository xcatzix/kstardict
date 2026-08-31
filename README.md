* ---*- coding: utf-8 -*---
* Application's name: kstardict
* Author: xcatzix
* mail:343451012@qq.com
* test version.

# kstardict
  * An excellent dictionary baced on qstardict.

# Using License
  * Using ,sharing and so on it in paying money.

----使用者须知<Start>----
# kstardict <The dictionary of the king of star>(Using in unix-like system desktop with the krunner) --星王字典
    -- [EN] the Instruction of installation in README(below)
    -- [CN] 安装说明看README部分(below)...
    -- [EN] ...English...
    -- [CN] 若想在krunner上看到一些操作提示,需要在System Settings=>Plasma Search(krunner)内,点击kstardict一栏的
            五角形,将其添加到Favorite Plugins.鼠标点击kstardict的右边'对号'右边的图标,上拉至第一行,即可方便在
            krunner输入框下边看到一些操作提示.
    -- [EN] Using: Pressing alt+space to open the krunner<plasma search>,typing: tr woRd.(. is a trigger).
    -- [CN] 使用:按键盘alt+space打开krunner即plasma search,在输入框内输入: tr woRd.(.触发查询).
    -- [EN] ...English...
    -- [CN] 查询成功,结果默认显示在屏幕顶部中间krunner下面的浮窗内;返回字词太多，浮窗内容区，支持鼠标滚轮下拉,
            也可以使用右侧滚动条下拉(滚动条隐藏,鼠标靠近右侧边缘触发显现且能自动隐藏).
    -- [EN] ...English...
    -- [CN] 查询失败,即查询结果为空,不弹浮窗,将在krunner上直接显示"woRd未收录..."等信息.
    -- [EN] ...English...
    -- [CN] 输入框内想再次输入查询时,只需按一次BackSpace,即可完整删除已查询的单词,保留'tr ',利于快速查询,无修
            改单词功能.
    -- [EN] ...English...
    -- [CN] 按ESC或切换焦点,即可关闭krunner和悬浮窗;不愿关闭查询入口及浮窗,可以将krunner的keep open打开,即点击
            krunner最左边的大头针图标,将krunner置于屏幕的最顶层,方便使用.
    -- [EN] ...English...
    -- [CN] 需要关闭浮窗,只需要按一次BackSpace,即可.
    -- [EN] ...English...
    -- [CN] 浮窗底部状态bar由<字典名称>、<word>、<天气>、<时间>, <下拉指示器>组成,具有点击功能,鼠标悬浮于bar上
            有提示信息显示(暂未完成,敬请关注).
------------使用者须知<End>-------------------

# ==README==
## 安装及卸载(install and uninstall)
### ENV
  -- [EN] ...English...
  -- [CN] 由于依赖较少，只要装了krunner的其他类linux桌面环境也应该可用，作者未测试。
### Dependence:
  -- [EN] Dependence: krunner + qstardict + python3 + tkinter(GUI)(* Needing Application installed)
### Install:
```install.sh(chmod +x ./install.sh)
#!/usr/bin/env bash
if [[ "$1" == '-h' ]] || [[ -z "$1" ]]; then
        echo "Using: install -(i|r) install or uninstall"
        return
fi

if [[ "$1" == '-i' ]]; then
        chmod +x ./kstardict.py
        cp ./kstardict.py ~/.local/bin/
        cp ./*.desktop ~/.local/share/krunner/dbusplugins/
        cp ./*.service ~/.local/share/dbus-1/services/com.yourname.krunner-stardict.service
        kbuildsycoca6
        kquitapp6 krunner
        krunner --daemon &
fi

if [[ "$1" == '-r' ]]; then
        rm ~/.local/bin/kstardict.py
        rm ~/.local/share/krunner/dbusplugins/com.yourname.krunner-stardict.desktop
        rm ~/.local/share/dbus-1/services/com.yourname.krunner-stardict.service
        pkill -f kstardict.py
        # kbuildsycoca6
        # kquitapp6 krunner
        # krunner --daemon &
fi
```

# ==创作背景==
# The origin of the kstardict
     -- 由于wayland合成器特性，使得qstardict字典无法在托盘状态下进行快捷键悬浮窗翻译，也不能适应现今多窗口多任
        务的桌面环境,对不同窗口的切换及点击,便有了本kstardict字典。(*注 其利用qstardict及krunner软件的D-Bus接口配合
        python的轻量级GUI(Tkinter)完成的。)

# (features)特性:
    -- 每次查询都直接调用 qstardict（无本地缓存），保证结果实时、最新。
    -- 在使用本软件时，会出现截词现象，这是由于qstardict(stardict)未收录使用者所输入的单词，导致其进行了截词
       查找。如输入earer,其将显示ear翻译。(翻译窗口内，使用者可以清楚看到翻译哪个词.)
    -- 以KRunner作为翻译查询输入框，tr和.为触发器，调用后端翻译软件qstardict。qstardict将翻译结果放在字典内，
       显示在GUI上。
    -- 当KRunner 输入框内触发"tr wOrd(.)"删除时，wOrd.部分任意删除一次，即:
          1) 重置内部待查询单词，(若有)旧浮窗则自动关闭，触发2事件；
          2) 主动调用 `qdbus org.kde.krunner /App query 'tr ', 让 KRunner 保持/重置为 "tr "状态，便于快速输
             入新单词。
          3) 单词长度阈值(MIN_WORD_LEN): 单词长度(清洗掉末尾 '.' 后)小于该值的，视为"短词/无效词"，不发
             起翻译、不弹窗，关闭已有浮窗并重置KRunner 为 "tr "。默认2，即仅1个字母的单词(如tr a./tr b.)
             会被忽略, 如需更严格，可将其改为3或更大。
# 浮窗视觉(Tkinter，单层Toplevel + 内嵌Frame模拟阴影):
    -- 仍只使用1个 Toplevel(Wayland下, 单surface，居中稳定、不会分离).
    -- Toplevel背景设为阴影色(SHADOW_COLOR), 内部嵌一个Frame(内容区，POPUP_BG), 四周留出SHADOW_MARGIN像素边距，
       形成"阴影边框"立体感。
    -- 红色描边border=2: 用highlight*在内容Frame上画2px红色边框。
    -- 浮窗尺寸固定: 宽POPUP_WIDTH、高POPUP_HEIGHT. Text组件wrap="word"，文字长度超出固定宽度时自动折叠到下一行
       显示。
