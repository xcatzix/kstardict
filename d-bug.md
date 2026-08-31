# python3脚本语法检查
>$python3 -c "import ast; ast.parse(open('/Path/To/yOur/kstardict.py').read()); print('syntax OK')"
```terminal
syntax OK
```
# 检查 D-Bus 服务是否注册
>$qdbus | grep kstardict
```terminal
org.kstardict.dbus
```
>$dbus-send --session --dest=org.freedesktop.DBus \
  --print-reply /org/freedesktop/DBus \
  org.freedesktop.DBus.ListNames | grep kstardict
```terminal
string "org.kstardict.dbus"
```
>$busctl --user list | grep kstardict
```terminal
org.qstardict.dbus   1885 qstardict       ${user} :1.53  user@1000.service -       -

```
# 验证接口可调用（核心检查）
>$qdbus org.kstardict.dbus /kstardict org.kstardict.dbus.translate "hello"
```terminal
<p>
<font class="dict_name">简明英汉字典增强版</font><br>
<font class="title">hello</font><br>
*<font class="transcription">[hә'lәu]</font>   -K3<br>interj. 喂, 嘿<br>(中高 2238/</li><li></p>
```
# 查看完整接口清单
>$qdbus org.kstardict.dbus /kstardict org.freedesktop.DBus.Introspectable.Introspect
```terminal
<!DOCTYPE node PUBLIC "-//freedesktop//DTD D-BUS Object Introspection 1.0//EN"
"http://www.freedesktop.org/standards/dbus/1.0/introspect.dtd">
<node>
  <interface name="org.qstardict.dbus">
    <property name="mainWindowVisible" type="i" access="readwrite"/>
    <method name="showTranslation">
      <arg name="text" type="s" direction="in"/>
    </method>
    <method name="showPopup">
      <arg name="text" type="s" direction="in"/>
    </method>
```
或直接浏览
>$qdbus org.kstardict.dbus /kstardict
```terminal
property readwrite int org.qstardict.dbus.mainWindowVisible
method void org.qstardict.dbus.showPopup(QString text)
method void org.qstardict.dbus.showTranslation(QString text)
method QString org.qstardict.dbus.translate(QString text)
method QString org.qstardict.dbus.translateHtml(QString text)
signal void org.freedesktop.DBus.Properties.PropertiesChanged(QString interface_name, QVariantMap changed_properties, 
QStringList invalidated_properties)
method QDBusVariant org.freedesktop.DBus.Properties.Get(QString interface_name, QString property_name)
method QVariantMap org.freedesktop.DBus.Properties.GetAll(QString interface_name)
method void org.freedesktop.DBus.Properties.Set(QString interface_name, QString property_name, QDBusVariant value)
method QString org.freedesktop.DBus.Introspectable.Introspect()
method QString org.freedesktop.DBus.Peer.GetMachineId()
method void org.freedesktop.DBus.Peer.Ping()
```
# 直接判定
>$qdbus org.kstardict.dbus /kstardict org.kstardict.dbus.translate "test" && echo "✅ D-Bus OK"
```terminal
<p>
<font class="dict_name">简明英汉字典增强版</font><br>
<font class="title">test</font><br>
*<font class="transcription">[test]</font>   -K5<br>n. 测试, 试验, 化验, 检验, 考验, 甲壳<br>vt. 测试, 试验, 化验<br>vi. 接受测验, 
进行测试</p><p><font class="transcription">[时态]</font> tested, testing, tests<br>(中高研四六雅 575/</li><li></p>

✅ D-Bus OK

```
# 1. 确认服务注册
>$qdbus | grep com.yourname.krunner-stardict
```result
com.yourname.krunner-stardict
```
# 2. 确认对象路径
>$qdbus com.yourname.krunner-stardict /com/yourname/krunner_stardict
```result
method QString org.freedesktop.DBus.Introspectable.Introspect()
method {D-Bus type "a(sssida{sv})"} org.kde.krunner1.Match(QString query)
method void org.kde.krunner1.Run(QString id, QString action_id)
method void org.kde.krunner1.Teardown()
```

