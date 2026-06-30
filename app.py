import base64
import hashlib
import json
import re
from io import BytesIO

import pandas as pd
import streamlit as st
import plotly.express as px
from openai import OpenAI
from PIL import Image


# =========================
# 基础设置
# =========================

st.set_page_config(
    page_title="Ivy 的小红书春节洞察工作台",
    page_icon="🧧",
    layout="wide",
)

REQUIRED_COLUMNS = [
    "编号", "搜索关键词", "内容链接", "笔记标题", "作者昵称", "作者类型", "发布时间",
    "点赞数", "收藏数", "评论数", "分享数",
    "高赞评论摘录", "评论内赞量", "评论内互动量",
    "二创潜力", "二创方向", "品牌能否自然下场", "一句话洞察",
    "截图识别备注", "screenshot_post_id", "post_id"
]

POST_LEVEL_COLUMNS = [
    "搜索关键词", "内容链接", "笔记标题", "作者昵称", "作者类型", "发布时间",
    "点赞数", "收藏数", "评论数", "分享数", "截图识别备注"
]


# =========================
# AI 配置与调用
# =========================

AI_NOT_CONFIGURED_MESSAGE = "未配置 AI，当前使用规则版分析。"
AI_ERROR_MESSAGE = "AI 调用失败，请检查 API Key、Base URL、模型名、免费额度开关或网络状态。"
MONICA_ERROR_MESSAGE = "Monica 调用失败，请检查 API Key、Base URL、模型名、账户余额、额度限制或网络状态。"
ALIYUN_TEXT_ERROR_MESSAGE = "阿里云百炼调用失败，请检查 API Key、Base URL、模型名、免费额度开关或网络状态。"
AI_COST_TIP = "高级洞察会优先使用 Monica 文本模型；未配置 Monica 时回退到阿里云百炼文本模型，全部不可用时使用规则版分析。"
VISION_NOT_CONFIGURED_MESSAGE = "未配置视觉模型，当前无法使用截图识别。"
VISION_ERROR_MESSAGE = "截图识别失败，请检查视觉模型配置（API Key、Base URL、视觉模型名、免费额度开关）或图片大小。"
TEXT_INSIGHT_ERROR_MESSAGE = "AI 洞察生成失败，请检查 Monica 或阿里云文本模型配置、额度或网络状态。"
VISION_COST_TIP = "截图识别会消耗阿里云百炼视觉模型额度。建议先使用免费额度，并开启免费额度用完即停。每次建议上传 1-5 张截图。"


def get_secret_value(name: str, default=None):
    """从 Streamlit secrets 读取配置；未配置 secrets.toml 时保持静默降级。"""
    try:
        return st.secrets.get(name, default)
    except Exception:
        return default


def is_ai_enabled() -> bool:
    value = get_secret_value("ENABLE_AI_FEATURES", False)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    return bool(value)


def is_monica_insight_enabled() -> bool:
    value = get_secret_value("USE_MONICA_FOR_INSIGHT", False)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    return bool(value)


def get_ai_config() -> dict:
    config = {
        "provider": "aliyun",
        "enabled": is_ai_enabled(),
        "api_key": get_secret_value("ALIYUN_API_KEY", ""),
        "base_url": get_secret_value("ALIYUN_BASE_URL", ""),
        "model": get_secret_value("ALIYUN_TEXT_MODEL", ""),
    }
    config["available"] = all([config["enabled"], config["api_key"], config["base_url"], config["model"]])
    config["disabled_reason"] = "" if config["available"] else "阿里云文本模型配置不完整或 AI 功能未启用。"
    return config


def get_insight_model_config() -> dict:
    if not is_ai_enabled():
        return {"provider": "rule", "api_key": "", "base_url": "", "model": "", "available": False, "disabled_reason": "ENABLE_AI_FEATURES=false，当前使用规则版分析。", "use_monica": is_monica_insight_enabled()}

    use_monica = is_monica_insight_enabled()
    monica_config = {
        "provider": "monica",
        "api_key": get_secret_value("MONICA_API_KEY", ""),
        "base_url": get_secret_value("MONICA_BASE_URL", "https://openapi.monica.im/v1"),
        "model": get_secret_value("MONICA_TEXT_MODEL", "gpt-4o"),
        "use_monica": use_monica,
    }
    monica_available = all([monica_config["api_key"], monica_config["base_url"], monica_config["model"]])
    if use_monica and monica_available:
        return {**monica_config, "available": True, "disabled_reason": ""}

    aliyun_config = get_ai_config()
    if aliyun_config["available"]:
        reason = "Monica 未启用。" if not use_monica else "Monica 配置不完整，已回退到阿里云百炼文本模型。"
        return {**aliyun_config, "provider": "aliyun", "use_monica": use_monica, "disabled_reason": reason}

    reason = "Monica 未启用，且阿里云文本模型不可用。" if not use_monica else "Monica 配置不完整，且阿里云文本模型不可用。"
    return {"provider": "rule", "api_key": "", "base_url": "", "model": "", "available": False, "disabled_reason": reason, "use_monica": use_monica}


def get_vision_config() -> dict:
    config = {
        "enabled": is_ai_enabled(),
        "api_key": get_secret_value("ALIYUN_API_KEY", ""),
        "base_url": get_secret_value("ALIYUN_BASE_URL", ""),
        "model": get_secret_value("ALIYUN_VISION_MODEL", ""),
        "text_model": get_secret_value("ALIYUN_TEXT_MODEL", ""),
    }
    config["available"] = all([config["enabled"], config["api_key"], config["base_url"], config["model"]])
    return config


def compact_comments(comments_text: str, limit: int = 12) -> str:
    comments = [line.strip() for line in str(comments_text).splitlines() if line.strip()]
    return "\n".join(f"- {comment}" for comment in comments[:limit])


def post_context(row: pd.Series, brand_name: str, campaign_name: str) -> str:
    return f"""
当前品牌名称：{brand_name}
当前项目名称：{campaign_name}
搜索关键词：{row.get("搜索关键词", "")}
笔记标题：{row.get("笔记标题", "")}
笔记正文 / 内容链接中的可用文本：{row.get("内容链接", "")}
高赞评论列表：
{compact_comments(row.get("全部高赞评论", ""))}
现有规则版洞察主题：{row.get("洞察主题", "")}
现有一句话洞察：{row.get("一句话洞察", "")}
规则版品牌机会判断：{row.get("品牌能否自然下场", "")}
规则版风险等级：{row.get("风险等级", "")}
规则版二创方向：{row.get("二创方向", "")}
""".strip()


def call_insight_text_model(system_prompt: str, user_prompt: str, config: dict) -> str:
    if config.get("provider") == "rule" or not config.get("available"):
        return ""
    try:
        client = OpenAI(api_key=config["api_key"], base_url=config["base_url"])
        response = client.chat.completions.create(
            model=config["model"],
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.65,
        )
        return response.choices[0].message.content or ""
    except Exception as exc:
        if config.get("provider") == "monica":
            raise RuntimeError(MONICA_ERROR_MESSAGE) from exc
        raise RuntimeError(ALIYUN_TEXT_ERROR_MESSAGE) from exc


