#!/usr/bin/env python3
"""
投行部智能图片生成器 v3.0 — 精致桌面应用
基于 customtkinter 现代化 UI 框架 · 速创API (wuyinkeji.com)

三大功能页:
  · 配置      —— API Key / 接口地址 / 模型·画质 / 默认保存路径
  · 单次出图  —— 单条提示词，一次可出多张
  · 批量出图  —— 提示词列表(默认5条可增减)，每条单独出 1 张，最多 30 张

特性: 多模型(GPT-Image-2 / NanoBanana2 高清) / 并发出图 / 参考图上传 / 调用日志
打包: pyinstaller --onefile --windowed --name="投行部智能图片生成器" image_generator_app.py
"""

import customtkinter as ctk
from tkinter import filedialog, messagebox
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests
import json
import os
import time
import base64
import ipaddress
from urllib.parse import urlparse
from datetime import datetime
from pathlib import Path
from PIL import Image, ImageTk
from io import BytesIO

# ============================================================
# 全局配置
# ============================================================
APP_NAME = "投行部智能图片生成器"
APP_VERSION = "v3.3"

# 批量并发上限（两种模型 API 均支持并发，留余量避免触发 QPS 限制）
MAX_CONCURRENCY = 5
SINGLE_MAX = 10   # 单次出图：一条提示词最多张数
BATCH_MAX = 30    # 批量出图：最多提示词条数（每条出 1 张）

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

# 模型注册表：不同模型对应不同接口端点与画质能力
# size_kind="pixel": 把像素值塞进 size 字段（GPT-Image-2 现行可用方式）
# size_kind="ratio": 把宽高比塞进 aspectRatio 字段，并用 quality_field 控制画质档位
MODELS = {
    "GPT-Image-2 · 标准画质": {
        "endpoint": "image_gpt",
        "size_kind": "pixel",
        "size_field": "size",
        "ratio_field": None,
        "quality_field": None,
        "qualities": [],
        "desc": "速创 GPT-Image-2，标准画质，不支持高清档位",
    },
    "NanoBanana2 · 高清(1K/2K/4K)": {
        "endpoint": "image_nanoBanana2",
        "size_kind": "ratio",
        "size_field": None,
        "ratio_field": "aspectRatio",
        "quality_field": "size",
        "qualities": ["1K", "2K", "4K"],
        "desc": "速创 NanoBanana2，支持 2K/4K 高清，最高 14 张参考图",
    },
}

