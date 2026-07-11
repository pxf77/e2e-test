from __future__ import annotations

import json
import sys


def main() -> int:
    payload = json.loads(sys.stdin.read() or "{}")
    inputs = payload.get("inputs") or {}
    config = payload.get("plugin_config") or {}
    message = str(inputs.get("message") or config.get("default_message") or "hello")
    result = {
        "status": "success",
        "outputs": {
            "plugin_echo": {
                "message": message,
                "plugin_id": "echo",
            }
        },
        "metrics": {"message_length": len(message)},
        "warnings": [],
    }
    sys.stdout.write(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
