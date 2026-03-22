from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
import re
from typing import Any
import xml.etree.ElementTree as ET


@dataclass(slots=True)
class TaskMetadata:
    final_answer: str
    level: int
    question: str
    file_name: str


DEFAULT_METADATA = TaskMetadata(final_answer="", level=0, question="", file_name="")


def load_metadata(metadata_path: Path) -> dict[str, TaskMetadata]:
    """Load task metadata from a JSONL metadata file."""
    mapping: dict[str, TaskMetadata] = {}
    if not metadata_path.is_file():
        raise FileNotFoundError(f"Metadata file not found: {metadata_path}")

    with metadata_path.open("r", encoding="utf-8") as handle:
        for lineno, raw_line in enumerate(handle, start=1):
            line = raw_line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"Failed to parse JSON on line {lineno} of {metadata_path}"
                ) from exc
            task_id = record.get("task_id")
            final_answer = record.get("Final answer", "")
            if not task_id:
                continue
            question = str(record.get("Question", "")).strip()
            file_name = str(record.get("file_name", "")).strip()
            level_raw: Any = record.get("Level", 0)
            try:
                level = int(level_raw)
            except (TypeError, ValueError):
                level = 0
            mapping[task_id] = TaskMetadata(
                final_answer=str(final_answer).strip(),
                level=level,
                question=question,
                file_name=file_name,
            )
    return mapping


def extract_agent_answer(result_path: Path) -> str:
    """Return the stripped final answer text from an agent result XML file."""
    text = result_path.read_text(encoding="utf-8", errors="replace")

    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        root = None

    if root is not None:
        final_answer = root.findtext("final_answer", default="")
        if final_answer:
            return final_answer.strip()

    # Fallback: regex extraction to handle truncated or malformed XML.
    cdata_match = re.search(
        r"<final_answer>\s*<!\[CDATA\[(.*?)\]\]>\s*</final_answer>",
        text,
        flags=re.DOTALL,
    )
    if cdata_match:
        return cdata_match.group(1).strip()

    plain_match = re.search(
        r"<final_answer>(.*?)</final_answer>", text, flags=re.DOTALL
    )
    if plain_match:
        return plain_match.group(1).strip()

    return ""


WHITESPACE_RUNS = re.compile(r"[\t\r\n]+")


def sanitize_cell(value: str) -> str:
    """Collapse newlines/tabs so every cell renders on a single line."""
    if not value:
        return ""
    return WHITESPACE_RUNS.sub(" ", value.strip())


def format_rows(rows: list[tuple[str, str, str, str, str, str, str]]) -> str:
    """Return a tab-delimited table string for the statistics output."""
    headers = [
        "task_id",
        "标准答案",
        "agent结果",
        "是否相同",
        "Level",
        "Question",
        "file_name",
    ]
    lines = ["\t".join(headers)]
    for row in rows:
        cleaned_row = [sanitize_cell(cell) for cell in row]
        lines.append("\t".join(cleaned_row))
    return "\n".join(lines)



def collect_statistics(
    metadata_path: Path, result_dir: Path
) -> list[tuple[str, str, str, str, str, str, str]]:
    """Build the statistics rows."""
    metadata = load_metadata(metadata_path)
    if not result_dir.is_dir():
        raise FileNotFoundError(f"Result directory not found: {result_dir}")

    row_entries: list[
        tuple[tuple[bool | int | str, ...], tuple[str, str, str, str, str, str, str]]
    ] = []

    for xml_path in sorted(result_dir.glob("*.xml")):
        task_id = xml_path.stem
        agent_answer = extract_agent_answer(xml_path)
        task_meta = metadata.get(task_id, DEFAULT_METADATA)
        standard_answer = task_meta.final_answer
        is_same = "1" if agent_answer.strip() == standard_answer.strip() else "0"
        level_value = task_meta.level
        question = task_meta.question
        file_name = task_meta.file_name
        row_entries.append(
            (
                (file_name != "", level_value, xml_path.name),
                (
                    f"{xml_path.name}",
                    standard_answer,
                    agent_answer,
                    is_same,
                    str(level_value),
                    question,
                    file_name,
                ),
            )
        )

    return [entry[1] for entry in sorted(row_entries, key=lambda item: item[0])]


def main() -> None:
    script_dir = Path(__file__).resolve().parent
    default_metadata = script_dir / "dataset" / "metadata.jsonl"
    default_result_dir = script_dir / "result"
    default_output = script_dir / "statistic.txt"

    parser = argparse.ArgumentParser(
        description="Generate statistics comparing GAIA gold answers and agent outputs."
    )
    parser.add_argument(
        "--metadata",
        type=Path,
        default=default_metadata,
        help=f"Path to metadata JSONL file (default: {default_metadata})",
    )
    parser.add_argument(
        "--results",
        type=Path,
        default=default_result_dir,
        help=f"Directory containing agent result XML files (default: {default_result_dir})",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=default_output,
        help=f"Output file path for statistics (default: {default_output})",
    )
    args = parser.parse_args()

    rows = collect_statistics(args.metadata, args.results)
    table_content = format_rows(rows)

    args.output.write_text(table_content + ("\n" if table_content else ""), encoding="utf-8")


if __name__ == "__main__":
    main()

