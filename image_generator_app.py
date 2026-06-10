#!/usr/bin/env python3
"""
投行部智能图片生成器 v4.0 — 精致桌面应用
基于 customtkinter 现代化 UI 框架 · 云雾API (yunwu.ai) · OpenAI 兼容协议

三大功能页:
  · 配置      —— API Key / 接口地址 / 模型管理(查询·手动·多保存) / 画质 / 默认保存路径
  · 单次出图  —— 单条提示词，一次可出多张
  · 批量出图  —— 提示词列表(默认5条可增减)，每条单独出 1 张，最多 30 张

协议: OpenAI 兼容同步协议
  · 文生图   —— POST /v1/images/generations (JSON)
  · 图生图   —— POST /v1/images/edits (multipart 文件直传)
  · 查询模型 —— GET  /v1/models
特性: 自定义模型管理 / 文生图+图生图 / 并发出图(最多30) / 参考图上传 / 调用日志
打包: pyinstaller --onefile --windowed --name="投行部智能图片生成器" image_generator_app.py
"""

import customtkinter as ctk
from tkinter import filedialog, messagebox
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests
import json
import os
import base64
from datetime import datetime
from pathlib import Path
from PIL import Image, ImageTk
from io import BytesIO

# ============================================================
# 全局配置
# ============================================================
APP_NAME = "投行部智能图片生成器"
APP_VERSION = "v4.0"

# 批量并发上限（云雾 API 支持高并发，最多 30 路同时出图）
MAX_CONCURRENCY = 30
SINGLE_MAX = 10   # 单次出图：一条提示词最多张数
BATCH_MAX = 30    # 批量出图：最多提示词条数（每条出 1 张）

# gpt-image-2 画质档位（quality 字段），默认 auto
QUALITY_OPTIONS = ["auto", "low", "medium", "high"]

# 统一风格指令：批量出图勾选「锁定参考图风格」时，自动拼到每条提示词前，
# 强制模型把参考图当作视觉模板（配色/版式/字体/质感保持一致），只替换内容。
STYLE_LOCK_DIRECTIVE = (
    "严格沿用所附参考图的视觉风格：保持完全一致的整体配色方案、版式布局结构、"
    "字体风格、图标与装饰元素的设计语言、画面质感与光影氛围。"
    "参考图作为风格模板，仅按下方描述替换具体内容文字与主体，"
    "其余视觉风格元素必须与参考图统一，确保系列图片风格连贯。具体内容："
)
BATCH_DEFAULT_ROWS = 5  # 批量页默认提示词行数

# 色彩方案
COLORS = {
    "bg": "#0F1117",
    "card": "#1A1D27",
    "card_border": "#2A2D37",
    "text_primary": "#E8EAED",
    "text_secondary": "#9AA0A6",
    "accent": "#4F8FFF",
    "accent_hover": "#6BA4FF",
    "success": "#34A853",
    "warning": "#FBBC04",
    "error": "#EA4335",
    "surface": "#242835",
    "divider": "#3C4043",
    "log_info": "#4F8FFF",
    "log_success": "#34A853",
    "log_error": "#EA4335",
    "log_request": "#FBBC04",
}

SIZE_MAP = {
    "1:1  正方形":      {"pixel": "1024x1024", "ratio": "1:1"},
    "3:2  相机横版":     {"pixel": "1536x1024", "ratio": "3:2"},
    "2:3  相机竖版":     {"pixel": "1024x1536", "ratio": "2:3"},
    "4:3  传统横版":     {"pixel": "1280x960",  "ratio": "4:3"},
    "3:4  传统竖版":     {"pixel": "960x1280",  "ratio": "3:4"},
    "16:9 宽屏横版":    {"pixel": "1792x1024", "ratio": "16:9"},
    "9:16 手机竖版":    {"pixel": "1024x1792", "ratio": "9:16"},
}

CONFIG_FILE = Path.home() / ".gpt_image_gen_config.json"
DEFAULT_CONFIG = {
    "api_key": "",
    "api_base": "https://yunwu.ai/v1",
    "save_dir": str(Path.home() / "Pictures"),
    "filename_prefix": "ai_image",
    "last_size_key": "1:1  正方形",
    # 用户保存的模型列表（云雾 API 内的模型 ID），下拉框从此读取
    "models": ["gpt-image-2"],
    "model": "gpt-image-2",
    "quality": "auto",
    "single_count": 1,
}


def load_config():
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                return {**DEFAULT_CONFIG, **json.load(f)}
        except Exception:
            pass
    return DEFAULT_CONFIG.copy()


def save_config(cfg: dict):
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


# ============================================================
# API 客户端 — OpenAI 兼容协议（云雾 yunwu.ai）
# ============================================================
def _normalize_openai_base(base: str) -> str:
    """把用户填的各种形态规整成以 /v1 结尾的根。
    接受 https://x、https://x/、https://x/v1、https://x/v1/images/generations 等。"""
    b = (base or "").strip().rstrip("/")
    if not b:
        return "https://yunwu.ai/v1"
    # 砍掉常见的接口尾巴
    for tail in ("/images/generations", "/images/edits",
                 "/chat/completions", "/completions"):
        if b.endswith(tail):
            b = b[: -len(tail)]
            break
    b = b.rstrip("/")
    if not b.endswith("/v1"):
        b = b + "/v1"
    return b


def _safe_json(r, context: str):
    if not r.text or not r.text.strip():
        raise RuntimeError(f"{context}: API 返回空响应 (HTTP {r.status_code})")
    try:
        return r.json()
    except Exception:
        raise RuntimeError(f"{context}: API 返回非 JSON (HTTP {r.status_code})\n{r.text[:300]}")


def _extract_error(data, status_code):
    """从 OpenAI 风格错误体中提取人类可读消息。"""
    err = data.get("error") if isinstance(data, dict) else None
    if isinstance(err, dict):
        return err.get("message") or err.get("code") or f"HTTP {status_code}"
    if isinstance(err, str):
        return err
    return data.get("message", f"HTTP {status_code}") if isinstance(data, dict) else f"HTTP {status_code}"


def _decode_image_item(item):
    """从返回的 data[i] 中拿到图片字节：优先 b64_json，否则下载 url。"""
    if item.get("b64_json"):
        return base64.b64decode(item["b64_json"])
    if item.get("url"):
        dl = requests.get(item["url"], timeout=120)
        if dl.status_code != 200:
            raise RuntimeError(f"图片下载失败 (HTTP {dl.status_code})")
        return dl.content
    raise RuntimeError("返回结果无 b64_json / url")


def openai_list_models(api_base, api_key, timeout=20):
    """查询云雾 API 可用模型列表：GET /v1/models。
    返回模型 ID 字符串列表（已排序）。失败抛 RuntimeError。"""
    base = _normalize_openai_base(api_base)
    url = f"{base}/models"
    headers = {"Authorization": f"Bearer {api_key}"}
    r = requests.get(url, headers=headers, timeout=timeout)
    data = _safe_json(r, "查询模型")
    if r.status_code >= 400 or (isinstance(data, dict) and "error" in data):
        raise RuntimeError(_extract_error(data, r.status_code))
    items = data.get("data", []) if isinstance(data, dict) else []
    ids = [it.get("id") for it in items if isinstance(it, dict) and it.get("id")]
    return sorted(set(ids)), {"url": url, "count": len(ids)}


def openai_generations(api_base, api_key, model, prompt, size_pixel,
                       quality="auto", n=1, timeout=300):
    """文生图：POST /v1/images/generations (application/json)。
    返回 (image_bytes, log_payload)。"""
    base = _normalize_openai_base(api_base)
    url = f"{base}/images/generations"
    headers = {"Authorization": f"Bearer {api_key}",
               "Content-Type": "application/json"}
    payload = {"model": model, "prompt": prompt, "n": n, "size": size_pixel}
    if quality and quality != "auto":
        payload["quality"] = quality

    r = requests.post(url, json=payload, headers=headers, timeout=timeout)
    data = _safe_json(r, "文生图")
    if r.status_code >= 400 or (isinstance(data, dict) and "error" in data):
        raise RuntimeError(_extract_error(data, r.status_code))
    items = data.get("data", [])
    if not items:
        raise RuntimeError("文生图返回空结果")
    log = {"url": url, "payload": {**payload}}
    return _decode_image_item(items[0]), log


def openai_edits(api_base, api_key, model, prompt, size_pixel,
                 ref_files, quality="auto", n=1, timeout=300):
    """图生图：POST /v1/images/edits (multipart/form-data 文件直传)。
    ref_files: [(filename, bytes, mime), ...]，最多 16 张，用 image[] 字段上传。
    返回 (image_bytes, log_payload)。"""
    base = _normalize_openai_base(api_base)
    url = f"{base}/images/edits"
    headers = {"Authorization": f"Bearer {api_key}"}  # multipart 边界由 requests 自动设置

    data_fields = {"model": (None, model), "prompt": (None, prompt),
                   "n": (None, str(n)), "size": (None, size_pixel)}
    if quality and quality != "auto":
        data_fields["quality"] = (None, quality)

    files = []
    for fname, fbytes, mime in (ref_files or [])[:16]:
        files.append(("image[]", (fname, fbytes, mime)))
    # 合并文本字段与文件字段一起作为 multipart
    multipart = list(data_fields.items()) + files

    r = requests.post(url, files=multipart, headers=headers, timeout=timeout)
    data = _safe_json(r, "图生图")
    if r.status_code >= 400 or (isinstance(data, dict) and "error" in data):
        raise RuntimeError(_extract_error(data, r.status_code))
    items = data.get("data", [])
    if not items:
        raise RuntimeError("图生图返回空结果")
    log = {"url": url,
           "payload": {"model": model, "prompt": prompt[:80], "n": n,
                       "size": size_pixel, "image": f"[{len(files)} 张参考图]"}}
    return _decode_image_item(items[0]), log




