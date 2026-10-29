"""联网搜索：聊天模型要查资料时，交给另一个自带联网搜索的模型去搜，顶替 AstrBot 自带的网页搜索。"""

from datetime import datetime
from zoneinfo import ZoneInfo

from openai import AsyncOpenAI

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
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


class GeminiSearchPlugin(Star):
    def __init__(self, context: Context, config: dict):
        super().__init__(context)
        self.provider_id = str(config.get("provider_id", "")).strip()
        self.replace_builtin = bool(config.get("replace_builtin_search", True))
        self.timeout = float(config.get("timeout", 60))
        self.reasoning_effort = str(config.get("reasoning_effort", "low")).strip()

    async def initialize(self):
        # 当前 AstrBot 从注释生成参数，但未填写 required。
        tool = self.context.get_llm_tool_manager().get_func("online_search")
        if tool:
            tool.parameters["required"] = ["query"]

    @filter.on_llm_request()
    async def drop_builtin_search(self, event: AstrMessageEvent, req):
        if self.replace_builtin and req.func_tool:
            for name in BUILTIN_SEARCH_TOOLS:
                req.func_tool.remove_tool(name)

    @filter.llm_tool(name="online_search")
    async def online_search(self, event: AstrMessageEvent, query: str):
        """联网搜索。遇到下面这些情况先搜再答，不要装作知道，也不要只回一句没印象：需要实时信息，比如新闻、天气、比赛结果、游戏活动和版本更新、价格；拿不准的事实；不认识的人名、角色、作品、游戏内容、梗和新词。常识和本来就清楚的内容不用搜。

        Args:
            query(string): 要查的问题，用一句完整的话写清楚要找什么，带上时间、地点、作品名等必要信息。
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
