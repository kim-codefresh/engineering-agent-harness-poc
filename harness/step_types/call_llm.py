import json
import os
import litellm


def execute(state: dict, config: dict) -> dict:
    prompt = config["prompt"].format(**state)
    model  = os.getenv("LITELLM_MODEL", "anthropic/claude-haiku-4-5-20251001")
    resp   = litellm.completion(
        model=model,
        max_tokens=config.get("max_tokens", 512),
        messages=[{"role": "user", "content": prompt}],
    )
    result = json.loads(resp.choices[0].message.content)
    return {config["output_field"]: result}