CONFIG_FILE = Path.home() / ".gpt_image_gen_config.json"
DEFAULT_CONFIG = {
    "api_key": "",
    "api_base": "https://api.wuyinkeji.com/api/async",
    "save_dir": str(Path.home() / "Pictures"),
    "filename_prefix": "ai_image",
    "last_size_key": "1:1  正方形",
    "model": "GPT-Image-2 · 标准画质",
    "quality": "2K",
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
# API 客户端 (带日志回调)
# ============================================================
def is_unreachable_for_remote(url: str) -> bool:
    """判断一个参考图 URL 是否为远程 API 服务器无法访问的本地/内网地址。"""
    try:
        host = (urlparse(url).hostname or "").strip().lower()
    except Exception:
        return False
    if not host:
        return False
    if host in ("localhost", "localhost.localdomain", "ip6-localhost"):
        return True
    if host.endswith(".local") or host.endswith(".localhost"):
        return True
    try:
        ip = ipaddress.ip_address(host)
        return (ip.is_loopback or ip.is_private or ip.is_link_local
                or ip.is_unspecified or ip.is_reserved)
    except ValueError:
        return False


def _safe_json(r, context: str):
    if not r.text or not r.text.strip():
        raise RuntimeError(f"{context}: API 返回空响应 (HTTP {r.status_code})")
    try:
        return r.json()
    except Exception:
        raise RuntimeError(f"{context}: API 返回非 JSON (HTTP {r.status_code})\n{r.text[:300]}")


def submit_task(api_base, api_key, prompt, size_key, model_name,
                quality=None, ref_urls=None):
    """提交生图任务（多模型）。"""
    model = MODELS.get(model_name, MODELS["GPT-Image-2 · 标准画质"])
    size_info = SIZE_MAP.get(size_key, {"pixel": "1024x1024", "ratio": "1:1"})

    url = f"{api_base}/{model['endpoint']}"
    payload = {"prompt": prompt, "key": api_key}

    if model["size_kind"] == "pixel":
        payload[model["size_field"]] = size_info["pixel"]
    else:
        if model["ratio_field"]:
            payload[model["ratio_field"]] = size_info["ratio"]
        if model["quality_field"] and quality:
            payload[model["quality_field"]] = quality

    if ref_urls:
        payload["urls"] = ref_urls

    r = requests.post(url, json=payload, timeout=30)
    data = _safe_json(r, "提交任务")
    if data.get("code") != 200:
        raise RuntimeError(data.get("msg", f"code={data.get('code')}"))
    return data["data"], {"url": url, "payload": {**payload, "key": "***"}, "response": data}


def query_task(api_base, api_key, task_id):
    """查询任务结果（全模型通用 /detail 接口）。"""
    url = f"{api_base}/detail"
    r = requests.get(url, params={"key": api_key, "id": task_id}, timeout=30)
    data = _safe_json(r, "查询任务")
    if data.get("code") != 200:
        raise RuntimeError(data.get("msg", f"code={data.get('code')}"))
    return data["data"], {"url": r.url, "response": data}


def extract_result_urls(result: dict):
    """从查询结果 data 中兼容提取图片 URL 列表。"""
    if not isinstance(result, dict):
        return []
    for key in ("result", "results", "urls", "images", "image", "url", "output"):
        val = result.get(key)
        if not val:
            continue
        if isinstance(val, str):
            return [val]
        if isinstance(val, list):
            out = []
            for item in val:
                if isinstance(item, str):
                    out.append(item)
                elif isinstance(item, dict):
                    for k in ("url", "image", "src"):
                        if isinstance(item.get(k), str):
                            out.append(item[k])
                            break
            if out:
                return out
    return []




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
                                      corner_radius=8, placeholder_text="请输入速创API的Key...",
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

        # 模型 / 画质（全局默认）
        card_model = self._mk_card(page)
        card_model.pack(fill="x", pady=(0, 12), padx=2)

        self._mk_label(card_model, "🤖  默认模型 / 画质", size=15, weight="bold").pack(
            anchor="w", padx=20, pady=(18, 4))
        self._mk_label(card_model,
                       "标准出图选 GPT-Image-2；需要高清(2K/4K)选 NanoBanana2。\n"
                       "单次/批量页也可临时切换模型。",
                       size=11, color=COLORS["text_secondary"]).pack(
            anchor="w", padx=20, pady=(0, 10))

        mr = ctk.CTkFrame(card_model, fg_color="transparent")
        mr.pack(fill="x", padx=20, pady=(0, 6))
        self._mk_label(mr, "模型", size=12,
                       color=COLORS["text_secondary"]).pack(side="left", padx=(0, 10))
        self.combo_model = self._mk_optionmenu(
            mr, list(MODELS.keys()), width=260, height=36,
            command=self._on_model_changed)
        self.combo_model.pack(side="left")
        cur_model = self.config.get("model", "GPT-Image-2 · 标准画质")
        self.combo_model.set(cur_model if cur_model in MODELS else list(MODELS.keys())[0])

        qr = ctk.CTkFrame(card_model, fg_color="transparent")
        qr.pack(fill="x", padx=20, pady=(6, 4))
        self._mk_label(qr, "画质", size=12,
                       color=COLORS["text_secondary"]).pack(side="left", padx=(0, 10))
        self.combo_quality = self._mk_optionmenu(
            qr, ["1K", "2K", "4K"], width=120, height=36,
            command=self._on_quality_changed)
        self.combo_quality.pack(side="left")
        self.combo_quality.set(self.config.get("quality", "2K"))
        self.lbl_quality_hint = self._mk_label(
            qr, "", size=10, color=COLORS["text_secondary"])
        self.lbl_quality_hint.pack(side="left", padx=(10, 0))

        self._mk_label(card_model, "", size=2).pack(pady=(0, 8))
        self._refresh_quality_state()

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
                       "投行部智能图片生成器，基于速创API\n"
                       "单次出图 / 批量出图(最多30张) / GPT-Image-2 / NanoBanana2(2K/4K高清)\n"
                       "中英文提示词 / 参考图上传 / 并发出图 / 调用日志追踪",
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
            rm, list(MODELS.keys()), width=240,
            command=self._on_single_model_changed)
        self.combo_single_model.pack(side="left")
        cur_model = self.config.get("model", "GPT-Image-2 · 标准画质")
        self.combo_single_model.set(cur_model if cur_model in MODELS else list(MODELS.keys())[0])
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
            rm, list(MODELS.keys()), width=240,
            command=self._on_batch_model_changed)
        self.combo_batch_model.pack(side="left")
        cur_model = self.config.get("model", "GPT-Image-2 · 标准画质")
        self.combo_batch_model.set(cur_model if cur_model in MODELS else list(MODELS.keys())[0])
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

        # URL 行（紧凑）
        url_row = ctk.CTkFrame(ref_card, fg_color="transparent")
        url_row.pack(fill="x", padx=16, pady=(0, 12))
        self._mk_label(url_row, "或URL", size=10,
                       color=COLORS["text_secondary"]).pack(side="left", padx=(0, 6))
        ctx.entry_ref_urls = ctk.CTkEntry(
            url_row, height=28, fg_color=COLORS["surface"],
            border_color=COLORS["divider"], border_width=1, corner_radius=6,
            placeholder_text="https://... 每行一个公网URL",
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
            if f not in ctx.ref_paths and len(ctx.ref_paths) < 5:
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

    def _get_ref_urls(self, ctx):
        """将 ctx 的参考图压缩后转 data URL"""
        result = []
        for p in ctx.ref_paths[:3]:
            try:
                img = Image.open(p)
                max_side = max(img.width, img.height)
                if max_side > 512:
                    ratio = 512 / max_side
                    img = img.resize((int(img.width * ratio), int(img.height * ratio)),
                                     Image.LANCZOS)
                buf = BytesIO()
                quality = 75
                img.convert("RGB").save(buf, format="JPEG", quality=quality)
                while buf.tell() > 50 * 1024 and quality > 15:
                    quality -= 15
                    buf.seek(0)
                    buf.truncate()
                    img.convert("RGB").save(buf, format="JPEG", quality=quality)
                b64 = base64.b64encode(buf.getvalue()).decode("utf-8")
                size_kb = buf.tell() / 1024
                result.append(f"data:image/jpeg;base64,{b64}")
                self.log_info(f"参考图 {os.path.basename(p)}: "
                              f"{img.width}x{img.height} → {size_kb:.0f}KB data URL")
            except Exception as e:
                self.log_error(f"参考图失败: {os.path.basename(p)} — {e}")
        return result if result else None

    def _get_pasted_urls(self, ctx):
        text = ctx.entry_ref_urls.get().strip()
        if not text:
            return []
        valid, blocked = [], []
        for u in text.split("\n"):
            u = u.strip()
            if not u.startswith("http"):
                continue
            if is_unreachable_for_remote(u):
                blocked.append(u)
            else:
                valid.append(u)
        if blocked:
            for u in blocked:
                self.log_error(f"已忽略本地/内网参考图地址（云端 API 无法访问）: {u[:80]}")
            self.log_info("提示: 参考图请使用公网 https 链接，或用「选择」上传本地图片。")
        return valid

    def _collect_ref_urls(self, ctx):
        """合并某页的本地参考图(data URL) + 粘贴的公网 URL"""
        return (self._get_ref_urls(ctx) or []) + self._get_pasted_urls(ctx)

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
        self.config["model"] = self.combo_model.get()
        self.config["quality"] = self.combo_quality.get()
        save_config(self.config)
        self._check_api_status()
        self._refresh_save_dir_labels()
        # 同步两个生成页的模型下拉
        self.combo_single_model.set(self.config["model"])
        self.combo_batch_model.set(self.config["model"])
        self._refresh_single_model_hint()
        self._refresh_batch_model_hint()
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

    def _size_caption(self, size_key, model_name):
        info = SIZE_MAP.get(size_key, {"pixel": "1024x1024", "ratio": "1:1"})
        model = MODELS.get(model_name, None)
        if model and model["size_kind"] == "ratio":
            return f"(比例 {info['ratio']})"
        return f"({info['pixel']})"

    # ---- 配置页模型/画质 ----
    def _on_model_changed(self, choice):
        self.config["model"] = choice
        save_config(self.config)
        self._refresh_quality_state()
        # 同步生成页
        if hasattr(self, "combo_single_model"):
            self.combo_single_model.set(choice)
            self._refresh_single_model_hint()
        if hasattr(self, "combo_batch_model"):
            self.combo_batch_model.set(choice)
            self._refresh_batch_model_hint()

    def _on_quality_changed(self, choice):
        self.config["quality"] = choice
        save_config(self.config)
        self._refresh_single_model_hint()
        self._refresh_batch_model_hint()

    def _refresh_quality_state(self):
        if not hasattr(self, "combo_quality"):
            return
        model = MODELS.get(self.combo_model.get(), {})
        if model.get("qualities"):
            self.combo_quality.configure(values=model["qualities"], state="normal")
            if self.combo_quality.get() not in model["qualities"]:
                self.combo_quality.set(model["qualities"][-1])
            self.lbl_quality_hint.configure(text="2K/4K 出图更慢，按张计费")
        else:
            self.combo_quality.configure(state="disabled")
            self.lbl_quality_hint.configure(text="该模型为标准画质，无高清档位")

    # ---- 单次页模型/尺寸 ----
    def _on_single_model_changed(self, choice):
        self.config["model"] = choice
        save_config(self.config)
        if hasattr(self, "combo_model"):
            self.combo_model.set(choice)
            self._refresh_quality_state()
        if hasattr(self, "combo_batch_model"):
            self.combo_batch_model.set(choice)
            self._refresh_batch_model_hint()
        self._refresh_single_model_hint()
        self.lbl_single_pixel.configure(
            text=self._size_caption(self.combo_single_size.get(), choice))

    def _on_single_size_changed(self, choice):
        self.lbl_single_pixel.configure(
            text=self._size_caption(choice, self.combo_single_model.get()))
        self.config["last_size_key"] = choice
        save_config(self.config)
        if hasattr(self, "combo_batch_size"):
            self.combo_batch_size.set(choice)
            self.lbl_batch_pixel.configure(
                text=self._size_caption(choice, self.combo_batch_model.get()))

    def _refresh_single_model_hint(self):
        if not hasattr(self, "lbl_single_model_hint"):
            return
        model = MODELS.get(self.combo_single_model.get(), {})
        if model.get("qualities"):
            self.lbl_single_model_hint.configure(
                text=f"高清 {self.config.get('quality', '2K')}（可在配置页改画质）")
        else:
            self.lbl_single_model_hint.configure(text="标准画质")

    # ---- 批量页模型/尺寸 ----
    def _on_batch_model_changed(self, choice):
        self.config["model"] = choice
        save_config(self.config)
        if hasattr(self, "combo_model"):
            self.combo_model.set(choice)
            self._refresh_quality_state()
        if hasattr(self, "combo_single_model"):
            self.combo_single_model.set(choice)
            self._refresh_single_model_hint()
        self._refresh_batch_model_hint()
        self.lbl_batch_pixel.configure(
            text=self._size_caption(self.combo_batch_size.get(), choice))

    def _on_batch_size_changed(self, choice):
        self.lbl_batch_pixel.configure(
            text=self._size_caption(choice, self.combo_batch_model.get()))
        self.config["last_size_key"] = choice
        save_config(self.config)
        if hasattr(self, "combo_single_size"):
            self.combo_single_size.set(choice)
            self.lbl_single_pixel.configure(
                text=self._size_caption(choice, self.combo_single_model.get()))

    def _refresh_batch_model_hint(self):
        if not hasattr(self, "lbl_batch_model_hint"):
            return
        model = MODELS.get(self.combo_batch_model.get(), {})
        if model.get("qualities"):
            self.lbl_batch_model_hint.configure(
                text=f"高清 {self.config.get('quality', '2K')}（可在配置页改画质）")
        else:
            self.lbl_batch_model_hint.configure(text="标准画质")

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

    def _model_quality(self, model_name):
        model = MODELS.get(model_name, MODELS["GPT-Image-2 · 标准画质"])
        quality = self.config.get("quality", "2K") if model.get("qualities") else None
        return model, quality

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
        model, quality = self._model_quality(model_name)

        # 出图张数（单条提示词，一次可出多张）
        try:
            count = int(self.combo_single_count.get())
        except Exception:
            count = 1
        count = max(1, min(count, SINGLE_MAX))
        self.config["single_count"] = count

        ref_urls = self._collect_ref_urls(self.ctx_single)

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
        q_txt = f" | 画质={quality}" if quality else ""
        self.log_info(f"单次开始 | 模型={model_name} | 尺寸={size_key}{q_txt} "
                      f"| 张数={count} | 参考图={len(ref_urls)}")

        self._begin_generation(self.ctx_single, "⏳  生成中...",
                               api_base, api_key, size_key, model_name,
                               quality, ref_urls, jobs)

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
        model, quality = self._model_quality(model_name)

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
        ref_urls = self._collect_ref_urls(self.ctx_batch)

        # 风格锁定：勾选且有参考图时，给每条提示词注入"沿用参考图风格"前缀
        style_lock = bool(self.ctx_batch.style_lock_var
                          and self.ctx_batch.style_lock_var.get())
        if style_lock and ref_urls:
            for job in jobs:
                job["prompt"] = STYLE_LOCK_DIRECTIVE + job["prompt"]
            self.log_info("已启用「锁定参考图风格」：每条提示词均注入统一风格指令")
        elif style_lock and not ref_urls:
            self.log_info("提示: 已勾选锁定风格，但未提供参考图，风格指令不生效")

        self._persist_gen_config(api_key, api_base, save_dir, prefix, size_key)

        if not self.log_expanded:
            self._toggle_log()
        q_txt = f" | 画质={quality}" if quality else ""
        self.log_info(f"批量开始 | 模型={model_name} | 尺寸={size_key}{q_txt} "
                      f"| {total} 条提示词 = {total} 张 "
                      f"| 参考图={len(ref_urls)} "
                      f"| 并发={min(MAX_CONCURRENCY, total)}")

        self._begin_generation(self.ctx_batch, "⏳  批量生成中...",
                               api_base, api_key, size_key, model_name,
                               quality, ref_urls, jobs)

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
                          model_name, quality, ref_urls, jobs):
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
            args=(ctx, api_base, api_key, size_key, model_name, quality, ref_urls, jobs),
            daemon=True)
        t.start()

    def _batch_worker(self, ctx, api_base, api_key, size_key, model_name,
                      quality, ref_urls, jobs):
        total = len(jobs)
        done = {"n": 0, "ok": 0, "fail": 0}
        lock = threading.Lock()

        def run_one(job):
            try:
                fp = self._generate_one(api_base, api_key, job["prompt"],
                                        size_key, model_name, quality,
                                        job["filepath"], ref_urls, job["seq"])
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
                      quality, filepath, ref_urls, seq):
        model = MODELS.get(model_name, MODELS["GPT-Image-2 · 标准画质"])

        # ---- 速创异步协议 ----
        endpoint = model["endpoint"]

        task, log_data = submit_task(api_base, api_key, prompt, size_key,
                                     model_name, quality, ref_urls)
        task_id = task["id"]
        self.log_request("POST", f"{api_base}/{endpoint}", log_data["payload"])
        self.log_info(f"#{seq} 任务ID: {task_id}")

        waited, max_wait = 0, 240
        while waited < max_wait:
            time.sleep(2)
            waited += 2
            result, _ = query_task(api_base, api_key, task_id)
            status = result.get("status")

            if status == 2:
                urls = extract_result_urls(result)
                if not urls:
                    raise RuntimeError("API 返回空结果")
                dl_r = requests.get(urls[0], timeout=60)
                if dl_r.status_code != 200:
                    raise RuntimeError(f"图片下载失败 (HTTP {dl_r.status_code})")
                img_data = dl_r.content
                os.makedirs(os.path.dirname(filepath), exist_ok=True)
                with open(filepath, "wb") as f:
                    f.write(img_data)
                self.log_success(f"#{seq} 完成: {os.path.basename(filepath)} "
                                 f"({len(img_data)/1024/1024:.2f} MB)")
                return filepath
            elif status == 3:
                raise RuntimeError(result.get("message", "生成失败（可能触发内容审核）"))
            # status 0/1 继续等待

        hint = "（参考图若为本地/内网地址云端无法访问）" if ref_urls else ""
        raise RuntimeError(f"生成超时(>{max_wait}s){hint}")

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
