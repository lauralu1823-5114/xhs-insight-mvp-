# Ivy 的小红书春节洞察工作台 MVP

这是一个给品牌/市场营销经理使用的个人版洞察工具。

它不是全网爬虫，也不是通用舆情平台。第一版目标很克制：

> 上传你手动收集的小红书春节高热帖子和高赞评论，自动清洗、聚合，并生成洞察主题、品牌机会判断、风险等级、二创方向和 brief 素材。

## 适合解决什么问题

- 我收集了一批小红书春节帖子，不知道怎么沉淀成洞察
- 我想看评论区到底接住了什么情绪/梗
- 我想判断伊利能不能自然下场
- 我想把素材转成 brief 语言，发给代理继续发展
- 我想慢慢形成自己的“品牌社交洞察库”

## 文件结构

```text
xhs_insight_mvp/
├── app.py
├── requirements.txt
├── README.md
└── sample_data/
    └── spring_festival_sample.csv
```

## 表格字段要求

第一版优先适配这份表头：

```text
编号
搜索关键词
内容链接
笔记标题
作者类型
发布时间
高赞评论摘录
评论内赞量
评论内互动量
二创潜力
二创方向
品牌能否自然下场
一句话洞察
```

如果你的表里有空列也没关系，工具会尽量自动补齐。

## 如何运行

### 方式一：在 Codex / 云端开发环境运行

把整个文件夹上传到 GitHub，然后让 Codex 打开这个 repo，运行：

```bash
pip install -r requirements.txt
streamlit run app.py
```

它会给你一个预览链接。

### 方式二：本地运行

如果你愿意在电脑上运行，同样执行：

```bash
pip install -r requirements.txt
streamlit run app.py
```

浏览器会自动打开工具页面。

## 怎么使用

1. 打开工具
2. 侧边栏填写品牌名称、项目名称
3. 上传你的 Excel/CSV
4. 查看“数据预览”确认清洗是否正确
5. 在“热点看板”看主题分布和高热样本
6. 在“评论区洞察”看单条帖子洞察卡片
7. 在“品牌机会”里人工修正判断
8. 在“Brief 素材”里复制给自己、代理或 ChatGPT 精修

## 当前版本限制

- 不会自动抓取小红书数据
- 不调用大模型 API
- 洞察判断基于轻量关键词规则
- 更适合做“个人灵感库 MVP”，不适合当正式舆情系统

## 下一步可以升级什么

建议按这个顺序升级：

1. 加入更多关键词规则，让主题识别更贴近你的品牌语感
2. 加入“伊利品牌资产/禁区/语气”配置
3. 加入历史记录保存，形成个人洞察库
4. 加入导出 Word/PPT 大纲
5. 再考虑接入 AI API，让每条洞察更像资深策略同事写的

## V1.2 截图识别测试记录

当前 PR 合并前已补充基础测试：

- `python -m py_compile app.py`：通过，语法检查无报错。
- `python app.py`：通过，未配置 Streamlit secrets / 视觉模型时 App 在 bare mode 下不会崩溃。
- `streamlit.testing.v1.AppTest.from_file("app.py").run(timeout=20)`：通过，未配置视觉模型时可正常渲染，并显示截图识别不可用提示；未上传截图时，“📸 截图识别”页面可正常显示上传提示；V1.1 AI 洞察增强入口仍在原页面中。
- CSV / XLSX 示例读写检查：通过，示例数据字段与上传兼容；`.xls` 读取依赖 `xlrd` 已在 `requirements.txt` 中声明。
- API Key 检查：通过，代码中只读取 `ALIYUN_API_KEY` 等 secrets 名称，没有写入真实 API Key。

如需真实调用截图识别，请在 Streamlit secrets 中配置 `ENABLE_AI_FEATURES=true`、`ALIYUN_API_KEY`、`ALIYUN_BASE_URL` 和 `ALIYUN_VISION_MODEL`。

## V1.3 Monica 高级洞察模型接入

推荐使用方式：

- 阿里云百炼视觉模型继续负责截图识别 / OCR，请配置 `ALIYUN_API_KEY`、`ALIYUN_BASE_URL`、`ALIYUN_VISION_MODEL`，并保持 `ENABLE_AI_FEATURES=true`。
- Monica 文本模型负责高级洞察、AI Brief 增强，以及截图识别确认后的品牌策略洞察。
- 如果 Monica 未配置或 `USE_MONICA_FOR_INSIGHT` 缺失，系统会自动回退到阿里云百炼文本模型。
- 如果 Monica 和阿里云文本模型都不可用，系统继续使用规则版分析，上传、示例数据、看板、截图确认、下载等功能不受影响。

Streamlit Secrets 示例：

```toml
ENABLE_AI_FEATURES = true
ALIYUN_API_KEY = "你的阿里云百炼 API Key"
ALIYUN_BASE_URL = "你的阿里云 OpenAI-compatible Base URL"
ALIYUN_TEXT_MODEL = "你的阿里云文本模型"
ALIYUN_VISION_MODEL = "你的阿里云视觉模型"

USE_MONICA_FOR_INSIGHT = true
MONICA_API_KEY = "你的 Monica API Key"
MONICA_BASE_URL = "https://openapi.monica.im/v1"
MONICA_TEXT_MODEL = "gpt-4o"
```

注意：`MONICA_BASE_URL` 只需要填写到 `/v1`，不要填写 `/chat/completions`。Monica API 会按模型和 token 单独计费，请注意用量；如果 Monica 调用失败，请检查 API Key、Base URL、模型名、账户余额、额度限制或网络状态。
