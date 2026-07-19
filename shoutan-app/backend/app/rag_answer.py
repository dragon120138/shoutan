"""无 LLM 时的 RAG 直答引擎 v2。
当用户没填 API key，或 LLM 调用失败时，用这个生成结构化回答。
v2 改进：不再堆砌原文片段，而是抽取每段的"要点句"，生成真正可读的回答。
"""
import re
from .retriever import retrieve


def synthesize_answer(query: str, params: dict | None = None) -> tuple[str, dict]:
    """基于 RAG 检索结果合成结构化回答。"""
    result = retrieve(query)
    meta = {
        "hits": [
            {"source": h["source"], "library": h["library"],
             "section": h["section"], "score": h["score"]}
            for h in result["hits"]
        ],
        "coverage": result["coverage"],
        "avg_score": result["avg_score"],
        "mode": "rag-direct",
    }

    params = params or {}
    scene = params.get("scene", "")
    game = params.get("game", "")
    rank = params.get("rank", "")

    if not result["hits"]:
        return _no_hit_reply(query, scene, game, rank), meta

    # 按库归类主检索结果
    by_lib: dict[str, list] = {}
    for h in result["hits"]:
        by_lib.setdefault(h["library"], []).append(h)

    # 定向补充检索：保证规则库、战术库、文化库都有内容
    # 当用户指定了游戏时，单独检索该游戏的规则和战术
    if game:
        for lib_query, lib_name in [
            (f"{game} 规则 牌型 进贡", "硬规则库"),
            (f"{game} 战术 博弈 原理", "战术与数学原理库"),
        ]:
            if not by_lib.get(lib_name):
                extra = retrieve(lib_query)
                extra_hits = [h for h in extra["hits"] if h["library"] == lib_name][:2]
                if extra_hits:
                    by_lib[lib_name] = extra_hits
                    # 也并入 meta 的 hits 供前端展示来源
                    for h in extra_hits:
                        meta["hits"].append({
                            "source": h["source"], "library": h["library"],
                            "section": h["section"], "score": h["score"]
                        })

    # 文化库补充：若主检索没命中文化库，或命中条目质量低，补一次精准检索
    culture_hits_existing = by_lib.get("文化与主观评价库", [])
    # 检查现有文化库命中是否含有实质条目（避免"案例·情境"叙述句）
    culture_has_substance = any(
        any(kw in h["section"] for kw in ["牌品", "棋品", "酒品", "看人", "条目", "面子", "人情", "拒酒", "敬酒", "座次"])
        for h in culture_hits_existing
    )
    if not culture_has_substance:
        # 优先检索"牌品看人/人情世故素材汇编"这种有条目的
        extra_q = "牌品 看人 老手 评价" if game in ("掼蛋","德州","桥牌","麻将","保皇") else f"{rank} 人情 面子 看人"
        extra = retrieve(extra_q)
        extra_hits = [h for h in extra["hits"]
                      if h["library"] == "文化与主观评价库"
                      and any(kw in h["section"] for kw in
                              ["牌品","棋品","酒品","看人","条目","面子","人情","拒酒","敬酒","座次","心理"])][:2]
        if extra_hits:
            by_lib["文化与主观评价库"] = extra_hits
            for h in extra_hits:
                if not any(m["section"] == h["section"] for m in meta["hits"]):
                    meta["hits"].append({
                        "source": h["source"], "library": h["library"],
                        "section": h["section"], "score": h["score"]
                    })

    lines = [_verdict(scene, game, rank), ""]

    # 第一层：规则速记
    rule_hits = by_lib.get("硬规则库", [])
    if rule_hits:
        lines.append("### 第一层 · 规则速记")
        lines.append(_extract_bullets(rule_hits, max_items=4, max_chars=140))
        lines.append("")

    # 第二层：人情与看人
    culture_hits = by_lib.get("文化与主观评价库", [])
    if culture_hits:
        lines.append("### 第二层 · 人情与看人（老手怎么看你）")
        lines.append(_extract_bullets(culture_hits, max_items=4, max_chars=140))
        lines.append("")

    # 第三层：战术
    tactic_hits = by_lib.get("战术与数学原理库", [])
    if tactic_hits:
        lines.append("### 第三层 · 高阶战术（带博弈原理）")
        lines.append(_extract_bullets(tactic_hits, max_items=4, max_chars=140))
        lines.append("")

    # 位阶剧本
    scene_hits = by_lib.get("场景位阶剧本库", [])
    if scene_hits:
        lines.append("### 位阶剧本要点")
        lines.append(_extract_bullets(scene_hits[:2], max_items=3, max_chars=140))
        lines.append("")

    # 变通提示
    lines.append("### ⚖ 变通提醒")
    lines.append(_flexibility_hint(scene, game, rank))
    lines.append("")

    if meta["coverage"] == "low":
        lines.append("> ⚠️ **知识库覆盖较低**：以上为有限相关内容。"
                     "配置 LLM API Key 后追问，可得到更深入的情景化推断（会标注【推测】）。")
        lines.append("")

    lines.append("---")
    lines.append("⚠️ **以上是「知识库检索」模式——只有片段拼凑，没有针对你今晚具体情境的策略。**")
    lines.append("")
    lines.append("**想要「今晚这局我具体怎么打」？** 在左侧 **⚙ LLM 接入** 填入你的 API Key：")
    lines.append("- 智谱 GLM（推荐，有免费额度）：bigmodel.cn 注册 → 控制台 → API Keys")
    lines.append("- 或 OpenAI / DeepSeek 任一兼容接口")
    lines.append("- 填好后点「保存配置」（会自动验证 key），重新发送，就能拿到分阶段的具体打法 + 可照搬的话术。")

    return "\n".join(lines), meta


