"""AI（OpenAI 兼容接口）：自然语言形态描述 -> 条件整理 -> 筛选 DSL(JSON)。

默认使用 DeepSeek，也可在「AI 设置」中改用 OpenAI、通义千问、智谱等
任意兼容 OpenAI chat/completions 协议的服务。
"""
from __future__ import annotations

import json

import requests

API_URL = "https://api.deepseek.com/chat/completions"
DEFAULT_MODEL = "deepseek-chat"
VISION_MODEL = "deepseek-flash"   # 支持图片输入的模型

# 常用服务商预设：(接口地址, 默认模型)
AI_PRESETS = {
    "DeepSeek": (API_URL, DEFAULT_MODEL),
    "OpenAI": ("https://api.openai.com/v1/chat/completions", "gpt-4o-mini"),
    "通义千问（兼容模式）": (
        "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
        "qwen-plus"),
    "智谱 GLM": ("https://open.bigmodel.cn/api/paas/v4/chat/completions",
                 "glm-4-flash"),
    "月之暗面 Kimi": ("https://api.moonshot.cn/v1/chat/completions",
                      "moonshot-v1-8k"),
    "自定义": ("", ""),
}


def normalize_url(url: str) -> str:
    """把用户填写的接口地址补全为完整的 chat/completions 地址。"""
    url = (url or "").strip()
    if not url:
        return API_URL
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    url = url.rstrip("/")
    if url.endswith("/chat/completions"):
        return url
    return url + "/chat/completions"


def _user_content(text: str, image_b64: str = ""):
    """有图时构造 OpenAI 兼容的多模态 content 数组。"""
    if not image_b64:
        return text
    blocks = []
    if text.strip():
        blocks.append({"type": "text", "text": text})
    blocks.append({"type": "image_url",
                   "image_url": {"url": f"data:image/png;base64,{image_b64}"}})
    return blocks

INTERPRET_PROMPT = """你是A股K线形态量化助手。用户用自然语言描述一种K线形态，你的任务是：
用自己的语言把用户的选股条件重新整理成清晰、无歧义、可量化的条件清单，供用户检查和修改。

要求：
- 不要输出任何公式、代码或 JSON，只用中文分条描述。
- 按K线顺序描述（如：前一日 / 命中日），每条写清楚：阴阳、实体/影线大小（用百分比量化）、价格与均线关系、成交量/成交额条件。
- 用户描述模糊的地方，给出合理的量化默认值并标注（如"放量"默认为成交量≥前一日2倍）。
- 最后列一句"以上条件如有不妥可直接修改"。
- 控制在 12 行以内。"""

SYSTEM_PROMPT = """你是A股K线形态量化助手。用户用自然语言描述一种K线形态，你把它转换成如下 JSON 筛选条件（只输出 JSON，不要输出任何其他文字）：

{
  "name": "形态名称",
  "rules": [
    {"offset": -1, "expr": "close < open"},
    {"offset": 0, "expr": "close > open"}
  ]
}

规则说明：
- offset=0 表示形态的最后一根K线（即命中日/信号日），负数表示向前数第几根。offset 必须 <= 0。
- expr 是 Python 布尔表达式，在该 offset 对应的K线上求值。
- 可用字段（当日值）：open 开盘, high 最高, low 最低, close 收盘, volume 成交量(手), amount 成交额, pct 涨跌幅(%), turn 换手率(%), ma5, ma10, ma20, ma30（对应周期收盘均线）。
- 可用函数：ref(name, k) 表示相对当前K线第 k 根（k 为负表示向前）的字段值，例如 ref(close,-1) 是前一日收盘价；min(a,b)、max(a,b)、abs(a)。
- 用 and/or 组合多个条件；数值比较用 >、>=、<、<=、==。
- 常用的量：实体大小 abs(close-open)、上影线 high-max(open,close)、下影线 min(open,close)-low、涨幅 close/ref(close,-1)-1。

示例：
用户: 阳包阴
输出: {"name":"阳包阴","rules":[{"offset":-1,"expr":"close < open"},{"offset":0,"expr":"close > open and close >= ref(open,-1) and open <= ref(close,-1)"}]}

用户: 放量突破20日均线
输出: {"name":"放量突破20日线","rules":[{"offset":-1,"expr":"close <= ma20"},{"offset":0,"expr":"close > ma20 and volume >= 2 * ref(volume,-1)"}]}

用户: 早晨之星
输出: {"name":"早晨之星","rules":[{"offset":-2,"expr":"close < open and abs(close-open) >= 0.03 * ref(close,-1)"},{"offset":-1,"expr":"abs(close-open) <= 0.3 * abs(ref(close,-1)-ref(open,-1))"},{"offset":0,"expr":"close > open and close >= (ref(open,-2)+ref(close,-2))/2"}]}

只输出合法 JSON，不要 markdown 代码块，不要解释。"""


