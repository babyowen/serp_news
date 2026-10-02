"use strict";
document.querySelectorAll(".news-summary").forEach((details) => {
  const status = document.createElement("div");
  status.className = "small text-muted mt-2";
  status.setAttribute("role", "status");
  status.setAttribute("aria-live", "polite");
  const body = document.createElement("div");
  body.className = "mt-2";
  body.style.whiteSpace = "pre-wrap";
  body.style.overflowWrap = "anywhere";
  const search = document.createElement("div");
  search.className = "small text-muted mt-2";
  const retry = document.createElement("button");
  retry.type = "button";
  retry.className = "btn btn-sm btn-outline-secondary mt-2";
  retry.hidden = true;
  retry.textContent = "重试读取";
  details.append(status, body, search, retry);
  let pending = false;
  let loaded = false;
  async function load() {
    if (pending || loaded || !details.open) return;
    pending = true;
    retry.hidden = true;
    status.textContent = "正在读取…";
    try {
      const response = await fetch(details.dataset.summaryUrl, {
        headers: { Accept: "application/json" }, cache: "no-store"
      });
      if (!response.ok) throw new Error("summary_unavailable");
      const data = await response.json();
      body.textContent = data.short_summary || "暂无摘要";
      search.textContent = "首次命中检索词：" + (data.search_keyword || "未记录");
      status.textContent = "";
      loaded = true;
    } catch (_) {
      status.textContent = "读取失败，请重试。";
      retry.hidden = false;
    } finally {
      pending = false;
    }
  }
  details.addEventListener("toggle", load);
  retry.addEventListener("click", load);
});
