# MIME Multipart 报文检查器

基于 **FastAPI + 原生前端**（无框架、无构建步骤）的 MIME `multipart/*` 报文检查工具。
粘贴 Content-Type、boundary 与原始报文后，系统按 **RFC 2046 / RFC 2045** 解析嵌套
multipart、引号参数与 CRLF 边界，展示每个部件的头部、正文与解析错误；可在线修改
任意部件后按原 boundary 规则重新编码，并下载结果报文。

## 功能

- **RFC 2046 解析**
  - 嵌套 `multipart/*` 递归解析（mixed / alternative / related / form-data …）
  - 引号参数：支持 `boundary="a;b"`、转义 `\"`、`\\`，引号内的 `;` 不误判为分隔符
  - 边界行识别：`--boundary` / `--boundary--`，允许尾部 transport padding（空格/制表符）
  - 前言（preamble）/ 尾声（epilogue）识别与保留
  - 头部折叠行（folding）展开、Content-Type 参数解析
- **错误与警告报告**（定位到部件路径，如 `部件 2.1`）
  - 缺少结束边界线、找不到边界分隔线
  - 头部行缺少冒号、头部块以续行起始、非法头部名
  - multipart 部件缺少 boundary 参数
  - boundary 超过 70 字符、含非法字符、以空格结尾
  - 报文使用 LF 或混用换行（RFC 2046 要求 CRLF）
- **编辑与重新编码**
  - 每个部件的原始头部与正文均可直接编辑
  - 按原 boundary 规则重新组装，换行统一为 CRLF
  - 完整报文模式下保留并回写顶层头部（Subject、From 等）
  - 一键下载（`.eml` / `.txt`）
- **两种输入模式**：仅主体（multipart body）/ 完整报文（含头部，自动提取 Content-Type）
- 支持从文件导入报文；内置正常与含错误两个示例

## 快速开始

### Docker Compose（推荐）

```bash
docker compose up --build
```

然后浏览器访问 <http://localhost:8000>

### 仅 Docker

```bash
docker build -t mime-inspector .
docker run -p 8000:8000 mime-inspector
```

### 本地开发

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
uvicorn app.main:app --reload --port 8000
```

## 使用方法

1. 选择报文模式（仅主体 / 完整报文）。
2. 粘贴 Content-Type（如 `multipart/mixed; boundary="frontier"`）；boundary 留空时自动从
   Content-Type 提取，也可显式填写覆盖。
3. 粘贴原始报文（或点击「从文件导入」），点击 **解析**。
4. 查看问题列表与部件树；直接在部件卡片中编辑头部或正文。
5. 点击 **重新编码** 预览结果，点击 **下载报文** 保存。

> 提示：浏览器 `<textarea>` 会把粘贴内容的 CRLF 规范化为 LF，服务端会检测并提示；
> 重新编码的输出始终使用规范的 CRLF。通过 API 直接提交可保留原始 CRLF。

## API

### `POST /api/parse`

```json
{
  "contentType": "multipart/mixed; boundary=\"abc\"",
  "boundary": "",
  "body": "--abc\r\nContent-Type: text/plain\r\n\r\nhello\r\n--abc--\r\n",
  "mode": "body"
}
```

- `mode`：`"body"`（仅主体）或 `"message"`（完整报文，自动切分并解析顶层头部）
- `boundary`：可选，优先级高于 Content-Type 中的 boundary 参数

响应：`ok`、`contentType`、`params`、`boundary`、`lineEnding`、`messageHeaders`、
`tree`（嵌套部件树，含每个部件的 `headers` / `body` / `issues`）、`issues`（扁平列表）。

### `POST /api/encode`

```json
{ "tree": { "boundary": "abc", "parts": [ ... ] }, "messageHeaders": null }
```

将（可能已编辑的）部件树按原 boundary 重新编码为 CRLF 报文，返回
`{ "ok", "message", "bytes" }`。

### `GET /api/health`

健康检查，返回 `{"status": "ok"}`。

```bash
curl -s -X POST http://localhost:8000/api/parse \
  -H 'Content-Type: application/json' \
  -d '{"contentType":"multipart/mixed; boundary=abc","body":"--abc\r\n\r\nhi\r\n--abc--","mode":"body"}'
```

## 项目结构

```
app/
  main.py            # FastAPI 应用与 API 路由
  mime_parser.py     # RFC 2046 解析器与重编码器（无第三方依赖）
  static/
    index.html       # 单页界面
    app.js           # 原生 JS：解析渲染、编辑绑定、编码与下载
    style.css
tests/
  test_mime_parser.py  # 解析器单元测试
  test_api.py          # API 集成测试
Dockerfile
docker-compose.yml
requirements.txt       # 运行依赖
requirements-dev.txt   # 测试依赖
```

## 测试

```bash
pip install -r requirements-dev.txt
pytest -q
```

## 解析规则与限制

- 解析在文本层面进行（UTF-8）；二进制附件通常以 Base64/Quoted-Printable 形式出现在
  原始报文中，可正常检查与编辑，本工具不做内容解码。
- 嵌套深度限制为 50 层，防止畸形报文导致无限递归。
- 重新编码始终输出 CRLF；前言、尾声、头部原始文本与部件正文均按原样保留（除换行
  规范化外不做任何改写）。
- 编辑部件头部中的 `boundary` 参数不会自动改变组装用的边界——重新编码始终使用解析
  时确定的原 boundary（「按原边界规则重新编码」）。
