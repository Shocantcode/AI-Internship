"""CLI entry point for the Detik Finance computer-use agent."""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

OUTPUT_DIR = Path(__file__).resolve().parents[1] / "output"


def format_text(result) -> str:
    lines = ["=" * 50, "DETIK FINANCE NEWS", "=" * 50, ""]
    for index, article in enumerate(result.articles, 1):
        lines.extend([
            f"{index}. JUDUL:", article.title, "", f"KATEGORI:\n{article.category}",
            f"\nTANGGAL:\n{article.published_at or '-'}", f"\nAUTHOR:\n{article.author or '-'}",
            "\nSOURCE:\nDetik Finance", f"\nURL:\n{article.url}",
            f"\nISI:\n{article.content}", "\n" + "-" * 50, "",
        ])
    if result.failed_articles:
        lines.extend(["ARTIKEL GAGAL:", *result.failed_articles, ""])
    if result.message:
        lines.extend([f"STATUS: {result.status.upper()}", result.message])
    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    from .agent import ComputerUseAgent

    parser = argparse.ArgumentParser(description="Collect relevant Detik Finance news with a browser agent.")
    parser.add_argument("--query", required=True, help="Topic, for example: ekonomi Indonesia")
    parser.add_argument("--limit", type=int, default=5, help="Maximum number of articles")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    result = ComputerUseAgent().run(args.query, args.limit)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    payload = result.model_dump() if hasattr(result, "model_dump") else result.dict() if hasattr(result, "dict") else vars(result)
    (OUTPUT_DIR / "news.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    (OUTPUT_DIR / "news.txt").write_text(format_text(result), encoding="utf-8")
    print(format_text(result))
    return 0 if result.articles else 1


if __name__ == "__main__":
    raise SystemExit(main())