def _extract_bullets(hits: list, max_items: int = 4, max_chars: int = 120) -> str:
    """从片段里抽取要点，组织成项目符号列表，而不是堆原文。
    策略：优先抽表格行、列表项、带冒号的定义句、加粗句。"""
    bullets = []
    for h in hits:
        if len(bullets) >= max_items:
            break
        section_short = h["section"].split(" / ")[-1] if " / " in h["section"] else h["section"]
        candidates = _extract_key_sentences(h["content"])
        for cand in candidates[:3]:  # 每段最多看3条，找合格的
            if len(bullets) >= max_items:
                break
            cand = cand.strip()
            if not cand or len(cand) < 6:
                continue
            # 清理 markdown 符号但保留加粗
            cand = cand.lstrip("-*• ").strip()
            # 跳过"纯标题行"：去掉加粗/书名号/中英冒号后太短的（通常是 "XXX：" 这种没内容的）
            stripped_check = re.sub(r'[*`《》「」"]', '', cand).strip()
            stripped_check = re.sub(r'[：:]+$'  , '', stripped_check).strip()
            stripped_check = stripped_check.strip('· ').strip()
            if len(stripped_check) < 8:
                continue
            # 跳过仍含来源标签的（双保险）
            if '[来源' in cand:
                continue
            if len(cand) > max_chars:
                cand = cand[:max_chars] + "…"
            bullets.append(f"- **{section_short}**：{cand}")
    return "\n".join(bullets) if bullets else "（暂无要点）"


