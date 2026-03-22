#!/usr/bin/env python3
"""Convert GAIA XML results in a directory to JSONL."""

import argparse
import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path


def _get_text(root: ET.Element, tag: str) -> str:
    elem = root.find(tag)
    if elem is None or elem.text is None:
        return ""
    return elem.text


def _normalize_reasoning(text: str, keep_newlines: bool) -> str:
    if keep_newlines:
        return text.strip()
    return " ".join(text.split())


def _parse_file(path: Path, keep_newlines: bool) -> dict:
    try:
        tree = ET.parse(path)
    except ET.ParseError as exc:
        raise ValueError(f"XML parse error: {exc}") from exc

    root = tree.getroot()
    reasoning = _normalize_reasoning(_get_text(root, "reasoning"), keep_newlines)
    answer = _get_text(root, "final_answer").strip()

    return {
        "task_id": path.stem,
        "model_answer": answer,
        "reasoning_trace": reasoning,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Convert result/*.xml to JSON Lines (one record per task_id)."
    )
    parser.add_argument(
        "input_dir",
        nargs="?",
        default="result",
        help="Directory containing *.xml result files (default: result)",
    )
    parser.add_argument(
        "-o",
        "--output",
        default="result.jsonl",
        help='Output JSONL file path, or "-" for stdout (default: result.jsonl)',
    )
    parser.add_argument(
        "--keep-newlines",
        action="store_true",
        help="Preserve newlines in reasoning_trace (default collapses whitespace).",
    )
    args = parser.parse_args()

    input_dir = Path(args.input_dir)
    if not input_dir.is_dir():
        print(f"Input dir not found: {input_dir}", file=sys.stderr)
        return 2

    files = sorted(input_dir.glob("*.xml"))
    if not files:
        print(f"No .xml files found in {input_dir}", file=sys.stderr)
        return 2

    if args.output == "-":
        out_fh = sys.stdout
    else:
        out_path = Path(args.output)
        out_fh = out_path.open("w", encoding="utf-8", newline="\n")

    had_errors = False
    try:
        for path in files:
            try:
                record = _parse_file(path, args.keep_newlines)
            except ValueError as exc:
                had_errors = True
                print(f"Skip {path.name}: {exc}", file=sys.stderr)
                continue
            out_fh.write(json.dumps(record, ensure_ascii=True))
            out_fh.write("\n")
    finally:
        if out_fh is not sys.stdout:
            out_fh.close()

    return 1 if had_errors else 0


if __name__ == "__main__":
    raise SystemExit(main())

