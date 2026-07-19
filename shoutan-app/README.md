# 手谈 · 牌桌棋桌酒桌策略导师

> 淡雅水墨风界面 · 本地 RAG 知识库 + LLM 通用兜底 · 用户自带 API Key
>
> 一个面向政、商、学界青年精英的"策略导师"——在牌桌、棋桌、酒桌上，把每一手处理得既有格调又有逼格。

---

## 一、它能做什么

输入**场景（政/商/学交叉）+ 游戏 + 位阶（下/平/上）** 三参数，"手谈"按三层结构作答：

1. **规则速记 + 定位判词**：精要规则 + 这一局你的核心 KPI
2. **老手主观评价 + 人情梗**：你的牌品/棋品/酒品会被老手怎么"看人"
3. **高阶战术 + 数学/博弈论原理**：纳什均衡、混合策略、ICM、信号博弈……

支持**多轮追问**。知识库覆盖不足时，自动转 LLM 通用推断并标注**【推测】**，避免"检索太死板搜不出答案"。

---

## 二、目录结构

```
ZCodeProject/
├── shoutan-kb/                  ← 知识库（29 个 markdown，约 6 万字）
│   ├── 01-硬规则库/             （8 类游戏规则）
│   ├── 02-战术与数学原理库/     （高阶打法 + 博弈论）
│   ├── 03-文化与主观评价库/     （牌品/棋品/酒品/人情四件套/心理战/案例库）
│   └── 04-场景位阶剧本库/       （政商学交叉 + 酒桌专项）
│
└── shoutan-app/                 ← 应用
    ├── run.py                   ← 一键启动
    ├── backend/
    │   ├── requirements.txt
    │   └── app/
    │       ├── config.py        系统提示词 + 路径配置
    │       ├── indexer.py       知识库切分 + BM25 索引
    │       ├── retriever.py     检索 + 覆盖率判定
    │       ├── llm.py           兼容 OpenAI 协议的流式 LLM
    │       └── main.py          FastAPI 服务
    └── frontend/
        ├── index.html           单页结构
        ├── styles.css           水墨风样式
        └── app.js               对话/参数/流式渲染逻辑
```

---

## 三、快速启动

### 前置
- Python 3.10+
- 网络可访问 LLM API（智谱 GLM / OpenAI / DeepSeek 任一）

### 一键启动（Windows / macOS / Linux 通用）

```bash
cd C:\Users\Administrator\ZCodeProject\shoutan-app
python run.py
```

启动脚本会自动：建虚拟环境 → 装依赖 → 建索引 → 起服务。

浏览器打开 **http://127.0.0.1:8000** 即可。

### 手动分步（调试用）

```bash
cd shoutan-app
python -m venv .venv
.venv\Scripts\activate            # Windows
# source .venv/bin/activate        # macOS/Linux
pip install -r backend/requirements.txt
cd backend
python -m app.indexer             # 建索引（首次必做）
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

---

## 四、配置你自己的 API Key

**应用不内置任何 key，全程使用你自己的。** 后端不存储 key，前端把 key 存在浏览器 `localStorage`，每次请求带上。

1. 打开页面后，点左侧 **⚙ 接入** 展开设置。
2. 选一个预设（或手动填）：

| 服务商 | base_url | 模型示例 |
|---|---|---|
| 智谱 GLM | `https://open.bigmodel.cn/api/paas/v4/` | `glm-4-flash` / `glm-4-plus` |
| OpenAI | `https://api.openai.com/v1/` | `gpt-4o-mini` / `gpt-4o` |
| DeepSeek | `https://api.deepseek.com/v1` | `deepseek-chat` |

3. 填入你的 API Key，点 **保存配置**。

> 任何兼容 OpenAI `/chat/completions` 协议的接口都能用（自部署的 vLLM / Ollama / LM Studio 同理，填对应 `base_url` 即可）。

---

## 五、怎么用

1. **布局**：左侧依次选 **场景 / 游戏 / 位阶**，背景可选填。
2. 点 **填入提问**，参数会自动拼到输入框（你还可继续追问/补充）。
3. 点 **投子**（或回车）发送。
4. 看顶部 **RAG 徽章**：
   - 🟢 **高**：知识库命中充足，回答基于片段
   - 🟡 **中**：部分基于片段，少量推测
   - 🔴 **低**：转 LLM 通用推断，会明确标注【推测】
5. **多轮追问**：直接在输入框继续问即可，保留最近 8 轮上下文。

---

## 六、设计取舍说明

### 为什么用 BM25 而不是向量检索？
- BM25 **零额外模型依赖**，启动快、CPU 即可，Windows 上零折腾。
- 中文关键词命中精度高，规则/战术类问答尤其友好。
- 搭配**覆盖率判定 + LLM 通用兜底**，弥补了语义召回的不足。
- 若日后需要更强语义检索，可在 `retriever.py` 平行接入向量库（接口已预留）。

### 为什么用关键词 + LLM 兜底的混合？
之前你提到"rag 检索太死板搜不出答案"——典型死法是：关键词没命中 → BM25 返回空 → 系统无话可说。
本设计的解法：
- 召回分**高/中/低**三档覆盖率；
- 覆盖率低时，**不硬塞空检索**，而是让 LLM 基于通用知识回答，但**强制要求它声明"知识有限"并标注【推测】**；
- 前端用红色徽章明示用户"这是推测，需核实"。
- 这样既不死板，又不失诚实。

### 安全边界
- 后端**不存储任何 API Key**。
- 系统提示词内置了严格的合规约束：不鼓励腐败、八项规定、健康饮酒、不虚构数据。
- 政商交叉场景全程强调"亲清"底线，禁止借牌桌谈业务。

---

## 七、扩展与定制

| 想做的事 | 改哪里 |
|---|---|
| 增加知识库内容 | 在 `shoutan-kb/` 对应库目录加 `.md`，重跑 `python -m app.indexer` |
| 调整 RAG 召回数 / 阈值 | `backend/app/config.py` |
| 换默认 LLM | `config.py` 的 `DEFAULT_LLM_*` |
| 调手谈人格/输出风格 | `config.py` 的 `SYSTEM_PROMPT` |
| 接向量检索 | 在 `retriever.py` 加一路召回，与 BM25 结果融合 |

---

## 八、常见问题

**Q：启动报"未找到 .md 文件"？**
A：确认 `shoutan-kb` 目录与 `shoutan-app` 同级。`config.py` 里 `KB_DIR = BASE_DIR.parent / "shoutan-kb"`。

**Q：前端打不开？**
A：后端必须先起。`run.py` 会同时托管前端。

**Q：LLM 报 401？**
A：API Key 错或过期。检查设置面板。

**Q：中文分词慢？**
A：首次启动 `jieba` 初始化需要几秒，之后索引构建会很快。

---

## 九、一句话总结
> **手谈 = 本地知识库（懂规矩）+ 大模型（会说话）+ 你自己的 key（不蹭你的）。**
> 牌桌上的师爷，端的就是这份分寸。
