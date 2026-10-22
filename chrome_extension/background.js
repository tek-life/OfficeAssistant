// 阅读疑难 · 后台：右键菜单投递 + 转发 popup 的投递请求 + 复用已打开标签页回跳。
const ENDPOINT = "http://127.0.0.1:8765/questions";
const HEALTH = "http://127.0.0.1:8765/health";
const MENU_ID = "oaq-mark";

chrome.runtime.onInstalled.addListener(() => {
  chrome.contextMenus.create({
    id: MENU_ID,
    title: "标记疑难",
    contexts: ["selection"],
  });
});

function flashBadge(text, color) {
  chrome.action.setBadgeBackgroundColor({ color: color || "#2f6b28" });
  chrome.action.setBadgeText({ text });
  setTimeout(() => chrome.action.setBadgeText({ text: "" }), 2500);
}

async function selectionPayload(tabId) {
  if (tabId == null) return null;
  try {
    const res = await chrome.tabs.sendMessage(tabId, { type: "oaq:getSelection" });
    return (res && res.payload) || null;
  } catch (e) {
    return null;
  }
}

async function postQuestion(body) {
  const resp = await fetch(ENDPOINT, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!resp.ok) throw new Error("HTTP " + resp.status);
  return resp.json();
}

async function markSelection(tab, extra) {
  const payload = await selectionPayload(tab && tab.id);
  const body = Object.assign(
    { url: (tab && tab.url) || "", title: (tab && tab.title) || "" },
    payload || {},
    extra || {}
  );
  if (!body.quote && !body.question) {
    flashBadge("!", "#9a2b2b");
    return { ok: false, error: "没有可记录的内容" };
  }
  try {
    const out = await postQuestion(body);
    flashBadge("\u2713", "#2f6b28");
    return { ok: true, id: out.id };
  } catch (e) {
    flashBadge("!", "#9a2b2b");
    return { ok: false, error: "无法连接办公助手（" + e.message + "）" };
  }
}

chrome.contextMenus.onClicked.addListener((info, tab) => {
  if (info.menuItemId !== MENU_ID) return;
  markSelection(tab, {});
});

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (msg && msg.type === "oaq:mark") {
    chrome.tabs.query({ active: true, currentWindow: true }, (tabs) => {
      markSelection(tabs && tabs[0], msg.extra || {}).then(sendResponse);
    });
    return true;
  }
  if (msg && msg.type === "oaq:health") {
    fetch(HEALTH)
      .then((r) => r.json())
      .then((j) => sendResponse({ ok: true, health: j }))
      .catch((e) => sendResponse({ ok: false, error: String(e) }));
    return true;
  }
  return false;
});

// ─────────────────────────────────────────────────────────────
// 复用已打开的标签页：当新标签带着 #oaq= 锚点被打开时，
// 若已存在同一篇文章的标签，就激活那个标签、把锚点直接投递给它，
// 然后关掉这个多出来的新标签。
//
// 相比原先依赖 tabs.onCreated 记录新标签的做法，这里只看 URL 变化：
// MV3 后台 Service Worker 会被回收，onCreated 记下的 Set 会丢失，
// 而 tabs.onUpdated 每次都会唤醒后台，所以不会漏判。

// 从 URL 片段里取出最后一段 oaq= 的 token（片段里可能残留旧的 oaq）。
function parseOaqToken(url) {
  const text = url || "";
  const hashIndex = text.indexOf("#");
  if (hashIndex === -1) return "";
  let token = "";
  for (const part of text.slice(hashIndex + 1).split("&")) {
    if (part.indexOf("oaq=") === 0) token = part.slice(4);
  }
  return token;
}

// base64url(JSON) → 锚点对象，与 content.js 的 anchorFromHash 保持一致。
function decodeAnchorToken(token) {
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

// 去掉 oaq 锚点后的“同一篇文章”基准串；URL 解析失败时退回字符串裁剪。
function baseUrlOf(url) {
  const text = url || "";
  try {
    const u = new URL(text);
    const kept = u.hash.replace(/^#/, "").split("&")
      .filter((part) => part && part.indexOf("oaq=") !== 0);
    u.hash = kept.join("&");
    return u.href;
  } catch (e) {
    const hashIndex = text.indexOf("#");
    if (hashIndex === -1) return text;
    const kept = text.slice(hashIndex + 1).split("&")
      .filter((part) => part && part.indexOf("oaq=") !== 0);
    return kept.length ? text.slice(0, hashIndex) + "#" + kept.join("&")
      : text.slice(0, hashIndex);
  }
}

function decodeSafe(text) {
  try {
    return decodeURIComponent(text);
  } catch (e) {
    return text;
  }
}

// 同一篇文章判定：剥掉 oaq 片段后完全一致，或百分号编码归一后一致。
// 后者容忍 app 与浏览器对中文路径编码（%E8%AE%BA... 与原文）的差异。
function sameArticle(a, b) {
  if (!a || !b) return false;
  if (a === b) return true;
  return decodeSafe(a) === decodeSafe(b);
}

// 把锚点投递给已打开的标签。内容脚本可能尚未注入，短时重试；
// 多次仍失败就退回改 URL 片段（走 content.js 的 hashchange 流程）。
function deliverJump(tabId, url, anchor, attempt) {
  const tries = attempt || 0;
  chrome.tabs.sendMessage(tabId, { type: "oaq:jump", anchor }, () => {
    if (!chrome.runtime.lastError) return;
    if (tries < 6) {
      setTimeout(() => deliverJump(tabId, url, anchor, tries + 1), 250);
    } else {
      chrome.tabs.update(tabId, { url }, () => void chrome.runtime.lastError);
    }
  });
}

chrome.tabs.onUpdated.addListener((tabId, changeInfo) => {
  const url = changeInfo.url;
  if (!url || url.indexOf("oaq=") === -1) return;

  const token = parseOaqToken(url);
  if (!token) return;
  const target = baseUrlOf(url);

  chrome.tabs.query({}, (tabs) => {
    const existing = (tabs || []).find(
      (t) => t.id !== tabId && t.url && sameArticle(baseUrlOf(t.url), target)
    );
    if (!existing) return;

    chrome.tabs.update(existing.id, { active: true }, () => {
      chrome.windows.update(existing.windowId, { focused: true });
      deliverJump(existing.id, url, decodeAnchorToken(token), 0);
      if (existing.id !== tabId) {
        chrome.tabs.remove(tabId, () => void chrome.runtime.lastError);
      }
    });
  });
});
