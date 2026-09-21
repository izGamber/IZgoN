#!/usr/bin/env python3
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INDEX = ROOT / "index.html"


def fail(msg: str) -> None:
    print(f"ERROR: {msg}")
    sys.exit(1)


def extract_lang_block(html: str, lang: str) -> str:
    marker = re.search(rf"\b{lang}:\s*\{{", html)
    if not marker:
        fail(f"Missing translation block for language '{lang}'")
    i = marker.end()
    depth = 1
    start = i
    in_string = False
    escaped = False
    while i < len(html) and depth > 0:
        ch = html[i]
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == "\"":
                in_string = False
        else:
            if ch == "\"":
                in_string = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
        i += 1
    if depth != 0:
        fail(f"Unbalanced braces in translation block for language '{lang}'")
    return html[start : i - 1]


def extract_keys(block: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for m in re.finditer(r"\b([a-zA-Z0-9_]+)\s*:\s*\"((?:\\.|[^\"\\])*)\"", block):
        out[m.group(1)] = m.group(2)
    return out


def main() -> None:
    if not INDEX.exists():
        fail(f"Missing {INDEX}")

    html = INDEX.read_text(encoding="utf-8")

    for section_id in ("price", "calc", "video", "faq"):
        if f'<section id="{section_id}">' not in html:
            fail(f"Missing required section id: #{section_id}")

    required_price_keys = [
        "pr_h", "pr_p1", "pr_p2",
        "pr_c1_price", "pr_c1_title", "pr_c1_desc", "pr_c1_l1", "pr_c1_l2", "pr_c1_l3", "pr_c1_btn",
        "pr_c2_price", "pr_c2_title", "pr_c2_desc", "pr_c2_l1", "pr_c2_l2", "pr_c2_l3", "pr_c2_btn",
        "pr_c3_price", "pr_c3_title", "pr_c3_desc", "pr_c3_l1", "pr_c3_l2", "pr_c3_l3", "pr_c3_btn",
        "pr_c4_price", "pr_c4_title", "pr_c4_desc", "pr_c4_l1", "pr_c4_l2", "pr_c4_l3", "pr_c4_btn",
        "pr_scope", "pr_buy",
        "ca_l8", "ca_pkg1", "ca_pkg2", "ca_pkg3", "ca_pkg4", "ca_pkg_note",
    ]

    data_i_keys = set(re.findall(r'data-i="([a-zA-Z0-9_]+)"', html))
    for key in required_price_keys:
        if key not in data_i_keys:
            fail(f"Missing data-i usage in markup for key: {key}")

    bs = extract_keys(extract_lang_block(html, "bs"))
    en = extract_keys(extract_lang_block(html, "en"))

    for lang_name, translations in (("bs", bs), ("en", en)):
        for key in required_price_keys:
            if key not in translations:
                fail(f"Missing {lang_name} translation key: {key}")
            if not translations[key].strip():
                fail(f"Empty {lang_name} translation value for key: {key}")

    fallback_ok = "d[k] !== undefined ? d[k] : T.en[k]" in html
    if not fallback_ok:
        fail("Per-key English fallback is missing in apply()")

    for key in required_price_keys:
        if key not in bs and key in en and not fallback_ok:
            fail(f"Key {key} relies on fallback but fallback is not explicit")

    expected_price_fragments = {
        "pr_c1_price": "29",
        "pr_c2_price": "99",
        "pr_c3_price": "299",
        "pr_c4_price": "750",
        "pr_c4_desc": "49",
    }
    for lang_name, translations in (("bs", bs), ("en", en)):
        for key, fragment in expected_price_fragments.items():
            if fragment not in translations.get(key, ""):
                fail(f"{lang_name}.{key} must include pricing fragment '{fragment}'")

    stale_terms = ["HMAC-SHA256", "HMAC SHA256"]
    for term in stale_terms:
        if term in html:
            fail(f"Stale licence wording found in index.html: {term}")

    for lang_name, translations in (("bs", bs), ("en", en)):
        for key in ("c2p", "how_stack"):
            val = translations.get(key, "")
            if "Ed25519" not in val:
                fail(f"{lang_name}.{key} must reference Ed25519/local verification")

    print("OK: site validation checks passed")


if __name__ == "__main__":
    main()
