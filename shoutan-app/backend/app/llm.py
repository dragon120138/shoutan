"""LLM 客户端：用户自带 API key，兼容 OpenAI 协议（GLM/OpenAI/DeepSeek 等）。
支持流式输出 + 429 自动重试 + 友好错误提示。"""
import asyncio
import json
from typing import AsyncIterator

import httpx


class LLMError(Exception):
    pass


def _friendly_error(status: int, body: str) -> str:
    """把 HTTP 错误码翻译成用户能看懂的话。"""
    body_short = body[:300] if body else ""
    if status == 401:
        return "API Key 无效或已过期，请到「⚙ 设置」检查并重新填写。"
    if status == 403:
        return "无权访问该模型（403）。可能该 Key 未开通此模型权限，或套餐不支持。试试换 glm-4-flash。"
    if status == 404:
        return f"接口地址或模型名错误（404）。检查 base_url 和 model 是否匹配。详情：{body_short}"
    if status == 429:
        # 解析限流提示
        msg = ""
        try:
            err = json.loads(body)
            msg = err.get("error", {}).get("message", "") if isinstance(err, dict) else ""
        except Exception:
            pass
        hint = "速率限制（429）。"
        if "balance" in msg.lower() or "余额" in msg or "额度" in msg:
            hint += "你的账户余额/免费额度已用尽，请充值或更换 Key。"
        elif "limit" in msg.lower() or "频繁" in msg or "频繁" in msg:
            hint += "请求过于频繁，请等 10-30 秒后重试。旗舰模型（如 glm-5.2）限流更严，可换 glm-4-flash 试试。"
        else:
            hint += "请稍候重试，或换 glm-4-flash（限流宽松、免费额度大）。"
        if msg:
            hint += f"\n接口提示：{msg}"
        return hint
    if status >= 500:
        return f"LLM 服务端错误（{status}），稍候重试。详情：{body_short}"
    return f"LLM 接口返回 {status}：{body_short}"


async def stream_chat(
    messages: list[dict],
    api_key: str,
    base_url: str,
    model: str,
    timeout: float = 90.0,
    max_retries: int = 2,
) -> AsyncIterator[str]:
    """流式调用兼容 OpenAI 协议的接口，逐 token 产出文本。
    遇 429/5xx 自动重试（指数退避）。
    """
    if not api_key:
        raise LLMError("未提供 API key，请在「⚙ 设置」中填入。")
    if not base_url:
        raise LLMError("未提供 base_url。")

    url = base_url.rstrip("/") + "/chat/completions"
    payload = {
        "model": model,
        "messages": messages,
        "stream": True,
        "temperature": 0.7,
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    last_err = None
    for attempt in range(max_retries + 1):
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                async with client.stream("POST", url, json=payload, headers=headers) as resp:
                    if resp.status_code == 429 and attempt < max_retries:
                        # 速率限制：退避后重试
                        wait = 5 * (attempt + 1)
                        body = await resp.aread()
                        last_err = _friendly_error(429, body.decode("utf-8", "ignore"))
                        # 给前端一个提示（通过 yield 一个标记）
                        yield f"\n\n> ⏳ {last_err.split(chr(10))[0]}，{wait}秒后自动重试…\n\n"
                        await asyncio.sleep(wait)
                        continue
                    if resp.status_code != 200:
                        body = await resp.aread()
                        raise LLMError(_friendly_error(resp.status_code, body.decode("utf-8", "ignore")))
                    async for line in resp.aiter_lines():
                        if not line:
                            continue
                        if line.startswith("data: "):
                            data = line[6:].strip()
                            if data == "[DONE]":
                                break
                            try:
                                obj = json.loads(data)
                            except json.JSONDecodeError:
                                continue
                            choices = obj.get("choices", [])
                            if choices:
                                delta = choices[0].get("delta", {})
                                content = delta.get("content")
                                if content:
                                    yield content
                    return  # 成功完成
        except httpx.RequestError as e:
            last_err = f"网络请求失败：{e}"
            if attempt < max_retries:
                await asyncio.sleep(3)
                continue
            raise LLMError(last_err)
        except LLMError:
            raise
        except Exception as e:
            last_err = f"未知错误：{e}"
            if attempt < max_retries:
                await asyncio.sleep(3)
                continue
            raise LLMError(last_err)
    if last_err:
        raise LLMError(last_err)