def call_ai_model(system_prompt: str, user_prompt: str, config: dict) -> str:
    return call_insight_text_model(system_prompt, user_prompt, config)


def generate_ai_insight(row: pd.Series, brand_name: str, campaign_name: str, config: dict) -> str:
    comments_count = len([line for line in str(row.get("全部高赞评论", "")).splitlines() if line.strip()])
    sample_warning = "评论样本较少，洞察仅供初筛。" if comments_count < 3 else ""
    system_prompt = "你是一位资深中国品牌策略同事，擅长从小红书春节内容和高赞评论里判断品牌怎么接才自然。请用中文输出，适合中国品牌营销语境。必须像策略同事一样给出取舍、证据和风险，不要像数据分析师，不要写‘增强情感连接’‘打造节日氛围’‘结合春节元素’这类空泛套话，不要强行硬贴产品，不要把品牌机会写成广告口号。"
    user_prompt = f"""
请基于以下材料，优化成一条更像资深品牌策略同事写出的洞察。

{post_context(row, brand_name, campaign_name)}
{sample_warning}

重点判断：评论区到底接住了什么情绪/欲望/生活想象；高赞评论为什么会被赞；背后的消费者洞察；伊利/牛奶作为春节品牌能不能自然下场、怎么接才不尴尬；哪些方向不要碰；风险是什么；可以给代理公司什么创意任务；哪些内容可以变成小红书话题、评论区互动或二创机制。

请严格使用以下 Markdown 结构输出，每个标题下给出具体判断：
## 评论区接住了什么
## 高赞评论为什么成立
## 一句话消费者洞察
## 品牌机会判断
## 品牌下场理由
## 不建议硬接的原因
## 风险提醒
## 可转化为品牌内容的方向
## 可二创 / 评论区互动方向
## 给代理公司的 brief 任务提示
""".strip()
    return call_ai_model(system_prompt, user_prompt, config)


def generate_ai_brief(row: pd.Series, brand_name: str, campaign_name: str, config: dict) -> str:
    comments_count = len([line for line in str(row.get("全部高赞评论", "")).splitlines() if line.strip()])
    sample_warning = "评论样本较少，洞察仅供初筛。" if comments_count < 3 else ""
    system_prompt = "你是一位资深中国品牌策略与创意 brief 负责人。请把小红书帖子和评论区情绪转成给代理公司可执行的中文 brief。语气专业、具体、克制，说明品牌怎么接才不尴尬、哪些方向不要碰、风险和创意任务是什么；不要空泛口号，不要强行硬贴产品。"
    user_prompt = f"""
请基于以下材料，生成更完整的 AI brief。

{post_context(row, brand_name, campaign_name)}
{sample_warning}

请把输出写得像给代理公司开的策略任务，而不是泛泛总结；参考消费者原话必须来自材料，不足就说明不足。

请严格使用以下 Markdown 结构输出：
## 背景观察
## 消费者洞察
## 品牌机会
## 创意任务
## 必须避免
## 可探索方向
## 评论区互动机制
## 参考消费者原话
## 给代理公司的任务说明
""".strip()
    return call_ai_model(system_prompt, user_prompt, config)




# =========================
# V1.2 截图识别工具函数
# =========================

SCREENSHOT_STRUCT_FIELDS = [
    "搜索关键词", "内容链接", "笔记标题", "笔记正文", "作者昵称", "作者类型", "发布时间",
    "点赞数", "收藏数", "评论数", "分享数", "高赞评论列表", "补充说明", "截图识别备注", "识别置信度", "可能不确定字段"
]

SCREENSHOT_INSIGHT_FIELDS = [
    "评论区接住了什么", "高赞评论为什么成立", "一句话消费者洞察", "品牌机会判断", "品牌下场理由",
    "不建议硬接的原因", "风险提醒", "可转化为品牌内容的方向", "可二创/评论区互动方向", "给代理公司的brief任务提示"
]


def image_to_data_url(uploaded_image, max_side: int = 1600, quality: int = 82) -> str:
    """压缩上传截图并转为 data URL；仅在当前会话内存中处理，不落盘。"""
    uploaded_image.seek(0)
    image = Image.open(uploaded_image)
    image = image.convert("RGB")
    image.thumbnail((max_side, max_side))
    output = BytesIO()
    image.save(output, format="JPEG", quality=quality, optimize=True)
    encoded = base64.b64encode(output.getvalue()).decode("utf-8")
    return f"data:image/jpeg;base64,{encoded}"


def parse_json_from_model_output(text: str):
    """容错解析模型输出：先直接解析，再抽取首尾 JSON 片段。"""
    if not text:
        return None
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    start_candidates = [i for i in [cleaned.find("{"), cleaned.find("[")] if i >= 0]
    if not start_candidates:
        return None
    start = min(start_candidates)
    end = max(cleaned.rfind("}"), cleaned.rfind("]"))
    if end <= start:
        return None
    try:
        return json.loads(cleaned[start:end + 1])
    except json.JSONDecodeError:
        return None


def normalize_screenshot_result(parsed: dict, keyword: str, manual_link: str, note: str = "") -> dict:
    """兼容模型返回扁平 JSON 或旧版嵌套 JSON；V1.2.1 只保留 OCR 结构化结果。"""
    parsed = parsed or {}
    structured = parsed.get("截图结构化识别") or parsed.get("结构化信息") or parsed.get("post") or parsed

    structured = {field: structured.get(field, "") for field in SCREENSHOT_STRUCT_FIELDS}
    structured["搜索关键词"] = structured.get("搜索关键词") or keyword or "春节"
    structured["内容链接"] = manual_link or structured.get("内容链接", "")
    structured["补充说明"] = note or structured.get("补充说明", "")
    comments = structured.get("高赞评论列表")
    structured["高赞评论列表"] = comments if isinstance(comments, list) else []
    if isinstance(structured.get("可能不确定字段"), list):
        structured["可能不确定字段"] = "、".join(str(item) for item in structured["可能不确定字段"] if item)

    return {"截图结构化识别": structured, "品牌洞察初稿": {field: "" for field in SCREENSHOT_INSIGHT_FIELDS}}


