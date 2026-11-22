"""``tokens.json`` → CSS variables.

The theme takes its scales from the Klyro design system and nothing of its
brand: the accent, the name and the logo are Shotforge's own. Run
``python -m shotforge.web.tokens`` after editing the tokens to rewrite
``static/tokens.css``.
"""

from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
TOKENS_FILE = HERE / "tokens.json"
CSS_FILE = HERE / "static" / "tokens.css"

HEADER = """\
/* Generated from tokens.json by `python -m shotforge.web.tokens`. Do not edit. */
"""


def flatten(tokens: dict, prefix: str = "") -> dict[str, str]:
    flat: dict[str, str] = {}
    for key, value in tokens.items():
        if key.startswith("$"):
            continue
        name = f"{prefix}-{key}" if prefix else key
        if isinstance(value, dict):
            flat.update(flatten(value, name))
        else:
            flat[name] = str(value)
    return flat


def build_css(tokens: dict) -> str:
    lines = [HEADER, ":root {"]
    for name, value in flatten(tokens).items():
        lines.append(f"  --{name}: {value};")
    lines.append("}")
    return "\n".join(lines) + "\n"


def main() -> None:
    tokens = json.loads(TOKENS_FILE.read_text(encoding="utf-8"))
    CSS_FILE.parent.mkdir(parents=True, exist_ok=True)
    CSS_FILE.write_text(build_css(tokens), encoding="utf-8")
    print(f"wrote {CSS_FILE}")


if __name__ == "__main__":
    main()
