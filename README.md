# 投行部智能图片生成器

基于 customtkinter 构建的桌面图片生成工具，对接速创（速影科技）异步图片生成 API，支持单次出图与批量出图，内置 GPT-Image-2 与 NanoBanana2 两款模型。

当前版本：**v3.3**

---

## 功能特性

- **三大功能页**
  - **配置**：填写 API Key、选择模型与默认参数，本地持久化保存
  - **单次出图**：单张提示词生成，可指定出图张数（1–10）、尺寸/比例、画质档位，支持参考图（图生图）
  - **批量出图**：多条提示词并发生成，自动下载保存
- **双模型支持**
  - `GPT-Image-2 · 标准画质`：按像素尺寸出图
  - `NanoBanana2 · 高清(1K/2K/4K)`：按宽高比 + 画质档位出图
- **异步任务流**：提交任务 → 轮询状态 → 下载结果，全程后台线程执行，UI 不卡顿
- **参考图上传**：支持本地图片上传作为图生图参考
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

首次运行后，在「配置」页填入速创 API Key 并保存即可使用。

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

- 速创异步图片接口 base：`https://api.wuyinkeji.com/api/async`
- 调用流程：`submit_task` 提交 → 轮询 `query_task` 状态 → `extract_result_urls` 取结果 URL → 下载保存

---

## 版本历史

| 版本 | 说明 |
| --- | --- |
| **v3.3** | 纯速创单协议稳定版。仅保留 GPT-Image-2 + NanoBanana2，单次出图含「出图张数」控件。作为 Git 版本管理基线。 |

> v3.4 / v3.5 曾实验性接入 OpenAI 协议、云雾 Seedream 图生图与文本对话功能，后整体回滚至 v3.3。相关代码快照保留在 `image_generator_app.v3.5.bak.py`（已被 `.gitignore` 排除，未入库）。

---

## 版本管理约定

- `main` 分支保持可运行的稳定版本
- 新功能在独立分支开发（如 `feature/openai-protocol`），完成并测试后合并
- 提交信息使用清晰的中文或英文描述，标注版本号变更
- 每次发布在对应提交打 tag（如 `v3.3`、`v3.4`）
