# 投行部智能图片生成器

基于 customtkinter 构建的桌面 AI 图片生成 + 智能对话工具，采用 **OpenAI 兼容协议**，以云雾 API（`https://yunwu.ai/v1`）为例接入，支持单次出图、批量出图与 AI 对话分析，默认出图模型 `gpt-image-2`。

当前版本：**v4.4**

---

## 功能特性

- **四大功能页**
  - **配置**：填写 API Key、接口地址，内置「模型管理」（查询/手动添加/保存多个模型）与默认画质档位，本地持久化保存；API 密钥申请快捷入口
  - **智能分析**：AI 对话（流式输出逐字显示）、推理模型思考过程展示、文件分析（支持 .docx/.xlsx/.pptx/.pdf/.txt/.md/.csv/.json 等格式）
  - **单次出图**：单张提示词生成，可指定出图张数、尺寸/比例，支持参考图（图生图）
  - **批量出图**：多条提示词并发生成（最多 30 并发），自动下载保存
- **OpenAI 兼容协议（同步）**
  - **文生图**：`POST /v1/images/generations`（application/json）
  - **图生图**：`POST /v1/images/edits`（multipart/form-data 文件直传，最多 16 张参考图）
  - **查询模型**：`GET /v1/models`，一键拉取账号可用模型列表供勾选保存
  - **对话**：`POST /v1/chat/completions`，支持流式输出（SSE 逐块读取，逐字显示）
- **流式输出**：SSE 流使用 `iter_content` 逐块读取 + 手动分割事件，避免代理缓冲导致的延迟；UI 刷新节流优化
- **推理模型支持**：自动识别推理模型（o1/o3/deepseek-reasoner/r1/qwq 等），展示思考过程；兼容 `reasoning_content`/`reasoning`/`think`/`thought` 多种字段名
- **模型管理**：可保存多个模型（上限 10 个），下拉框展示已保存项；查询超时可重试，亦可手动输入模型 ID
- **画质档位**：`auto`（默认）/ `low` / `medium` / `high`
- **文档解析**：智能分析页支持上传并解析 .docx/.xlsx/.pptx/.pdf 等文档格式
- **结果预览与保存**：生成图片即时预览，自动落盘到输出目录

---

## 环境要求

- Python 3.10 及以上（打包推荐 3.13.12）
- 操作系统：Windows（已验证），理论兼容 macOS / Linux

---

## 安装与运行

```bash
# 1. 克隆仓库
git clone https://github.com/ebzhengfang/solos-hub.git
cd solos-hub

# 2. 安装依赖
pip install -r requirements.txt

# 3. 运行
python image_generator_app.py
```

首次运行后，在「配置」页填入云雾 API Key 与接口地址（默认 `https://yunwu.ai/v1`），通过「模型管理」查询或手动添加模型并保存即可使用。

---

## 打包为 EXE

项目内置 PyInstaller 配置，可直接打包成独立可执行文件：

```bash
# 方式一：使用 build.bat（自动清理 + 打包）
build.bat

# 方式二：手动 PyInstaller
pyinstaller GPT图片生成器.spec
```

产物位于 `dist/` 目录，约 23MB。

---

## 项目结构

```
.
├── image_generator_app.py      # 主程序（UI + 网络层 + 配置持久化 + 文档解析）
├── GPT图片生成器.spec           # PyInstaller 打包配置（含 excludes 瘦身）
├── build.bat                   # Windows 一键打包脚本
├── requirements.txt            # Python 依赖
└── README.md
```

> 注意：`config.json`（含 API Key）、`*.bak` 备份文件、`test_*.py`（开发测试脚本）、`build/`、`dist/`、`__pycache__/` 已通过 `.gitignore` 排除，不纳入版本管理。API Key 通过环境变量传入测试脚本，不硬编码在代码中。

---

## API 说明

- 采用 OpenAI 兼容协议，默认 base：`https://yunwu.ai/v1`（可在配置页修改）
- **文生图**：`POST /v1/images/generations`，JSON 入参 `{model, prompt, n, size, quality?}`
- **图生图**：`POST /v1/images/edits`，multipart 文件直传，参考图以 `image[]` 字段提交（最多 16 张）
- **查询模型**：`GET /v1/models`，返回 `data[].id` 列表
- **对话**：`POST /v1/chat/completions`，支持 `stream: true` 流式输出
- 响应解析：图片优先取 `b64_json`（base64 解码），否则下载 `url`
- 同步协议：请求即返回结果，无需轮询；`n` 固定为 1，批量出图通过多次调用实现

---

## 版本历史

| 版本 | 说明 |
| --- | --- |
| **v4.4** | 流式输出改为 `iter_content` 逐块读取 + 追加模式，修复前5秒无输出问题；扩展推理模型检测（deep-think/think）；兼容多种 reasoning 字段名；UI 刷新节流优化 |
| **v4.3.1** | 版本号同步修复；配置页 API 密钥标题行添加注册链接按钮 + 申明提示图标 |
| **v4.3** | 支持读取 .docx/.xlsx/.pptx/.pdf 四种文档格式；Chat 回复区使用 CTkTextbox 自适应高度 |
| **v4.2** | spec excludes 瘦身打包（42MB→23MB）；添加 docx hiddenimports；Textbox 自适应高度 |
| **v4.0** | 放弃速创异步协议，全面改用 OpenAI 兼容协议（以云雾 API 为例）。新增模型管理、画质档位、图生图 multipart 直传；批量并发上限提升至 30 |
| **v3.3** | 纯速创单协议稳定版 |

> v3.4 / v3.5 曾实验性接入 OpenAI 协议，后整体回滚至 v3.3，再于 v4.0 正式重构。

---

## 版本管理约定

- `main` 分支保持可运行的稳定版本
- 提交信息使用中文，格式：`类型: 简要描述`（feat / fix / refactor / style / docs / chore）
- 每次代码变更完成后自动 git add → commit → push
- 打包命令：`py3.13 -m PyInstaller --clean --noconfirm "GPT图片生成器.spec"`