def _extract_key_sentences(text: str) -> list[str]:
    """从一段文本里抽出最有信息量的句子。"""
    sents = []

    def is_noise(line: str) -> bool:
        """过滤无信息量的行。"""
        l = line.strip()
        if not l:
            return True
        # 引用来源行：> [来源：...] 或纯 [来源：...]
        if l.startswith(">") and "[来源" in l:
            return True
        # 任何"以来源标签为主"的行（含 + 拼接、含 ` 包裹、含 "通行打法"等通用标签）
        stripped = l.strip("`> ")
        if re.match(r'^(\[来源[：:].*?\]\s*[+\s]*)+$', stripped):
            return True
        if stripped.startswith("[来源") and stripped.endswith("]"):
            return True
        # 含 " + [来源" 这种拼接形式的来源标签行，直接判噪音
        if "[来源" in stripped and "+ [来源" in stripped:
            return True
        # 行内只要有 2 个以上 [来源：...] 标签，且去掉标签和加粗符号后实质内容很短，判为噪音
        src_tags = re.findall(r'\[来源[：:].*?\]', stripped)
        if len(src_tags) >= 2:
            non_tag = re.sub(r'\[来源[：:].*?\]', '', stripped)
            non_tag = re.sub(r'[*`>]', '', non_tag).strip(' +：:').strip()
            # 去掉中文/英文冒号后再判
            non_tag = non_tag.strip('：:').strip()
            if len(non_tag) < 25:
                return True
        # markdown 标题
        if l.startswith("#"):
            return True
        # 纯分隔线
        if set(l.strip("-*`> ")) <= set():
            return True
        # 纯符号/标点
        if len(l.strip("-*•>` ")) < 4:
            return True
        return False

    # 1. 优先表格行（| 分隔）—— 文化库的"看人"评价多是表格
    for line in text.split("\n"):
        line = line.strip()
        if line.startswith("|") and not re.match(r'^\|[\s:|-]+\|$', line):
            cells = [c.strip() for c in line.strip("|").split("|")]
            cells = [c for c in cells if c and c not in ("---",)]
            if cells:
                joined = " / ".join(cells[:3])
                if not is_noise(joined) and len(joined) > 8:
                    sents.append(joined)
    # 2. 列表项
    for line in text.split("\n"):
        line = line.strip()
        if re.match(r'^[-*•]\s+', line):
            content = re.sub(r'^[-*•]\s+', '', line)
            if not is_noise(content):
                sents.append(content)
    # 3. 带冒号的定义句（信息密度高）
    for line in text.split("\n"):
        line = line.strip()
        if "：" in line and 10 < len(line) < 150 and not is_noise(line):
            sents.append(line)
    # 4. 加粗句
    for line in text.split("\n"):
        line = line.strip()
        if "**" in line and 10 < len(line) < 150 and not is_noise(line):
            sents.append(line)

    # 去重 + 清理残留的来源标签
    seen = set()
    out = []
    for s in sents:
        # 清理行内 [来源：...] 标签
        s_clean = re.sub(r'\s*\[来源[：:].*?\]\s*', '', s).strip()
        s_clean = re.sub(r'`+', '', s_clean).strip()
        if not s_clean or len(s_clean) < 6:
            continue
        if s_clean not in seen:
            seen.add(s_clean)
            out.append(s_clean)

    # 兜底：如果结构化抽取落空（叙述型文本），按句号切分取前几句
    if not out:
        plain = re.sub(r'[#|>`*_\-]', ' ', text)
        plain = re.sub(r'\s+', ' ', plain).strip()
        # 按中文/英文句号切
        sents_plain = re.split(r'[。！？；\.\!\?;]', plain)
        for s in sents_plain:
            s = s.strip()
            if 12 < len(s) < 150:
                out.append(s)
                if len(out) >= 3:
                    break
    return out


def _verdict(scene: str, game: str, rank: str) -> str:
    """生成定位判词——这是回答的灵魂，必须犀利、具体。"""
    if not any([scene, game, rank]):
        return ("### 定位判词\n"
                "你没选参数就直接问——那我只能泛泛而谈。**建议先在左侧选定 场景/游戏/位阶**，"
                "或者在下方把情境说清楚，我才能给你今晚真能用的东西。")

    # 拼情境描述
    parts = []
    if scene: parts.append(f"**{scene}**")
    if game: parts.append(f"**{game}**")
    if rank: parts.append(f"**{rank}**")
    situation = " · ".join(parts) if parts else "未指明情境"

    # 判词核心——按位阶
    # 根据游戏类型选词：牌局/棋局/酒局
    if game == "酒桌":
        action_word = "赴这局酒"
        win_word = "喝得多"
        partner_word = "主家/上位者"
    elif game in ("围棋", "象棋"):
        action_word = "下这盘棋"
        win_word = "赢棋"
        partner_word = "上位者"
    else:
        action_word = "打这局牌"
        win_word = "赢牌"
        partner_word = "对门/主家"

    verdict_body = ""
    if "下位" in rank:
        verdict_body = (
            f"你今晚不是来{action_word}的，是来**被看见、被考察、被记为「懂事」**的。"
            f"核心 KPI：让{partner_word}舒服、有面子、自己甘当最佳配角。"
            f"**{win_word}是次要的，输得漂亮、衬托得体才是本事。**"
        )
    elif "上位" in rank:
        verdict_body = (
            "你今晚是**定调者与考察者**。你的话定基调、你的杯（你的牌/你的棋）定节奏。"
            "威仪与亲和要平衡——太严是刻薄，太松是被拿捏。**让大家舒服地散场，比赢了更有面子。**"
        )
    elif "平位" in rank:
        verdict_body = (
            "你今晚在**互相掂量**。几局下来看清对方是赌徒型、理性派、还是格局型——"
            "读懂一个人，比赢他十次更值钱。**不卑不亢，展示格调，快速识别路数。**"
        )
    else:
        verdict_body = "位阶未明，以下按通用原则给。选定下/平/上可得更精准的判词。"

    # 场景加成
    scene_note = ""
    if "政" in scene and "商" in scene:
        scene_note = "\n\n⚠️ **政商交叉是高危场景**：亲清是底线，牌桌酒桌可以交朋友，**绝不谈业务**。"
    elif "学" in scene:
        scene_note = "\n\n🎓 **学圈交叉**：保持风骨的同时懂人情，智力游戏（桥牌/围棋）是你的主场。"
    elif "纯政" in scene:
        scene_note = "\n\n🏛 **纯政内部**：牌桌是办公室的延伸，'懂事'比'能干'更被看，八项规定是硬约束。"
    elif "纯商" in scene:
        scene_note = "\n\n💼 **纯商内部**：利益驱动，效率导向，德州/掼蛋是看清决策风格的窗口。"

    return f"### 定位判词\n情境：{situation}\n\n{verdict_body}{scene_note}"


