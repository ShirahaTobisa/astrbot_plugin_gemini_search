"""联网搜索：聊天模型要查资料时，交给另一个自带联网搜索的模型去搜，顶替 AstrBot 自带的网页搜索；另外能在 Pixiv 上搜插画给它看。"""

import asyncio
import base64
import random
import re
import urllib.parse
from datetime import datetime
from zoneinfo import ZoneInfo

import aiohttp
from mcp.types import CallToolResult, ImageContent, TextContent
from openai import AsyncOpenAI

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.message_components import Image
from astrbot.api.star import Context, Star

# AstrBot 自带的搜索工具；读网页的工具（如 tavily_extract_web_page）不在其中，照常保留
BUILTIN_SEARCH_TOOLS = (
    "web_search_tavily",
    "web_search_bocha",
    "web_search_brave",
    "web_search_firecrawl",
    "web_search_baidu",
    "web_search_exa",
)
SEARCH_PROMPT = (
    "你是联网搜索助手，替另一个 AI 查资料。现在是 {now}（北京时间）。"
    "先联网搜索，再只根据搜到的内容用中文简要作答：只写回答问题需要的要点，写清日期、数字、名称等关键信息，最后一句说明信息来自哪些网站。"
    "搜不到、信息过时或说法不一致时直接说明，不要编造。"
)
SEARCH_REMINDER = (
    "\n\n对方提到你不认识的人名、角色、作品或新词时，先调用 online_search 查清楚再回答，不要直接说没印象或反问；"
    "问到人物时，把这个人和你的关系一起查。查到的和你自己有关时，按你的身份自然地说出来。"
)
PIXIV = "https://www.pixiv.net"
PIXIV_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Referer": "https://www.pixiv.net/",
}


def pixiv_image(thumb: str, size: str = "") -> str:
    """把搜索结果里 250 的方形缩略图地址换成别的尺寸；size 留空就是 1200 的大图。"""
    url = re.sub(r"/c/250x250_80_a2/(?:custom-thumb|img-master)/", f"/{size}/img-master/" if size else "/img-master/", thumb)
    return re.sub(r"_(?:square|custom)1200", "_master1200", url)


