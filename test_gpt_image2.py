#!/usr/bin/env python3
"""测试 yunwu.ai 的 gpt-image-2 模型 API

从 models.json 读取配置，依次测试：
1. 列举可用模型
2. Chat Completions 接口（纯文本）
3. Chat Completions 接口（图片生成）
4. Images API 接口
"""

import requests
import json
import sys
from datetime import datetime

# ===== 配置 =====
BASE_URL = "https://yunwu.ai/v1"
API_KEY = "sk-eKMqTsEJ40rJg4HAEkPVlK1dKXwAx7X5Ewn9sVHtwzcKYt7m"
MODEL_ID = "gpt-image-2"

HEADERS = {
    "Authorization": f"Bearer {API_KEY}",
    "Content-Type": "application/json",
}


def log(title: str, body: str = ""):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}")
    if body:
        print(body)


def test_models():
    """测试 1: 列举模型"""
    log("测试 1: GET /v1/models — 列举可用模型")
    try:
        r = requests.get(f"{BASE_URL}/models", headers=HEADERS, timeout=15)
        print(f"状态码: {r.status_code}")
        data = r.json()
        # 只打印模型 ID 列表
        if "data" in data:
            model_ids = [m["id"] for m in data["data"]]
            print(f"模型数量: {len(model_ids)}")
            print(f"模型列表: {json.dumps(model_ids, indent=2)}")
            # 检查 gpt-image-2 是否存在
            if MODEL_ID in model_ids:
                print(f"\n✅ 找到 {MODEL_ID}")
            else:
                print(f"\n❌ 未找到 {MODEL_ID}，可用模型中包含 gpt-image 的有:")
                img_models = [m for m in model_ids if "image" in m.lower() or "gpt" in m.lower()]
                print(f"   {img_models}")
        else:
            print(f"返回内容: {json.dumps(data, indent=2)[:500]}")
        return True
    except Exception as e:
        print(f"❌ 失败: {e}")
        return False


def test_chat_text():
    """测试 2: Chat Completions 纯文本"""
    log("测试 2: POST /v1/chat/completions — 纯文本对话")
    payload = {
        "model": MODEL_ID,
        "messages": [
            {"role": "user", "content": "用一句话介绍你自己"}
        ],
        "max_tokens": 100,
    }
    try:
        r = requests.post(
            f"{BASE_URL}/chat/completions",
            headers=HEADERS,
            json=payload,
            timeout=60,
        )
        print(f"状态码: {r.status_code}")
        data = r.json()
        if r.status_code == 200:
            content = data["choices"][0]["message"]["content"]
            print(f"回复: {content[:200]}")
            print(f"✅ Chat 文本接口正常")
            return True
        else:
            print(f"❌ 错误: {json.dumps(data, indent=2, ensure_ascii=False)[:500]}")
            return False
    except Exception as e:
        print(f"❌ 异常: {e}")
        return False


def test_chat_image():
    """测试 3: Chat Completions 图片生成"""
    log("测试 3: POST /v1/chat/completions — 图片生成请求")
    payload = {
        "model": MODEL_ID,
        "messages": [
            {
                "role": "user",
                "content": "Generate a simple image: a cute orange cat sitting on a blue cushion",
            }
        ],
        "max_tokens": 500,
    }
    try:
        r = requests.post(
            f"{BASE_URL}/chat/completions",
            headers=HEADERS,
            json=payload,
            timeout=120,
        )
        print(f"状态码: {r.status_code}")
        data = r.json()
        if r.status_code == 200:
            content = data["choices"][0]["message"]["content"]
            print(f"回复长度: {len(content)} 字符")
            print(f"回复前 500 字符: {content[:500]}")
            # 检查是否返回了图片 URL
            if "http" in content and ("png" in content or "jpg" in content or "webp" in content):
                print("✅ 看起来包含图片链接")
            print("✅ Chat 图片接口正常")
            return True
        else:
            print(f"❌ 错误: {json.dumps(data, indent=2, ensure_ascii=False)[:500]}")
            return False
    except Exception as e:
        print(f"❌ 异常: {e}")
        return False


def test_images_api():
    """测试 4: Images API"""
    log("测试 4: POST /v1/images/generations — 标准 Images API")
    payload = {
        "model": MODEL_ID,
        "prompt": "A cute orange cat sitting on a blue cushion",
        "n": 1,
        "size": "1024x1024",
    }
    try:
        r = requests.post(
            f"{BASE_URL}/images/generations",
            headers=HEADERS,
            json=payload,
            timeout=120,
        )
        print(f"状态码: {r.status_code}")
        data = r.json()
        if r.status_code == 200 and "data" in data:
            for img in data["data"]:
                if "url" in img:
                    print(f"图片 URL: {img['url']}")
                if "b64_json" in img:
                    print(f"Base64 长度: {len(img['b64_json'])}")
            print("✅ Images API 正常")
            return True
        else:
            print(f"❌ 失败: {json.dumps(data, indent=2, ensure_ascii=False)[:500]}")
            return False
    except Exception as e:
        print(f"❌ 异常: {e}")
        return False


def main():
    print(f"╔{'═'*58}╗")
    print(f"║  gpt-image-2 API 连通性测试")
    print(f"║  目标: {BASE_URL}")
    print(f"║  模型: {MODEL_ID}")
    print(f"║  时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"╚{'═'*58}╝")

    results = {}

    results["models"] = test_models()
    results["chat_text"] = test_chat_text()
    results["chat_image"] = test_chat_image()
    results["images_api"] = test_images_api()

    # ===== 汇总 =====
    log("测试汇总")
    for name, ok in results.items():
        status = "✅ PASS" if ok else "❌ FAIL"
        print(f"  {status}  {name}")

    all_pass = all(results.values())
    print(f"\n{'🎉 全部通过！API 本身没问题' if all_pass else '⚠️  有测试失败，见上方详情'}")

    return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(main())
