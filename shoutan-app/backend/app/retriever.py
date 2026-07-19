"""检索器：加载 BM25 索引，对查询返回 top-k 相关片段，并判定覆盖率。"""
import json
from functools import lru_cache

from rank_bm25 import BM25Okapi

from .config import INDEX_PATH, DOCS_PATH, RETRIEVAL_TOP_K, RETRIEVAL_MIN_SCORE, RAG_COVERAGE_THRESHOLD
from .indexer import _tokenize_zh, _meaningful_tokens


@lru_cache(maxsize=1)
def _load():
    if not INDEX_PATH.exists() or not DOCS_PATH.exists():
        raise RuntimeError("索引不存在，请先运行 python -m app.indexer 构建索引。")
    with INDEX_PATH.open("r", encoding="utf-8") as f:
        idx = json.load(f)
    with DOCS_PATH.open("r", encoding="utf-8") as f:
        docs = json.load(f)
    # 直接用落盘的语料分词重建 BM25 对象（不依赖内部字段格式，跨版本稳定）
    corpus_tokens = idx["corpus_tokens"]
    bm25 = BM25Okapi(corpus_tokens)
    return bm25, docs


def retrieve(query: str, top_k: int = RETRIEVAL_TOP_K) -> dict:
    """对查询进行 BM25 检索，返回相关片段 + 覆盖率判定。
    覆盖率判定综合两个信号：
      1. BM25 绝对分（avg_score）
      2. 查询词在 top1 文档中的命中比例（token_hit_ratio）——
         防止"完全无关话题"也因少量词碰撞拿到中等分数。
    返回结构:
      {
        "hits": [{"content","source","library","section","score"}, ...],
        "coverage": "high" | "medium" | "low",
        "avg_score": float
      }
    """
    bm25, docs = _load()
    tokens = _tokenize_zh(query)
    if not tokens:
        return {"hits": [], "coverage": "low", "avg_score": 0.0}

    scores = bm25.get_scores(tokens)
    ranked = sorted(enumerate(scores), key=lambda x: x[1], reverse=True)[:top_k]

    hits = []
    for doc_id, score in ranked:
        if score < RETRIEVAL_MIN_SCORE * 0.3:  # 过低分直接过滤
            continue
        d = docs[doc_id]
        hits.append({
            "content": d["content"][:1500],
            "source": d["source"],
            "library": d["library"],
            "section": d["section"],
            "score": round(float(score), 3),
        })

    if not hits:
        return {"hits": [], "coverage": "low", "avg_score": 0.0}

    avg_score = sum(h["score"] for h in hits) / len(hits)

    # 用"去停用词后的有意义的查询词"计算命中比例，防止虚高
    meaningful_query = _meaningful_tokens(query)
    top_text = f"{hits[0]['section']} {hits[0]['content']}"
    top_text_tokens = set(_tokenize_zh(top_text))
    n_meaningful = len(set(meaningful_query))
    if n_meaningful > 0:
        query_unique = set(meaningful_query)
        hit_ratio = len(query_unique & top_text_tokens) / len(query_unique)
    else:
        # 查询全是停用词（如"是什么"），无法判断相关性，判低
        hit_ratio = 0.0

    # 覆盖率判定：综合"绝对分""命中比例""有意义词数量"
    # 有意义词太少（<=1）时，相关性判断不可靠，压低 coverage
    sparse_query_penalty = (n_meaningful <= 1)

    if sparse_query_penalty:
        coverage = "low"
    elif avg_score >= 7.0 and hit_ratio >= 0.5:
        coverage = "high"
    elif avg_score >= RAG_COVERAGE_THRESHOLD and hit_ratio >= 0.34:
        coverage = "medium"
    else:
        coverage = "low"

    return {"hits": hits, "coverage": coverage, "avg_score": round(avg_score, 3)}
