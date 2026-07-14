"""Claude APIレスポンスのJSONパースユーティリティ。

システムプロンプトで「JSON形式のみを出力」と指示していても、Claudeが
```json ... ``` のようにコードフェンスで囲んで返すことがあるため、
パース前に剥がす。
"""

import json
import re

_CODE_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


def parse(text: str) -> dict:
    """Claudeの応答テキストをJSONとしてパースする。"""
    cleaned = _CODE_FENCE_RE.sub("", text.strip()).strip()
    return json.loads(cleaned)