# ============================================================
# 页面上下文：持有某个生成页的 UI 组件引用（一览/进度/按钮）
# ============================================================
class PageContext:
    def __init__(self):
        self.gallery = None
        self.gallery_photos = []
        self.gallery_col = 0
        self.gallery_row = 0
        self.placeholder = None
        self.progress_frame = None
        self.progress_bar = None
        self.lbl_progress = None
        self.btn_generate = None
        # 参考图相关（单次/批量各自独立一套）
        self.ref_paths = []
        self.ref_photos = []
        self.ref_thumb_frame = None
        self.lbl_ref_count = None
        self.entry_ref_urls = None
        self.style_lock_var = None    # 批量页"锁定参考图风格"开关


# ============================================================
# 主窗口
# ============================================================
class ImageGeneratorApp:
    def __init__(self):
        self.config = load_config()
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")

        self.win = ctk.CTk()
        self.win.title(APP_NAME)
        self.win.geometry("760x840")
        self.win.minsize(620, 640)
        self.win.configure(fg_color=COLORS["bg"])

        self.win.update_idletasks()
        sw, sh = self.win.winfo_screenwidth(), self.win.winfo_screenheight()
        self.win.geometry(f"760x840+{(sw-760)//2}+{(sh-840)//2}")

        self.generating = False
        self.batch_rows = []          # 批量页的提示词行 [{frame, entry, idx_lbl}]
        self.log_expanded = False
        self.MAX_LOG_LINES = 500

        # 两个生成页各自的上下文
        self.ctx_single = PageContext()
        self.ctx_batch = PageContext()

        self._build()

    # ================================================================
    #  UI 工具方法
    # ================================================================
    def _mk_card(self, parent, **kw):
        return ctk.CTkFrame(parent, fg_color=COLORS["card"],
                            border_color=COLORS["card_border"],
                            border_width=1, corner_radius=12, **kw)

    def _mk_label(self, parent, text, size=13, color=None, weight="normal", **kw):
        ff = "Microsoft YaHei UI" if os.name == "nt" else "Segoe UI"
        return ctk.CTkLabel(parent, text=text, font=(ff, size, weight),
                            text_color=color or COLORS["text_primary"], **kw)

    def _mk_entry(self, parent, placeholder="", **kw):
        return ctk.CTkEntry(parent, height=38, fg_color=COLORS["surface"],
                            border_color=COLORS["divider"], border_width=1,
                            corner_radius=8, text_color=COLORS["text_primary"],
                            placeholder_text=placeholder,
                            placeholder_text_color=COLORS["text_secondary"], **kw)

    def _mk_btn(self, parent, text, style="primary", **kw):
        if style == "primary":
            return ctk.CTkButton(parent, text=text, height=42, corner_radius=10,
                                 fg_color=COLORS["accent"],
                                 hover_color=COLORS["accent_hover"],
                                 font=("Microsoft YaHei UI", 14, "bold"), **kw)
        elif style == "secondary":
            return ctk.CTkButton(parent, text=text, height=34, corner_radius=8,
                                 fg_color=COLORS["surface"],
                                 hover_color=COLORS["card_border"],
                                 border_color=COLORS["divider"], border_width=1,
                                 font=("Microsoft YaHei UI", 12), **kw)
        else:
            return ctk.CTkButton(parent, text=text, height=34, corner_radius=8,
                                 fg_color="transparent", hover_color=COLORS["surface"],
                                 font=("Microsoft YaHei UI", 11),
                                 text_color=COLORS["text_secondary"], **kw)

    def _mk_optionmenu(self, parent, values, width=170, height=34, command=None):
        return ctk.CTkOptionMenu(
            parent, values=values, height=height, width=width,
            fg_color=COLORS["surface"], button_color=COLORS["accent"],
            button_hover_color=COLORS["accent_hover"],
            text_color=COLORS["text_primary"],
            font=("Microsoft YaHei UI", 12), corner_radius=8,
            command=command)

    def _bind_tooltip(self, widget, text):
        """给控件绑定悬停提示（轻量级 Toplevel 实现）。"""
        tip = {"win": None}

        def show(_event=None):
            if tip["win"] is not None:
                return
            x = widget.winfo_rootx() + 20
            y = widget.winfo_rooty() + widget.winfo_height() + 6
            tw = ctk.CTkToplevel(self.win)
            tw.overrideredirect(True)
            tw.attributes("-topmost", True)
            tw.geometry(f"+{x}+{y}")
            tw.configure(fg_color=COLORS["card"])
            lbl = ctk.CTkLabel(tw, text=text, font=("Microsoft YaHei UI", 11),
                               text_color=COLORS["text_primary"],
                               fg_color=COLORS["card"], wraplength=260,
                               justify="left")
            lbl.pack(padx=10, pady=6)
            tip["win"] = tw

        def hide(_event=None):
            if tip["win"] is not None:
                tip["win"].destroy()
                tip["win"] = None

        widget.bind("<Enter>", show)
        widget.bind("<Leave>", hide)

    # ================================================================
    #  整体布局
    # ================================================================
    def _build(self):
        # --- 顶部标题 ---
        header = ctk.CTkFrame(self.win, fg_color="transparent", height=50)
        header.pack(fill="x", padx=24, pady=(18, 4))
        header.pack_propagate(False)

        tf = ctk.CTkFrame(header, fg_color="transparent")
        tf.pack(side="left")
        self._mk_label(tf, "🎨", size=24).pack(side="left", padx=(0, 8))
        self._mk_label(tf, "投行部", size=20, weight="bold",
                       color=COLORS["accent"]).pack(side="left")
        self._mk_label(tf, "智能图片生成器", size=13,
                       color=COLORS["text_secondary"]).pack(side="left", padx=(6, 0))

        self._mk_label(header, APP_VERSION, size=10,
                       color=COLORS["text_secondary"]).pack(side="right", pady=(10, 0))

        # --- 导航（三 Tab）---
        nav = ctk.CTkFrame(self.win, fg_color="transparent")
        nav.pack(fill="x", padx=24, pady=(2, 4))

        self.nav_config = self._mk_btn(nav, "⚙️  配置", "ghost",
                                       command=lambda: self._switch_tab("config"))
        self.nav_config.pack(side="left", padx=(0, 4))
        self.nav_single = self._mk_btn(nav, "🖼️  单次出图", "ghost",
                                       command=lambda: self._switch_tab("single"))
        self.nav_single.pack(side="left", padx=4)
        self.nav_batch = self._mk_btn(nav, "🗂️  批量出图", "ghost",
                                      command=lambda: self._switch_tab("batch"))
        self.nav_batch.pack(side="left", padx=4)

        # --- 主内容区 ---
        self.main = ctk.CTkFrame(self.win, fg_color="transparent")
        self.main.pack(fill="both", expand=True, padx=24)

        self.page_config = self._build_config_page()
        self.page_single = self._build_single_page()
        self.page_batch = self._build_batch_page()

        self.page_config.pack_forget()
        self.page_batch.pack_forget()
        self.page_single.pack(fill="both", expand=True, pady=(4, 0))
        self.current_page = "single"
        self._highlight_nav("single")

        # --- 日志面板 (折叠) ---
        self._build_log_panel()

        # --- 底栏 ---
        footer = ctk.CTkFrame(self.win, fg_color="transparent", height=36)
        footer.pack(fill="x", padx=24, pady=(2, 10))
        footer.pack_propagate(False)

        self.status_bar = self._mk_label(footer, "就绪，输入提示词后点击「开始生成」",
                                         size=11, color=COLORS["text_secondary"])
        self.status_bar.pack(side="left")

        self.lbl_api_status = self._mk_label(footer, "🔴 未配置 API", size=11,
                                             color=COLORS["error"])
        self.lbl_api_status.pack(side="right")
        self._check_api_status()

    # ================================================================
    #  日志面板
    # ================================================================
    def _build_log_panel(self):
        self.log_container = ctk.CTkFrame(self.win, fg_color="transparent")
        self.log_container.pack(fill="x", padx=24, pady=(0, 0))

        toggle_bar = ctk.CTkFrame(self.log_container, fg_color=COLORS["card"],
                                  corner_radius=10, height=32)
        toggle_bar.pack(fill="x")
        toggle_bar.pack_propagate(False)

        self.btn_log_toggle = self._mk_btn(toggle_bar, "📋  调用日志  ▸", "ghost",
                                           command=self._toggle_log)
        self.btn_log_toggle.pack(side="left", padx=(12, 0))

        self.lbl_log_hint = self._mk_label(toggle_bar, "点击展开查看 API 调用详情",
                                           size=10, color=COLORS["text_secondary"])
        self.lbl_log_hint.pack(side="left", padx=(10, 0))

        btn_clear_log = self._mk_btn(toggle_bar, "清空", "ghost",
                                     command=self._clear_log)
        btn_clear_log.pack(side="right", padx=(0, 8), pady=4)

        self.log_frame = ctk.CTkFrame(self.log_container, fg_color=COLORS["card"],
                                      corner_radius=0)
        self.log_text = ctk.CTkTextbox(
            self.log_frame, height=140,
            fg_color=COLORS["surface"], border_color=COLORS["divider"],
            border_width=1, corner_radius=6, wrap="word",
            font=("Consolas", 10), text_color=COLORS["text_secondary"],
            state="disabled",
        )
        self.log_text.pack(fill="both", expand=True, padx=8, pady=(6, 8))

    def _toggle_log(self):
        if self.log_expanded:
            self.log_frame.pack_forget()
            self.btn_log_toggle.configure(text="📋  调用日志  ▸")
            self.lbl_log_hint.configure(text="点击展开查看 API 调用详情")
            self.log_expanded = False
        else:
            self.log_frame.pack(fill="x", before=self.log_container.winfo_children()[0])
            self.btn_log_toggle.configure(text="📋  调用日志  ▾")
            self.lbl_log_hint.configure(text="")
            self.log_expanded = True
            self._scroll_log_to_bottom()

    def _clear_log(self):
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")

    def _append_log(self, text, tag=None):
        def _do():
            ts = datetime.now().strftime("%H:%M:%S")
            line = f"[{ts}] {text}\n"
            self.log_text.configure(state="normal")
            self.log_text.insert("end", line)
            total = int(self.log_text.index("end-1c").split(".")[0])
            if total > self.MAX_LOG_LINES:
                self.log_text.delete("1.0", f"{total - self.MAX_LOG_LINES}.0")
            self.log_text.configure(state="disabled")
            if self.log_expanded:
                self._scroll_log_to_bottom()
        self.win.after(0, _do)

    def log_request(self, method, url, body=None):
        body_str = json.dumps(body, ensure_ascii=False)[:200] if body else ""
        self._append_log(f"⬆ {method} {url}", "request")
        if body_str:
            self._append_log(f"   Body: {body_str}", "request")

    def log_info(self, msg):
        self._append_log(f"ℹ️  {msg}", "info")

    def log_error(self, msg):
        self._append_log(f"❌ {msg}", "error")

    def log_success(self, msg):
        self._append_log(f"✅ {msg}", "success")

    def _scroll_log_to_bottom(self):
        self.log_text.see("end")

    # ================================================================
    #  配置页
    # ================================================================
    def _build_config_page(self):
        page = ctk.CTkScrollableFrame(self.main, fg_color="transparent")

        # API Key
        card1 = self._mk_card(page)
        card1.pack(fill="x", pady=(0, 12), padx=2)

        self._mk_label(card1, "🔑  API 密钥", size=15, weight="bold").pack(
            anchor="w", padx=20, pady=(18, 12))
        kr = ctk.CTkFrame(card1, fg_color="transparent")
        kr.pack(fill="x", padx=20, pady=(0, 6))
        self.entry_key = ctk.CTkEntry(kr, height=40, fg_color=COLORS["surface"],
                                      border_color=COLORS["divider"], border_width=1,
                                      corner_radius=8, placeholder_text="请输入云雾API的Key...",
                                      placeholder_text_color=COLORS["text_secondary"], show="•")
        self.entry_key.pack(side="left", fill="x", expand=True)
        self.entry_key.insert(0, self.config.get("api_key", ""))
        self.btn_toggle_key = self._mk_btn(kr, "👁", "ghost", command=self._toggle_key_vis)
        self.btn_toggle_key.pack(side="left", padx=(8, 0))

        self._mk_label(card1, "接口地址", size=12,
                       color=COLORS["text_secondary"]).pack(anchor="w", padx=20, pady=(10, 4))
        self.entry_base = self._mk_entry(card1)
        self.entry_base.pack(fill="x", padx=20, pady=(0, 6))
        self.entry_base.insert(0, self.config.get("api_base", ""))

        self._mk_btn(card1, "💾  保存配置", "primary",
                     command=self._save_config).pack(padx=20, pady=(8, 18), anchor="w")

        # 模型管理（查询 / 手动输入 / 多保存）
        card_model = self._mk_card(page)
        card_model.pack(fill="x", pady=(0, 12), padx=2)

        self._mk_label(card_model, "🤖  模型管理", size=15, weight="bold").pack(
            anchor="w", padx=20, pady=(18, 4))
        self._mk_label(card_model,
                       "保存你在云雾 API 内可用的模型 ID。单次/批量页的模型下拉从这里读取。\n"
                       "可点「查询可用模型」自动拉取，也可手动输入后添加。",
                       size=11, color=COLORS["text_secondary"]).pack(
            anchor="w", padx=20, pady=(0, 10))

        # 已保存模型列表
        self._mk_label(card_model, "已保存模型", size=12,
                       color=COLORS["text_secondary"]).pack(anchor="w", padx=20, pady=(0, 4))
        self.frame_saved_models = ctk.CTkFrame(card_model, fg_color=COLORS["surface"],
                                               corner_radius=8)
        self.frame_saved_models.pack(fill="x", padx=20, pady=(0, 10))

        # 查询 + 手动输入 行
        add_row = ctk.CTkFrame(card_model, fg_color="transparent")
        add_row.pack(fill="x", padx=20, pady=(0, 6))
        self.btn_query_models = self._mk_btn(add_row, "🔍 查询可用模型", "secondary",
                                             command=self._query_models)
        self.btn_query_models.pack(side="left")
        self.lbl_query_hint = self._mk_label(add_row, "", size=10,
                                             color=COLORS["text_secondary"])
        self.lbl_query_hint.pack(side="left", padx=(10, 0))

        manual_row = ctk.CTkFrame(card_model, fg_color="transparent")
        manual_row.pack(fill="x", padx=20, pady=(0, 16))
        self._mk_label(manual_row, "手动输入", size=12,
                       color=COLORS["text_secondary"]).pack(side="left", padx=(0, 6))
        # ⚠️ 注意符号，悬停提示模型ID须与云雾一致
        warn_lbl = self._mk_label(manual_row, "⚠️", size=13, color=COLORS["warning"])
        warn_lbl.pack(side="left", padx=(0, 6))
        self._bind_tooltip(warn_lbl,
                           "模型ID请保持与云雾api内的模型一致，否则将调用失败。")
        self.entry_manual_model = self._mk_entry(manual_row,
                                                 placeholder="例如 gpt-image-2")
        self.entry_manual_model.configure(width=200)
        self.entry_manual_model.pack(side="left", fill="x", expand=True)
        self._mk_btn(manual_row, "➕ 添加", "secondary",
                     command=self._add_manual_model).pack(side="left", padx=(8, 0))

        self._refresh_saved_models()

        # 画质（gpt-image-2 档位）
        card_quality = self._mk_card(page)
        card_quality.pack(fill="x", pady=(0, 12), padx=2)
        self._mk_label(card_quality, "🎚️  默认画质", size=15, weight="bold").pack(
            anchor="w", padx=20, pady=(18, 4))
        self._mk_label(card_quality,
                       "gpt-image-2 画质档位，默认 auto；单次/批量页也可临时切换。",
                       size=11, color=COLORS["text_secondary"]).pack(
            anchor="w", padx=20, pady=(0, 10))
        qr = ctk.CTkFrame(card_quality, fg_color="transparent")
        qr.pack(fill="x", padx=20, pady=(0, 18))
        self._mk_label(qr, "画质", size=12,
                       color=COLORS["text_secondary"]).pack(side="left", padx=(0, 10))
        self.combo_quality = self._mk_optionmenu(
            qr, QUALITY_OPTIONS, width=120, height=36,
            command=self._on_quality_changed)
        self.combo_quality.pack(side="left")
        cur_q = self.config.get("quality", "auto")
        self.combo_quality.set(cur_q if cur_q in QUALITY_OPTIONS else "auto")
        self._mk_label(qr, "（auto 由模型自动决定；high 更慢更贵）", size=10,
                       color=COLORS["text_secondary"]).pack(side="left", padx=(10, 0))

        # 默认路径
        card2 = self._mk_card(page)
        card2.pack(fill="x", pady=(0, 12), padx=2)

        self._mk_label(card2, "📁  默认保存路径", size=15, weight="bold").pack(
            anchor="w", padx=20, pady=(18, 12))
        pr = ctk.CTkFrame(card2, fg_color="transparent")
        pr.pack(fill="x", padx=20, pady=(0, 18))
        self.entry_save_dir = self._mk_entry(pr)
        self.entry_save_dir.pack(side="left", fill="x", expand=True)
        self.entry_save_dir.insert(0, self.config.get("save_dir", ""))

        ctk.CTkButton(pr, text="📂  浏览", height=38, width=80, corner_radius=8,
                      fg_color=COLORS["surface"], hover_color=COLORS["card_border"],
                      border_color=COLORS["divider"], border_width=1,
                      command=self._browse_save_dir).pack(side="left", padx=(8, 0))

        # 关于
        card3 = self._mk_card(page)
        card3.pack(fill="x", padx=2)

        self._mk_label(card3, "ℹ️  关于", size=15, weight="bold").pack(
            anchor="w", padx=20, pady=(18, 8))
        self._mk_label(card3,
                       "投行部智能图片生成器，基于云雾API（OpenAI 兼容协议）\n"
                       "单次出图 / 批量出图(最多30张并发) / 自定义模型管理 / gpt-image-2\n"
                       "文生图 + 图生图(上传参考图模板) / 中英文提示词 / 调用日志追踪",
                       size=12, color=COLORS["text_secondary"]).pack(
            anchor="w", padx=20, pady=(0, 10))
        self._mk_label(card3,
                       "⚠️ 注意：暂用于投行部内部使用，请勿外传",
                       size=12, weight="bold", color=COLORS["warning"]).pack(
            anchor="w", padx=20, pady=(0, 18))

        return page

    # ================================================================
    #  单次出图页
    # ================================================================
    def _build_single_page(self):
        page = ctk.CTkScrollableFrame(self.main, fg_color="transparent")

        # -- 参考图附件（紧凑卡片，单次/批量共用同一构建器）--
        self._build_ref_card(page, self.ctx_single)

        # -- 提示词（单条） --
        card_prompt = self._mk_card(page)
        card_prompt.pack(fill="x", pady=(0, 10), padx=2)

        self._mk_label(card_prompt, "✏️  提示词 Prompt", size=14, weight="bold").pack(
            anchor="w", padx=20, pady=(16, 4))
        self.text_single_prompt = ctk.CTkTextbox(
            card_prompt, height=150, fg_color=COLORS["surface"],
            border_color=COLORS["divider"], border_width=1, corner_radius=8,
            wrap="word", font=("Microsoft YaHei UI", 14),
            text_color=COLORS["text_primary"],
        )
        self.text_single_prompt.pack(fill="x", padx=20, pady=(4, 16))

        # -- 参数设置 --
        card_params = self._mk_card(page)
        card_params.pack(fill="x", pady=(0, 10), padx=2)

        self._mk_label(card_params, "⚙️  参数设置", size=14, weight="bold").pack(
            anchor="w", padx=20, pady=(16, 10))

        # 模型
        rm = ctk.CTkFrame(card_params, fg_color="transparent")
        rm.pack(fill="x", padx=20, pady=(0, 6))
        self._mk_label(rm, "模型", size=12,
                       color=COLORS["text_secondary"]).pack(side="left", padx=(0, 10))
        self.combo_single_model = self._mk_optionmenu(
            rm, self._model_list(), width=240,
            command=self._on_single_model_changed)
        self.combo_single_model.pack(side="left")
        self.combo_single_model.set(self._current_model())
        self.lbl_single_model_hint = self._mk_label(rm, "", size=10,
                                                    color=COLORS["text_secondary"])
        self.lbl_single_model_hint.pack(side="left", padx=(10, 0))

        # 尺寸
        r1 = ctk.CTkFrame(card_params, fg_color="transparent")
        r1.pack(fill="x", padx=20, pady=(0, 6))
        self._mk_label(r1, "尺寸比例", size=12,
                       color=COLORS["text_secondary"]).pack(side="left", padx=(0, 10))
        self.combo_single_size = self._mk_optionmenu(
            r1, list(SIZE_MAP.keys()), width=170,
            command=self._on_single_size_changed)
        self.combo_single_size.pack(side="left")
        lk = self.config.get("last_size_key", "1:1  正方形")
        self.combo_single_size.set(lk if lk in SIZE_MAP else "1:1  正方形")
        self.lbl_single_pixel = self._mk_label(
            r1, self._size_caption(self.combo_single_size.get(), self.combo_single_model.get()),
            size=11, color=COLORS["text_secondary"])
        self.lbl_single_pixel.pack(side="left", padx=(10, 0))

        # 文件名
        r2 = ctk.CTkFrame(card_params, fg_color="transparent")
        r2.pack(fill="x", padx=20, pady=(0, 6))
        self._mk_label(r2, "文件名", size=12,
                       color=COLORS["text_secondary"]).pack(side="left", padx=(0, 10))
        self.entry_single_prefix = self._mk_entry(r2)
        self.entry_single_prefix.configure(width=160)
        self.entry_single_prefix.pack(side="left")
        self.entry_single_prefix.insert(0, self.config.get("filename_prefix", "ai_image"))
        self._mk_label(r2, ".png（自动追加时间戳）", size=10,
                       color=COLORS["text_secondary"]).pack(side="left", padx=(8, 0))

        # 出图张数（单条提示词，一次可出多张）
        rc = ctk.CTkFrame(card_params, fg_color="transparent")
        rc.pack(fill="x", padx=20, pady=(0, 6))
        self._mk_label(rc, "出图张数", size=12,
                       color=COLORS["text_secondary"]).pack(side="left", padx=(0, 10))
        self.combo_single_count = self._mk_optionmenu(
            rc, [str(i) for i in range(1, SINGLE_MAX + 1)], width=90)
        self.combo_single_count.pack(side="left")
        self.combo_single_count.set(str(self.config.get("single_count", 1)))
        self._mk_label(rc, f"（同一提示词最多 {SINGLE_MAX} 张，多条提示词请用批量出图）",
                       size=10, color=COLORS["text_secondary"]).pack(side="left", padx=(8, 0))

        # 保存路径提示
        r3 = ctk.CTkFrame(card_params, fg_color="transparent")
        r3.pack(fill="x", padx=20, pady=(0, 14))
        self._mk_label(r3, "保存到", size=12,
                       color=COLORS["text_secondary"]).pack(side="left", padx=(0, 10))
        self.lbl_single_out_dir = self._mk_label(
            r3, self.config.get("save_dir", "") or "（未设置，请到「配置」页设置）",
            size=12, color=COLORS["text_primary"])
        self.lbl_single_out_dir.pack(side="left")

        # -- 生成按钮 --
        bf = ctk.CTkFrame(page, fg_color="transparent")
        bf.pack(fill="x", padx=2, pady=(6, 0))
        self.ctx_single.btn_generate = ctk.CTkButton(
            bf, text="🚀  开始生成", height=48, corner_radius=12,
            fg_color=COLORS["accent"], hover_color=COLORS["accent_hover"],
            font=("Microsoft YaHei UI", 15, "bold"),
            command=self._start_single)
        self.ctx_single.btn_generate.pack(fill="x")

        # -- 进度 + 一览 --
        self._build_progress_and_gallery(page, self.ctx_single,
                                         "✨ 图片将直接生成并保存到默认路径，生成后在此查看")

        self._refresh_single_model_hint()
        return page

    # ================================================================
    #  批量出图页
    # ================================================================
    def _build_batch_page(self):
        page = ctk.CTkScrollableFrame(self.main, fg_color="transparent")

        # -- 参考图附件（置顶，与单次出图风格统一；作用于本批所有提示词）--
        self._build_ref_card(page, self.ctx_batch,
                             note="（作为模板应用到本批所有提示词）",
                             style_lock=True)

        # -- 提示词列表 --
        card_list = self._mk_card(page)
        card_list.pack(fill="x", pady=(0, 10), padx=2)

        lh = ctk.CTkFrame(card_list, fg_color="transparent")
        lh.pack(fill="x", padx=20, pady=(16, 4))
        self._mk_label(lh, "📝  提示词列表（每条单独生成 1 张图）", size=14,
                       weight="bold").pack(side="left")
        self._mk_btn(lh, "📥  导入txt", "secondary",
                     command=self._import_batch_prompts).pack(side="right")
        self._mk_btn(lh, "🗑  清空", "ghost",
                     command=self._clear_batch_rows).pack(side="right", padx=(0, 8))

        # 行容器
        self.batch_rows_frame = ctk.CTkFrame(card_list, fg_color="transparent")
        self.batch_rows_frame.pack(fill="x", padx=20, pady=(4, 4))

        # 底部：添加一行 + 计数
        add_row = ctk.CTkFrame(card_list, fg_color="transparent")
        add_row.pack(fill="x", padx=20, pady=(4, 16))
        self._mk_btn(add_row, "➕  添加一行", "secondary",
                     command=lambda: self._add_batch_row()).pack(side="left")
        self.lbl_batch_count = self._mk_label(add_row, "", size=12,
                                              color=COLORS["accent"])
        self.lbl_batch_count.pack(side="left", padx=(16, 0))

        # 默认 5 行
        for _ in range(BATCH_DEFAULT_ROWS):
            self._add_batch_row()

        # -- 参数设置 --
        card_params = self._mk_card(page)
        card_params.pack(fill="x", pady=(0, 10), padx=2)

        self._mk_label(card_params, "⚙️  参数设置", size=14, weight="bold").pack(
            anchor="w", padx=20, pady=(16, 10))

        # 模型选择（替代原版本选择）
        rm = ctk.CTkFrame(card_params, fg_color="transparent")
        rm.pack(fill="x", padx=20, pady=(0, 6))
        self._mk_label(rm, "模型选择", size=12,
                       color=COLORS["text_secondary"]).pack(side="left", padx=(0, 10))
        self.combo_batch_model = self._mk_optionmenu(
            rm, self._model_list(), width=240,
            command=self._on_batch_model_changed)
        self.combo_batch_model.pack(side="left")
        self.combo_batch_model.set(self._current_model())
        self.lbl_batch_model_hint = self._mk_label(rm, "", size=10,
                                                   color=COLORS["text_secondary"])
        self.lbl_batch_model_hint.pack(side="left", padx=(10, 0))

        # 尺寸
        r1 = ctk.CTkFrame(card_params, fg_color="transparent")
        r1.pack(fill="x", padx=20, pady=(0, 6))
        self._mk_label(r1, "尺寸比例", size=12,
                       color=COLORS["text_secondary"]).pack(side="left", padx=(0, 10))
        self.combo_batch_size = self._mk_optionmenu(
            r1, list(SIZE_MAP.keys()), width=170,
            command=self._on_batch_size_changed)
        self.combo_batch_size.pack(side="left")
        lk = self.config.get("last_size_key", "1:1  正方形")
        self.combo_batch_size.set(lk if lk in SIZE_MAP else "1:1  正方形")
        self.lbl_batch_pixel = self._mk_label(
            r1, self._size_caption(self.combo_batch_size.get(), self.combo_batch_model.get()),
            size=11, color=COLORS["text_secondary"])
        self.lbl_batch_pixel.pack(side="left", padx=(10, 0))

        # 文件名
        r2 = ctk.CTkFrame(card_params, fg_color="transparent")
        r2.pack(fill="x", padx=20, pady=(0, 6))
        self._mk_label(r2, "文件名", size=12,
                       color=COLORS["text_secondary"]).pack(side="left", padx=(0, 10))
        self.entry_batch_prefix = self._mk_entry(r2)
        self.entry_batch_prefix.configure(width=160)
        self.entry_batch_prefix.pack(side="left")
        self.entry_batch_prefix.insert(0, self.config.get("filename_prefix", "ai_image"))
        self._mk_label(r2, ".png（自动追加序号与时间戳）", size=10,
                       color=COLORS["text_secondary"]).pack(side="left", padx=(8, 0))

        # 保存路径
        r3 = ctk.CTkFrame(card_params, fg_color="transparent")
        r3.pack(fill="x", padx=20, pady=(0, 14))
        self._mk_label(r3, "保存到", size=12,
                       color=COLORS["text_secondary"]).pack(side="left", padx=(0, 10))
        self.lbl_batch_out_dir = self._mk_label(
            r3, self.config.get("save_dir", "") or "（未设置，请到「配置」页设置）",
            size=12, color=COLORS["text_primary"])
        self.lbl_batch_out_dir.pack(side="left")

        # -- 生成按钮 --
        bf = ctk.CTkFrame(page, fg_color="transparent")
        bf.pack(fill="x", padx=2, pady=(6, 0))
        self.ctx_batch.btn_generate = ctk.CTkButton(
            bf, text="🚀  批量生成", height=48, corner_radius=12,
            fg_color=COLORS["accent"], hover_color=COLORS["accent_hover"],
            font=("Microsoft YaHei UI", 15, "bold"),
            command=self._start_batch)
        self.ctx_batch.btn_generate.pack(fill="x")

        # -- 进度 + 一览 --
        self._build_progress_and_gallery(page, self.ctx_batch,
                                         "✨ 填写提示词列表后点击「批量生成」，生成结果在此一览")

        self._refresh_batch_model_hint()
        self._update_batch_count()
        return page

    def _build_progress_and_gallery(self, page, ctx, placeholder_text):
        """为某个生成页构建进度条 + 缩略图一览网格，存入 ctx"""
        ctx.progress_frame = ctk.CTkFrame(page, fg_color="transparent")
        ctx.progress_frame.pack(fill="x", padx=2, pady=(14, 0))
        ctx.progress_bar = ctk.CTkProgressBar(
            ctx.progress_frame, height=6, corner_radius=3,
            fg_color=COLORS["surface"], progress_color=COLORS["accent"],
            mode="determinate")
        ctx.lbl_progress = self._mk_label(ctx.progress_frame, "", size=12,
                                          color=COLORS["accent"])
        ctx.progress_frame.pack_forget()

        prev_card = self._mk_card(page)
        prev_card.pack(fill="both", expand=True, pady=(10, 0), padx=2)

        ph2 = ctk.CTkFrame(prev_card, fg_color="transparent")
        ph2.pack(fill="x", padx=20, pady=(16, 8))
        self._mk_label(ph2, "🖼️  生成一览", size=14, weight="bold").pack(side="left")
        self._mk_label(ph2, "双击图片可打开原图", size=10,
                       color=COLORS["text_secondary"]).pack(side="left", padx=(10, 0))
        self._mk_btn(ph2, "📂  打开目录", "ghost",
                     command=self._open_save_dir).pack(side="right")

        ctx.gallery = ctk.CTkScrollableFrame(
            prev_card, fg_color=COLORS["surface"], corner_radius=8,
            height=260, orientation="vertical")
        ctx.gallery.pack(fill="both", expand=True, padx=20, pady=(0, 16))

        ctx.gallery_photos = []
        ctx.gallery_col = 0
        ctx.gallery_row = 0
        ctx.placeholder = self._mk_label(
            ctx.gallery, placeholder_text, size=13, color=COLORS["text_secondary"])
        ctx.placeholder.pack(pady=40)

    # ================================================================
    #  批量提示词行管理
    # ================================================================
    def _add_batch_row(self, text=""):
        if len(self.batch_rows) >= BATCH_MAX:
            messagebox.showinfo("提示", f"批量出图最多 {BATCH_MAX} 条提示词")
            return
        row = ctk.CTkFrame(self.batch_rows_frame, fg_color="transparent")
        row.pack(fill="x", pady=3)

        idx_lbl = self._mk_label(row, "", size=12, color=COLORS["text_secondary"],
                                 width=28)
        idx_lbl.pack(side="left", padx=(0, 6))

        entry = ctk.CTkEntry(
            row, height=36, fg_color=COLORS["surface"],
            border_color=COLORS["divider"], border_width=1, corner_radius=8,
            text_color=COLORS["text_primary"],
            placeholder_text="输入一条提示词，将单独生成一张图...",
            placeholder_text_color=COLORS["text_secondary"],
            font=("Microsoft YaHei UI", 13))
        entry.pack(side="left", fill="x", expand=True)
        if text:
            entry.insert(0, text)

        rec = {"frame": row, "entry": entry, "idx_lbl": idx_lbl}
        del_btn = ctk.CTkButton(
            row, text="✕", width=36, height=36, corner_radius=8,
            fg_color=COLORS["surface"], hover_color=COLORS["error"],
            text_color=COLORS["text_secondary"],
            font=("Microsoft YaHei UI", 13),
            command=lambda r=rec: self._remove_batch_row(r))
        del_btn.pack(side="left", padx=(6, 0))

        self.batch_rows.append(rec)
        self._renumber_batch_rows()
        self._update_batch_count()

    def _remove_batch_row(self, rec):
        if rec in self.batch_rows:
            self.batch_rows.remove(rec)
        rec["frame"].destroy()
        self._renumber_batch_rows()
        self._update_batch_count()

    def _clear_batch_rows(self):
        for rec in list(self.batch_rows):
            rec["frame"].destroy()
        self.batch_rows.clear()
        # 保留一行空白方便继续填
        self._add_batch_row()

    def _renumber_batch_rows(self):
        for i, rec in enumerate(self.batch_rows, 1):
            rec["idx_lbl"].configure(text=f"{i:>2}.")

    def _get_batch_prompts(self):
        return [rec["entry"].get().strip() for rec in self.batch_rows
                if rec["entry"].get().strip()]

    def _update_batch_count(self):
        if not hasattr(self, "lbl_batch_count"):
            return
        n = len(self._get_batch_prompts())
        if n == 0:
            self.lbl_batch_count.configure(text="请在上方填写提示词",
                                           text_color=COLORS["text_secondary"])
        else:
            self.lbl_batch_count.configure(
                text=f"共 {n} 条有效提示词 = {n} 张待生成（上限 {BATCH_MAX}）",
                text_color=COLORS["accent"])

    def _import_batch_prompts(self):
        path = filedialog.askopenfilename(
            title="导入提示词文件",
            filetypes=[("文本文件", "*.txt"), ("所有文件", "*.*")])
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()
        except UnicodeDecodeError:
            with open(path, "r", encoding="gbk", errors="ignore") as f:
                content = f.read()
        except Exception as e:
            messagebox.showerror("导入失败", str(e))
            return
        lines = [ln.strip() for ln in content.splitlines() if ln.strip()]
        if not lines:
            messagebox.showwarning("提示", "文件中没有有效提示词")
            return

        # 先把已有空行填掉，再追加新行
        added = 0
        for ln in lines:
            if len(self.batch_rows) >= BATCH_MAX:
                break
            # 找一个空行复用
            empty = next((r for r in self.batch_rows if not r["entry"].get().strip()), None)
            if empty:
                empty["entry"].insert(0, ln)
            else:
                self._add_batch_row(ln)
            added += 1
        self._update_batch_count()
        self.log_info(f"已导入 {added} 条提示词到批量列表"
                      + ("（已达上限，部分未导入）" if added < len(lines) else ""))

    # ================================================================
    #  参考图管理（单次 / 批量 共用，按 ctx 区分）
    # ================================================================
    def _build_ref_card(self, page, ctx, note="（本地图片将自动转码上传）",
                        style_lock=False):
        """构建紧凑型参考图卡片，组件引用存入 ctx。
        style_lock=True 时额外提供「锁定参考图风格」开关（批量出图用）。"""
        ref_card = self._mk_card(page)
        ref_card.pack(fill="x", pady=(0, 10), padx=2)

        # 标题行 + 操作按钮全部挤在一行，节省高度
        head = ctk.CTkFrame(ref_card, fg_color="transparent")
        head.pack(fill="x", padx=16, pady=(12, 6))
        self._mk_label(head, f"📎 参考图 {note}", size=12,
                       weight="bold").pack(side="left")
        ctx.lbl_ref_count = self._mk_label(head, "", size=10, color=COLORS["accent"])
        ctx.lbl_ref_count.pack(side="right")
        ctk.CTkButton(
            head, text="📁 选择", height=26, width=58, corner_radius=6,
            fg_color=COLORS["surface"], hover_color=COLORS["card_border"],
            border_color=COLORS["divider"], border_width=1,
            font=("Microsoft YaHei UI", 11),
            command=lambda c=ctx: self._add_ref_images(c)).pack(side="right", padx=(0, 8))
        ctk.CTkButton(
            head, text="清除", height=26, width=46, corner_radius=6,
            fg_color="transparent", hover_color=COLORS["surface"],
            text_color=COLORS["text_secondary"],
            font=("Microsoft YaHei UI", 11),
            command=lambda c=ctx: self._clear_ref_images(c)).pack(side="right", padx=(0, 6))

        # 缩略图行（小尺寸）
        ctx.ref_thumb_frame = ctk.CTkFrame(ref_card, fg_color="transparent")
        ctx.ref_thumb_frame.pack(fill="x", padx=16, pady=(0, 4))

        # URL 行（紧凑）—— gpt-image-2 图生图走文件上传，URL 仅作占位提示
        url_row = ctk.CTkFrame(ref_card, fg_color="transparent")
        url_row.pack(fill="x", padx=16, pady=(0, 12))
        self._mk_label(url_row, "或URL", size=10,
                       color=COLORS["text_secondary"]).pack(side="left", padx=(0, 6))
        ctx.entry_ref_urls = ctk.CTkEntry(
            url_row, height=28, fg_color=COLORS["surface"],
            border_color=COLORS["divider"], border_width=1, corner_radius=6,
            placeholder_text="当前模型图生图请用「选择」上传图片，URL 暂不支持",
            placeholder_text_color=COLORS["text_secondary"],
            font=("Consolas", 10))
        ctx.entry_ref_urls.pack(side="left", fill="x", expand=True)

        # 风格锁定开关（仅批量页）：勾选后给每条提示词自动注入"沿用参考图风格"指令
        if style_lock:
            sl_row = ctk.CTkFrame(ref_card, fg_color="transparent")
            sl_row.pack(fill="x", padx=16, pady=(0, 12))
            ctx.style_lock_var = ctk.BooleanVar(value=True)
            ctk.CTkCheckBox(
                sl_row, text="  锁定参考图风格（让每张图都沿用参考图的配色/版式/质感，保持系列统一）",
                variable=ctx.style_lock_var,
                checkbox_width=18, checkbox_height=18, corner_radius=5,
                fg_color=COLORS["accent"], hover_color=COLORS["accent_hover"],
                border_color=COLORS["divider"], border_width=2,
                text_color=COLORS["text_primary"],
                font=("Microsoft YaHei UI", 11)).pack(side="left")

    def _add_ref_images(self, ctx):
        files = filedialog.askopenfilenames(
            title="选择参考图片",
            filetypes=[("图片文件", "*.png *.jpg *.jpeg *.webp *.bmp"), ("所有文件", "*.*")]
        )
        if not files:
            return
        for f in files:
            if f not in ctx.ref_paths and len(ctx.ref_paths) < 16:
                ctx.ref_paths.append(f)
                self._add_ref_thumbnail(ctx, f)
        self._update_ref_count(ctx)
        self.log_info(f"已添加参考图，共 {len(ctx.ref_paths)} 张")

    def _add_ref_thumbnail(self, ctx, path):
        try:
            img = Image.open(path)
            img.thumbnail((44, 44), Image.LANCZOS)
            photo = ImageTk.PhotoImage(img)
            ctx.ref_photos.append(photo)

            container = ctk.CTkFrame(ctx.ref_thumb_frame, fg_color=COLORS["surface"],
                                     corner_radius=6, width=52, height=52)
            container.pack_propagate(False)
            container.pack(side="left", padx=3, pady=3)

            lbl = ctk.CTkLabel(container, image=photo, text="", cursor="hand2")
            lbl.place(relx=0.5, rely=0.5, anchor="center")

            def _remove(p=path, c=container, ph=photo):
                if p in ctx.ref_paths:
                    ctx.ref_paths.remove(p)
                if ph in ctx.ref_photos:
                    ctx.ref_photos.remove(ph)
                c.destroy()
                self._update_ref_count(ctx)

            lbl.bind("<Button-1>", lambda e: _remove())
        except Exception as e:
            self.log_error(f"加载缩略图失败: {e}")

    def _clear_ref_images(self, ctx):
        ctx.ref_paths.clear()
        ctx.ref_photos.clear()
        for w in ctx.ref_thumb_frame.winfo_children():
            w.destroy()
        self._update_ref_count(ctx)

    def _update_ref_count(self, ctx):
        n = len(ctx.ref_paths)
        ctx.lbl_ref_count.configure(text=f"{n} 张（点击缩略图可删除）" if n > 0 else "")

    def _get_ref_files(self, ctx):
        """把 ctx 的本地参考图压缩为字节流，供 multipart 上传 /v1/images/edits。
        返回 [(filename, bytes, mime), ...]，最多 16 张。单张压到 ~1.5MB 以内最稳。"""
        result = []
        for p in ctx.ref_paths[:16]:
            try:
                img = Image.open(p)
                # 长边超 2048 先缩小，控制体积
                max_side = max(img.width, img.height)
                if max_side > 2048:
                    ratio = 2048 / max_side
                    img = img.resize((int(img.width * ratio), int(img.height * ratio)),
                                     Image.LANCZOS)
                buf = BytesIO()
                quality = 90
                img.convert("RGB").save(buf, format="JPEG", quality=quality)
                # 逐步压到 1.5MB 以内
                while buf.tell() > 1536 * 1024 and quality > 30:
                    quality -= 10
                    buf.seek(0)
                    buf.truncate()
                    img.convert("RGB").save(buf, format="JPEG", quality=quality)
                fbytes = buf.getvalue()
                fname = os.path.splitext(os.path.basename(p))[0] + ".jpg"
                result.append((fname, fbytes, "image/jpeg"))
                self.log_info(f"参考图 {os.path.basename(p)}: "
                              f"{img.width}x{img.height} → {len(fbytes)/1024:.0f}KB")
            except Exception as e:
                self.log_error(f"参考图失败: {os.path.basename(p)} — {e}")
        return result

    def _warn_pasted_urls(self, ctx):
        """gpt-image-2 图生图走 multipart 文件上传，不支持外部 URL。
        若用户在 URL 框粘了内容，给出提示但不使用。"""
        if not getattr(ctx, "entry_ref_urls", None):
            return
        text = ctx.entry_ref_urls.get().strip()
        if text:
            self.log_info("提示: 当前模型图生图通过上传图片文件实现，"
                          "URL 粘贴框对其无效，已忽略。请用「选择」上传本地图片。")

    # ================================================================
    #  导航 & 交互
    # ================================================================
    def _switch_tab(self, tab):
        if self.current_page == tab:
            return
        self.page_config.pack_forget()
        self.page_single.pack_forget()
        self.page_batch.pack_forget()
        if tab == "config":
            self.page_config.pack(fill="both", expand=True, pady=(4, 0))
        elif tab == "single":
            self.page_single.pack(fill="both", expand=True, pady=(4, 0))
        else:
            self.page_batch.pack(fill="both", expand=True, pady=(4, 0))
        self.current_page = tab
        self._highlight_nav(tab)

    def _highlight_nav(self, active):
        for btn, tab in [(self.nav_config, "config"),
                         (self.nav_single, "single"),
                         (self.nav_batch, "batch")]:
            if tab == active:
                btn.configure(fg_color=COLORS["accent"], text_color="white",
                              hover_color=COLORS["accent_hover"])
            else:
                btn.configure(fg_color="transparent", text_color=COLORS["text_secondary"],
                              hover_color=COLORS["surface"])

    def _toggle_key_vis(self):
        if self.entry_key.cget("show") == "•":
            self.entry_key.configure(show="")
            self.btn_toggle_key.configure(text="🙈")
        else:
            self.entry_key.configure(show="•")
            self.btn_toggle_key.configure(text="👁")

    def _save_config(self):
        self.config["api_key"] = self.entry_key.get().strip()
        self.config["api_base"] = self.entry_base.get().strip()
        self.config["save_dir"] = self.entry_save_dir.get().strip()
        self.config["quality"] = self.combo_quality.get()
        save_config(self.config)
        self._check_api_status()
        self._refresh_save_dir_labels()
        self._set_status("✅ 配置已保存", COLORS["success"])
        self.log_success("配置已保存")
        self.win.after(2000, lambda: self._set_status("就绪", COLORS["text_secondary"]))

    def _browse_save_dir(self):
        d = filedialog.askdirectory(title="选择保存目录")
        if d:
            self.entry_save_dir.delete(0, "end")
            self.entry_save_dir.insert(0, d)
            self.config["save_dir"] = d
            save_config(self.config)
            self._refresh_save_dir_labels()

    def _refresh_save_dir_labels(self):
        d = self.config.get("save_dir", "") or "（未设置，请到「配置」页设置）"
        if hasattr(self, "lbl_single_out_dir"):
            self.lbl_single_out_dir.configure(text=d)
        if hasattr(self, "lbl_batch_out_dir"):
            self.lbl_batch_out_dir.configure(text=d)

    def _size_caption(self, size_key, model_name=None):
        info = SIZE_MAP.get(size_key, {"pixel": "1024x1024", "ratio": "1:1"})
        return f"({info['pixel']})"

    # ================================================================
    #  模型管理（查询 / 手动 / 多保存）
    # ================================================================
    def _model_list(self):
        """返回用户已保存的模型列表（至少含一个占位，避免下拉为空）。"""
        models = self.config.get("models") or []
        return models if models else ["gpt-image-2"]

    def _current_model(self):
        models = self._model_list()
        cur = self.config.get("model", "")
        return cur if cur in models else models[0]

    def _refresh_saved_models(self):
        """重绘配置页「已保存模型」列表。"""
        if not hasattr(self, "frame_saved_models"):
            return
        for w in self.frame_saved_models.winfo_children():
            w.destroy()
        models = self.config.get("models") or []
        if not models:
            self._mk_label(self.frame_saved_models, "（暂无，请查询或手动添加）",
                           size=11, color=COLORS["text_secondary"]).pack(
                anchor="w", padx=12, pady=8)
            return
        for m in models:
            row = ctk.CTkFrame(self.frame_saved_models, fg_color="transparent")
            row.pack(fill="x", padx=10, pady=3)
            self._mk_label(row, f"• {m}", size=12).pack(side="left")
            ctk.CTkButton(
                row, text="删除", height=24, width=48, corner_radius=6,
                fg_color="transparent", hover_color=COLORS["error"],
                text_color=COLORS["text_secondary"],
                font=("Microsoft YaHei UI", 11),
                command=lambda name=m: self._remove_model(name)).pack(side="right")

    def _sync_model_dropdowns(self):
        """模型列表变动后，同步更新单次/批量页的模型下拉。"""
        values = self._model_list()
        cur = self._current_model()
        for combo in ("combo_single_model", "combo_batch_model"):
            if hasattr(self, combo):
                getattr(self, combo).configure(values=values)
                getattr(self, combo).set(cur)

    def _add_model(self, name):
        name = (name or "").strip()
        if not name:
            return
        models = self.config.get("models") or []
        if name in models:
            self._set_status(f"模型 {name} 已存在", COLORS["warning"])
            return
        models.append(name)
        self.config["models"] = models
        self.config["model"] = name
        save_config(self.config)
        self._refresh_saved_models()
        self._sync_model_dropdowns()
        self.log_success(f"已添加模型: {name}")
        self._set_status(f"✅ 已添加模型 {name}", COLORS["success"])

    def _add_manual_model(self):
        name = self.entry_manual_model.get().strip()
        if not name:
            messagebox.showinfo("提示", "请输入模型 ID")
            return
        self._add_model(name)
        self.entry_manual_model.delete(0, "end")

    def _remove_model(self, name):
        models = self.config.get("models") or []
        if name in models:
            models.remove(name)
        self.config["models"] = models
        if self.config.get("model") == name:
            self.config["model"] = models[0] if models else "gpt-image-2"
        save_config(self.config)
        self._refresh_saved_models()
        self._sync_model_dropdowns()
        self.log_info(f"已删除模型: {name}")

    def _query_models(self):
        """查询云雾可用模型（GET /v1/models），子线程执行，失败可重试。"""
        api_key = self.entry_key.get().strip() or self.config.get("api_key", "")
        api_base = self.entry_base.get().strip() or self.config.get("api_base", "")
        if not api_key:
            messagebox.showwarning("提示", "请先填入 API Key 再查询模型")
            return
        self.btn_query_models.configure(state="disabled", text="查询中...")
        self.lbl_query_hint.configure(text="正在请求 /v1/models ...",
                                      text_color=COLORS["text_secondary"])

        def worker():
            try:
                ids, info = openai_list_models(api_base, api_key)
                self._ui(lambda: self._on_models_queried(ids))
            except Exception as e:
                msg = str(e)
                self._ui(lambda m=msg: self._on_models_query_failed(m))

        threading.Thread(target=worker, daemon=True).start()

    def _on_models_queried(self, ids):
        self.btn_query_models.configure(state="normal", text="🔍 查询可用模型")
        if not ids:
            self.lbl_query_hint.configure(text="未返回模型，可手动输入",
                                          text_color=COLORS["warning"])
            return
        self.lbl_query_hint.configure(text=f"查到 {len(ids)} 个模型",
                                      text_color=COLORS["success"])
        self.log_success(f"查询到 {len(ids)} 个可用模型")
        self._show_model_picker(ids)

    def _on_models_query_failed(self, msg):
        self.btn_query_models.configure(state="normal", text="🔄 重试查询")
        self.lbl_query_hint.configure(text=f"查询失败：{msg[:50]}（可重试或手动输入）",
                                      text_color=COLORS["error"])
        self.log_error(f"查询模型失败: {msg[:120]}")

    def _show_model_picker(self, ids):
        """弹窗列出查询到的模型，勾选后加入已保存列表。"""
        win = ctk.CTkToplevel(self.win)
        win.title("选择要添加的模型")
        win.geometry("420x520")
        win.configure(fg_color=COLORS["bg"])
        win.transient(self.win)
        win.grab_set()

        self._mk_label(win, f"云雾 API 可用模型（{len(ids)} 个）", size=14,
                       weight="bold").pack(anchor="w", padx=20, pady=(16, 4))
        self._mk_label(win, "勾选需要的模型，点底部「添加所选」", size=11,
                       color=COLORS["text_secondary"]).pack(anchor="w", padx=20, pady=(0, 8))

        scroll = ctk.CTkScrollableFrame(win, fg_color=COLORS["surface"], corner_radius=8)
        scroll.pack(fill="both", expand=True, padx=20, pady=(0, 10))

        existing = set(self.config.get("models") or [])
        vars_map = {}
        for mid in ids:
            v = ctk.BooleanVar(value=False)
            vars_map[mid] = v
            label = f"  {mid}" + ("  (已保存)" if mid in existing else "")
            ctk.CTkCheckBox(
                scroll, text=label, variable=v,
                checkbox_width=18, checkbox_height=18, corner_radius=5,
                fg_color=COLORS["accent"], hover_color=COLORS["accent_hover"],
                border_color=COLORS["divider"], border_width=2,
                text_color=COLORS["text_primary"],
                font=("Microsoft YaHei UI", 12)).pack(anchor="w", padx=12, pady=4)

        def do_add():
            chosen = [m for m, v in vars_map.items() if v.get()]
            added = 0
            for m in chosen:
                if m not in (self.config.get("models") or []):
                    self._add_model(m)
                    added += 1
            win.destroy()
            if added:
                self._set_status(f"✅ 已添加 {added} 个模型", COLORS["success"])

        bf = ctk.CTkFrame(win, fg_color="transparent")
        bf.pack(fill="x", padx=20, pady=(0, 16))
        self._mk_btn(bf, "添加所选", "primary", command=do_add).pack(side="left")
        self._mk_btn(bf, "取消", "secondary", command=win.destroy).pack(side="left", padx=(8, 0))

    def _on_quality_changed(self, choice):
        self.config["quality"] = choice
        save_config(self.config)

    # ---- 单次页模型/尺寸 ----
    def _on_single_model_changed(self, choice):
        self.config["model"] = choice
        save_config(self.config)
        if hasattr(self, "combo_batch_model"):
            self.combo_batch_model.set(choice)
        self.lbl_single_pixel.configure(
            text=self._size_caption(self.combo_single_size.get(), choice))

    def _on_single_size_changed(self, choice):
        self.lbl_single_pixel.configure(text=self._size_caption(choice))
        self.config["last_size_key"] = choice
        save_config(self.config)
        if hasattr(self, "combo_batch_size"):
            self.combo_batch_size.set(choice)
            self.lbl_batch_pixel.configure(text=self._size_caption(choice))

    def _refresh_single_model_hint(self):
        if not hasattr(self, "lbl_single_model_hint"):
            return
        self.lbl_single_model_hint.configure(
            text=f"画质 {self.config.get('quality', 'auto')}")

    # ---- 批量页模型/尺寸 ----
    def _on_batch_model_changed(self, choice):
        self.config["model"] = choice
        save_config(self.config)
        if hasattr(self, "combo_single_model"):
            self.combo_single_model.set(choice)
        self.lbl_batch_pixel.configure(
            text=self._size_caption(self.combo_batch_size.get(), choice))

    def _on_batch_size_changed(self, choice):
        self.lbl_batch_pixel.configure(text=self._size_caption(choice))
        self.config["last_size_key"] = choice
        save_config(self.config)
        if hasattr(self, "combo_single_size"):
            self.combo_single_size.set(choice)
            self.lbl_single_pixel.configure(text=self._size_caption(choice))

    def _refresh_batch_model_hint(self):
        if not hasattr(self, "lbl_batch_model_hint"):
            return
        self.lbl_batch_model_hint.configure(
            text=f"画质 {self.config.get('quality', 'auto')}")

    def _check_api_status(self):
        has = bool(self.entry_key.get().strip() or self.config.get("api_key", ""))
        self.lbl_api_status.configure(
            text="🟢 API 已配置" if has else "🔴 未配置 API",
            text_color=COLORS["success"] if has else COLORS["error"])

    # ================================================================
    #  一览网格
    # ================================================================
    def _reset_gallery(self, ctx):
        for w in ctx.gallery.winfo_children():
            w.destroy()
        ctx.gallery_photos = []
        ctx.gallery_col = 0
        ctx.gallery_row = 0

    def _add_gallery_item(self, ctx, filepath, ok=True, caption=""):
        cols = 3
        cell = ctk.CTkFrame(ctx.gallery, fg_color=COLORS["card"],
                            corner_radius=8, width=150, height=170)
        cell.grid(row=ctx.gallery_row, column=ctx.gallery_col,
                  padx=6, pady=6, sticky="n")
        cell.grid_propagate(False)

        if ok:
            try:
                img = Image.open(filepath)
                img.thumbnail((130, 120), Image.LANCZOS)
                photo = ImageTk.PhotoImage(img)
                ctx.gallery_photos.append(photo)
                lbl = ctk.CTkLabel(cell, image=photo, text="", cursor="hand2")
                lbl.pack(pady=(8, 4))
                lbl.bind("<Double-Button-1>",
                         lambda e, p=filepath: self._open_file(p))
                ctk.CTkLabel(cell, text=os.path.basename(filepath),
                             font=("Microsoft YaHei UI", 9),
                             text_color=COLORS["text_secondary"],
                             wraplength=134).pack(pady=(0, 6))
            except Exception:
                ctk.CTkLabel(cell, text="✅ 已保存\n(预览失败)",
                             font=("Microsoft YaHei UI", 11),
                             text_color=COLORS["success"]).pack(expand=True)
        else:
            ctk.CTkLabel(cell, text="❌", font=("Microsoft YaHei UI", 28),
                         text_color=COLORS["error"]).pack(pady=(20, 4))
            ctk.CTkLabel(cell, text=caption or "失败",
                         font=("Microsoft YaHei UI", 9),
                         text_color=COLORS["error"],
                         wraplength=134).pack(pady=(0, 6))

        ctx.gallery_col += 1
        if ctx.gallery_col >= cols:
            ctx.gallery_col = 0
            ctx.gallery_row += 1

    def _set_status(self, msg, color=None):
        self.status_bar.configure(text=msg, text_color=color or COLORS["text_secondary"])

    def _open_save_dir(self):
        d = self.config.get("save_dir", "")
        if d and os.path.isdir(d):
            self._open_file(d)
        else:
            messagebox.showwarning("提示", "保存目录无效，请到「配置」页设置")

    # ================================================================
    #  通用前置校验
    # ================================================================
    def _validate_common(self):
        """返回 (api_key, api_base, save_dir) 或 None。"""
        api_key = self.entry_key.get().strip() or self.config.get("api_key", "")
        if not api_key:
            messagebox.showwarning("提示", "请先在「配置」页填入 API Key")
            self._switch_tab("config")
            return None
        api_base = self.entry_base.get().strip() or self.config["api_base"]
        save_dir = self.config.get("save_dir", "").strip()
        if not save_dir or not os.path.isdir(save_dir):
            messagebox.showwarning(
                "提示", f"默认保存目录无效或不存在，请到「配置」页设置:\n{save_dir}")
            self._switch_tab("config")
            return None
        return api_key, api_base, save_dir

    def _quality(self):
        """当前画质档位（gpt-image-2 的 quality 字段），默认 auto。
        只接受 QUALITY_OPTIONS 内的值，旧配置（如速创时代的 4K）一律回退 auto，
        避免把非法画质传给云雾 API 导致调用失败。"""
        q = self.config.get("quality", "auto")
        return q if q in QUALITY_OPTIONS else "auto"

    # ================================================================
    #  单次出图流程
    # ================================================================
    def _start_single(self):
        if self.generating:
            return
        prompt = self.text_single_prompt.get("1.0", "end-1c").strip()
        if not prompt:
            messagebox.showwarning("提示", "请输入提示词")
            return
        model_name = self.combo_single_model.get()
        common = self._validate_common()
        if not common:
            return
        api_key, api_base, save_dir = common

        size_key = self.combo_single_size.get()
        prefix = self.entry_single_prefix.get().strip() or "ai_image"
        quality = self._quality()

        # 出图张数（单条提示词，一次可出多张）
        try:
            count = int(self.combo_single_count.get())
        except Exception:
            count = 1
        count = max(1, min(count, SINGLE_MAX))
        self.config["single_count"] = count

        self._warn_pasted_urls(self.ctx_single)
        ref_files = self._get_ref_files(self.ctx_single)

        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        jobs = []
        if count == 1:
            jobs.append({"seq": 1, "prompt": prompt,
                         "filepath": os.path.join(save_dir, f"{prefix}_{ts}.png")})
        else:
            for i in range(1, count + 1):
                fname = f"{prefix}_{ts}_{i:02d}.png"
                jobs.append({"seq": i, "prompt": prompt,
                             "filepath": os.path.join(save_dir, fname)})

        self._persist_gen_config(api_key, api_base, save_dir, prefix, size_key)

        if not self.log_expanded:
            self._toggle_log()
        mode = "图生图" if ref_files else "文生图"
        self.log_info(f"单次开始 | 模型={model_name} | {mode} | 尺寸={size_key} "
                      f"| 画质={quality} | 张数={count} | 参考图={len(ref_files)}")

        self._begin_generation(self.ctx_single, "⏳  生成中...",
                               api_base, api_key, size_key, model_name,
                               quality, ref_files, jobs)

    # ================================================================
    #  批量出图流程
    # ================================================================
    def _start_batch(self):
        if self.generating:
            return
        prompts = self._get_batch_prompts()
        if not prompts:
            messagebox.showwarning("提示", "请在提示词列表中至少填写一条")
            return
        if len(prompts) > BATCH_MAX:
            messagebox.showwarning("提示", f"批量出图最多 {BATCH_MAX} 张")
            return
        model_name = self.combo_batch_model.get()
        common = self._validate_common()
        if not common:
            return
        api_key, api_base, save_dir = common

        size_key = self.combo_batch_size.get()
        prefix = self.entry_batch_prefix.get().strip() or "ai_image"
        quality = self._quality()

        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        jobs = []
        for i, prompt in enumerate(prompts, 1):
            fname = f"{prefix}_{ts}_{i:02d}.png"
            jobs.append({"seq": i, "prompt": prompt,
                         "filepath": os.path.join(save_dir, fname)})
        total = len(jobs)

        if total > 20:
            if not messagebox.askyesno(
                    "确认批量生成",
                    f"本次将生成 {total} 张图片（每条提示词 1 张）。\n按张计费，确认继续？"):
                return

        # 批量页参考图：作为模板应用到所有提示词
        self._warn_pasted_urls(self.ctx_batch)
        ref_files = self._get_ref_files(self.ctx_batch)

        # 风格锁定：勾选且有参考图时，给每条提示词注入"沿用参考图风格"前缀
        style_lock = bool(self.ctx_batch.style_lock_var
                          and self.ctx_batch.style_lock_var.get())
        if style_lock and ref_files:
            for job in jobs:
                job["prompt"] = STYLE_LOCK_DIRECTIVE + job["prompt"]
            self.log_info("已启用「锁定参考图风格」：每条提示词均注入统一风格指令")
        elif style_lock and not ref_files:
            self.log_info("提示: 已勾选锁定风格，但未提供参考图，风格指令不生效")

        self._persist_gen_config(api_key, api_base, save_dir, prefix, size_key)

        if not self.log_expanded:
            self._toggle_log()
        mode = "图生图" if ref_files else "文生图"
        self.log_info(f"批量开始 | 模型={model_name} | {mode} | 尺寸={size_key} "
                      f"| 画质={quality} | {total} 条提示词 = {total} 张 "
                      f"| 参考图={len(ref_files)} "
                      f"| 并发={min(MAX_CONCURRENCY, total)}")

        self._begin_generation(self.ctx_batch, "⏳  批量生成中...",
                               api_base, api_key, size_key, model_name,
                               quality, ref_files, jobs)

    def _persist_gen_config(self, api_key, api_base, save_dir, prefix, size_key):
        for key, val in [("api_key", api_key), ("api_base", api_base),
                         ("save_dir", save_dir), ("filename_prefix", prefix),
                         ("last_size_key", size_key)]:
            self.config[key] = val
        save_config(self.config)

    # ================================================================
    #  通用生成执行（两页共用）
    # ================================================================
    def _begin_generation(self, ctx, busy_text, api_base, api_key, size_key,
                          model_name, quality, ref_files, jobs):
        total = len(jobs)
        self.generating = True
        ctx.btn_generate.configure(text=busy_text, state="disabled",
                                   fg_color=COLORS["surface"],
                                   text_color=COLORS["text_secondary"])
        # 锁住另一个页的生成按钮，避免并行冲突
        other = self.ctx_batch if ctx is self.ctx_single else self.ctx_single
        other.btn_generate.configure(state="disabled")

        ctx.progress_frame.pack(fill="x", padx=2, pady=(14, 0))
        ctx.progress_bar.configure(mode="determinate")
        ctx.progress_bar.set(0)
        ctx.progress_bar.pack(fill="x")
        ctx.lbl_progress.pack(anchor="w", pady=(4, 0))
        ctx.lbl_progress.configure(text=f"0 / {total} 已完成")
        self._reset_gallery(ctx)

        t = threading.Thread(
            target=self._batch_worker,
            args=(ctx, api_base, api_key, size_key, model_name, quality, ref_files, jobs),
            daemon=True)
        t.start()

    def _batch_worker(self, ctx, api_base, api_key, size_key, model_name,
                      quality, ref_files, jobs):
        total = len(jobs)
        done = {"n": 0, "ok": 0, "fail": 0}
        lock = threading.Lock()

        def run_one(job):
            try:
                fp = self._generate_one(api_base, api_key, job["prompt"],
                                        size_key, model_name, quality,
                                        job["filepath"], ref_files, job["seq"])
                with lock:
                    done["ok"] += 1
                self._ui(lambda p=fp: self._add_gallery_item(ctx, p, ok=True))
            except Exception as e:
                msg = str(e)
                with lock:
                    done["fail"] += 1
                self.log_error(f"#{job['seq']} 失败: {msg[:80]}")
                cap = msg[:40]
                self._ui(lambda c=cap: self._add_gallery_item(ctx, "", ok=False, caption=c))
            finally:
                with lock:
                    done["n"] += 1
                    n = done["n"]
                frac = n / total
                self._ui(lambda f=frac, c=n: (
                    ctx.progress_bar.set(f),
                    ctx.lbl_progress.configure(text=f"{c} / {total} 已完成")))

        workers = min(MAX_CONCURRENCY, total)
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futures = [ex.submit(run_one, j) for j in jobs]
            for _ in as_completed(futures):
                pass

        self._ui(lambda: self._batch_done(ctx, done["ok"], done["fail"], total))

    def _generate_one(self, api_base, api_key, prompt, size_key, model_name,
                      quality, filepath, ref_files, seq):
        """OpenAI 兼容同步出图：有参考图走图生图(edits)，否则文生图(generations)。"""
        size_info = SIZE_MAP.get(size_key, {"pixel": "1024x1024", "ratio": "1:1"})
        size_pixel = size_info["pixel"]

        if ref_files:
            img_data, log_data = openai_edits(
                api_base, api_key, model_name, prompt, size_pixel,
                ref_files, quality=quality, n=1)
            self.log_request("POST", log_data["url"], log_data["payload"])
        else:
            img_data, log_data = openai_generations(
                api_base, api_key, model_name, prompt, size_pixel,
                quality=quality, n=1)
            self.log_request("POST", log_data["url"], log_data["payload"])

        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        with open(filepath, "wb") as f:
            f.write(img_data)
        self.log_success(f"#{seq} 完成: {os.path.basename(filepath)} "
                         f"({len(img_data)/1024/1024:.2f} MB)")
        return filepath

    def _batch_done(self, ctx, ok, fail, total):
        self.generating = False
        ctx.progress_bar.set(1.0)
        # 恢复两个按钮
        self.ctx_single.btn_generate.configure(
            text="🚀  开始生成", state="normal",
            fg_color=COLORS["accent"], hover_color=COLORS["accent_hover"],
            text_color="white")
        self.ctx_batch.btn_generate.configure(
            text="🚀  批量生成", state="normal",
            fg_color=COLORS["accent"], hover_color=COLORS["accent_hover"],
            text_color="white")

        if fail == 0:
            self._set_status(f"✅ 完成：成功 {ok}/{total} 张", COLORS["success"])
        else:
            self._set_status(f"⚠️ 结束：成功 {ok}，失败 {fail}，共 {total} 张",
                             COLORS["warning"])
        self.log_info(f"结束 | 成功 {ok} | 失败 {fail} | 共 {total}")
        self.win.after(800, lambda: ctx.progress_frame.pack_forget())

    def _ui(self, fn):
        self.win.after(0, fn)

    def _open_file(self, path):
        try:
            if os.name == "nt":
                os.startfile(path)
            else:
                import subprocess
                subprocess.Popen(["xdg-open", path])
        except Exception as e:
            self.log_error(f"打开失败: {e}")

    def run(self):
        self.win.mainloop()


if __name__ == "__main__":
    app = ImageGeneratorApp()
    app.run()
