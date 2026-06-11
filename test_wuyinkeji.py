#!/usr/bin/env python3
"""测试 wuyinkeji.com 的 image_gpt API"""

import requests
import json
import sys
import os

API_KEY = os.environ.get("WUYIN_KEJI_API_KEY", "")
BASE_URL = "https://api.wuyinkeji.com/api/async/image_gpt"

HEADERS = {
    "Authorization": f"Bearer {API_KEY}",
    "Content-Type": "application/json",
}


def log(title: str):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}")


def post(payload: dict, timeout: int = 60) -> dict:
    print(f"请求 Body: {json.dumps(payload, ensure_ascii=False)[:200]}")
    r = requests.post(BASE_URL, headers=HEADERS, json=payload, timeout=timeout)
    print(f"状态码: {r.status_code}")
    data = r.json()
    print(f"响应: {json.dumps(data, ensure_ascii=False, indent=2)[:800]}")
    return data


# 尝试多种请求格式
log("尝试1: 标准格式 (prompt + size)")
post({
    "model": "gpt-image-2",
    "prompt": "A cute orange cat sitting on a blue cushion",
    "n": 1,
    "size": "1024x1024",
})

log("尝试2: 不带 model 字段")
post({
    "prompt": "A cute orange cat sitting on a blue cushion",
    "n": 1,
    "size": "1024x1024",
})

log("尝试3: OpenAI images 格式")
post({
    "model": "gpt-image-2",
    "prompt": "A cute orange cat",
    "n": 1,
    "size": "1024x1024",
    "response_format": "url",
})

log("尝试4: 简化 Body")
post({
    "prompt": "cat",
})