# 3.验证文本输出格式是否满意-debug输出测试
```code
    ......
    # ★ 规则 2：遇到 '[' 就换行（不在行首的 [ 前面插入换行）
    text = re.sub(r"(?<!\n)\[", "\n[", text)

    # 7. ★ 规则 3：还原占位符
    text = text.replace("__BRACKET__", "[")

    # 8. ★ 规则 4：半角-->全角
    _half_to_full = {
        ",": "，",
        ";": "；",
        ":": "：",
        "!": "！",
        "?": "？",
        ".": "。",
    }
    text = re.sub(
        r"(?<![a-zA-Z])[,\.;:!?]",
        lambda m: _half_to_full.get(m.group(), m.group()),
        text,
    )
    
    # ---debug输出测试----
    print(f"[DEBUG strip_html] {repr(text)}")

    ......
```
>$python3 -c '
import sys
sys.path.insert(0, "/home/giqorg/.local/bin")
from kstardict import strip_html
print(repr(strip_html("apple,banana")))
print(repr(strip_html("n.苹果;果树")))
print(repr(strip_html("light<br>*[lait] -K3")))
'
```terminal
[DEBUG strip_html] 'apple,banana'
'apple,banana'
[DEBUG strip_html] 'n.苹果；果树'
'n.苹果；果树'
[DEBUG strip_html] 'light   *[lait] -K3'
'light   *[lait] -K3'


```

# 3. 测试 Match 方法
>$qdbus --literal com.yourname.krunner-stardict /com/yourname/krunner_stardict org.kde.krunner1.Match "tr fire"
```result
[Argument: a(sssida{sv}) {[Argument: (sssida{sv}) "stardict-fire", "正在查询：fire ...", "accessories-dictionary", 
100, 1, [Argument: a{sv} {"subtext" = [Variant(QString): "fire"]}]]}]
```
# 测试qstardict的D-Bus是否正常
## 1. 先确认QStarDict本身处于运行状态
 - QStarDict的D-Bus接口只有在程序启动后才会注册生效，先打开QStarDict主程序，确保它没有后台崩溃退出。
2. 用系统自带的dbus-send命令直接探测D-Bus服务
 - 打开终端执行以下命令，直接查询目标服务是否已经在会话总线上成功注册：
>$dbus-send --print-reply --dest=org.qstardict.dbus /qstardict org.freedesktop.DBus.Introspectable.Introspect
 or
>$qdbus org.qstardict.dbus /qstardict org.freedesktop.DBus.Introspectable.Introspect
  * 如果服务正常，终端会输出完整的接口XML描述内容；如果提示找不到该服务，说明QStarDict的D-Bus功能没有正常启用，可以
  进入QStarDict设置确认「启用D-Bus接口」的选项已经开启。
 ```bash
 method return time=1786601779.506272 sender=:1.55 -> destination=:1.293 serial=207 reply_serial=2
   string "<!DOCTYPE node PUBLIC "-//freedesktop//DTD D-BUS Object Introspection 1.0//EN"
   "http://www.freedesktop.org/standards/dbus/1.0/introspect.dtd">
<node>
  <interface name="org.qstardict.dbus">
    <property name="mainWindowVisible" type="i" access="readwrite"/>
    <method name="showTranslation">
      <arg name="text" type="s" direction="in"/>
    </method>
    .......
```
# 打开终端并输入tr test进行脚本测试
>$qdbus org.kde.krunner /App query 'tr test'

# ================================================
## 重新加载应用程序...
pkill -f krunner-stardict.py
## 4. 用自带的krunner-stardict.py脚本做Python侧调用验证
 - 直接单独运行这个脚本，观察启动是否报错：
