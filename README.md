# astrbot_plugin_gemini_search

AstrBot 插件。聊天模型要查资料时，交给另一个自带联网搜索的模型（比如 Gemini 的 Google 搜索）去搜，再把结论交回来。聊天模型不用换，用来顶替 AstrBot 自带的网页搜索工具。

## 怎么工作

- 给聊天模型加一个 `online_search` 工具。模型调用它时，插件向设置里选的模型发一条只带搜索的请求（OpenAI 格式，带 `web_search_options`），把回答原样交回给聊天模型。
- 默认会从每次请求里去掉 AstrBot 自带的网页搜索工具（Tavily、博查、Brave、Firecrawl、百度、Exa），读网页的工具保留。
- 默认还会在系统提示词里加一句提醒：遇到不认识的人名、作品、新词先搜再答，问到人物时把这个人和自己的关系一起查。不加这句的话，模型在人设聊天里遇到陌生名字常常直接说没印象，或者反问对方。
- 另给模型一个 `search_pixiv` 工具：按标签在 Pixiv 上搜插画（只要全年龄的，默认跳过标了 AI 生成的），随机挑几张缩略图直接给模型看，同时列出标题、画师、原图地址和作品页。模型看中哪张，用 `send_pixiv_image` 把那张的大图发给对方；原图地址走 `i.pixiv.re` 镜像，别的插件不带来源也能下载（比如拿来换头像）。不用登录 Pixiv。

## 要求

- AstrBot 4.26 及以上。
- 搜索用的模型要按「OpenAI 兼容」类型添加，而且上游要认 `web_search_options`。
- 上游是 CLIProxyAPI 时：它的 OpenAI 入口不认 `web_search_options`，只认 tools 里的 `{"google_search": {}}`。经 new-api 转发的话，可以在那个渠道的「参数覆盖」里换成它，请求里已经带了别的工具时只去掉搜索（Antigravity 不让搜索和自定义工具同时用）：

```json
{"operations": [
  {"path": "web_search_options", "mode": "delete", "logic": "AND",
   "conditions": [{"path": "web_search_options", "mode": "prefix", "value": "{"}, {"path": "tools", "mode": "prefix", "value": "["}]},
  {"path": "tools", "mode": "set", "value": [{"google_search": {}}],
   "conditions": [{"path": "web_search_options", "mode": "prefix", "value": "{"}]},
  {"path": "web_search_options", "mode": "delete",
   "conditions": [{"path": "web_search_options", "mode": "prefix", "value": "{"}]}
]}
```

## 设置

| 项 | 说明 |
| --- | --- |
| `provider_id` | 用来联网搜索的模型 |
| `replace_builtin_search` | 停用 AstrBot 自带的网页搜索工具，默认开 |
| `timeout` | 单次搜索最长等待秒数，默认 60 |
| `reasoning_effort` | 搜索模型的思考档位（OpenAI 的 `reasoning_effort`），默认 `low`，档位越低搜得越快；留空按模型默认 |
| `remind_in_prompt` | 在系统提示词里加那句"不认识的先搜"的提醒，默认开；每条消息都会带上 |
| `pixiv_count` | 在 Pixiv 上搜插画时一次给模型看几张，默认 6 |
| `pixiv_skip_ai` | 在 Pixiv 上搜插画时跳过标了 AI 生成的作品，默认开 |

## 安装

在 AstrBot 面板的插件页选择从链接安装，填仓库地址 `https://github.com/ShirahaTobisa/astrbot_plugin_gemini_search`；装完在插件设置里选好搜索用的模型。

也可以把 `main.py`、`metadata.yaml`、`_conf_schema.json` 打成 zip（文件放在压缩包根目录），从文件安装。

## 已知限制

- 拿不到来源链接：转成 OpenAI 格式后，上游一般不返回搜索来源，只能靠模型在回答里写出网站名。
- 每次搜索都要多调一次模型，会多等几秒。
- Pixiv 不登录时只能按发布时间搜，看不到热度排序；有的标签（比如精确的角色标签）结果会很少，用空格分开的几个词反而搜得多。

## 许可证

CC BY-NC 4.0，详见 `LICENSE`。
