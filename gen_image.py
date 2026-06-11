#!/usr/bin/env python3
"""
速创API (wuyinkeji.com) GPT-Image-2 图片生成工具

用法:
  python gen_image.py "一只坐在蓝色垫子上的橘猫"
  python gen_image.py "a red apple" --size 1024x1024 --output mypic.png
"""

import requests
import time
import sys
import os

API_KEY = os.environ.get("WUYIN_KEJI_API_KEY", "")
BASE = "https://api.wuyinkeji.com/api/async"


def submit(prompt: str, size: str = "1024x1024") -> str:
    """提交任务，返回 task_id"""
    r = requests.post(f"{BASE}/image_gpt",
        json={"prompt": prompt, "size": size, "key": API_KEY}, timeout=60)
    data = r.json()
    if data["code"] != 200:
        raise RuntimeError(f"提交失败: {data['msg']}")
    return data["data"]["id"]


def query(task_id: str) -> dict:
    """查询任务状态"""
    r = requests.get(f"{BASE}/detail",
        params={"key": API_KEY, "id": task_id}, timeout=30)
    return r.json()["data"]


def generate(prompt: str, size: str = "1024x1024", output: str = None) -> list:
    """生成图片，返回 URL 列表，可选保存到本地"""
    task_id = submit(prompt, size)
    print(f"✅ 任务已提交: {task_id}")

    while True:
        result = query(task_id)
        status = result["status"]

        if status == 2:  # 完成
            urls = result["result"]
            print(f"🎉 生成完成！{len(urls)} 张图片")
            for i, url in enumerate(urls):
                print(f"   [{i+1}] {url}")

            if output:
                # 下载第一张图片
                img = requests.get(urls[0], timeout=60).content
                with open(output, "wb") as f:
                    f.write(img)
                print(f"💾 已保存: {output}")
            return urls

        elif status == 0:  # 处理中
            print(f"⏳ 处理中，等待 2 秒...")
            time.sleep(2)

        else:  # 失败
            raise RuntimeError(f"生成失败: {result}")


def main():
    if len(sys.argv) < 2:
        print("用法: python gen_image.py <提示词> [--size 1024x1024] [--output 文件.png]")
        print("示例: python gen_image.py \"一只橘猫\" --size 512x512 --output cat.png")
        sys.exit(1)

    prompt = sys.argv[1]
    size = "1024x1024"
    output = None

    i = 2
    while i < len(sys.argv):
        if sys.argv[i] == "--size" and i + 1 < len(sys.argv):
            size = sys.argv[i + 1]
            i += 2
        elif sys.argv[i] == "--output" and i + 1 < len(sys.argv):
            output = sys.argv[i + 1]
            i += 2
        else:
            print(f"未知参数: {sys.argv[i]}")
            sys.exit(1)

    urls = generate(prompt, size, output)
    return urls


if __name__ == "__main__":
    main()