>$ python3 ~/.local/bin/krunner-stardict.py
```bash
#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import sys
import re
import dbus
import dbus.service
import dbus.mainloop.glib
from gi.repository import GLib

# -----------------------------------------------------------------------------
# 配置部分
# -----------------------------------------------------------------------------
DBUS_SERVICE_NAME = "org.kde.krunner1"
DBUS_OBJECT_PATH = "/Plugin/stardict"
DBUS_INTERFACE = "org.kde.krunner1.Plugin"

QSTARDICT_SERVICE = "org.qstardict.dbus"
QSTARDICT_PATH = "/qstardict"
QSTARDICT_INTERFACE = "org.qstardict.dbus"

# -----------------------------------------------------------------------------
# 辅助函数
# -----------------------------------------------------------------------------

def clean_query(text):
    """
    清理查询字符串：
    1. 去除首尾空白
    2. 如果开头是非英文字符，删除从开头到下一个空格之间的所有内容
       (适配用户之前提到的正则需求)
    """
    if not text:
        return ""
    
    text = text.strip()
    
    # 规则：删除开头到首个非英文字符之后、直到下一个空格处的所有字符
    # 如果末尾没有空格，则删到字符串结尾
    cleaned = re.sub(r"^[^a-zA-Z]+.*?(\s|$)", "", text, count=1)
    
    # 如果清洗后为空，或者原字符串本身就是纯英文/数字，返回原字符串(去空后)
    if not cleaned:
        return text
        
    return cleaned.strip()

def get_translation_from_qstardict(word):
    """
    通过 D-Bus 调用 QStarDict 获取翻译
    """
    try:
        bus = dbus.SessionBus()
        proxy = bus.get_object(QSTARDICT_SERVICE, QSTARDICT_PATH)
        interface = dbus.Interface(proxy, QSTARDICT_INTERFACE)
        
        # 调用 translateHtml 方法
        # 注意：不同版本的 QStarDict 接口可能略有不同，通常是 translateHtml 或 lookup
        html_content = interface.translateHtml(word)
        
        if html_content:
            # 简单清理 HTML 标签以便在 subtext 中显示更干净
            # 移除 <[^>]+> 标签
            plain_text = re.sub(r"<[^>]+>", "", html_content)
            # 移除多余空白
            plain_text = re.sub(r"\s+", " ", plain_text).strip()
            return plain_text
        else:
            return None
            
    except dbus.exceptions.DBusException as e:
        print(f"D-Bus Error: {e}", file=sys.stderr)
        return None
    except Exception as e:
        print(f"Unexpected Error: {e}", file=sys.stderr)
        return None

# -----------------------------------------------------------------------------
# KRunner Plugin 类定义
# -----------------------------------------------------------------------------

class StarDictPlugin(dbus.service.Object):
    def __init__(self):
        bus_name = dbus.service.BusName(DBUS_SERVICE_NAME, bus=dbus.SessionBus())
        dbus.service.Object.__init__(self, bus_name, DBUS_OBJECT_PATH)

    @dbus.service.method(DBUS_INTERFACE, in_signature='s', out_signature='a(sssidva{sv})')
    def Match(self, query):
        """
        KRunner 调用此方法进行匹配
        :param query: 用户输入的查询字符串
        :return: 匹配结果列表
        """
        # 1. 预处理查询词
        original_query = query.strip()
        if not original_query:
            return []

        # 应用清理逻辑 (可选，根据用户需求)
        # 如果希望严格只查英文，可以启用 clean_query
        # word_to_lookup = clean_query(original_query)
        
        # 通常直接查原词，或者只取第一个单词
        word_to_lookup = original_query.split() if original_query else ""
        
        if not word_to_lookup:
            return []

        # 2. 获取翻译
        translation = get_translation_from_qstardict(word_to_lookup)
        
        if not translation:
            return []

        # 3. 构造返回结果
        # 签名: a(sssidva{sv})
        # s: id
        # s: text (显示的主标题)
        # s: iconName
        # i: type (0=Standard, 1=Contact, etc., usually 0 or 100 for custom)
        # d: relevance (0.0 - 1.0)
        # v: actions (variant, usually empty list or dict for legacy compatibility)
        # a{sv}: properties (extra data like subtext)
        
        result_item = (
            f"stardict-{word_to_lookup}",  # id
            word_to_lookup,                # text (主显示文本)
            "accessories-dictionary",      # iconName
            100,                           # type
            1.0,                           # relevance
            "",                            # variant (占位符，对应签名中的 v)
            {
                "subtext": translation     # properties: 副标题显示翻译内容
            }
        )
        
        return [result_item]

    @dbus.service.method(DBUS_INTERFACE, in_signature='ss', out_signature='')
    def Run(self, id, actionId):
        """
        当用户选中结果并回车时触发
        :param id: 匹配项的 ID
        :param actionId: 动作 ID
        """
        # 这里可以添加点击后的行为，例如复制翻译到剪贴板或打开 QStarDict 窗口
        # 目前仅做占位，避免报错
        pass

    @dbus.service.method(DBUS_INTERFACE, in_signature='', out_signature='a{ss}')
    def Actions(self):
        """
        返回支持的动作列表
        """
        return {}

# -----------------------------------------------------------------------------
# 主程序入口
# -----------------------------------------------------------------------------

if __name__ == '__main__':
    dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
    
    print("Starting StarDict KRunner Plugin...")
    plugin = StarDictPlugin()
    
    loop = GLib.MainLoop()
    try:
        loop.run()
    except KeyboardInterrupt:
        print("Plugin stopped.")
        sys.exit(0)
# Arch/Fedora/Debian 上通过包管理器安装 python-dbus 和 python-gobject
```