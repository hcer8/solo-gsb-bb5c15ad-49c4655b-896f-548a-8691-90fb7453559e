"use strict";

const $ = (sel) => document.querySelector(sel);

// 当前解析结果（可变模型：编辑直接写回，重新编码时整体提交）
let tree = null;
let lastEncoded = "";

const SAMPLE_CT = 'multipart/mixed; boundary="outer-42"';
const SAMPLE_BODY = `This is the preamble. It is ignored by MIME readers.

--outer-42
Content-Type: text/plain; charset="utf-8"

第一部分：纯文本问候。
--outer-42
Content-Type: multipart/alternative; boundary=inner-7

--inner-7
Content-Type: text/plain

纯文本版本正文
--inner-7
Content-Type: text/html

<html><body><b>HTML 版本正文</b></body></html>
--inner-7--
--outer-42
Content-Type: application/octet-stream
Content-Transfer-Encoding: base64
Content-Disposition: attachment; filename="hello.txt"

aGVsbG8sIHdvcmxkIQ==
--outer-42--
This is the epilogue. It is also ignored.`;

const BAD_SAMPLE_CT = 'multipart/mixed; boundary="err-b"';
const BAD_SAMPLE_BODY = `--err-b
Content-Type text/plain; charset=utf-8
X-Ok: yes

这一行头部缺少冒号
--err-b
Content-Type: multipart/related

--inner
Content-Type: text/plain

内层 multipart 缺少 boundary 参数，整体按正文处理
--inner--
--err-b
Content-Type: text/html

<p>报文到此结束，缺少结束边界线</p>`;

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function show(id) { $(id).classList.remove("hidden"); }
function hide(id) { $(id).classList.add("hidden"); }

function setError(msg) {
  const banner = $("#error-banner");
  if (msg) {
    banner.textContent = msg;
    banner.classList.remove("hidden");
  } else {
    banner.classList.add("hidden");
  }
}

