// 阅读疑难 · 内容脚本：采集选中文字的锚点，并从 #oaq= 片段回跳高亮。
(() => {
  const CONTEXT = 24;
  const HIGHLIGHT_MS = 30000;

  function collectTextNodes() {
    const nodes = [];
    let text = "";
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT, {
      acceptNode(node) {
        const parent = node.parentElement;
        if (!parent) return NodeFilter.FILTER_REJECT;
        const tag = parent.tagName;
        if (tag === "SCRIPT" || tag === "STYLE" || tag === "NOSCRIPT" || tag === "TEMPLATE") {
          return NodeFilter.FILTER_REJECT;
        }
        return NodeFilter.FILTER_ACCEPT;
      },
    });
    let node;
    while ((node = walker.nextNode())) {
      nodes.push(node);
      text += node.nodeValue;
    }
    return { nodes, text };
  }

  function normalize(text) {
    let norm = "";
    const map = [];
    let prevSpace = false;
    for (let i = 0; i < text.length; i += 1) {
      const ch = text[i];
      if (/\s/.test(ch)) {
        if (!prevSpace) {
          norm += " ";
          map.push(i);
          prevSpace = true;
        }
      } else {
        norm += ch;
        map.push(i);
        prevSpace = false;
      }
    }
    return { norm, map };
  }

  function squeeze(text) {
    return (text || "").replace(/\s+/g, " ").trim();
  }

  // 回跳链接会在片段里追加 oaq= 锚点：采集时必须剥掉这一段，
  // 否则在跳转打开的文章里继续标记，新疑难会继承上一次的锚点。
  function pageUrl() {
    const { href, hash } = location;
    if (!hash) return href;
    const kept = hash.replace(/^#/, "").split("&")
      .filter((part) => part && part.indexOf("oaq=") !== 0);
    const head = href.slice(0, href.length - hash.length);
    return kept.length ? `${head}#${kept.join("&")}` : head;
  }

  function buildPayload() {
    const sel = window.getSelection();
    const quote = sel ? sel.toString().trim() : "";
    const payload = { url: pageUrl(), title: document.title, quote, anchor: null };
    if (!sel || sel.rangeCount === 0 || sel.isCollapsed || !quote) return payload;
    const range = sel.getRangeAt(0);
    const { nodes, text } = collectTextNodes();
    let offset = -1;
    let acc = 0;
    for (const node of nodes) {
      if (node === range.startContainer) {
        offset = acc + range.startOffset;
        break;
      }
      acc += node.nodeValue.length;
    }
    if (offset < 0) {
      const idx = text.indexOf(quote);
      offset = idx >= 0 ? idx : -1;
    }
    if (offset < 0) {
      payload.anchor = { q: quote, p: "", s: "", o: -1 };
      return payload;
    }
    const prefix = squeeze(text.slice(Math.max(0, offset - CONTEXT), offset));
    const suffix = squeeze(text.slice(offset + quote.length, offset + quote.length + CONTEXT));
    payload.anchor = { q: quote, p: prefix, s: suffix, o: offset };
    return payload;
  }

  function anchorFromHash() {
    const hash = location.hash.replace(/^#/, "");
    if (!hash) return null;
    // 片段里可能残留多次标记留下的旧 oaq：只认最后追加的那一个。
    let token = "";
    for (const part of hash.split("&")) {
      if (part.indexOf("oaq=") === 0) token = part.slice(4);
    }
    if (!token) return null;
    try {
      const b64 = token.replace(/-/g, "+").replace(/_/g, "/");
      const padded = b64 + "=".repeat((4 - (b64.length % 4)) % 4);
      const binary = atob(padded);
      const bytes = Uint8Array.from(binary, (c) => c.charCodeAt(0));
      return JSON.parse(new TextDecoder("utf-8").decode(bytes));
    } catch (e) {
      return null;
    }
  }

  function rangeFromOffsets(startRaw, lengthRaw) {
    const { nodes, text } = collectTextNodes();
    const endRaw = Math.min(text.length, startRaw + lengthRaw);
    let acc = 0;
    let startNode = null;
    let startLocal = 0;
    let endNode = null;
    let endLocal = 0;
    for (const node of nodes) {
      const len = node.nodeValue.length;
      if (startNode === null && startRaw <= acc + len) {
        startNode = node;
        startLocal = startRaw - acc;
      }
      if (startNode !== null && endRaw <= acc + len) {
        endNode = node;
        endLocal = endRaw - acc;
        break;
      }
      acc += len;
    }
    if (!startNode || !endNode) return null;
    const range = document.createRange();
    range.setStart(startNode, Math.max(0, startLocal));
    range.setEnd(endNode, Math.max(0, endLocal));
    return range;
  }

  let styleReady = false;
  function ensureHighlightStyle() {
    if (styleReady) return;
    const style = document.createElement("style");
    style.setAttribute("data-oaq", "1");
    style.textContent =
      "::highlight(oaq){background-color:#ffe08a;color:inherit;" +
      "text-decoration:underline;text-decoration-color:#c9781a;" +
      "text-decoration-thickness:2px;text-underline-offset:2px;}";
    document.documentElement.appendChild(style);
    styleReady = true;
  }

  // 兜底高亮：页面不支持 CSS Custom Highlight API 时，用 <mark> 包住命中的文字。
  let wrappedMarks = [];

  function clearWrappedMarks() {
    for (const mark of wrappedMarks) {
      const parent = mark.parentNode;
      if (!parent) continue;
      parent.replaceChild(document.createTextNode(mark.textContent), mark);
      parent.normalize();
    }
    wrappedMarks = [];
  }

  function markRange(range) {
    clearWrappedMarks();
    const nodes = [];
    if (range.commonAncestorContainer.nodeType === 3) {
      nodes.push(range.commonAncestorContainer);
    } else {
      const walker = document.createTreeWalker(
        range.commonAncestorContainer, NodeFilter.SHOW_TEXT, null);
      let node;
      while ((node = walker.nextNode())) {
        if (range.intersectsNode(node) && node.nodeValue.trim()) nodes.push(node);
      }
    }
    for (const node of nodes) {
      const start = node === range.startContainer ? range.startOffset : 0;
      const end = node === range.endContainer
        ? range.endOffset : node.nodeValue.length;
      if (end <= start) continue;
      let target = node;
      target.splitText(Math.min(end, target.nodeValue.length));
      const middle = start > 0 ? target.splitText(start) : target;
      const mark = document.createElement("mark");
      mark.setAttribute("data-oaq", "mark");
      mark.style.cssText =
        "background-color:#ffe08a;color:inherit;text-decoration:underline;" +
        "text-decoration-color:#c9781a;text-decoration-thickness:2px;";
      middle.parentNode.insertBefore(mark, middle);
      mark.appendChild(middle);
      wrappedMarks.push(mark);
    }
  }

  // 每次跳转只保留一份高亮：先清掉上一次的高亮和它的清除定时器，
  // 否则旧定时器会在 HIGHLIGHT_MS 后把新跳转的高亮一并抹掉。
  let highlightTimer = null;

  function clearHighlight() {
    if (highlightTimer) {
      clearTimeout(highlightTimer);
      highlightTimer = null;
    }
    if (window.CSS && CSS.highlights) CSS.highlights.delete("oaq");
    clearWrappedMarks();
  }

  function scheduleHighlightClear() {
    if (highlightTimer) clearTimeout(highlightTimer);
    highlightTimer = setTimeout(() => {
      highlightTimer = null;
      clearHighlight();
    }, HIGHLIGHT_MS);
  }

  function highlightRange(range) {
    clearHighlight();
    const container = range.startContainer;
    const el = container.nodeType === 1 ? container : container.parentElement;
    if (el) el.scrollIntoView({ block: "center", behavior: "smooth" });
    if (window.CSS && CSS.highlights && typeof Highlight === "function") {
      ensureHighlightStyle();
      CSS.highlights.set("oaq", new Highlight(range));
    } else {
      markRange(range);
    }
    scheduleHighlightClear();
    return true;
  }

  function locateRange(anchor) {
    const quote = (anchor.q || "").trim();
    if (!quote) return null;
    const { text } = collectTextNodes();
    const { norm, map } = normalize(text);
    const normQuote = normalize(quote).norm.trim();
    if (!normQuote) return null;
    const pre = (anchor.p || "").trim();
    const suf = (anchor.s || "").trim();
    // 同一句话可能在页面里出现多次：先按前/后文打分，
    // 分数相同时再参考采集时的字符偏移 o，避免总是命中第一处。
    const offsetHint = typeof anchor.o === "number" ? anchor.o : -1;
    let idx = norm.indexOf(normQuote);
    let bestIdx = -1;
    let bestScore = -1;
    let bestDist = Infinity;
    while (idx >= 0) {
      let score = 0;
      if (pre && norm.slice(Math.max(0, idx - pre.length), idx) === pre) score += 2;
      if (suf && norm.slice(idx + normQuote.length, idx + normQuote.length + suf.length) === suf) score += 2;
      const dist = offsetHint >= 0 ? Math.abs(map[idx] - offsetHint) : Infinity;
      if (score > bestScore || (score === bestScore && dist < bestDist)) {
        bestScore = score;
        bestDist = dist;
        bestIdx = idx;
      }
      idx = norm.indexOf(normQuote, idx + 1);
    }
    if (bestIdx < 0) return null;
    const startRaw = map[bestIdx];
    const endNorm = bestIdx + normQuote.length - 1;
    const endRaw = endNorm >= 0 && endNorm < map.length ? map[endNorm] + 1 : text.length;
    return rangeFromOffsets(startRaw, Math.max(1, endRaw - startRaw));
  }

  // 页面内容可能是异步渲染出来的：定位失败时短期反复重试，成功即停。
  const JUMP_RETRY_MS = 400;
  const JUMP_RETRY_LIMIT = 12;
  let retryTimer = null;
  let retryLeft = 0;

  function stopRetry() {
    if (retryTimer) {
      clearTimeout(retryTimer);
      retryTimer = null;
    }
    retryLeft = 0;
  }

  function tryJump() {
    const anchor = anchorFromHash();
    if (!anchor || !anchor.q) return true;
    const range = locateRange(anchor);
    if (range) {
      stopRetry();
      highlightRange(range);
      return true;
    }
    if (retryLeft > 0) {
      retryLeft -= 1;
      retryTimer = setTimeout(tryJump, JUMP_RETRY_MS);
    }
    return false;
  }

  function applyJump() {
    stopRetry();
    clearHighlight();
    retryLeft = JUMP_RETRY_LIMIT;
    tryJump();
  }

  if (typeof chrome !== "undefined" && chrome.runtime && chrome.runtime.onMessage) {
    chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
      if (msg && msg.type === "oaq:getSelection") {
        sendResponse({ ok: true, payload: buildPayload() });
      }
      return true;
    });
  }

  window.addEventListener("hashchange", applyJump);
  window.addEventListener("pageshow", applyJump);
  applyJump();
})();