def build_screenshot_prompt(keyword: str, manual_link: str, note: str, brand_name: str, campaign_name: str) -> str:
    return f"""
你将看到用户主动上传的小红书笔记正文截图、评论区截图或高赞评论截图。请不要访问链接、不要抓取网页、不要假设截图之外的信息。

请只完成 OCR 和结构化识别：从截图中提取结构化信息；截图里没有的字段填空字符串，不要编造。
不要生成品牌洞察、品牌机会、创意建议或营销判断。

用户补充信息：
- 搜索关键词：{keyword or '春节'}
- 手动补充内容链接：{manual_link or ''}
- 补充说明：{note or ''}
- 品牌名称：{brand_name}
- 项目名称：{campaign_name}

输出要求：只输出严格 JSON，不要输出 Markdown，不要加解释。JSON 顶层只包含“截图结构化识别”。字段如下：
{{
  "截图结构化识别": {{
    "搜索关键词": "",
    "内容链接": "",
    "笔记标题": "",
    "笔记正文": "",
    "作者昵称": "",
    "作者类型": "",
    "发布时间": "",
    "点赞数": "",
    "收藏数": "",
    "评论数": "",
    "分享数": "",
    "高赞评论列表": [{{"评论文本": "", "点赞数": "", "评论者昵称": ""}}],
    "截图识别备注": "",
    "识别置信度": "",
    "可能不确定字段": ""
  }}
}}

识别要求：高赞评论必须尽量逐条 OCR；看不清就写入“可能不确定字段”和“截图识别备注”。不要因为评论少而补写不存在的评论。
""".strip()


def call_vision_model(uploaded_images, keyword: str, manual_link: str, note: str, brand_name: str, campaign_name: str, config: dict) -> str:
    client = OpenAI(api_key=config["api_key"], base_url=config["base_url"])
    content = [{"type": "text", "text": build_screenshot_prompt(keyword, manual_link, note, brand_name, campaign_name)}]
    for uploaded_image in uploaded_images:
        content.append({"type": "image_url", "image_url": {"url": image_to_data_url(uploaded_image)}})

    response = client.chat.completions.create(
        model=config["model"],
        messages=[{"role": "user", "content": content}],
        temperature=0.2,
        response_format={"type": "json_object"},
    )
    return response.choices[0].message.content or ""



def build_confirmed_screenshot_insight_prompt(structured: dict, brand_name: str, campaign_name: str) -> str:
    comments = structured.get("高赞评论列表") or []
    comment_lines = []
    for idx, comment in enumerate(comments, start=1):
        if isinstance(comment, dict):
            text = comment.get("评论文本", "")
            likes = comment.get("点赞数", "")
            nickname = comment.get("评论者昵称", "")
            comment_lines.append(f"{idx}. {text}（赞：{likes}；评论者：{nickname}）")
        else:
            comment_lines.append(f"{idx}. {comment}")
    sample_warning = "评论样本较少，洞察仅供初筛。" if len([c for c in comment_lines if c.strip()]) < 3 else ""
    return f"""
请基于用户已确认/编辑过的截图识别结果，输出像资深品牌策略同事的判断。

品牌名称：{brand_name}
项目名称：{campaign_name}
搜索关键词：{structured.get('搜索关键词', '')}
内容链接：{structured.get('内容链接', '')}
笔记标题：{structured.get('笔记标题', '')}
笔记正文：{structured.get('笔记正文', '')}
作者昵称：{structured.get('作者昵称', '')}
作者类型：{structured.get('作者类型', '')}
发布时间：{structured.get('发布时间', '')}
互动数据：点赞 {structured.get('点赞数', '')} / 收藏 {structured.get('收藏数', '')} / 评论 {structured.get('评论数', '')} / 分享 {structured.get('分享数', '')}
高赞评论：
{chr(10).join(comment_lines) or '（未识别到高赞评论）'}
补充说明：{structured.get('补充说明', '')}
{sample_warning}

必须重点回答：评论区到底接住了什么情绪/欲望/生活想象；高赞评论为什么会被赞；这条内容背后的消费者洞察是什么；伊利/牛奶作为春节品牌能不能自然下场；怎么接才不尴尬；哪些方向不要碰；能给代理公司什么创意任务。

不要泛化：如果评论较少或评论信息不足，必须明确提示“评论样本较少，洞察仅供初筛”，不要编造复杂结论。不要输出“增强情感连接”“打造春节氛围”“结合节日元素”等空泛内容，除非能具体说明来自哪条评论或正文。

请严格使用以下 Markdown 结构输出：
## 评论区接住了什么
## 高赞评论为什么成立
## 一句话消费者洞察
## 品牌机会判断
## 品牌下场理由
## 不建议硬接的原因
## 风险提醒
## 可转化为品牌内容的方向
## 可二创/评论区互动方向
## 给代理公司的 brief 任务提示
""".strip()


def generate_confirmed_screenshot_insight(structured: dict, brand_name: str, campaign_name: str, config: dict) -> str:
    system_prompt = "你是一位资深中国品牌策略同事。你只基于用户确认过的标题、正文、高赞评论和补充说明做判断；证据不足就明确说不足。请用中文输出，具体、克制、有取舍，不写空泛广告套话，不强行硬贴产品。"
    return call_ai_model(system_prompt, build_confirmed_screenshot_insight_prompt(structured, brand_name, campaign_name), config)

def make_stable_post_id(prefix: str, *parts) -> str:
    """用稳定哈希生成内部 post_id，避免不同来源的帖子被混在一起。"""
    raw = "||".join(str(part or "").strip() for part in parts)
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]
    return f"{prefix}_{digest}"


def screenshot_result_to_dataframe(result: dict) -> pd.DataFrame:
    structured = result.get("截图结构化识别", {})
    comments = structured.get("高赞评论列表") or [{"评论文本": "", "点赞数": "", "评论者昵称": ""}]
    rows = []
    interaction = "\n".join([
        f"{structured.get('点赞数', '')}赞" if structured.get("点赞数") else "",
        f"{structured.get('收藏数', '')}收藏" if structured.get("收藏数") else "",
        f"{structured.get('评论数', '')}评论" if structured.get("评论数") else "",
        f"{structured.get('分享数', '')}分享" if structured.get("分享数") else "",
    ]).strip()
    screenshot_post_id = make_stable_post_id(
        "screenshot",
        structured.get("内容链接", ""),
        structured.get("笔记标题", ""),
        json.dumps(comments, ensure_ascii=False, sort_keys=True),
    )

    for idx, comment in enumerate(comments):
        rows.append({
            "编号": f"截图识别-{screenshot_post_id}" if idx == 0 else None,
            "搜索关键词": structured.get("搜索关键词", "") if idx == 0 else None,
            "内容链接": structured.get("内容链接", "") if idx == 0 else None,
            "笔记标题": structured.get("笔记标题", "") if idx == 0 else None,
            "作者类型": structured.get("作者类型", "") if idx == 0 else None,
            "发布时间": structured.get("发布时间", "") if idx == 0 else None,
            "高赞评论摘录": comment.get("评论文本", "") if isinstance(comment, dict) else str(comment),
            "评论内赞量": comment.get("点赞数", "") if isinstance(comment, dict) else "",
            "评论内互动量": interaction if idx == 0 else None,
            "二创潜力": None,
            "二创方向": None,
            "品牌能否自然下场": None,
            "一句话洞察": None,
            "截图识别备注": structured.get("截图识别备注", "") if idx == 0 else None,
            "作者昵称": structured.get("作者昵称", "") if idx == 0 else None,
            "笔记正文": structured.get("笔记正文", "") if idx == 0 else None,
            "评论者昵称": comment.get("评论者昵称", "") if isinstance(comment, dict) else "",
            "screenshot_post_id": screenshot_post_id,
            "post_id": screenshot_post_id,
        })
    return pd.DataFrame(rows)


