// 阅读疑难 · 后台：右键菜单投递 + 转发 popup 的投递请求。
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




// 复用已打开的标签页：只在新标签首次导航时处理
const newTabs = new Set();

chrome.tabs.onCreated.addListener((tab) => {
  if (tab.id) newTabs.add(tab.id);
});

chrome.tabs.onUpdated.addListener((tabId, changeInfo, tab) => {
  // 只处理新创建的标签，且只在 URL 首次确定时
  if (!newTabs.has(tabId) || !changeInfo.url) return;
  
  const hashIndex = changeInfo.url.indexOf('#');
  if (hashIndex === -1) {
    newTabs.delete(tabId);
    return;
  }
  
  const baseUrl = changeInfo.url.substring(0, hashIndex);
  newTabs.delete(tabId);
  
  chrome.tabs.query({}, (tabs) => {
    const existingTab = tabs.find(t => 
      t.id !== tabId && 
      t.url && 
      t.url.split('#')[0] === baseUrl
    );
    
    if (existingTab) {
      chrome.tabs.update(existingTab.id, { url: changeInfo.url, active: true }, () => {
        chrome.windows.update(existingTab.windowId, { focused: true });
        chrome.tabs.remove(tabId);
      });
    }
  });
});

chrome.tabs.onRemoved.addListener((tabId) => {
  newTabs.delete(tabId);
});
