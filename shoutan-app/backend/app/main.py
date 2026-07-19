"""手谈后端 v3 —— 参考申论项目重构
Tab 架构：游戏图谱 / 咨询模式 / 一键生成 / 说明
接口：
- /api/health       健康检查
- /api/graph        返回 games_graph.json
- /api/interpret    节点 AI 解读（按 doc+keywords 精准 RAG，结构化输出）
- /api/free-chat    咨询模式（无参数约束的 RAG+LLM 对话）
- /api/generate     一键生成（场景+游戏+位阶+背景 → 今晚怎么打）
密钥：前端 localStorage 存，请求体传给后端，后端转发，绝不入库。
"""
import json
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .config import SYSTEM_PROMPT, DEFAULT_LLM_MODEL, DEFAULT_LLM_BASE_URL
from .retriever import retrieve
from .graph_index import retrieve_in_doc
from .llm import stream_chat, LLMError

BASE_DIR = Path(__file__).resolve().parent.parent
FRONTEND_DIR = BASE_DIR.parent / "frontend"
GRAPH_FILE = FRONTEND_DIR / "games_graph.json"

app = FastAPI(title="手谈 · 策略导师", version="3.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


# ============ 数据模型 ============
class NodeInterpretRequest(BaseModel):
    node_name: str
    doc: str                       # 对应知识库 md 文件
    keywords: list[str] = []
    category: str = ""             # rule/tactic/culture/scene
    api_key: str = ""
    base_url: Optional[str] = None
    model: Optional[str] = None


class FreeChatRequest(BaseModel):
    message: str
    history: list[dict] = []
    api_key: str = ""
    base_url: Optional[str] = None
    model: Optional[str] = None


class GenerateRequest(BaseModel):
    scene: str = ""
    game: str = ""
    rank: str = ""
    background: str = ""
    api_key: str = ""
    base_url: Optional[str] = None
    model: Optional[str] = None


class RetrieveRequest(BaseModel):
    query: str


# ============ 通用：构建 messages 并流式调用 LLM ============
async def _stream_llm_response(messages: list[dict], api_key: str, base_url: str, model: str):
    """统一流式输出：meta(可选) 由调用方先发，这里只发 token + done/error。"""
    try:
        async for chunk in stream_chat(messages=messages, api_key=api_key,
                                        base_url=base_url, model=model):
            yield f"data: {json.dumps({'type':'token','data':chunk}, ensure_ascii=False)}\n\n"
        yield f"data: {json.dumps({'type':'done'}, ensure_ascii=False)}\n\n"
    except LLMError as e:
        yield f"data: {json.dumps({'type':'error','data':str(e)}, ensure_ascii=False)}\n\n"


def _need_key(api_key: str):
    if not api_key.strip():
        raise HTTPException(401, "未配置 API Key，请在右上角「⚙ 设置」填入你的 Key 后重试。")


# ============ /api/graph：返回知识树 ============
@app.get("/api/graph")
async def get_graph():
    if not GRAPH_FILE.exists():
        raise HTTPException(404, "games_graph.json 不存在")
    return JSONResponse(json.loads(GRAPH_FILE.read_text(encoding="utf-8")))


# ============ /api/interpret：节点 AI 解读 ============
@app.post("/api/interpret")
async def interpret_node(req: NodeInterpretRequest):
    _need_key(req.api_key)
    base_url = (req.base_url or DEFAULT_LLM_BASE_URL).strip()
    model = (req.model or DEFAULT_LLM_MODEL).strip()

    # 按 doc+keywords 精准检索
    hits = retrieve_in_doc(req.doc, req.keywords, topk=8)
    context = "\n\n".join(
        f"【片段{i+1}】[来源：{h['source']} · {h['section']}]\n{h['content']}"
        for i, h in enumerate(hits)
    ) if hits else "（该节点未检索到相关片段，基于通用知识解读，请标注【推测】）"

    # 按 category 决定结构化输出的板块
    if req.category == "rule":
        structure = "**📌 一句话定位** · **📜 核心规则与胜负条件** · **⚡ 关键机制** · **⚠ 新手最易踩的雷** · **💡 实战要点**"
    elif req.category == "tactic":
        structure = "**📌 战术核心思想** · **🎯 具体打法（分阶段）** · **🔢 背后的博弈论/数学原理** · **💬 局间可照搬的话术** · **⚖ 变通条件**"
    elif req.category == "culture":
        structure = "**📌 核心概念** · **👁 老手怎么看（含过度推论）** · **🤝 中国人情逻辑关联** · **✅ 基础规范** · **🔄 变通条件** · **🚫 红线**"
    elif req.category == "scene":
        structure = "**📌 场景定位判词** · **🎯 下/平/上位各自的核心任务** · **📊 具体操作（分阶段）** · **💬 可照搬的话术** · **🚫 绝对红线**"
    else:
        structure = "**📌 定位** · **📜 核心内容** · **🎯 要点** · **⚠ 注意**"

    sys = f"""{SYSTEM_PROMPT}

## 当前任务：解读知识节点「{req.node_name}」
严格按以下板块输出，每个板块用 **加粗标题** 引导：
{structure}

## 知识库检索片段（弹药库，消化为具体建议，不要复述原文）
{context}

要求：
- 把片段消化成针对该节点的、具体可执行的解读。
- 片段未覆盖处可用通用知识补充，标注「（通用知识）」或「【推测】」。
- 不要输出片段的标题/来源标签作为正文——那是元信息。
- 控制在 800-1400 字，分点清晰。
"""

    messages = [{"role": "system", "content": sys},
                {"role": "user", "content": f"请解读「{req.node_name}」。关键词：{', '.join(req.keywords)}"}]

    meta = {"mode": "interpret", "hits_count": len(hits),
            "doc": req.doc, "node": req.node_name}

    async def gen():
        yield f"data: {json.dumps({'type':'meta','data':meta}, ensure_ascii=False)}\n\n"
        async for x in _stream_llm_response(messages, req.api_key, base_url, model):
            yield x
    return StreamingResponse(gen(), media_type="text/event-stream")


# ============ /api/free-chat：咨询模式（无参数约束）============
@app.post("/api/free-chat")
async def free_chat(req: FreeChatRequest):
    _need_key(req.api_key)
    if not req.message.strip():
        raise HTTPException(400, "消息不能为空")

    base_url = (req.base_url or DEFAULT_LLM_BASE_URL).strip()
    model = (req.model or DEFAULT_LLM_MODEL).strip()

    # 通用 RAG 检索（带泛化：覆盖低也允许 LLM 兜底）
    result = retrieve(req.message)
    if result["hits"]:
        context = "\n\n".join(
            f"【片段{i+1}】[来源：{h['source']} · {h['section']}]\n{h['content'][:800]}"
            for i, h in enumerate(result["hits"])
        )
    else:
        context = "（知识库未检索到直接相关内容，基于通用知识回答，请标注【推测】）"

    sys = f"""{SYSTEM_PROMPT}

## 当前模式：咨询模式（自由问答）
用户在咨询模式下提问，不受三参数（场景/游戏/位阶）约束。请：
1. 优先且忠实地使用下面知识库片段回答，引用时标注来源文件名。
2. 知识库未覆盖的部分，用通用博弈论/人情常识/文化知识补充，标注「（通用知识）」或「【推测】」。
3. 条理清晰、重点突出，用 **加粗** 标注关键术语。
4. 即使问题用词不规范或抽象，也要尽力理解意图，关联到相关概念（如问"贪"→关联"麻将贪大牌/德州滥诈唬"）。
5. 回答控制在 300-800 字，不要拒答——你是专家。

## 知识库检索片段
{context}
"""
    messages = [{"role": "system", "content": sys}]
    for m in req.history[-8:]:
        if m.get("role") in ("user", "assistant") and m.get("content"):
            messages.append({"role": m["role"], "content": m["content"]})
    messages.append({"role": "user", "content": req.message})

    meta = {"mode": "free-chat", "coverage": result["coverage"],
            "hits_count": len(result["hits"])}

    async def gen():
        yield f"data: {json.dumps({'type':'meta','data':meta}, ensure_ascii=False)}\n\n"
        async for x in _stream_llm_response(messages, req.api_key, base_url, model):
            yield x
    return StreamingResponse(gen(), media_type="text/event-stream")


# ============ /api/generate：一键生成（场景+游戏+位阶+背景 → 今晚怎么打）============
@app.post("/api/generate")
async def generate(req: GenerateRequest):
    _need_key(req.api_key)
    base_url = (req.base_url or DEFAULT_LLM_BASE_URL).strip()
    model = (req.model or DEFAULT_LLM_MODEL).strip()

    # 拼一个综合查询去检索
    query_parts = [p for p in [req.scene, req.game, req.rank] if p]
    query = " ".join(query_parts)
    result = retrieve(query) if query else {"hits": [], "coverage": "low", "avg_score": 0}

    if result["hits"]:
        context = "\n\n".join(
            f"【片段{i+1}】[来源：{h['source']} · {h['section']}]\n{h['content'][:800]}"
            for i, h in enumerate(result["hits"])
        )
    else:
        context = "（未检索到直接相关内容，基于通用知识推断，请标注【推测】）"

    sys = f"""{SYSTEM_PROMPT}

## 当前任务：一键生成「今晚怎么打」的策略方案
用户参数：
- 场景：{req.scene or '未指定'}
- 游戏：{req.game or '未指定'}
- 位阶：{req.rank or '未指定'}
- 背景：{req.background or '（用户未补充背景）'}

严格按以下结构输出，每个板块用 **加粗标题** 引导：

**🎯 一、今晚的核心任务（3 句话内）**
犀利点出 KPI、红线、赢/输处理原则。

**📋 二、分阶段打法（最重要——给具体的动作）**
按"开局→中盘→收官"或"入座→开局→关键节点→散场"拆，每阶段 2-3 条**具体到动作**的建议。
不要写"要注意分寸"这种空话。具体怎么做、说什么、什么时机。

**⚖ 三、变通与雷区**
- 每个关键动作的「基础规范 vs 变通条件」
- 绝对红线（政商不谈业务/酒桌不强劝/不接受输送）

**💬 四、可直接照搬的话术（2-3 句）**
进贡/敬酒/被问及/输牌后/赢牌后各给一句自然、不谄媚、有格调的话。

## 知识库检索片段（弹药库，消化为具体建议）
{context}

要求：
- 严禁停在"要注意分寸""要察言观色"——必须落到具体动作。
- 800-1500 字。
"""
    messages = [{"role": "system", "content": sys},
                {"role": "user", "content": f"场景：{req.scene} | 游戏：{req.game} | 位阶：{req.rank}\n背景：{req.background}"}]

    meta = {"mode": "generate", "coverage": result["coverage"],
            "hits_count": len(result["hits"])}

    async def gen():
        yield f"data: {json.dumps({'type':'meta','data':meta}, ensure_ascii=False)}\n\n"
        async for x in _stream_llm_response(messages, req.api_key, base_url, model):
            yield x
    return StreamingResponse(gen(), media_type="text/event-stream")


# ============ 调试/健康 ============
@app.post("/api/retrieve")
async def api_retrieve(req: RetrieveRequest):
    return JSONResponse(retrieve(req.query))


@app.get("/api/health")
async def health():
    return {"status": "ok", "version": "3.0"}


# ============ 静态前端 ============
if FRONTEND_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")

    @app.get("/")
    async def index():
        return FileResponse(str(FRONTEND_DIR / "index.html"))

    @app.get("/{full_path:path}")
    async def spa(full_path: str):
        candidate = FRONTEND_DIR / full_path
        if candidate.is_file():
            return FileResponse(str(candidate))
        return FileResponse(str(FRONTEND_DIR / "index.html"))