# =========================
# 工具函数：数据读取/清洗
# =========================

@st.cache_data(show_spinner=False)
def load_sample_data() -> pd.DataFrame:
    """读取项目自带的示例数据。"""
    return pd.read_csv("sample_data/spring_festival_sample.csv")


def read_uploaded_file(uploaded_file) -> pd.DataFrame:
    """支持 xlsx / xls / csv 上传。"""
    if uploaded_file is None:
        return load_sample_data()

    name = uploaded_file.name.lower()
    if name.endswith(".csv"):
        return pd.read_csv(uploaded_file)
    if name.endswith((".xlsx", ".xls")):
        return pd.read_excel(uploaded_file)

    raise ValueError("目前只支持上传 CSV / Excel 文件。")


def normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    """把列名首尾空格去掉，并补齐缺失列，避免小白上传表格时报错。"""
    df = df.copy()
    df.columns = [str(c).strip() for c in df.columns]
    for col in REQUIRED_COLUMNS:
        if col not in df.columns:
            df[col] = None
    return df


def is_non_empty(value) -> bool:
    if pd.isna(value):
        return False
    return str(value).strip() != ""


def parse_wan_number(value) -> float:
    """
    把小红书常见数字转成数值：
    9.9w -> 99000
    10w+ -> 100000
    9000 -> 9000
    """
    if pd.isna(value):
        return 0.0

    text = str(value).lower().replace(",", "").replace("＋", "+").strip()
    if not text:
        return 0.0

    match = re.search(r"(\d+(?:\.\d+)?)\s*w", text)
    if match:
        return float(match.group(1)) * 10000

    match = re.search(r"(\d+(?:\.\d+)?)", text)
    if match:
        return float(match.group(1))

    return 0.0


def extract_metric(text, metric_name: str) -> float:
    """
    从“3000评论\n5.2w赞”里提取评论/赞等。
    metric_name 可以是：赞、评论、收藏、分享。
    """
    if pd.isna(text):
        return 0.0
    t = str(text).lower().replace(",", "")
    pattern = rf"(\d+(?:\.\d+)?\s*w?\+?)\s*{metric_name}"
    match = re.search(pattern, t)
    if not match:
        return 0.0
    return parse_wan_number(match.group(1))


def extract_url(text) -> str:
    if pd.isna(text):
        return ""
    match = re.search(r"https?://\S+", str(text))
    return match.group(0) if match else ""


def extract_title_from_link_text(text) -> str:
    """
    有些表格没有填“笔记标题”，但“内容链接”里是：
    春节三次上门喂猫... http://xhslink...
    这里自动截取 http 前面的文字当标题。
    """
    if pd.isna(text):
        return ""
    raw = str(text).strip()
    raw = re.sub(r"\s+", " ", raw)
    raw = re.split(r"https?://", raw)[0].strip()
    raw = raw.replace("复制这段文字，打开【小红书】一键直达笔记。", "")
    raw = raw.replace("把文字复制下来，打开【小红书】查看详情。", "")
    raw = raw.replace("快戳【小红书】瞧瞧这篇笔记！", "")
    return raw.strip()


def assign_post_ids(df: pd.DataFrame) -> pd.Series:
    """按明确边界生成稳定 post_id，避免截图识别数据污染旧数据。"""
    post_ids = []
    current_post_id = None
    for row_idx, row in df.iterrows():
        if is_non_empty(row.get("post_id")):
            current_post_id = str(row.get("post_id")).strip()
        elif is_non_empty(row.get("screenshot_post_id")):
            current_post_id = str(row.get("screenshot_post_id")).strip()
        elif is_non_empty(row.get("编号")):
            current_post_id = f"编号:{str(row.get('编号')).strip()}"
        elif is_non_empty(row.get("内容链接")):
            current_post_id = f"链接:{extract_url(row.get('内容链接')) or str(row.get('内容链接')).strip()}"
        elif current_post_id is None:
            current_post_id = f"row:{row_idx}"
        post_ids.append(current_post_id)
    return pd.Series(post_ids, index=df.index, dtype="string")


def clean_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    """
    清洗原始表格：
    1. 基于编号 / 内容链接 / screenshot_post_id / 行号生成稳定 post_id
    2. 帖子级字段只在同一个 post_id 内继承，禁止跨帖子全局填充
    3. 解析评论点赞数字
    4. 自动补充安全展示标题和链接
    """
    df = normalize_columns(df)
    df = df.copy()

    # 去掉全空行
    df = df.dropna(how="all").reset_index(drop=True)
    df["post_id"] = assign_post_ids(df)

    # 帖子级字段仅允许在同一 post_id 内从帖子首行向评论行继承；不做 bfill，避免旧空标题被后续截图标题污染。
    for col in POST_LEVEL_COLUMNS:
        df[col] = df.groupby("post_id", sort=False)[col].ffill()

    df["url"] = df["内容链接"].apply(extract_url)
    df["标题_clean"] = df["笔记标题"].where(df["笔记标题"].apply(is_non_empty), df["内容链接"].apply(extract_title_from_link_text))
    df["评论内赞量_num"] = df["评论内赞量"].apply(parse_wan_number)
    df["笔记赞_num"] = df["评论内互动量"].apply(lambda x: extract_metric(x, "赞"))
    df["笔记评论_num"] = df["评论内互动量"].apply(lambda x: extract_metric(x, "评论"))
    df["评论文本"] = df["高赞评论摘录"].fillna("").astype(str).str.strip()

    # 只保留有评论或有标题的行
    df = df[(df["评论文本"] != "") | (df["标题_clean"].astype(str).str.strip() != "")]
    return df.reset_index(drop=True)


# =========================
# 工具函数：洞察规则
# =========================

THEME_RULES = {
    "小家牵挂/猫狗陪伴": ["猫", "狗", "喂猫", "留守", "孩子能栓住娘", "猫狗双全"],
    "自由生活/自我节奏": ["自由", "自在", "自己决定", "一个人", "自己的房子", "自己安排", "被窝里计划", "想去哪里"],
    "亲戚社交/春节通关": ["亲戚", "走人户", "刻薄", "美甲", "人户", "做客", "尴尬"],
    "家人撑腰/同一战线": ["父母", "妈妈", "颜控", "同一战线", "站我这边", "撑腰"],
    "原生家庭/爱与消耗": ["东亚母女", "恨海情天", "爱也爱不彻底", "妈妈", "好欺负", "煎熬", "满意的大人"],
    "年俗协商/代际观念": ["男方家", "姥爷", "爷爷", "居委会", "风评"],
    "荒诞幽默/热梗二创": ["笑死", "走马灯", "朝闻道", "往生", "大运", "模仿"],
}


