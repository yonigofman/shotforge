"""``tokens.json`` → CSS variables and type classes.

The scales come from the Klyro design system at its own values: surfaces,
lines, ink, status colours, the type scale, spacing, radii, shadows, the focus
ring, control sizes, durations and easings — and the volt accent. What is
Shotforge's own is the name, the mark, and ``surface-neutral``, the only ground
allowed around frames and video.

Run ``python -m shotforge.web.tokens`` after editing the tokens to rewrite
``static/tokens.css``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
TOKENS_FILE = HERE / "tokens.json"
CSS_FILE = HERE / "static" / "tokens.css"

HEADER = "/* Generated from tokens.json by `python -m shotforge.web.tokens`. Do not edit. */\n"

#: Which theme the cockpit renders. Light values are carried in tokens.json
#: for a light mode that does not exist yet.
THEME = "dark"


def colour_vars(colours: dict[str, dict[str, str]], theme: str = THEME) -> list[str]:
    return [f"  --color-{name}: {value[theme]};" for name, value in colours.items()]


def scale_vars(prefix: str, scale: dict[str, Any]) -> list[str]:
    return [f"  --{prefix}-{name}: {value};" for name, value in scale.items()]


def type_vars(styles: dict[str, dict[str, Any]]) -> list[str]:
    lines: list[str] = []
    for name, style in styles.items():
        lines.append(f"  --type-{name}-size: {style['size']};")
        lines.append(f"  --type-{name}-line: {style['line']};")
        lines.append(f"  --type-{name}-weight: {style['weight']};")
        lines.append(f"  --type-{name}-tracking: {style['tracking']};")
    return lines


def type_classes(styles: dict[str, dict[str, Any]], stretch: str) -> list[str]:
    """One class per named style, so no component invents a size of its own."""
    blocks: list[str] = []
    for name, style in styles.items():
        rules = [
            f"  font-family: var(--font-{style['family']});",
            f"  font-size: var(--type-{name}-size);",
            f"  line-height: var(--type-{name}-line);",
            f"  font-weight: var(--type-{name}-weight);",
            f"  letter-spacing: var(--type-{name}-tracking);",
        ]
        if style.get("stretch"):
            rules.append(f"  font-stretch: {stretch};")
        blocks.append(f".type-{name} {{\n" + "\n".join(rules) + "\n}")
    return blocks


def build_css(tokens: dict[str, Any], theme: str = THEME) -> str:
    root: list[str] = []
    root += colour_vars(tokens["color"], theme)
    root += [f"  --font-{name}: {value};" for name, value in tokens["font"].items()]
    root += type_vars(tokens["type"])
    root += scale_vars("space", tokens["space"])
    root += scale_vars("radius", tokens["radius"])
    root += scale_vars("shadow", tokens["shadow"])
    root += scale_vars("size", tokens["size"])
    root += scale_vars("duration", tokens["duration"])
    root += scale_vars("ease", tokens["ease"])
    root += scale_vars("z", tokens["z"])

    parts = [HEADER, "\n:root {", *root, "}", ""]
    parts += type_classes(tokens["type"], tokens["font"]["display-stretch"])
    parts += [
        "",
        "/* Motion is a convenience, never information. */",
        "@media (prefers-reduced-motion: reduce) {",
        "  *,",
        "  *::before,",
        "  *::after {",
        "    animation-duration: 0.01ms !important;",
        "    transition-duration: 0.01ms !important;",
        "  }",
        "}",
    ]
    return "\n".join(parts) + "\n"


def main() -> None:
    tokens = json.loads(TOKENS_FILE.read_text(encoding="utf-8"))
    CSS_FILE.parent.mkdir(parents=True, exist_ok=True)
    CSS_FILE.write_text(build_css(tokens), encoding="utf-8")
    print(f"wrote {CSS_FILE}")


if __name__ == "__main__":
    main()