def _flexibility_hint(scene: str, game: str, rank: str) -> str:
    """情景变通提示——呼应"策略是相对的、情景化的"。
    每条都是「基础规范 vs 变通条件」双层结构。"""
    hints = []

    # 按位阶给基础变通（覆盖下/平/上三档）
    if "下位" in rank:
        hints.append("基础规范是「让」——但若上位者明显想试你深浅，可适度展示实力，"
                     "**赢一两手后立刻归功运气/教导**，反而显出可控的锋芒。")
    elif "上位" in rank:
        hints.append("基础规范是「定调」——但若全场过于拘谨，你可主动自嘲一两句、"
                     "或给下位者出彩机会，**威仪中带亲和，才不会被看作刻薄**。")
    elif "平位" in rank:
        hints.append("基础规范是「不卑不亢」——但若对方明显是长期合作对象，"
                     "可适度让对方一两手建立人情，**短期让步换长期信任**。")

    # 按游戏给专属变通
    if game == "酒桌":
        hints.append("拒酒基础规范是「硬盾（身体原因）+ 软盾（态度诚恳）」——"
                     "但若对方是至亲老友非正式场合，可破例小酌，**一致性比一次的拒绝更重要**。")
    elif game in ("掼蛋", "保皇") and "下位" in rank:
        hints.append("对门配合基础规范是「让牌权」——"
                     "但若对门连输两局颜面尽失，你**适度主动救场**反而显担当，别让对门太难堪。")
    elif game == "德州扑克" and "平位" in rank:
        hints.append("德州平位基础规范是「按数学频率诈唬」——"
                     "但若对手明显紧凶或松弱，**偏离均衡频率去剥削其倾向**更赚，均衡只是底线。")
    elif game in ("围棋", "象棋") and "下位" in rank:
        hints.append("受让子基础规范是「保守守势」——"
                     "但若上位者明显让你（让子过多），可放手一搏展示棋力，"
                     "**输了显学习态度，赢了归因让子**，分寸自己拿捏。")

    # 按场景给专属变通
    if "政" in scene and "商" in scene:
        hints.append("政商交往基础规范是「公开场合、不谈业务」——"
                     "但若涉及正当政企沟通，请走正式渠道，**牌桌酒桌只谈风月与文化**。")
    elif "学" in scene:
        hints.append("学圈基础规范是「保持风骨」——"
                     "但若对方（政/商）真心求教，可放下身段分享洞察，**风骨不等于傲慢**。")

    # 兜底
    if not hints:
        hints.append("所有规矩都有例外——**基础规范是稳态，变通要看具体关系、场合、对方性格**。")

    return "\n".join(f"- {h}" for h in hints[:3])


def _no_hit_reply(query: str, scene: str, game: str, rank: str) -> str:
    return (
        "### 知识库未直接命中\n"
        f"没找到与「{query}」强相关的内容。这通常意味着：\n\n"
        "- 问题超出本库范围（本库只讲牌桌/棋桌/酒桌/人情）\n"
        "- 或表述太宽泛\n\n"
        "**建议**：\n"
        "- 换个具体问法（如「掼蛋下位怎么进贡」「酒桌怎么拒头孢」）\n"
        "- 或在「⚙ 接入」配置 LLM API Key，开启通用知识兜底\n\n"
        f"当前参数：场景={scene or '空'} / 游戏={game or '空'} / 位阶={rank or '空'}"
    )