INSIGHT_LIBRARY = {
    "小家牵挂/猫狗陪伴": {
        "insight": "春节不是只有回老家的团圆，也有年轻人对自己小家的牵挂。",
        "brand_action": "谨慎下场",
        "risk": "中",
        "cocreation": "征集“春节回家前最放心不下的小事”：猫、狗、植物、冰箱、房间、快递、工作电脑。",
        "brief": "从“回家”扩展到“安顿生活”：人回了老家，心里也惦记着自己亲手经营的小家。品牌可以成为春节迁徙前后生活秩序的一部分。",
    },
    "自由生活/自我节奏": {
        "insight": "年轻人不是不想过年，而是不想被春节流程吞掉自己的节奏。",
        "brand_action": "建议下场",
        "risk": "低",
        "cocreation": "发起“今年过年，我想按自己意思做的一件小事”征集。",
        "brief": "羊年春节可以从“有自己的样子”切入：团圆很好，但年轻人也希望在春节里保留一点自己的节奏和选择。",
    },
    "亲戚社交/春节通关": {
        "insight": "春节走亲戚像一场关系通关，礼物和吃喝常常是缓解尴尬的社交动作。",
        "brand_action": "建议下场",
        "risk": "中",
        "cocreation": "做“春节尴尬时刻缓冲器”系列，征集网友走亲戚时最想逃过的问题。",
        "brief": "把春节送礼从“礼数”转成“社交缓冲”：有些话不好回答，但递上一箱奶、倒上一杯奶，可以让气氛先松下来。",
    },
    "家人撑腰/同一战线": {
        "insight": "春节最让人松一口气的，不是没人问你问题，而是家里有人和你一队。",
        "brand_action": "强烈建议下场",
        "risk": "低",
        "cocreation": "征集“过年时，家人哪一刻让你觉得TA站你这边”。",
        "brief": "从“团圆”升级到“同队”：过年最暖的瞬间，是家人没有审判你，而是替你挡了一下、接住了一下。",
    },
    "原生家庭/爱与消耗": {
        "insight": "春节会把亲密关系里说不出口的爱、委屈和亏欠一起放大。",
        "brand_action": "谨慎下场",
        "risk": "高",
        "cocreation": "不建议玩梗；可做克制文案：有些话今年还没说出口，也没关系，先好好照顾自己。",
        "brief": "这类议题情绪浓度高，但品牌不宜替用户和解或评判家人。若使用，应做低姿态陪伴式表达，而非事件化玩梗。",
    },
    "年俗协商/代际观念": {
        "insight": "春节习俗背后，是年轻人在亲密关系、婚恋关系和家庭规则之间寻找自己的位置。",
        "brand_action": "谨慎下场",
        "risk": "中",
        "cocreation": "适合做轻量观察，不建议品牌直接站队；可征集“你家最可爱的春节协商方式”。",
        "brief": "年俗正在被重新商量。品牌可以观察新的家庭协商方式，但不宜卷入立场对抗。",
    },
    "荒诞幽默/热梗二创": {
        "insight": "春节内容的热度常来自荒诞感：大家用玩梗消化年节压力。",
        "brand_action": "谨慎下场",
        "risk": "中",
        "cocreation": "筛选可爱、低冒犯的梗做二创，不碰死亡、宗教、极端表达。",
        "brief": "可把荒诞感转成春节喜剧内容，但品牌需要过滤风险，保留幽默，不放大负面。",
    },
}


def detect_theme(text: str) -> str:
    text = str(text)
    scores = {}
    for theme, keywords in THEME_RULES.items():
        scores[theme] = sum(1 for kw in keywords if kw in text)
    best_theme = max(scores, key=scores.get)
    if scores[best_theme] == 0:
        return "其他/待人工判断"
    return best_theme


def default_insight(theme: str) -> dict:
    return INSIGHT_LIBRARY.get(theme, {
        "insight": "这条内容有一定春节讨论价值，但需要人工进一步判断它背后的真实情绪和品牌连接方式。",
        "brand_action": "待判断",
        "risk": "中",
        "cocreation": "先作为灵感素材收集，暂不直接转成品牌动作。",
        "brief": "该样本可作为春节内容观察的一部分，后续需结合更多评论判断是否具备品牌下场价值。",
    })


def first_non_empty_value(series: pd.Series) -> str:
    """安全获取一组数据中的第一个非空文本。"""
    for value in series.dropna().astype(str):
        value = value.strip()
        if value:
            return value
    return ""


def make_post_display_label(row: pd.Series) -> str:
    """下拉框使用安全 fallback，不借用其他帖子的标题。"""
    title = str(row.get("笔记标题", "") or "").strip()
    if title:
        return title
    url = str(row.get("内容链接", "") or "").strip()
    if url:
        return f"{url[:20]}…" if len(url) > 20 else url
    return f"未命名笔记 #{row.get('post_id', '')}"


def build_post_level_table(df: pd.DataFrame) -> pd.DataFrame:
    """把“多行评论”聚合成“每行一个帖子”。"""
    rows = []

    for post_id, group in df.groupby("post_id"):
        title = first_non_empty_value(group["标题_clean"]) if "标题_clean" in group else ""
        url = first_non_empty_value(group["url"]) if "url" in group else ""
        keyword = first_non_empty_value(group["搜索关键词"]) if "搜索关键词" in group else ""
        comments = [c for c in group["评论文本"].dropna().astype(str).tolist() if c.strip()]
        joined_comments = "\n".join(comments)
        all_text = f"{title}\n{joined_comments}"

        theme = detect_theme(all_text)
        insight = default_insight(theme)

        top_comment = ""
        if comments:
            # 按评论内赞量找最高赞评论；如果没有数字，就取第一条
            idx = group["评论内赞量_num"].idxmax()
            top_comment = str(group.loc[idx, "评论文本"]) if idx in group.index else comments[0]

        max_comment_like = float(group["评论内赞量_num"].max()) if "评论内赞量_num" in group else 0.0
        comment_like_sum = float(group["评论内赞量_num"].sum()) if "评论内赞量_num" in group else 0.0
        note_likes = float(group["笔记赞_num"].max()) if "笔记赞_num" in group else 0.0
        note_comments = float(group["笔记评论_num"].max()) if "笔记评论_num" in group else 0.0

        # 简单热度分：评论内赞量更能代表“评论区接住了什么”
        hot_score = note_likes + note_comments * 3 + comment_like_sum

        rows.append({
            "post_id": str(post_id),
            "搜索关键词": keyword,
            "笔记标题": title,
            "内容链接": url,
            "评论条数": len(comments),
            "最高赞评论": top_comment,
            "最高评论赞": max_comment_like,
            "评论赞总和": comment_like_sum,
            "笔记赞": note_likes,
            "笔记评论": note_comments,
            "热度分": hot_score,
            "洞察主题": theme,
            "一句话洞察": insight["insight"],
            "品牌能否自然下场": insight["brand_action"],
            "风险等级": insight["risk"],
            "二创方向": insight["cocreation"],
            "Brief素材": insight["brief"],
            "全部高赞评论": joined_comments,
        })

    result = pd.DataFrame(rows)
    if not result.empty:
        result = result.sort_values("热度分", ascending=False).reset_index(drop=True)
        result["帖子显示名"] = result.apply(make_post_display_label, axis=1)
    return result