def sanitize_key(api_key: str) -> str:
    """清理 API Key：去掉换行/空白及误粘的多余文字（取第一段非空白）。"""
    return (api_key or "").split()[0] if api_key and api_key.split() else ""


def _chat(api_key: str, system: str, user, model: str,
          timeout: float, json_mode: bool, base_url: str = "") -> str:
    api_key = sanitize_key(api_key)
    if not api_key:
        raise RuntimeError("未设置 API Key")
    url = normalize_url(base_url)
    model = (model or "").strip() or DEFAULT_MODEL
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": 0.1,
        "stream": False,
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    try:
        resp = requests.post(
            url, json=payload, timeout=timeout,
            headers={"Authorization": f"Bearer {api_key}",
                     "Content-Type": "application/json"},
        )
    except requests.RequestException as e:
        raise RuntimeError(f"网络请求失败: {e}")
    if resp.status_code != 200:
        raise RuntimeError(f"AI 接口返回 {resp.status_code}: {resp.text[:300]}")
    try:
        return resp.json()["choices"][0]["message"]["content"]
    except (KeyError, IndexError) as e:
        raise RuntimeError(f"解析 AI 响应失败: {e}")


def interpret_description(api_key: str, description: str, image_b64: str = "",
                          model: str = DEFAULT_MODEL,
                          timeout: float = 60.0, base_url: str = "") -> str:
    """第一步：让 AI 用自己的语言整理用户条件（纯文本，供用户修改确认）。"""
    if image_b64:
        model = VISION_MODEL
        if not description.strip():
            description = "请分析这张K线截图，整理出图中所示形态的选股条件。"
    text = _chat(api_key, INTERPRET_PROMPT,
                 _user_content(description, image_b64), model, timeout,
                 json_mode=False, base_url=base_url)
    text = text.strip()
    if not text:
        raise RuntimeError("AI 未返回条件整理")
    return text


def generate_pattern(api_key: str, description: str, confirmed: str = "",
                     image_b64: str = "", model: str = DEFAULT_MODEL,
                     timeout: float = 60.0, base_url: str = "") -> dict:
    """第二步：把（用户确认后的）条件转换成 DSL dict。失败抛 RuntimeError。"""
    if image_b64:
        model = VISION_MODEL
    user = description or "请根据截图中的K线形态生成条件。"
    if confirmed.strip():
        user += "\n\n用户已确认的条件整理（以此为准）：\n" + confirmed.strip()
    try:
        content = _chat(api_key, SYSTEM_PROMPT,
                        _user_content(user, image_b64), model, timeout,
                        json_mode=True, base_url=base_url)
    except RuntimeError as e:
        # 视觉模型若不支持 response_format 则降级为纯文本再提取 JSON
        if image_b64 and "400" in str(e):
            content = _chat(api_key, SYSTEM_PROMPT,
                            _user_content(user, image_b64), model, timeout,
                            json_mode=False, base_url=base_url)
            content = (content.strip().removeprefix("```json")
                       .removeprefix("```").removesuffix("```").strip())
        else:
            raise
    try:
        spec = json.loads(content)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"解析 AI 响应失败: {e}")
    if not isinstance(spec, dict) or not spec.get("rules"):
        raise RuntimeError("AI 返回的内容不是有效条件 JSON")
    return spec


def fix_pattern(api_key: str, description: str, bad_content: str,
                error: str, model: str = DEFAULT_MODEL,
                timeout: float = 60.0, base_url: str = "") -> dict:
    """纠错：把出错的条件 JSON 和错误信息发回 AI，返回修正后的 DSL dict。"""
    user = (f"原始需求描述：{description or '（无）'}\n\n"
            f"下面这个条件 JSON 有错误，请修正它，保持原有选股语义不变：\n"
            f"{bad_content}\n\n"
            f"错误信息：{error}\n\n"
            f"只输出修正后的完整合法条件 JSON。")
    content = _chat(api_key, SYSTEM_PROMPT, user, model, timeout,
                    json_mode=True, base_url=base_url)
    try:
        spec = json.loads(content)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"解析 AI 响应失败: {e}")
    if not isinstance(spec, dict) or not spec.get("rules"):
        raise RuntimeError("AI 返回的内容不是有效条件 JSON")
    return spec
