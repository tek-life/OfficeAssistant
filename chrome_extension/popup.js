// 阅读疑难 · 弹窗：补一句问题/分类后投递到办公助手。
const $ = (id) => document.getElementById(id);

async function init() {
  const status = $("status");
  let tab = null;
  try {
    const tabs = await chrome.tabs.query({ active: true, currentWindow: true });
    tab = tabs && tabs[0];
  } catch (e) {
    tab = null;
  }
  window.__tab = tab;
  let payload = null;
  if (tab && tab.id != null) {
    try {
      const res = await chrome.tabs.sendMessage(tab.id, { type: "oaq:getSelection" });
      payload = (res && res.payload) || null;
    } catch (e) {
      payload = null;
    }
  }
  window.__payload = payload;
  const quote = payload && payload.quote;
  $("quote").textContent = quote || "（未选中文字：先在网页上选中句子，或只记录这条疑问）";
}

$("mark").addEventListener("click", async () => {
  const status = $("status");
  status.textContent = "正在记录…";
  const extra = {
    question: $("question").value.trim(),
    category: $("category").value.trim(),
    priority: Number($("priority").value) || 0,
  };
  try {
    const resp = await chrome.runtime.sendMessage({ type: "oaq:mark", extra });
    if (resp && resp.ok) {
      status.textContent = "已记录 \u2713 可在办公助手里查看";
      setTimeout(() => window.close(), 700);
    } else {
      status.textContent = "记录失败：" + ((resp && resp.error) || "未知错误");
    }
  } catch (e) {
    status.textContent = "记录失败：" + e.message;
  }
});

init();