# =========================
# 页面组件
# =========================

def kpi_card(label, value, help_text=None):
    st.metric(label=label, value=value, help=help_text)


def make_downloadable_excel(df: pd.DataFrame) -> bytes:
    output = BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="洞察结果")
    return output.getvalue()


def generate_brief_text(row: pd.Series, brand_name: str, campaign_name: str) -> str:
    title = row.get("笔记标题", "")
    theme = row.get("洞察主题", "")
    insight = row.get("一句话洞察", "")
    top_comment = row.get("最高赞评论", "")
    action = row.get("品牌能否自然下场", "")
    risk = row.get("风险等级", "")
    cocreation = row.get("二创方向", "")
    brief = row.get("Brief素材", "")

    return f"""# {campaign_name}｜小红书春节洞察卡片

## 1. 灵感来源
- 笔记标题：{title}
- 洞察主题：{theme}
- 高赞评论：{top_comment}

## 2. 一句话洞察
{insight}

## 3. 品牌机会判断
- 品牌：{brand_name}
- 下场建议：{action}
- 风险等级：{risk}

## 4. 可转化为品牌内容的方向
{brief}

## 5. 二创/互动方向
{cocreation}

## 6. 给代理的任务提示
请基于以上洞察，发展 3 个春节内容创意方向。要求：
1. 不要只堆春节符号，要回应真实春节关系与情绪；
2. 品牌出现方式要自然，避免硬广和说教；
3. 至少包含一个评论区可参与、可二创的机制；
4. 明确每个方向适合短片、图文、达人共创、评论区互动还是线下事件。
"""


# =========================
# 主界面
# =========================

st.title("🧧 Ivy 的小红书春节洞察工作台")
st.caption("个人版 MVP：上传/使用春节洞察表 → 自动清洗评论 → 生成洞察主题、品牌机会判断、二创方向和 brief 素材。")
st.info("这是一个帮助品牌经理从小红书春节内容和高赞评论中提炼消费者洞察、品牌机会、风险判断与 brief 素材的个人工作台。")

with st.sidebar:
    st.header("项目设置")
    brand_name = st.text_input("品牌名称", value="伊利")
    campaign_name = st.text_input("项目名称", value="羊年春节营销")
    uploaded_file = st.file_uploader("上传你的 Excel / CSV", type=["xlsx", "xls", "csv"])
    use_sample = st.checkbox("没有上传时使用示例春节洞察库", value=True)

    st.divider()
    ai_config = get_insight_model_config()
    st.subheader("AI 功能")
    st.caption(AI_COST_TIP)
    provider_label = {"monica": "Monica", "aliyun": "阿里云百炼", "rule": "规则版"}.get(ai_config["provider"], "规则版")
    st.markdown(f"**当前高级洞察模型：** {provider_label}")
    st.caption(f"当前文本模型名：{ai_config.get('model') or '未启用文本模型'}")
    st.caption(f"是否启用 Monica 洞察：{'是' if ai_config.get('use_monica') else '否'}")
    if ai_config["provider"] == "monica":
        st.warning("Monica API 会按模型和 token 单独计费，请注意用量。")
    if ai_config["available"]:
        st.success("AI 洞察增强已启用。")
    else:
        st.info(ai_config.get("disabled_reason") or AI_NOT_CONFIGURED_MESSAGE)
    st.caption("推荐：阿里云视觉模型负责截图识别；Monica 文本模型负责高级洞察和 brief；Monica 未配置时回退到阿里云文本模型；全部不可用时继续使用规则版分析。")

    st.divider()
    st.subheader("如何使用")
    st.markdown(
        """
1. 第一步：上传 Excel / CSV 数据；
2. 第二步：如果不上传，默认使用 `sample_data/spring_festival_sample.csv` 示例数据；
3. 第三步：先看“数据预览”，确认数据是否读取成功；
4. 第四步：再看“热点看板”“评论区洞察”“品牌机会”“Brief 素材”；
5. 第五步：下载分析结果或复制 brief 文本。
        """
    )

    st.divider()
    st.subheader("品牌判断原则")
    st.write("当前 MVP 默认按以下原则判断：")
    st.write("✅ 像人话，不说教")
    st.write("✅ 能接春节真实关系/情绪")
    st.write("✅ 有评论区二创空间")
    st.write("✅ 大品牌下场安全")
    st.write("✅ 能自然连接送礼/早餐/家庭/日常秩序")

try:
    screenshot_library_df = st.session_state.get("screenshot_library_df")
    if uploaded_file is None and not use_sample and (screenshot_library_df is None or screenshot_library_df.empty):
        st.info("请先上传 Excel/CSV，或在侧边栏勾选使用示例数据。")
        st.stop()

    raw_df = read_uploaded_file(uploaded_file) if (uploaded_file is not None or use_sample) else pd.DataFrame()
    if screenshot_library_df is not None and not screenshot_library_df.empty:
        raw_df = pd.concat([raw_df, screenshot_library_df], ignore_index=True, sort=False)
    clean_df = clean_dataframe(raw_df)
    post_df = build_post_level_table(clean_df)

except Exception as e:
    st.error("数据读取或清洗失败。请检查表头是否和模板一致，或把报错复制给 Codex / ChatGPT。")
    st.exception(e)
    st.stop()


tab1, tab3, tab2, tab4, tab5, tab6 = st.tabs([
    "📥 数据预览",
    "📸 截图识别",
    "📊 热点看板",
    "💬 评论区洞察",
    "🎯 品牌机会",
    "📝 Brief 素材"
])


with tab1:
    st.subheader("原始数据预览")
    st.dataframe(raw_df, use_container_width=True, height=240)

    st.subheader("清洗后的评论数据")
    st.caption("同一帖子下的多行评论会自动继承上方帖子信息。")
    st.dataframe(clean_df, use_container_width=True, height=300)

    st.subheader("聚合后的帖子级数据")
    st.caption("每一行 = 一篇帖子，便于后续看板和洞察分析。")
    st.dataframe(post_df, use_container_width=True, height=360)