async function postJSON(url, payload) {
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const data = await res.json().catch(() => null);
  if (!res.ok) {
    const detail = data && data.detail ? data.detail : `HTTP ${res.status}`;
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return data;
}

// ---------------------------------------------------------------------------
// 解析
// ---------------------------------------------------------------------------

async function doParse() {
  setError("");
  let data;
  try {
    data = await postJSON("/api/parse", {
      contentType: $("#content-type").value,
      boundary: $("#boundary").value,
      body: $("#raw-body").value,
      mode: $("#mode").value,
    });
  } catch (err) {
    setError("解析请求失败：" + err.message);
    return;
  }
  renderResult(data);
}

const EOL_LABEL = { crlf: "CRLF", lf: "LF", mixed: "混合", none: "无" };

function renderResult(data) {
  // 元信息
  const meta = $("#meta");
  meta.innerHTML = "";
  if (data.contentType) {
    meta.appendChild(document.createTextNode("类型 "));
    meta.appendChild(el("code", "", data.contentType));
    meta.appendChild(document.createTextNode("　boundary "));
    meta.appendChild(el("code", "", data.boundary || "(无)"));
    meta.appendChild(document.createTextNode("　换行符 "));
    meta.appendChild(el("code", "", EOL_LABEL[data.lineEnding] || data.lineEnding));
  }

  // 问题列表
  const issues = data.issues || [];
  const counts = { error: 0, warning: 0, info: 0 };
  for (const iss of issues) counts[iss.level] = (counts[iss.level] || 0) + 1;
  $("#issues-summary").textContent =
    `共 ${issues.length} 条：错误 ${counts.error} · 警告 ${counts.warning} · 提示 ${counts.info}`;
  const list = $("#issues");
  list.innerHTML = "";
  if (!issues.length) {
    list.appendChild(el("div", "muted", "未发现问题，报文结构良好。"));
  }
  for (const iss of issues) list.appendChild(issueEl(iss));
  show("#issues-section");

  // 部件树
  if (data.ok && data.tree) {
    tree = data.tree;
    renderTree();
    const mhWrap = $("#message-headers-wrap");
    if (data.messageHeaders !== null && data.messageHeaders !== undefined) {
      $("#message-headers").value = data.messageHeaders;
      mhWrap.classList.remove("hidden");
    } else {
      mhWrap.classList.add("hidden");
    }
    show("#tree-section");
  } else {
    tree = null;
    hide("#tree-section");
  }
  hide("#output-section");
  $("#issues-section").scrollIntoView({ behavior: "smooth", block: "nearest" });
}

function issueEl(iss) {
  const div = el("div", "issue " + iss.level);
  const tag = { error: "错误", warning: "警告", info: "提示" }[iss.level] || iss.level;
  div.appendChild(el("span", "tag", tag));
  div.appendChild(document.createTextNode(
    (iss.path ? `[部件 ${iss.path}] ` : "") + iss.message));
  return div;
}

function renderTree() {
  const container = $("#tree");
  container.innerHTML = "";
  container.appendChild(renderNode(tree, 0));
}

function renderNode(node, depth) {
  const wrap = el("div", "node");
  for (const iss of node.issues || []) wrap.appendChild(issueEl(iss));
  if (node.preamble) {
    wrap.appendChild(labeledArea("前言（preamble，原样保留）", node.preamble,
      (v) => { node.preamble = v; }, 2));
  }
  for (const part of node.parts || []) wrap.appendChild(renderPart(part, depth));
  if (node.epilogue) {
    wrap.appendChild(labeledArea("尾声（epilogue，原样保留）", node.epilogue,
      (v) => { node.epilogue = v; }, 2));
  }
  return wrap;
}

function renderPart(part, depth) {
  const card = el("div", "part");

  const head = el("div", "part-head");
  head.appendChild(el("span", "part-title", "部件 " + part.path));
  head.appendChild(el("code", "badge", part.contentType || "(无 Content-Type)"));
  if (part.isMultipart) head.appendChild(el("span", "badge multi", "嵌套 multipart"));
  card.appendChild(head);

  for (const iss of part.issues || []) card.appendChild(issueEl(iss));

  if (part.headers && part.headers.length) {
    const table = el("table", "headers");
    for (const h of part.headers) {
      const tr = el("tr", h.valid ? "" : "invalid");
      tr.appendChild(el("td", "hname", h.name || "(无法解析)"));
      tr.appendChild(el("td", "hval", h.valid ? h.value : h.raw));
      table.appendChild(tr);
    }
    card.appendChild(table);
  } else {
    card.appendChild(el("div", "muted", "（无头部）"));
  }

  const headerRows = Math.min(8, Math.max(2, (part.rawHeaders.match(/\n/g) || []).length + 1));
  card.appendChild(labeledArea("头部（原始文本，可编辑）", part.rawHeaders,
    (v) => { part.rawHeaders = v; }, headerRows));

  if (part.isMultipart) {
    const sub = el("div", "children");
    const subMeta = el("div", "muted");
    subMeta.appendChild(document.createTextNode("子边界 "));
    subMeta.appendChild(el("code", "", part.multipart.boundary));
    sub.appendChild(subMeta);
    sub.appendChild(renderNode(part.multipart, depth + 1));
    card.appendChild(sub);
  } else {
    card.appendChild(labeledArea("正文（可编辑）", part.body == null ? "" : part.body,
      (v) => { part.body = v; }, 5));
  }
  return card;
}

function labeledArea(label, value, oninput, rows) {
  const wrap = el("div", "field");
  wrap.appendChild(el("label", "field-label", label));
  const ta = el("textarea", "mono");
  ta.rows = rows || 3;
  ta.value = value;
  ta.spellcheck = false;
  ta.addEventListener("input", () => oninput(ta.value));
  wrap.appendChild(ta);
  return wrap;
}

// ---------------------------------------------------------------------------
// 重新编码与下载
// ---------------------------------------------------------------------------

async function doEncode() {
  if (!tree) return;
  setError("");
  const mhWrap = $("#message-headers-wrap");
  const messageHeaders = mhWrap.classList.contains("hidden")
    ? null : $("#message-headers").value;
  let data;
  try {
    data = await postJSON("/api/encode", { tree, messageHeaders });
  } catch (err) {
    setError("编码请求失败：" + err.message);
    return;
  }
  lastEncoded = data.message;
  $("#encoded-output").value = data.message;
  $("#encode-meta").textContent =
    `大小 ${data.bytes} 字节 · 换行已统一为 CRLF · 使用原 boundary 组装`;
  show("#output-section");
  $("#output-section").scrollIntoView({ behavior: "smooth", block: "nearest" });
}

function doDownload() {
  if (!lastEncoded) return;
  const hasHeaders = !$("#message-headers-wrap").classList.contains("hidden");
  const blob = new Blob([lastEncoded], { type: "message/rfc822" });
  const url = URL.createObjectURL(blob);
  const a = el("a");
  a.href = url;
  a.download = hasHeaders ? "message.eml" : "message.txt";
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

// ---------------------------------------------------------------------------
// 输入区辅助
// ---------------------------------------------------------------------------

function fillSample(ct, body, mode) {
  $("#content-type").value = ct;
  $("#boundary").value = "";
  $("#raw-body").value = body;
  $("#mode").value = mode || "body";
  setError("");
}

function clearAll() {
  $("#content-type").value = "";
  $("#boundary").value = "";
  $("#raw-body").value = "";
  tree = null;
  lastEncoded = "";
  setError("");
  hide("#issues-section");
  hide("#tree-section");
  hide("#output-section");
}

async function importFile(event) {
  const file = event.target.files[0];
  if (!file) return;
  // 注意：浏览器 textarea 会将 CRLF 规范化为 LF；服务端会检测并提示，
  // 重新编码的输出始终使用 CRLF。
  $("#raw-body").value = await file.text();
  event.target.value = "";
}

// ---------------------------------------------------------------------------
// 事件绑定
// ---------------------------------------------------------------------------

$("#parse-btn").addEventListener("click", doParse);
$("#encode-btn").addEventListener("click", doEncode);
$("#download-btn").addEventListener("click", doDownload);
$("#sample-btn").addEventListener("click", () => fillSample(SAMPLE_CT, SAMPLE_BODY, "body"));
$("#bad-sample-btn").addEventListener("click", () => fillSample(BAD_SAMPLE_CT, BAD_SAMPLE_BODY, "body"));
$("#clear-btn").addEventListener("click", clearAll);
$("#file-input").addEventListener("change", importFile);
