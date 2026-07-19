"""按文档名精准检索 —— 用于节点解读。
不同于通用 retrieve，这里先按 doc 字段过滤到指定文档，再在其中按 keywords 检索。
这样能保证'掼蛋战术'节点解读时，只检索掼蛋战术.md，不混入其他游戏。
"""
from .indexer import _tokenize_zh
from .retriever import _load


def retrieve_in_doc(doc_name: str, keywords: list[str], topk: int = 8) -> list[dict]:
    """在指定文档内按关键词检索。
    doc_name: 如 '掼蛋战术.md'（games_graph.json 里的 doc 字段）
    keywords: 节点的 keywords 列表
    返回该文档内最相关的 chunk 列表。
    """
    try:
        bm25, docs = _load()
    except Exception:
        return []

    # 过滤到指定文档
    doc_lower = doc_name.lower().replace("\\", "/")
    target_indices = [
        i for i, d in enumerate(docs)
        if doc_lower.split(".")[0] in d["source"].lower().replace("\\", "/")
    ]
    if not target_indices:
        return []

    # 在该文档内做关键词检索
    query = " ".join(keywords)
    tokens = _tokenize_zh(query)
    if not tokens:
        # 无关键词时返回该文档所有 chunk
        return [{"content": docs[i]["content"], "section": docs[i]["section"],
                 "source": docs[i]["source"], "library": docs[i]["library"]}
                for i in target_indices[:topk]]

    scores = bm25.get_scores(tokens)
    ranked = sorted(
        [(i, scores[i]) for i in target_indices],
        key=lambda x: x[1], reverse=True
    )[:topk]

    return [
        {"content": docs[i]["content"][:1200], "section": docs[i]["section"],
         "source": docs[i]["source"], "library": docs[i]["library"],
         "score": round(float(s), 3)}
        for i, s in ranked if s > 0.1
    ] or [
        {"content": docs[i]["content"][:1200], "section": docs[i]["section"],
         "source": docs[i]["source"], "library": docs[i]["library"], "score": 0}
        for i in target_indices[:topk]
    ]