with tab2:
    st.subheader("春节热点概览")

    col1, col2, col3, col4 = st.columns(4)
    with col1:
        kpi_card("帖子数", len(post_df))
    with col2:
        kpi_card("高赞评论数", int(clean_df["评论文本"].astype(bool).sum()))
    with col3:
        kpi_card("最高评论赞", f"{int(post_df['最高评论赞'].max()):,}" if not post_df.empty else 0)
    with col4:
        kpi_card("建议下场样本", int((post_df["品牌能否自然下场"].str.contains("建议", na=False)).sum()))

    st.divider()

    if not post_df.empty:
        c1, c2 = st.columns(2)

        with c1:
            theme_counts = post_df["洞察主题"].value_counts().reset_index()
            theme_counts.columns = ["洞察主题", "样本数"]
            fig = px.bar(theme_counts, x="样本数", y="洞察主题", orientation="h", title="洞察主题分布")
            st.plotly_chart(fig, use_container_width=True)

        with c2:
            action_counts = post_df["品牌能否自然下场"].value_counts().reset_index()
            action_counts.columns = ["下场建议", "样本数"]
            fig = px.pie(action_counts, names="下场建议", values="样本数", title="品牌下场建议分布")
            st.plotly_chart(fig, use_container_width=True)

        top_posts = post_df.sort_values("热度分", ascending=False).head(10)
        fig = px.bar(
            top_posts,
            x="热度分",
            y="笔记标题",
            orientation="h",
            title="热度分 Top 帖子",
            hover_data=["洞察主题", "最高赞评论", "品牌能否自然下场"],
        )
        st.plotly_chart(fig, use_container_width=True)


with tab3:
    st.subheader("📸 截图识别分析")
    st.info("适合上传小红书笔记正文截图、评论区截图、高赞评论截图。视频类内容建议补充一句话描述，因为当前版本不会自动访问小红书链接或播放视频。")
    st.warning(VISION_COST_TIP)
    st.caption("合规边界：本功能只分析用户主动上传的截图；不会自动访问小红书链接、抓取网页、下载/播放视频或保存原图。")

    vision_config = get_vision_config()
    if not vision_config["available"]:
        st.info(VISION_NOT_CONFIGURED_MESSAGE)

    with st.form("screenshot_ocr_form"):
        screenshot_files = st.file_uploader(
            "上传截图（建议每次 1-5 张）",
            type=["png", "jpg", "jpeg"],
            accept_multiple_files=True,
        )
        col_a, col_b = st.columns(2)
        with col_a:
            screenshot_keyword = st.text_input("搜索关键词（可选）", value="春节")
            screenshot_brand = st.text_input("品牌名称", value=brand_name)
        with col_b:
            screenshot_link = st.text_input("内容链接（可选）", value="")
            screenshot_campaign = st.text_input("项目名称", value=campaign_name)
        screenshot_note = st.text_area("补充说明（可选）", placeholder="例如：这是一条视频笔记，主要内容是……", height=90)
        recognize_clicked = st.form_submit_button("开始截图识别", disabled=not vision_config["available"])

    if not screenshot_files:
        st.caption("请上传 png / jpg / jpeg 截图后开始识别；未上传截图时不会调用视觉模型。")
    elif len(screenshot_files) > 5:
        st.warning("建议每次上传 1-5 张截图。当前仍会尝试压缩后识别，如失败请减少图片数量。")

    if recognize_clicked:
        if not screenshot_files:
            st.warning("请先上传至少一张截图。")
        else:
            try:
                with st.spinner("视觉模型正在进行 OCR 和结构化识别..."):
                    raw_output = call_vision_model(
                        screenshot_files,
                        screenshot_keyword,
                        screenshot_link,
                        screenshot_note,
                        screenshot_brand,
                        screenshot_campaign,
                        vision_config,
                    )
                parsed = parse_json_from_model_output(raw_output)
                if parsed is None:
                    st.session_state["screenshot_raw_output"] = raw_output
                    st.session_state.pop("screenshot_result", None)
                    st.warning("模型返回的 JSON 不规范，已展示原始输出。你可以手动复制其中有用内容。")
                else:
                    st.session_state["screenshot_result"] = normalize_screenshot_result(parsed, screenshot_keyword, screenshot_link, screenshot_note)
                    st.session_state.pop("screenshot_raw_output", None)
            except Exception as e:
                st.error(VISION_ERROR_MESSAGE)
                st.caption(str(e)[:500])

    if st.session_state.get("screenshot_raw_output"):
        st.text_area("原始模型输出（JSON 解析失败时展示）", st.session_state["screenshot_raw_output"], height=260)

    result = st.session_state.get("screenshot_result")
    if result:
        structured = result["截图结构化识别"]

        st.divider()
        st.markdown("### A. OCR 识别结果确认 / 编辑区")
        st.warning("截图识别可能有误，建议先确认标题、正文和高赞评论，再生成 AI 洞察。")

        with st.form("screenshot_confirm_form"):
            col_title, col_link = st.columns(2)
            with col_title:
                edited_title = st.text_input("笔记标题", value=structured.get("笔记标题", ""))
                edited_author = st.text_input("作者昵称", value=structured.get("作者昵称", ""))
                edited_author_type = st.text_input("作者类型", value=structured.get("作者类型", ""))
                edited_publish_time = st.text_input("发布时间", value=structured.get("发布时间", ""))
            with col_link:
                edited_link = st.text_input("内容链接", value=structured.get("内容链接", ""))
                edited_likes = st.text_input("点赞数", value=structured.get("点赞数", ""))
                edited_collects = st.text_input("收藏数", value=structured.get("收藏数", ""))
                edited_comment_count = st.text_input("评论数", value=structured.get("评论数", ""))
                edited_shares = st.text_input("分享数", value=structured.get("分享数", ""))

            edited_body = st.text_area("笔记正文", value=structured.get("笔记正文", ""), height=140)
            edited_note = st.text_area("补充说明", value=structured.get("补充说明", ""), height=90)

            st.markdown("#### 高赞评论文本与评论点赞数")
            comments_df = pd.DataFrame(structured.get("高赞评论列表", []))
            if comments_df.empty:
                comments_df = pd.DataFrame([{ "评论文本": "", "点赞数": "", "评论者昵称": "" }])
            for col in ["评论文本", "点赞数", "评论者昵称"]:
                if col not in comments_df.columns:
                    comments_df[col] = ""
            edited_comments_df = st.data_editor(
                comments_df[["评论文本", "点赞数", "评论者昵称"]],
                num_rows="dynamic",
                use_container_width=True,
                key="screenshot_comments_editor",
            )

            st.caption(f"识别置信度：{structured.get('识别置信度', '') or '未提供'} ｜ 可能不确定字段：{structured.get('可能不确定字段', '') or '未提供'}")
            st.caption(f"截图识别备注：{structured.get('截图识别备注', '') or '无'}")

            save_confirmed = st.form_submit_button("保存确认内容")

        if save_confirmed:
            confirmed = dict(structured)
            confirmed.update({
                "笔记标题": edited_title,
                "笔记正文": edited_body,
                "内容链接": edited_link,
                "作者昵称": edited_author,
                "作者类型": edited_author_type,
                "发布时间": edited_publish_time,
                "点赞数": edited_likes,
                "收藏数": edited_collects,
                "评论数": edited_comment_count,
                "分享数": edited_shares,
                "补充说明": edited_note,
                "高赞评论列表": [
                    {
                        "评论文本": str(row.get("评论文本", "")).strip(),
                        "点赞数": str(row.get("点赞数", "")).strip(),
                        "评论者昵称": str(row.get("评论者昵称", "")).strip(),
                    }
                    for _, row in edited_comments_df.fillna("").iterrows()
                    if str(row.get("评论文本", "")).strip() or str(row.get("点赞数", "")).strip()
                ],
            })
            st.session_state["screenshot_result"] = {"截图结构化识别": confirmed, "品牌洞察初稿": result.get("品牌洞察初稿", {})}
            st.success("已保存确认内容。")
            st.rerun()

        structured = st.session_state["screenshot_result"]["截图结构化识别"]
        text_config = get_insight_model_config()
        if not text_config["available"]:
            st.info("未配置文本模型，当前无法基于确认内容生成 AI 洞察。")

        if st.button("基于确认内容生成 AI 洞察", disabled=not text_config["available"]):
            try:
                with st.spinner("文本模型正在基于确认内容生成 AI 洞察..."):
                    insight_markdown = generate_confirmed_screenshot_insight(structured, screenshot_brand, screenshot_campaign, text_config)
                st.session_state["screenshot_insight_markdown"] = insight_markdown
            except Exception as e:
                st.error(str(e) or TEXT_INSIGHT_ERROR_MESSAGE)

        if st.session_state.get("screenshot_insight_markdown"):
            st.markdown("### B. AI 洞察结果")
            st.markdown(st.session_state["screenshot_insight_markdown"])

        result_df = screenshot_result_to_dataframe(st.session_state["screenshot_result"])
        col_join, col_download = st.columns(2)
        with col_join:
            if st.button("加入当前洞察库"):
                existing = st.session_state.get("screenshot_library_df")
                st.session_state["screenshot_library_df"] = pd.concat([existing, result_df], ignore_index=True, sort=False) if existing is not None else result_df
                st.success("已加入当前洞察库。页面将重新运行，截图识别内容会参与数据预览、热点看板、评论区洞察、品牌机会和 Brief 素材。")
                st.rerun()
        with col_download:
            st.download_button(
                label="下载截图识别结果 CSV",
                data=result_df.to_csv(index=False).encode("utf-8-sig"),
                file_name="截图识别结果.csv",
                mime="text/csv",
            )