class GeminiSearchPlugin(Star):
    def __init__(self, context: Context, config: dict):
        super().__init__(context)
        self.provider_id = str(config.get("provider_id", "")).strip()
        self.replace_builtin = bool(config.get("replace_builtin_search", True))
        self.timeout = float(config.get("timeout", 60))
        self.reasoning_effort = str(config.get("reasoning_effort", "low")).strip()
        self.remind = bool(config.get("remind_in_prompt", True))
        self.image_count = int(config.get("pixiv_count", 6))
        self.skip_ai = bool(config.get("pixiv_skip_ai", True))
        self.shown = {}  # 每个会话上一次搜到给模型看的那几张

    async def initialize(self):
        # 当前 AstrBot 从注释生成参数，但未填写 required。
        for name, arg in (("online_search", "query"), ("search_pixiv", "keyword"), ("send_pixiv_image", "number")):
            tool = self.context.get_llm_tool_manager().get_func(name)
            if tool:
                tool.parameters["required"] = [arg]

    # 优先级调高一点，让提醒排在多数插件追加的提示词前面
    @filter.on_llm_request(priority=120)
    async def prepare_search(self, event: AstrMessageEvent, req):
        if not req.func_tool:
            return
        if self.replace_builtin:
            for name in BUILTIN_SEARCH_TOOLS:
                req.func_tool.remove_tool(name)
        if self.remind and req.func_tool.get_tool("online_search"):
            req.system_prompt += SEARCH_REMINDER

    @filter.llm_tool(name="online_search")
    async def online_search(self, event: AstrMessageEvent, query: str):
        """联网搜索。遇到下面这些情况先搜再答，不要装作知道，也不要只说没印象或反问对方：需要实时信息，比如新闻、天气、比赛结果、游戏活动和版本更新、价格；拿不准的事实；对方提到你不认识的人名、角色、作品、游戏内容、梗和新词，哪怕只给了一个名字，也先拿它去搜。常识和本来就清楚的内容不用搜。搜索结果里没写到的细节不要自己补，拿不准就换个问法再搜一次。

        Args:
            query(string): 要查的问题，用一句完整的话写清楚要找什么，带上对方问的具体内容和时间、地点、作品名等信息；如果你在扮演某个角色，问到别的人物时，把这个人和你这个角色的关系一起问。
        """
        provider = self.context.get_provider_by_id(self.provider_id)
        client = getattr(provider, "client", None)
        if not isinstance(client, AsyncOpenAI):
            return "联网搜索没有配置好：插件设置里要选一个 OpenAI 兼容类型的模型。"
        now = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M")
        effort = {"reasoning_effort": self.reasoning_effort} if self.reasoning_effort else {}
        try:
            resp = await client.chat.completions.create(
                model=provider.get_model(),
                messages=[
                    {"role": "system", "content": SEARCH_PROMPT.format(now=now)},
                    {"role": "user", "content": query},
                ],
                extra_body={"web_search_options": {}},
                timeout=self.timeout,
                **effort,
            )
        except Exception as exc:
            logger.warning(f"联网搜索失败：{exc}")
            return f"联网搜索失败：{exc}"
        return (resp.choices[0].message.content or "").strip() or "联网搜索没有返回内容。"

    @filter.llm_tool(name="search_pixiv")
    async def search_pixiv(self, event: AstrMessageEvent, keyword: str):
        """在 Pixiv 上搜插画，搜到的几张会直接给你看。想找图、找头像、看别人画的某个题材时用。看中的用 send_pixiv_image 发给对方；要拿来换头像就用它的原图地址。

        Args:
            keyword(string): Pixiv 上的标签，多个用空格隔开，比如「明日方舟 夕」「水墨 山水」
        """
        word = keyword.strip()
        try:
            async with aiohttp.ClientSession(headers=PIXIV_HEADERS, timeout=aiohttp.ClientTimeout(total=self.timeout)) as session:
                async with session.get(f"{PIXIV}/ajax/search/artworks/{urllib.parse.quote(word)}", params={
                        "word": word, "order": "date_d", "mode": "safe", "p": 1, "s_mode": "s_tag", "type": "illust"}) as resp:
                    arts = (await resp.json(content_type=None))["body"]["illustManga"]["data"]
                arts = [a for a in arts if a.get("url") and not a.get("xRestrict") and not (self.skip_ai and a.get("aiType") == 2)]
                random.shuffle(arts)
                # 最新一页常被一个画师的连载占满，每个画师先挑一张，不够再补
                firsts = list({a.get("userId"): a for a in reversed(arts)}.values())
                picks = (firsts + [a for a in arts if a not in firsts])[:self.image_count]
                images = await asyncio.gather(*(self._download(session, pixiv_image(a["url"], "c/540x540_70")) for a in picks),
                                              return_exceptions=True)
        except Exception as exc:
            logger.warning(f"Pixiv 搜索失败：{exc}")
            return f"Pixiv 搜索失败：{exc}"
        arts = [a for a, data in zip(picks, images) if isinstance(data, bytes)]
        if not arts:
            return f"在 Pixiv 上没搜到「{word}」的插画，换个标签再试。"
        self.shown[event.unified_msg_origin] = arts
        # 原图地址走 i.pixiv.re 镜像：不查来源，换头像之类的插件能直接下载
        lines = [f"{n}.《{a.get('title', '')}》画师 {a.get('userName', '')}｜原图 {pixiv_image(a['url']).replace('i.pximg.net', 'i.pixiv.re')}"
                 f"｜作品页 {PIXIV}/artworks/{a['id']}" for n, a in enumerate(arts, 1)]
        head = f"在 Pixiv 搜「{word}」，挑了 {len(arts)} 张给你看，编号和下面图的顺序一致；想给对方看哪张就用 send_pixiv_image：\n"
        shown = [ImageContent(type="image", data=base64.b64encode(data).decode(), mimeType="image/jpeg")
                 for data in images if isinstance(data, bytes)]
        return CallToolResult(content=[TextContent(type="text", text=head + "\n".join(lines)), *shown])

    @filter.llm_tool(name="send_pixiv_image")
    async def send_pixiv_image(self, event: AstrMessageEvent, number: int):
        """把刚才用 search_pixiv 搜到的第几张发给对方看（发大图）。

        Args:
            number(number): 图的编号，就是搜索结果里的序号
        """
        arts = self.shown.get(event.unified_msg_origin) or []
        if not 1 <= int(number) <= len(arts):
            return f"没有第 {number} 张：这次对话里还没搜过，或者编号超出了 1～{len(arts)}。"
        art = arts[int(number) - 1]
        try:
            async with aiohttp.ClientSession(headers=PIXIV_HEADERS, timeout=aiohttp.ClientTimeout(total=self.timeout)) as session:
                data = await self._download(session, pixiv_image(art["url"]))
        except Exception as exc:
            return f"大图没下载下来：{exc}"
        await event.send(MessageChain(chain=[Image.fromBase64(base64.b64encode(data).decode())]))
        return f"已经把第 {number} 张《{art.get('title', '')}》（画师 {art.get('userName', '')}）发给对方了。"

    @staticmethod
    async def _download(session: aiohttp.ClientSession, url: str) -> bytes:
        async with session.get(url) as resp:
            resp.raise_for_status()
            return await resp.read()
