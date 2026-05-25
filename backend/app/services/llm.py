import os
from tenacity import retry, stop_after_attempt, wait_exponential
from databricks.sdk import WorkspaceClient
from databricks.sdk.errors import NotFound, BadRequest

DEFAULT_EP = os.getenv("ENDPOINT_NAME", "databricks-llama-4-maverick")

def _try_sdk_openai_client(w: WorkspaceClient):
    get_client = getattr(w.serving_endpoints, "get_open_ai_client", None)
    if callable(get_client):
        return get_client()
    return None

@retry(reraise=True, stop=stop_after_attempt(6), wait=wait_exponential(multiplier=1, min=1, max=20))
def chat(messages, model=None, temperature=0.2, max_tokens=1024):
    endpoint_name = model or DEFAULT_EP
    w = WorkspaceClient()

    client = _try_sdk_openai_client(w)
    if client:
        resp = client.chat.completions.create(
            model=endpoint_name,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        return resp.choices[0].message.content

    openai_payload = {
        "model": endpoint_name,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }

    try:
        r = w.api_client.do(
            "POST",
            f"/serving-endpoints/{endpoint_name}/openai/v1/chat/completions",
            body=openai_payload,
        )
        return r["choices"][0]["message"]["content"]
    except NotFound:
        pass
    except BadRequest as e:
        pass

    candidates = [
        openai_payload,
        {"messages": messages, "temperature": temperature, "max_tokens": max_tokens},
        {"input": {"messages": messages, "temperature": temperature, "max_tokens": max_tokens}},
        {"inputs": {"messages": messages, "temperature": temperature, "max_tokens": max_tokens}},
    ]

    last_err = None
    for payload in candidates:
        try:
            r = w.api_client.do(
                "POST",
                f"/serving-endpoints/{endpoint_name}/invocations",
                body=payload,
            )
            if isinstance(r, dict):
                if "choices" in r and r["choices"]:
                    return r["choices"][0]["message"]["content"]
                if "predictions" in r and r["predictions"]:
                    first = r["predictions"][0]
                    if isinstance(first, dict):
                        return first.get("content") or first.get("text") or str(first)
                    return str(first)
                if "output_text" in r:
                    return r["output_text"]
            return str(r)
        except Exception as e:
            last_err = e
            continue

    raise RuntimeError(f"Failed to query endpoint '{endpoint_name}': {last_err}")
