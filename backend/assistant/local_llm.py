"""可选 Ollama 适配器；模型只辅助措辞，不参与评分。"""
from __future__ import annotations

import json
import os
import urllib.request


def explain_with_local_model(context: dict) -> str | None:
    """调用本机免费模型；未配置或不可用时静默回退到模板。"""
    model = os.getenv("RENOVATION_LLM_MODEL", "").strip()
    if not model:
        return None
    endpoint = os.getenv("RENOVATION_LLM_URL", "http://127.0.0.1:11434/api/generate")
    prompt = (
        "你是居家安全改造助手。只能根据给定模拟结果写两句中文说明，"
        "不得改分数、不得声称真实房间已修改。数据："
        + json.dumps(context, ensure_ascii=False)
    )
    body = json.dumps({"model": model, "prompt": prompt, "stream": False}).encode("utf-8")
    try:
        request = urllib.request.Request(endpoint, data=body, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=4) as response:  # noqa: S310 - 明确仅本机可配置端点
            value = json.loads(response.read().decode("utf-8")).get("response")
            return str(value).strip() if value else None
    except (OSError, ValueError, TimeoutError):
        return None
