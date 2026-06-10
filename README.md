# 投行部智能图片生成器

基于 customtkinter 构建的桌面图片生成工具，采用 **OpenAI 兼容协议**，以云雾 API（`https://yunwu.ai/v1`）为例接入，支持单次出图与批量出图，默认出图模型 `gpt-image-2`。

当前版本：**v4.0**

---

## 功能特性

- **三大功能页**
  - **配置**：填写 API Key、接口地址，内置「模型管理」（查询/手动添加/保存多个模型）与默认画质档位，本地持久化保存
  - **单次出图**：单张提示词生成，可指定出图张数、尺寸/比例，支持参考图（图生图）
  - **批量出图**：多条提示词并发生成（最多 30 并发），自动下载保存
- **OpenAI 兼容协议（同步）**
  - **文生图**：`POST /v1/images/generations`（application/json）
  - **图生图**：`POST /v1/images/edits`（multipart/form-data 文件直传，最多 16 张参考图）
  - **查询模型**：`GET /v1/models`，一键拉取账号可用模型列表供勾选保存
- **模型管理**：可保存多个模型，下拉框展示已保存项；查询超时可重试，亦可手动输入模型 ID（带 ⚠️ 提示，须与云雾 API 内模型一致）
- **画质档位**：`auto`（默认，模型自动决定）/ `low` / `medium` / `high`
- **参考图上传**：本地图片上传，自动压缩后以文件直传方式提交（不再依赖外部 URL）
- **结果预览与保存**：生成图片即时预览，自动落盘到输出目录

---

## 环境要求

- Python 3.10 及以上
- 操作系统：Windows（已验证），理论兼容 macOS / Linux

---

## 安装与运行

```bash
# 1. 克隆仓库
git clone <你的仓库地址>
cd <仓库目录>

# 2. 安装依赖
pip install -r requirements.txt

# 3. 运行
python image_generator_app.py
```

首次运行后，在「配置」页填入云雾 API Key 与接口地址（默认 `https://yunwu.ai/v1`），通过「模型管理」查询或手动添加模型并保存即可使用。

---

## 打包为 EXE（可选）

项目内置 PyInstaller 配置，可直接打包成独立可执行文件：

```bash
pyinstaller 投行部智能图片生成器.spec
```

产物位于 `dist/` 目录。

---

## 项目结构

```
.
├── image_generator_app.py      # 主程序（UI + 网络层 + 配置持久化）
├── requirements.txt            # Python 依赖
├── 投行部智能图片生成器.spec    # PyInstaller 打包配置
├── gen_image.py                # 命令行出图脚本（辅助）
├── test_gpt_image2.py          # GPT-Image-2 接口测试
├── test_wuyinkeji.py           # 速创接口连通性测试
└── README.md
```

> 注意：`config.json`（含 API Key）、`*.bak` 备份文件、`build/`、`dist/`、`__pycache__/` 已通过 `.gitignore` 排除，不纳入版本管理。

---

## API 说明

- 采用 OpenAI 兼容协议，默认 base：`https://yunwu.ai/v1`（可在配置页修改）
- **文生图**：`POST /v1/images/generations`，JSON 入参 `{model, prompt, n, size, quality?}`
- **图生图**：`POST /v1/images/edits`，multipart 文件直传，参考图以 `image[]` 字段提交（最多 16 张）
- **查询模型**：`GET /v1/models`，返回 `data[].id` 列表
- 响应解析：优先取 `b64_json`（base64 解码），否则下载 `url`
- 同步协议：请求即返回结果，无需轮询；`n` 固定为 1，批量出图通过多次调用实现

---

## 版本历史

| 版本 | 说明 |
| --- | --- |
| **v4.0** | 放弃速创异步协议，全面改用 OpenAI 兼容协议（以云雾 API 为例）。新增模型管理（查询/手动/多保存）、画质档位（auto/low/medium/high）；图生图改为 multipart 文件直传；批量并发上限提升至 30。 |
| **v3.3** | 纯速创单协议稳定版。仅保留 GPT-Image-2 + NanoBanana2，单次出图含「出图张数」控件。 |

> v3.4 / v3.5 曾实验性接入 OpenAI 协议、云雾 Seedream 图生图与文本对话功能，后整体回滚至 v3.3，再于 v4.0 正式重构。相关代码快照保留在 `*.bak.py`（已被 `.gitignore` 排除，未入库）。

---

## 版本管理约定

- `main` 分支保持可运行的稳定版本
- 新功能在独立分支开发（如 `feature/openai-protocol`），完成并测试后合并
- 提交信息使用清晰的中文或英文描述，标注版本号变更
- 每次发布在对应提交打 tag（如 `v3.3`、`v3.4`）
