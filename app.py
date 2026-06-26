import re
from io import BytesIO

import pandas as pd
import streamlit as st
import plotly.express as px


# =========================
# 基础设置
# =========================

st.set_page_config(
    page_title="Ivy 的小红书春节洞察工作台",
    page_icon="🧧",
    layout="wide",
)

REQUIRED_COLUMNS = [
    "编号", "搜索关键词", "内容链接", "笔记标题", "作者类型", "发布时间",
    "高赞评论摘录", "评论内赞量", "评论内互动量",
    "二创潜力", "二创方向", "品牌能否自然下场", "一句话洞察"
]


# =========================
# 工具函数：数据读取/清洗
# =========================

@st.cache_data(show_spinner=False)
def load_sample_data() -> pd.DataFrame:
    """读取项目自带的示例数据。"""
    return pd.read_csv("sample_data/spring_festival_sample.csv")


def read_uploaded_file(uploaded_file) -> pd.DataFrame:
    """支持 xlsx / csv 上传。"""
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


def clean_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    """
    清洗原始表格：
    1. 自动识别一条新帖子
    2. 评论行继承上方帖子信息
    3. 解析评论点赞数字
    4. 自动补充标题和链接
    """
    df = normalize_columns(df)
    df = df.copy()

    # 去掉全空行
    df = df.dropna(how="all").reset_index(drop=True)

    # 一条“新帖子”的判断：内容链接不空，或者编号不空
    new_post_marker = df["内容链接"].apply(is_non_empty) | df["编号"].apply(is_non_empty)
    df["post_id"] = new_post_marker.cumsum()

    # 帖子级字段向下填充
    post_cols = ["编号", "搜索关键词", "内容链接", "笔记标题", "作者类型", "发布时间"]
    for col in post_cols:
        df[col] = df.groupby("post_id")[col].ffill().bfill()

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
            "post_id": int(post_id),
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

with st.sidebar:
    st.header("项目设置")
    brand_name = st.text_input("品牌名称", value="伊利")
    campaign_name = st.text_input("项目名称", value="羊年春节营销")
    uploaded_file = st.file_uploader("上传你的 Excel / CSV", type=["xlsx", "xls", "csv"])
    use_sample = st.checkbox("没有上传时使用示例春节洞察库", value=True)

    st.divider()
    st.subheader("品牌判断原则")
    st.write("当前 MVP 默认按以下原则判断：")
    st.write("✅ 像人话，不说教")
    st.write("✅ 能接春节真实关系/情绪")
    st.write("✅ 有评论区二创空间")
    st.write("✅ 大品牌下场安全")
    st.write("✅ 能自然连接送礼/早餐/家庭/日常秩序")

try:
    if uploaded_file is None and not use_sample:
        st.info("请先上传 Excel/CSV，或在侧边栏勾选使用示例数据。")
        st.stop()

    raw_df = read_uploaded_file(uploaded_file)
    clean_df = clean_dataframe(raw_df)
    post_df = build_post_level_table(clean_df)

except Exception as e:
    st.error("数据读取或清洗失败。请检查表头是否和模板一致，或把报错复制给 Codex / ChatGPT。")
    st.exception(e)
    st.stop()


tab1, tab2, tab3, tab4, tab5 = st.tabs([
    "📥 数据预览",
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
    st.subheader("评论区洞察卡片")
    st.caption("这里优先看“高赞评论接住了什么”，不是只看笔记标题。")

    if post_df.empty:
        st.warning("暂无可分析数据。")
    else:
        selected_title = st.selectbox("选择一篇帖子", post_df["笔记标题"].tolist())
        selected = post_df[post_df["笔记标题"] == selected_title].iloc[0]

        st.markdown(f"### {selected['洞察主题']}")
        st.write(f"**一句话洞察：** {selected['一句话洞察']}")
        st.write(f"**最高赞评论：** {selected['最高赞评论']}")

        with st.expander("查看全部高赞评论"):
            st.text(selected["全部高赞评论"])

        st.info(f"二创方向：{selected['二创方向']}")


with tab4:
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


with tab5:
    st.subheader("一键生成 Brief 素材")
    if post_df.empty:
        st.warning("暂无可生成 brief 的数据。")
    else:
        selected_title_2 = st.selectbox("选择要生成 brief 的帖子", post_df["笔记标题"].tolist(), key="brief_select")
        selected_2 = post_df[post_df["笔记标题"] == selected_title_2].iloc[0]
        brief_text = generate_brief_text(selected_2, brand_name, campaign_name)

        st.text_area("可复制给自己/代理/ChatGPT继续精修", brief_text, height=520)

        st.download_button(
            label="下载这条 Brief 素材 TXT",
            data=brief_text.encode("utf-8"),
            file_name="brief素材.txt",
            mime="text/plain",
        )

st.divider()
st.caption("MVP 提醒：当前版本是基于规则的轻量分析器，不会自动抓取小红书数据。建议先用手动收集的小样本跑通判断框架，再考虑接入更复杂的数据源。")