with tab4:
    st.subheader("评论区洞察卡片")
    st.caption("这里优先看“高赞评论接住了什么”，不是只看笔记标题。")

    if post_df.empty:
        st.warning("暂无可分析数据。")
    else:
        post_options = post_df.drop_duplicates("post_id").copy()
        label_by_id = dict(zip(post_options["post_id"], post_options["帖子显示名"]))
        selected_post_id = st.selectbox(
            "选择一篇帖子",
            post_options["post_id"].tolist(),
            format_func=lambda pid: label_by_id.get(pid, str(pid)),
        )
        selected = post_options[post_options["post_id"] == selected_post_id].iloc[0]

        st.markdown(f"### {selected['洞察主题']}")
        st.write(f"**一句话洞察：** {selected['一句话洞察']}")
        st.write(f"**最高赞评论：** {selected['最高赞评论']}")

        with st.expander("查看全部高赞评论"):
            st.text(selected["全部高赞评论"])

        st.info(f"二创方向：{selected['二创方向']}")

        st.divider()
        st.subheader("AI 洞察增强")
        st.caption(AI_COST_TIP)
        if not ai_config["available"]:
            st.info(AI_NOT_CONFIGURED_MESSAGE)
        if st.button("使用 AI 优化这条洞察", disabled=not ai_config["available"], key="ai_insight_button"):
            try:
                with st.spinner("AI 正在优化洞察..."):
                    ai_insight_text = generate_ai_insight(selected, brand_name, campaign_name, ai_config)
                st.session_state["ai_insight_text"] = ai_insight_text
            except Exception as e:
                st.error(str(e) or AI_ERROR_MESSAGE)

        if st.session_state.get("ai_insight_text"):
            st.markdown(st.session_state["ai_insight_text"])


with tab5:
    st.subheader("品牌机会判断")
    st.caption("这部分更像策略同事给你的初筛：能不能接、怎么接、风险是什么。")

    editable_df = st.data_editor(
        post_df[[
            "笔记标题", "洞察主题", "一句话洞察", "品牌能否自然下场",
            "风险等级", "二创方向", "Brief素材"
        ]],
        use_container_width=True,
        height=520,
        num_rows="dynamic",
    )

    st.download_button(
        label="下载洞察结果 Excel",
        data=make_downloadable_excel(post_df),
        file_name="小红书春节洞察分析结果.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


with tab6:
    st.subheader("一键生成 Brief 素材")
    if post_df.empty:
        st.warning("暂无可生成 brief 的数据。")
    else:
        brief_options = post_df.drop_duplicates("post_id").copy()
        brief_label_by_id = dict(zip(brief_options["post_id"], brief_options["帖子显示名"]))
        selected_post_id_2 = st.selectbox(
            "选择要生成 brief 的帖子",
            brief_options["post_id"].tolist(),
            key="brief_select",
            format_func=lambda pid: brief_label_by_id.get(pid, str(pid)),
        )
        selected_2 = brief_options[brief_options["post_id"] == selected_post_id_2].iloc[0]
        brief_text = generate_brief_text(selected_2, brand_name, campaign_name)

        st.text_area("可复制给自己/代理/ChatGPT继续精修", brief_text, height=520)

        st.download_button(
            label="下载这条 Brief 素材 TXT",
            data=brief_text.encode("utf-8"),
            file_name="brief素材.txt",
            mime="text/plain",
        )

        st.divider()
        st.subheader("AI Brief 增强")
        st.caption(AI_COST_TIP)
        if not ai_config["available"]:
            st.info(AI_NOT_CONFIGURED_MESSAGE)
        if st.button("使用 AI 生成更完整 brief", disabled=not ai_config["available"], key="ai_brief_button"):
            try:
                with st.spinner("AI 正在生成 brief..."):
                    ai_brief_text = generate_ai_brief(selected_2, brand_name, campaign_name, ai_config)
                st.session_state["ai_brief_text"] = ai_brief_text
            except Exception as e:
                st.error(str(e) or AI_ERROR_MESSAGE)

        if st.session_state.get("ai_brief_text"):
            st.markdown(st.session_state["ai_brief_text"])

st.divider()
st.caption("MVP 提醒：当前版本是基于规则的轻量分析器，不会自动抓取小红书数据。建议先用手动收集的小样本跑通判断框架，再考虑接入更复杂的数据源。")
