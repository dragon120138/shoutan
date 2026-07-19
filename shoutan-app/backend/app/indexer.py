"""知识库索引器：扫描 shoutan-kb，按标题/段落切分，构建 BM25 索引。"""
import json
import re
from pathlib import Path

import jieba
from rank_bm25 import BM25Okapi

from .config import KB_DIR, DATA_DIR, INDEX_PATH, DOCS_PATH


def _tokenize_zh(text: str) -> list[str]:
    """中文分词 + 英文/数字保留。"""
    tokens = []
    for tok in jieba.cut(text):
        tok = tok.strip()
        if not tok:
            continue
        # 过滤纯标点
        if re.fullmatch(r"[\s\W_]+", tok, re.UNICODE):
            continue
        tokens.append(tok.lower())
    return tokens


# 高频停用词（用于覆盖率判定时的查询侧过滤，不影响索引）
_STOPWORDS = {
    "的", "了", "是", "在", "我", "有", "和", "就", "不", "人", "都", "一",
    "个", "上", "也", "很", "到", "说", "要", "去", "你", "会", "着", "没",
    "看", "好", "自", "这", "那", "它", "他", "她", "们", "把", "被", "让",
    "什么", "怎么", "为什么", "如何", "可以", "能", "能不", "能不能",
    "吗", "呢", "吧", "啊", "哦", "嗯", "哈",
    "帮", "帮我", "写", "写个", "给", "给个", "下", "下个",
    "请问", "请", "麻烦", "一下", "那种", "这种", "那种",
    "the", "a", "an", "is", "are", "was", "what", "how", "why",
}


def _meaningful_tokens(text: str) -> list[str]:
    """分词并去除停用词，用于覆盖率判定。"""
    return [t for t in _tokenize_zh(text) if t not in _STOPWORDS and len(t) >= 1]


def _split_into_chunks(md_text: str, source_file: str) -> list[dict]:
    """把 markdown 按二级/三级标题切块，保留完整上下文。
    每个 chunk 的 section 字段会带上父标题链，便于检索时辨识。"""
    lines = md_text.splitlines()
    chunks = []
    h1 = h2 = h3 = ""
    current_buffer: list[str] = []
    # 当前的"显示标题"：取最深一层；若无则用上一级
    def cur_title():
        # 优先 h3，其次 h2，其次 h1
        return h3 or h2 or h1

    def full_title():
        chain = [t for t in (h1, h2, h3) if t]
        return " / ".join(chain) if chain else "(无标题)"

    def flush():
        nonlocal current_buffer
        if current_buffer:
            content = "\n".join(current_buffer).strip()
            if content:
                chunks.append({
                    "source": source_file,
                    "library": "",  # 由调用方注入
                    "section": full_title(),
                    "content": content,
                })
            current_buffer = []

    for line in lines:
        ls = line.strip()
        if ls.startswith("### "):
            flush()
            h3 = ls[4:].strip()
        elif ls.startswith("## "):
            flush()
            h3 = ""
            h2 = ls[3:].strip()
        elif ls.startswith("# "):
            flush()
            h3 = ""
            h2 = ""
            h1 = ls[2:].strip()
        else:
            current_buffer.append(line)
    flush()
    return chunks


def _infer_library(file_path: Path) -> str:
    """从文件路径推断所属库。"""
    parts = file_path.parts
    for p in parts:
        if "硬规则" in p:
            return "硬规则库"
        if "战术" in p and "数学" in p:
            return "战术与数学原理库"
        if "文化" in p and "主观" in p:
            return "文化与主观评价库"
        if "场景" in p and "位阶" in p:
            return "场景位阶剧本库"
    return "未分类"


# 排除清单：这些文件是"写给系统的操作手册"，不是给用户的内容。
# 它们关键词高频（讲的就是"怎么回答"），会污染检索结果。
EXCLUDE_FILES = {
    "README.md",
    "USAGE-调用指南.md",
}


def _should_exclude(file_path: Path) -> bool:
    """是否排除该文件不进索引。"""
    name = file_path.name
    if name in EXCLUDE_FILES:
        return True
    # 任何根目录（KB_DIR 直接下级）的 .md 都视为元文档，排除
    try:
        rel = file_path.relative_to(KB_DIR)
        if len(rel.parts) == 1:  # 根目录直接下级
            return True
    except ValueError:
        pass
    return False


def build_index():
    """扫描 KB_DIR，构建文档集与 BM25 索引，落盘。"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    all_md = sorted(KB_DIR.rglob("*.md"))
    md_files = [f for f in all_md if not _should_exclude(f)]
    if not md_files:
        raise RuntimeError(f"未在 {KB_DIR} 找到任何可索引的 .md 文件，请先构建知识库。")

    all_chunks: list[dict] = []
    for md_file in md_files:
        rel = str(md_file.relative_to(KB_DIR.parent))
        library = _infer_library(md_file)
        try:
            text = md_file.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            text = md_file.read_text(encoding="gbk", errors="ignore")
        # 注入 library 信息到切分函数
        chunks = _split_into_chunks(text, rel)
        for c in chunks:
            c["library"] = library
        all_chunks.extend(chunks)

    # 为每个 chunk 加 id
    for i, c in enumerate(all_chunks):
        c["id"] = i

    # 构建 BM25：用 section + content 一起分词，提升标题命中率
    corpus_tokens = [
        _tokenize_zh(f"{c['section']} {c['content']}") for c in all_chunks
    ]
    # 预热一次，确保可用
    BM25Okapi(corpus_tokens)

    # 落盘文档集
    with DOCS_PATH.open("w", encoding="utf-8") as f:
        json.dump(all_chunks, f, ensure_ascii=False, indent=2)

    # 落盘语料分词（加载时直接重建 BM25 对象，避免依赖内部字段格式）
    index_data = {
        "corpus_tokens": corpus_tokens,
        "num_docs": len(all_chunks),
    }
    with INDEX_PATH.open("w", encoding="utf-8") as f:
        json.dump(index_data, f, ensure_ascii=False)

    return len(all_chunks)


if __name__ == "__main__":
    n = build_index()
    print(f"索引构建完成，共 {n} 个文档块。")
