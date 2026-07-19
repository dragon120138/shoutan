/* 手谈 v3 · 参考申论项目重构
   Tab: 资料速查(左树+右详情+两段式AI) / 一键生成 / 咨询模式 / 说明 / 设置
*/
(function () {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const $$ = (sel) => Array.from(document.querySelectorAll(sel));

  /* ============ 配置 ============ */
  const CFG_KEY = "shoutan_settings_v3";
  const loadCfg = () => { try { return JSON.parse(localStorage.getItem(CFG_KEY) || "{}"); } catch { return {}; } };
  const saveCfg = (c) => localStorage.setItem(CFG_KEY, JSON.stringify(c));
  const cfg = loadCfg();
  let GRAPH = null;
  let chatHistory = [];

  function getLlm() {
    return {
      apiKey: $("set-apikey").value.trim() || cfg.apiKey || "",
      baseUrl: $("set-baseurl").value.trim() || cfg.baseUrl || "https://open.bigmodel.cn/api/paas/v4/",
      model: $("set-model").value.trim() || cfg.model || "glm-5.2",
    };
  }
  function updateApiBadge() {
    const { apiKey } = getLlm();
    const badge = $("api-badge");
    if (apiKey) { badge.classList.add("ok"); $("api-text").textContent = "已就绪"; }
    else { badge.classList.remove("ok"); $("api-text").textContent = "未配置"; }
  }

  /* ============ Tab 切换 ============ */
  $$(".tab").forEach(tab => {
    tab.addEventListener("click", () => {
      const t = tab.dataset.tab;
      $$(".tab").forEach(x => x.classList.toggle("active", x.dataset.tab === t));
      $$(".tab-pane").forEach(p => p.classList.toggle("active", p.id === "tab-" + t));
    });
  });

  /* ============ 热度色阶（参考申论项目）============ */
  function heatColor(h) {
    if (h >= 0.95) return "#8b0000";
    if (h >= 0.90) return "#a83a32";
    if (h >= 0.85) return "#c25447";
    if (h >= 0.80) return "#d97746";
    if (h >= 0.75) return "#c49a2b";
    if (h >= 0.70) return "#8a6d2f";
    return "#6b8e5a";
  }
  function heatLabel(h) {
    if (h >= 0.90) return "🔥 极高频";
    if (h >= 0.80) return "高频";
    if (h >= 0.70) return "常用";
    return "参考";
  }

  /* ============ 极简 markdown 渲染 ============ */
  function escapeHtml(s) {
    return s.replace(/[&<>"']/g, c => ({ "&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;" }[c]));
  }
  function renderMd(text) {
    if (!text) return "";
    let t = escapeHtml(text);
    t = t.replace(/```([\s\S]*?)```/g, (_, c) => `<pre><code>${c.replace(/^\n/,"")}</code></pre>`);
    t = t.replace(/`([^`]+)`/g, "<code>$1</code>");
    t = t.replace(/((?:^\|[^\n]+\|\s*\n)+)/gm, (block) => {
      const rows = block.trim().split("\n").filter(r => r.trim());
      if (rows.length < 2) return block;
      const isSep = r => /^\|[\s:|-]+\|$/.test(r.trim());
      let html = "<table>";
      rows.forEach((row, idx) => {
        if (isSep(row)) return;
        const cells = row.replace(/^\||\|$/g,"").split("|").map(c=>c.trim());
        const tag = (idx === 0 || (idx === 1 && isSep(rows[0]))) ? "th" : "td";
        html += `<tr>${cells.map(c=>`<${tag}>${c}</${tag}>`).join("")}</tr>`;
      });
      return html + "</table>";
    });
    t = t.replace(/^####\s+(.+)$/gm, "<h4>$1</h4>");
    t = t.replace(/^###\s+(.+)$/gm, "<h3>$1</h3>");
    t = t.replace(/^##\s+(.+)$/gm, "<h3>$1</h3>");
    t = t.replace(/^#\s+(.+)$/gm, "<h3>$1</h3>");
    t = t.replace(/^&gt;\s?(.+)$/gm, "<blockquote>$1</blockquote>");
    t = t.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
    t = t.replace(/(?<!\*)\*([^*\n]+)\*(?!\*)/g, "<em>$1</em>");
    t = t.replace(/^(\s*)[-*]\s+(.+)$/gm, "<li>$2</li>");
    t = t.replace(/(<li>[\s\S]*?<\/li>)(?!\s*<li>)/g, "<ul>$1</ul>");
    t = t.split(/\n\n+/).map(chunk => {
      if (/^\s*<(h\d|ul|ol|pre|table|blockquote)/.test(chunk)) return chunk;
      if (!chunk.trim()) return "";
      return `<p>${chunk.replace(/\n/g,"<br>")}</p>`;
    }).join("\n");
    return t;
  }

  /* ============ 通用 SSE 流式读取（跨 chunk 安全）============ */
  async function streamSse(response, onMeta, onToken, onError) {
    const reader = response.body.getReader();
    const decoder = new TextDecoder("utf-8");
    let buf = "";
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      let idx;
      while ((idx = buf.indexOf("\n\n")) !== -1) {
        const raw = buf.slice(0, idx);
        buf = buf.slice(idx + 2);
        for (const line of raw.split("\n")) {
          if (!line.startsWith("data: ")) continue;
          let evt;
          try { evt = JSON.parse(line.slice(6)); } catch { continue; }
          if (evt.type === "meta" && onMeta) onMeta(evt.data);
          else if (evt.type === "token" && onToken) onToken(evt.data);
          else if (evt.type === "error" && onError) onError(evt.data);
        }
      }
    }
  }

  /* ============ 资料速查：构建树 ============ */
  async function loadGraph() {
    try {
      const resp = await fetch("/api/graph");
      GRAPH = await resp.json();
      renderTree();
      renderAbout();  // 顺便用 graph 数据填充说明
    } catch (e) {
      $("tree-body").innerHTML = `<div style="padding:20px;color:var(--vermilion)">图谱加载失败：${e.message}</div>`;
    }
  }

  function renderTree() {
    const body = $("tree-body");
    body.innerHTML = "";
    GRAPH.children.forEach((cat, ci) => {
      const catNode = buildNode(cat, 0, [ci]);
      body.appendChild(catNode);
    });
  }

  function buildNode(node, level, path) {
    const wrap = document.createElement("div");
    wrap.className = "tree-node";
    wrap.dataset.level = level;
    wrap.dataset.path = path.join(".");

    const label = document.createElement("div");
    label.className = "tree-label";
    const hasChildren = node.children && node.children.length;
    const hasDetail = node.judgment || node.doc;

    const arrow = document.createElement("span");
    arrow.className = "tree-arrow" + (hasChildren ? "" : " leaf");
    arrow.textContent = "▶";

    const dot = document.createElement("span");
    dot.className = "tree-dot";
    dot.style.background = node.heat ? heatColor(node.heat) : "var(--ink-faint)";

    const text = document.createElement("span");
    text.className = "tree-text";
    text.textContent = node.name;

    label.appendChild(arrow);
    label.appendChild(dot);
    label.appendChild(text);
    wrap.appendChild(label);

    // 子节点容器
    let childWrap = null;
    if (hasChildren) {
      childWrap = document.createElement("div");
      childWrap.className = "tree-children";
      node.children.forEach((c, i) => {
        childWrap.appendChild(buildNode(c, level + 1, [...path, i]));
      });
      wrap.appendChild(childWrap);
    }

    // 存节点数据供点击使用
    wrap._nodeData = node;
    wrap._path = path;

    label.addEventListener("click", (e) => {
      e.stopPropagation();
      // 展开/折叠
      if (hasChildren && childWrap) {
        const isOpen = childWrap.classList.toggle("open");
        arrow.classList.toggle("open", isOpen);
      }
      // 高亮
      $$(".tree-label").forEach(l => l.classList.remove("active"));
      label.classList.add("active");
      // 显示详情（叶节点）
      if (hasDetail) showDetail(node, path);
    });

    return wrap;
  }

  /* ============ 资料速查：右侧详情（两段式）============ */
  function showDetail(node, path) {
    const el = $("detail-content");
    const empty = $("detail-empty");
    empty.classList.add("hidden");
    el.classList.remove("hidden");

    const heat = node.heat || 0.5;
    const col = heatColor(heat);
    const heatPct = Math.round(heat * 100);

    // 找父级分类名
    const catName = GRAPH.children[path[0]] ? GRAPH.children[path[0]].name : "";

    let html = `<div class="detail-breadcrumb">${catName} › ${node.name}</div>`;
    html += `<div class="detail-title">${node.name}</div>`;
    html += `<div class="detail-heat-row">
      <span class="heat-badge" style="background:${col}22;color:${col}">${heatLabel(heat)} · ${heatPct}%</span>
      <div class="heat-bar"><div class="heat-bar-fill" style="width:${heatPct}%;background:${col}"></div></div>
    </div>`;

    if (node.judgment) {
      html += `<div class="detail-section"><div class="label warning">⚡ 速讲（一句话）</div><div class="content">${escapeHtml(node.judgment)}</div></div>`;
    }
    if (node.examLink) {
      html += `<div class="detail-section"><div class="label exam">🎯 场景关联</div><div class="content">${escapeHtml(node.examLink)}</div></div>`;
    }
    if (node.keywords && node.keywords.length) {
      html += `<div class="detail-section"><div class="label">🏷 关键词</div><div class="content">${node.keywords.map(k=>`<span class="kw-tag">${escapeHtml(k)}</span>`).join("")}</div></div>`;
    }

    // AI 解读区（默认占位 + 按钮）
    html += `<div id="ai-interpret">
      <div id="ai-interpret-header">
        <h4>🤖 AI 深度解读</h4>
        <span class="desc">基于「${node.doc || '通用知识'}」RAG 检索 + 结构化输出</span>
        <button id="ai-interpret-btn" class="btn-interpret">🤖 点击解读</button>
      </div>
      <div id="ai-interpret-body"><div class="placeholder">点击上方按钮，AI 将基于知识库给出结构化深度解读</div></div>
    </div>`;

    el.innerHTML = html;
    const btn = $("ai-interpret-btn");
    if (btn) btn.addEventListener("click", () => doInterpret(node));
  }

  async function doInterpret(node) {
    const { apiKey, baseUrl, model } = getLlm();
    if (!apiKey) { flashHint("请先在「⚙ 设置」填入 API Key"); return; }
    const btn = $("ai-interpret-btn");
    const body = $("ai-interpret-body");
    btn.disabled = true; btn.textContent = "解读中...";
    body.innerHTML = '<div class="placeholder">正在检索知识库并生成解读...</div>';

    let buffer = "";
    let firstToken = true;
    try {
      const resp = await fetch("/api/interpret", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          node_name: node.name, doc: node.doc || "",
          keywords: node.keywords || [], category: node.category || "",
          api_key: apiKey, base_url: baseUrl, model,
        }),
      });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      await streamSse(resp,
        (meta) => {
          if (meta && meta.hits_count !== undefined) {
            body.innerHTML = `<div class="rag-badge ${meta.hits_count > 0 ? 'high' : 'low'}">知识库命中 ${meta.hits_count} 段</div>`;
          }
        },
        (tok) => {
          if (firstToken) { body.innerHTML = ""; firstToken = false; }
          buffer += tok;
          body.innerHTML = renderMd(buffer) + '<span class="typing-cursor"></span>';
          body.scrollTop = body.scrollHeight;
        },
        (err) => { body.innerHTML = `<p style="color:var(--vermilion)">⚠ ${escapeHtml(err)}</p>`; }
      );
      if (buffer) body.innerHTML = renderMd(buffer);
    } catch (e) {
      body.innerHTML = `<p style="color:var(--vermilion)">⚠ ${escapeHtml(e.message)}</p>`;
    } finally {
      btn.disabled = false;
      btn.textContent = "🔄 重新解读";
    }
  }

  /* ============ 树搜索 ============ */
  $("tree-search-input").addEventListener("input", (e) => {
    const q = e.target.value.trim().toLowerCase();
    $$("#tree-body .tree-node").forEach(n => {
      const text = n.querySelector(".tree-text")?.textContent.toLowerCase() || "";
      const nodeData = n._nodeData;
      const kws = (nodeData?.keywords || []).join(" ").toLowerCase();
      const match = !q || text.includes(q) || kws.includes(q);
      n.style.display = match ? "" : "none";
      if (match && q && n.parentElement && n.parentElement.classList.contains("tree-children")) {
        n.parentElement.classList.add("open");
        const arrow = n.parentElement.previousElementSibling?.querySelector(".tree-arrow");
        if (arrow) arrow.classList.add("open");
      }
    });
  });

  /* ============ 一键生成 ============ */
  $("gen-btn").addEventListener("click", async () => {
    const { apiKey, baseUrl, model } = getLlm();
    if (!apiKey) { flashHint("请先在「⚙ 设置」填入 API Key"); return; }
    const scene = $("gen-scene").value;
    const game = $("gen-game").value;
    const rank = $("gen-rank").value;
    const bg = $("gen-bg").value.trim();
    if (!scene && !game && !rank && !bg) { flashHint("至少选一个参数或填背景"); return; }

    const btn = $("gen-btn");
    const result = $("gen-result");
    btn.disabled = true; btn.textContent = "生成中...";
    result.innerHTML = '<div class="placeholder">正在检索知识库 + 生成今晚策略...</div>';

    let buffer = ""; let firstToken = true;
    try {
      const resp = await fetch("/api/generate", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ scene, game, rank, background: bg, api_key: apiKey, base_url: baseUrl, model }),
      });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      await streamSse(resp,
        (meta) => {
          if (meta && meta.coverage) {
            result.innerHTML = `<div class="rag-badge ${meta.coverage}">知识库覆盖：${meta.coverage === 'high' ? '高' : meta.coverage === 'medium' ? '中' : '低'} · 命中 ${meta.hits_count} 段</div>`;
          }
        },
        (tok) => {
          if (firstToken) { result.innerHTML = ""; firstToken = false; }
          buffer += tok;
          result.innerHTML = renderMd(buffer) + '<span class="typing-cursor"></span>';
          result.scrollTop = result.scrollHeight;
        },
        (err) => { result.innerHTML = `<p style="color:var(--vermilion)">⚠ ${escapeHtml(err)}</p>`; }
      );
      if (buffer) result.innerHTML = renderMd(buffer);
    } catch (e) {
      result.innerHTML = `<p style="color:var(--vermilion)">⚠ ${escapeHtml(e.message)}</p>`;
    } finally {
      btn.disabled = false; btn.textContent = "🎯 生成今晚策略";
    }
  });

  // 背景快捷 chip
  $$(".quick-chips .chip").forEach(c => {
    c.addEventListener("click", () => {
      $("gen-bg").value = c.dataset.bg;
      $("gen-bg").focus();
    });
  });

  /* ============ 咨询模式 ============ */
  function appendChat(role, contentHtml) {
    const div = document.createElement("div");
    div.className = "chat-msg " + role;
    div.innerHTML = `<div class="chat-content">${contentHtml}</div>`;
    $("chat-history").appendChild(div);
    $("chat-history").scrollTop = $("chat-history").scrollHeight;
  }

  async function sendChat() {
    const { apiKey, baseUrl, model } = getLlm();
    if (!apiKey) { flashHint("请先在「⚙ 设置」填入 API Key"); return; }
    const input = $("chat-input");
    const text = input.value.trim();
    if (!text) return;
    appendChat("user", escapeHtml(text).replace(/\n/g, "<br>"));
    input.value = "";
    chatHistory.push({ role: "user", content: text });

    const btn = $("chat-btn");
    btn.disabled = true;
    // 插入一个空的 assistant 气泡用于流式
    const asstDiv = document.createElement("div");
    asstDiv.className = "chat-msg assistant";
    asstDiv.innerHTML = `<div class="chat-content"><span class="typing-cursor"></span></div>`;
    $("chat-history").appendChild(asstDiv);
    const contentEl = asstDiv.querySelector(".chat-content");
    $("chat-history").scrollTop = $("chat-history").scrollHeight;

    let buffer = ""; let firstToken = true;
    try {
      const resp = await fetch("/api/free-chat", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: text, history: chatHistory.slice(-16), api_key: apiKey, base_url: baseUrl, model }),
      });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      await streamSse(resp,
        (meta) => {
          if (meta && meta.coverage) {
            contentEl.innerHTML = `<div class="rag-badge ${meta.coverage}">知识库：${meta.coverage === 'high' ? '高覆盖' : meta.coverage === 'medium' ? '部分' : '通用兜底'}</div>`;
          }
        },
        (tok) => {
          if (firstToken) { contentEl.innerHTML = ""; firstToken = false; }
          buffer += tok;
          contentEl.innerHTML = renderMd(buffer) + '<span class="typing-cursor"></span>';
          $("chat-history").scrollTop = $("chat-history").scrollHeight;
        },
        (err) => { contentEl.innerHTML = `<p style="color:var(--vermilion)">⚠ ${escapeHtml(err)}</p>`; }
      );
      if (buffer) {
        contentEl.innerHTML = renderMd(buffer);
        chatHistory.push({ role: "assistant", content: buffer });
      }
    } catch (e) {
      contentEl.innerHTML = `<p style="color:var(--vermilion)">⚠ ${escapeHtml(e.message)}</p>`;
    } finally {
      btn.disabled = false;
    }
  }

  $("chat-btn").addEventListener("click", sendChat);
  $("chat-input").addEventListener("keydown", (e) => {
    if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); sendChat(); }
  });

  /* ============ 设置 ============ */
  // 初始化设置输入框
  $("set-apikey").value = cfg.apiKey || "";
  $("set-baseurl").value = cfg.baseUrl || "https://open.bigmodel.cn/api/paas/v4/";
  $("set-model").value = cfg.model || "glm-5.2";

  $$(".preset").forEach(p => {
    p.addEventListener("click", () => {
      $("set-baseurl").value = p.dataset.base;
      $("set-model").value = p.dataset.model;
    });
  });

  $("set-save").addEventListener("click", async () => {
    const apiKey = $("set-apikey").value.trim();
    const baseUrl = $("set-baseurl").value.trim();
    const model = $("set-model").value.trim();
    saveCfg({ apiKey, baseUrl, model });
    updateApiBadge();
    const status = $("set-status");
    if (!apiKey) {
      status.className = "set-status"; status.textContent = "已保存（未填 key，仅能浏览图谱速查）";
      return;
    }
    status.className = "set-status ok";
    status.innerHTML = "✓ 已保存。直接去「资料速查/一键生成/咨询模式」使用即可。<br><span style='color:var(--ink-light);font-size:12px'>提示：若遇到 429（速率限制），多因旗舰模型 glm-5.2 限流严，可换 glm-4-flash（限流宽松、免费额度大）。</span>";
    flashHint("✓ 配置已保存");
  });

  /* ============ 说明 ============ */
  function renderAbout() {
    if (!GRAPH) return;
    const cats = GRAPH.children.map(c => `<li><b>${c.name}</b>：${c.children.length} 个节点</li>`).join("");
    $("about-content").innerHTML = `
      <div class="about-section">
        <h3>🎯 项目要旨</h3>
        <p>「手谈」是面向政、商、学界青年精英的<b>牌桌棋桌酒桌策略导师</b>。</p>
        <p>它不教你赢牌——它教你在「讨好上位、结交平辈、考察下位」的牌局/棋局/酒局上，把每一手处理得<b>既有格调又有分寸</b>。牌品即人品，酒风见作风。</p>
      </div>
      <div class="about-section">
        <h3>📋 四大功能</h3>
        <ul>
          <li><b>🌳 资料速查</b>：左树浏览规则/战术/人情/场景，点节点秒看速讲，再点按钮触发 AI 深度解读（基于知识库 RAG）。</li>
          <li><b>🎯 一键生成</b>：选场景+游戏+位阶+背景，一个按钮出「今晚怎么打」的具体策略（分阶段动作+话术+变通+红线）。</li>
          <li><b>💬 咨询模式</b>：不受三参数约束，自由提问，RAG + 通用知识兜底，回答标注来源属性。</li>
          <li><b>⚙ 设置</b>：填入你自己的 API Key（智谱 GLM / OpenAI / DeepSeek 任一）。</li>
        </ul>
      </div>
      <div class="about-section">
        <h3>📚 资料来源（四库 + 人情汇编）</h3>
        <p>本系统知识库共 <b>${GRAPH.children.reduce((s,c)=>s+c.children.length,0)} 个节点</b>，分四大类：</p>
        <ul>${cats}</ul>
        <p style="margin-top:10px">每条策略都标注来源属性：<b>【学术研究】</b>（费孝通《乡土中国》、黄光国《人情与面子》、翟学伟、梁漱溟、金耀基）/ <b>【坊间共识】</b>（牌桌老手通用认知）/ <b>【过度推论】</b>（主观臆断但广泛存在）/ <b>【推测】</b>（知识库未覆盖、基于通用博弈论推断）。</p>
        <p>外部参考：<b>SocialAI-tianji/Tianji</b>（人情世故大模型）、社会化能力训练 Skill 等 GitHub 高星项目。</p>
      </div>
      <div class="about-section">
        <h3>🔒 安全边界</h3>
        <ul>
          <li>所有建议框定在<b>合规、八项规定、健康、自愿</b>之内。</li>
          <li>政商场合强调「亲清」底线——牌桌酒桌可交友，<b>绝不谈业务</b>。</li>
          <li>酒桌内容强调「健康、自愿、尊重身体」，提供以茶代酒方案。</li>
          <li>不虚构名人轶事或数据，不鼓励腐败或利益输送。</li>
        </ul>
      </div>
      <div class="about-section">
        <h3>🔐 隐私</h3>
        <p>你的 API Key 仅存在浏览器 <code>localStorage</code>，请求时带给后端转发，<b>绝不入库、不上传 GitHub</b>。后端是无状态转发。</p>
      </div>
    `;
  }

  /* ============ 工具：浮层提示 ============ */
  function flashHint(msg) {
    const div = document.createElement("div");
    div.style.cssText = "position:fixed;top:72px;left:50%;transform:translateX(-50%);background:var(--ink);color:#fff;padding:10px 22px;border-radius:4px;z-index:999;font-size:14px;letter-spacing:1px;box-shadow:var(--shadow-deep);";
    div.textContent = msg;
    document.body.appendChild(div);
    setTimeout(() => div.remove(), 2500);
  }

  /* ============ 启动 ============ */
  updateApiBadge();
  loadGraph();

})();
