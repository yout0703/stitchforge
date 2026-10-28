"""Stream only deployment-required secrets to SSH; never run without redirecting stdout."""

import json
from pathlib import Path

from dotenv import dotenv_values


def main():
    local = dotenv_values(Path(__file__).resolve().parents[1] / ".env")
    values = {
        "STITCHFORGE_PUBLIC_ORIGIN": "https://stichforge.0703.pro",
        "STITCHFORGE_DATA_DIR": "/data",
        "STITCHFORGE_WORKER_ENABLED": "1",
        "STITCHFORGE_CODEX_ENABLED": "0",
        "STITCHFORGE_AI_PROVIDER": "deepseek" if local.get("DEEPSEEK_API_KEY") else "local",
        "STITCHFORGE_CREDIT_PACKS": "[]",
        "STITCHFORGE_WECHAT_ENABLED": "0",
        "STITCHFORGE_WECHAT_H5_ENABLED": "0",
        "STITCHFORGE_ALIPAY_ENABLED": "0",
        "STITCHFORGE_STRIPE_ENABLED": "0",
        "TZ": "Asia/Shanghai",
    }
    for key in (
        "DEEPSEEK_API_KEY",
        "STITCHFORGE_DEEPSEEK_MODEL",
        "STITCHFORGE_DEEPSEEK_TIMEOUT",
    ):
        if local.get(key):
            values[key] = local[key]
    for key, value in values.items():
        print(f"{key}={json.dumps(value, ensure_ascii=True)}")


if __name__ == "__main__":
    main()
