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
    endLanding();
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

  // 返回包含目标元素的可滚动祖先（内容确实超出时才滚动）；没有则返回
  // null，表示由整页窗口滚动。
  function scrollableAncestor(el) {
    let node = el;
    while (node && node !== document.body && node !== document.documentElement) {
      const style = window.getComputedStyle(node);
      const oy = style.overflowY;
      if ((oy === "auto" || oy === "scroll" || oy === "overlay") &&
          node.scrollHeight > node.clientHeight + 1) {
        return node;
      }
      node = node.parentElement;
    }
    return null;
  }

  // 目标区间顶部对齐到视口高度的这个比例处，让目标上方留出阅读上下文
  const LANDING_RATIO = 0.33;
  const SETTLE_INTERVAL_MS = 80;
  const SETTLE_MIN_MS = 1200;
  const SETTLE_MAX_MS = 3000;
  const SETTLE_TOLERANCE_PX = 2;
  const SETTLE_STABLE_TICKS = 3;

  let settleTimer = null;
  let settleStartedAt = 0;
  let settleStableTicks = 0;
  let settleLastTarget = null;
  let activeAnchor = null;
  let activeRange = null;

  function stopSettleTimer() {
    if (settleTimer) {
      clearTimeout(settleTimer);
      settleTimer = null;
    }
    settleStableTicks = 0;
    settleLastTarget = null;
  }

  // 结束本次跳转的落点校正：用户接管滚动、或高亮结束时调用
  function endLanding() {
    stopSettleTimer();
    activeAnchor = null;
    activeRange = null;
  }

  // 区间相对其滚动容器的落点信息：drift 是当前位置与目标的像素差，
  // apply 把区间顶部推到视口约 1/3 处并返回目标滚动量
  function landingMetrics(range) {
    const container = range.startContainer;
    const el = container.nodeType === 1 ? container : container.parentElement;
    let rect = null;
    try {
      rect = range.getBoundingClientRect();
    } catch (e) {
      rect = null;
    }
    if (!rect || (!rect.height && !rect.width)) return null;
    const scroller = scrollableAncestor(el);
    if (scroller) {
      const box = scroller.getBoundingClientRect();
      const offset = scroller.clientHeight * LANDING_RATIO;
      return {
        drift: rect.top - box.top - offset,
        apply(behavior) {
          const top = Math.max(0, scroller.scrollTop + rect.top - box.top - offset);
          scroller.scrollTo({ top, behavior });
          return top;
        },
      };
    }
    const offset = window.innerHeight * LANDING_RATIO;
    return {
      drift: rect.top - offset,
      apply(behavior) {
        const top = Math.max(0, window.scrollY + rect.top - offset);
        window.scrollTo({ top, behavior });
        return top;
      },
    };
  }

  // 用整段瞬时定位而不是平滑滚动：页面自身的 scroll-behavior:smooth 会把
  // 滚动变成一段动画，动画途中任何布局变动或再次滚动都会把它打断、停在半
  // 路，于是高亮落在视口之外，看起来像"跳到了上一段"。
  function scrollRangeIntoView(range) {
    const metrics = landingMetrics(range);
    if (metrics) {
      metrics.apply("instant");
      return;
    }
    const container = range.startContainer;
    const el = container.nodeType === 1 ? container : container.parentElement;
    if (el) el.scrollIntoView({ block: "center", behavior: "instant" });
  }

  // 跳转后短暂跟踪落点：页面（字体、图片解码、目录展开）继续改变布局时按
  // 当前矩形重新校正；区间失效则按锚点重新定位；位置稳定即收工。
  function settleLanding(anchor, range) {
    stopSettleTimer();
    activeAnchor = anchor || null;
    activeRange = range;
    settleStartedAt = Date.now();
    const tick = () => {
      settleTimer = null;
      const elapsed = Date.now() - settleStartedAt;
      if (elapsed > SETTLE_MAX_MS) {
        stopSettleTimer();
        return;
      }
      let metrics = landingMetrics(activeRange);
      if (!metrics && activeAnchor) {
        const fresh = locateRange(activeAnchor);
        if (fresh) {
          activeRange = fresh;
          paintHighlight(fresh);
          metrics = landingMetrics(fresh);
        }
      }
      if (!metrics) {
        if (elapsed > SETTLE_MIN_MS) {
          stopSettleTimer();
          return;
        }
      } else if (Math.abs(metrics.drift) <= SETTLE_TOLERANCE_PX) {
        settleStableTicks += 1;
        if (settleStableTicks >= SETTLE_STABLE_TICKS && elapsed >= SETTLE_MIN_MS) {
          stopSettleTimer();
          return;
        }
      } else {
        settleStableTicks = 0;
        const target = metrics.apply("instant");
        if (target === settleLastTarget) {
          stopSettleTimer();
          return;
        }
        settleLastTarget = target;
      }
      settleTimer = setTimeout(tick, SETTLE_INTERVAL_MS);
    };
    tick();
  }

  // 布局再次变化时（字体就绪、页面 load 完成）若仍在本轮跳转内就再校正一次
  function realignLanding() {
    if (!activeRange || !activeAnchor) return;
    const metrics = landingMetrics(activeRange);
    if (metrics && Math.abs(metrics.drift) > SETTLE_TOLERANCE_PX) {
      metrics.apply("instant");
    }
  }

  function paintHighlight(range) {
    if (window.CSS && CSS.highlights && typeof Highlight === "function") {
      ensureHighlightStyle();
      CSS.highlights.set("oaq", new Highlight(range));
    } else {
      markRange(range);
    }
    scheduleHighlightClear();
  }

  function highlightRange(range, anchor) {
    clearHighlight();
    scrollRangeIntoView(range);
    paintHighlight(range);
    settleLanding(anchor, range);
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
      highlightRange(range, anchor);
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

  // 用户自己开始滚动就把视口交还给用户，不再把位置抢回来
  const SCROLL_KEYS = {
    ArrowUp: 1, ArrowDown: 1, ArrowLeft: 1, ArrowRight: 1,
    PageUp: 1, PageDown: 1, Home: 1, End: 1, " ": 1,
  };
  window.addEventListener("wheel", endLanding, { passive: true });
  window.addEventListener("touchstart", endLanding, { passive: true });
  window.addEventListener("keydown", (e) => {
    if (SCROLL_KEYS[e.key]) endLanding();
  });
  window.addEventListener("load", realignLanding);
  if (document.fonts && document.fonts.ready) {
    document.fonts.ready.then(realignLanding).catch(() => {});
  }

  window.addEventListener("hashchange", applyJump);
  window.addEventListener("pageshow", applyJump);
  applyJump();
})();

