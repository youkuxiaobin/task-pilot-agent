"""GAIA dataset evaluator.

Reads GAIA metadata, queries the auto agent API for each question, stores the
XML responses, and records whether the predicted answers match the ground truth.
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from pathlib import Path
from queue import Queue
from threading import Lock, Thread
from typing import Iterable, List, Optional, Set, Tuple

import requests
import xml.etree.ElementTree as ET


SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_METADATA_PATH = SCRIPT_DIR / "dataset" / "metadata.jsonl"
DEFAULT_RESULT_ROOT = SCRIPT_DIR / "result"
RESULT_SUMMARY_NAME = "result.txt"
API_URL = "http://127.0.0.1:9010/agent/autoagent"
FILE_UPLOAD_URL = "http://127.0.0.1:9010/file/v1/upload_file_form"
UPLOAD_ROOT = SCRIPT_DIR / "upload"
TIMEOUT_SECONDS = 3600

TaskPayload = Tuple[int, str, str, Optional[str], str, Optional[int]]


class GaiaEvaluator:
    """Encapsulates the end-to-end GAIA evaluation pipeline."""

    def __init__(
        self,
        metadata_path: Path,
        output_dir: Path,
        *,
        skip_existing: bool = False,
        allowed_file_types: Optional[Set[str]] = None,
        allowed_task_ids: Optional[Set[str]] = None,
        allowed_levels: Optional[Set[int]] = None,
        api_url: str = API_URL,
        upload_url: str = FILE_UPLOAD_URL,
        only_with_file: bool = False,
    ) -> None:
        self.metadata_path = metadata_path
        self.output_dir = output_dir
        self.skip_existing = skip_existing
        self.allowed_file_types: Set[str] = allowed_file_types or {"image", "audio", "text"}
        self.allowed_task_ids: Optional[Set[str]] = set(allowed_task_ids) if allowed_task_ids else None
        self.allowed_levels: Optional[Set[int]] = set(allowed_levels) if allowed_levels else None
        self.api_url = api_url
        self.upload_url = upload_url
        self.only_with_file = only_with_file
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.result_txt_path = self.output_dir / RESULT_SUMMARY_NAME
        self._file_lock = Lock()

    def run(
        self,
        limit: Optional[int] = None,
        specific_task_id: Optional[str] = None,
        concurrency: int = 1,
    ) -> None:
        """Iterate through GAIA metadata entries and evaluate each task."""
        if concurrency < 1:
            raise ValueError("concurrency must be at least 1")

        results: List[Tuple[int, str]] = []
        results_lock = Lock()
        task_queue: "Queue[TaskPayload | None]" = Queue()

        def record_result(order: int, line: str) -> None:
            with results_lock:
                results.append((order, line))

        def handle_task(payload: TaskPayload) -> None:
            order, task_id, question, expected_answer, file_name, level = payload
            xml_file = self.output_dir / f"{task_id}.xml"

            if self.skip_existing and xml_file.exists():
                xml_payload = xml_file.read_text(encoding="utf-8")
                predicted_answer = self._extract_final_answer(xml_payload)
                match_result = self._compare_answers(expected_answer, predicted_answer)
                record_result(order, f"{task_id}\t{match_result}")
                return

            try:
                upload_files = None
                trace_id = str(uuid.uuid4())

                if file_name:
                    file_path = UPLOAD_ROOT / file_name
                    if not file_path.is_file():
                        print(
                            f"[ERROR] Attachment not found for task {task_id}: {file_path}",
                            file=sys.stderr,
                        )
                        record_result(order, f"{task_id}\t0")
                        return

                    file_type = self._categorize_file(file_path)
                    if file_type not in self.allowed_file_types:
                        print(
                            f"[WARN] Skipping task {task_id}: file type '{file_type}' not allowed "
                            f"(filename: {file_name})",
                            file=sys.stderr,
                        )
                        record_result(order, f"{task_id}\t0")
                        return

                    try:
                        download_url = self._upload_file(file_path, trace_id)
                    except Exception as upload_exc:  # noqa: BLE001 - continue after logging
                        print(
                            f"[ERROR] Upload failed for task {task_id} ({file_name}): {upload_exc}",
                            file=sys.stderr,
                        )
                        record_result(order, f"{task_id}\t0")
                        return

                    upload_files = [{"ossUrl": download_url, "fileName": file_path.name}]

                xml_payload = self._query_auto_agent(question, trace_id=trace_id, upload_files=upload_files)
            except Exception as exc:  # noqa: BLE001 - continue after logging
                print(f"[ERROR] Request failed for task {task_id}: {exc}", file=sys.stderr)
                record_result(order, f"{task_id}\t0")
                return

            if not xml_payload:
                print(f"[WARN] Empty XML response for task {task_id}", file=sys.stderr)
                record_result(order, f"{task_id}\t0")
                return

            with self._file_lock:
                xml_file.write_text(xml_payload, encoding="utf-8")

            predicted_answer = self._extract_final_answer(xml_payload)
            if predicted_answer is None:
                print(f"[WARN] Could not parse final answer for task {task_id}", file=sys.stderr)
                record_result(order, f"{task_id}\t0")
                return

            match_result = self._compare_answers(expected_answer, predicted_answer)
            record_result(order, f"{task_id}\t{match_result}")

        def worker() -> None:
            while True:
                payload = task_queue.get()
                if payload is None:
                    task_queue.task_done()
                    break

                try:
                    handle_task(payload)
                finally:
                    task_queue.task_done()

        threads = [
            Thread(target=worker, daemon=True)
            for _ in range(max(1, concurrency))
        ]
        for thread in threads:
            thread.start()

        scheduled = 0
        next_order = 0
        for entry in self._iter_metadata():
            if limit is not None and scheduled >= limit:
                break

            task_id = entry.get("task_id")
            if specific_task_id is not None and task_id != specific_task_id:
                continue

            question = entry.get("Question")
            expected_answer = entry.get("Final answer")
            level_raw = entry.get("Level")
            try:
                level: Optional[int] = int(level_raw)  # type: ignore[arg-type]
            except (TypeError, ValueError):
                level = None

            if not task_id or not question:
                print(f"[WARN] Skipping entry missing task_id or question: {entry}", file=sys.stderr)
                continue

            if self.allowed_task_ids is not None and task_id not in self.allowed_task_ids:
                continue

            if self.allowed_levels is not None and (level is None or level not in self.allowed_levels):
                continue

            file_name = (entry.get("file_name") or "").strip()
            if self.only_with_file and not file_name:
                print(f"[INFO] Skipping task without attachment due to --only-with-file: {task_id}")
                continue

            level_display = level if level is not None else "unknown"
            print(f"task_id: {task_id} (level: {level_display})")
            task_queue.put((next_order, task_id, question, expected_answer, file_name, level))
            scheduled += 1
            next_order += 1

        for _ in threads:
            task_queue.put(None)

        task_queue.join()
        for thread in threads:
            thread.join()

        ordered_results = [line for _, line in sorted(results, key=lambda item: item[0])]
        self._write_results(ordered_results)

    def _iter_metadata(self) -> Iterable[dict]:
        """Yield parsed JSON objects from the metadata JSONL file."""
        with self.metadata_path.open("r", encoding="utf-8") as fh:
            for line_number, raw_line in enumerate(fh, start=1):
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError as exc:
                    print(
                        f"[WARN] Invalid JSON at line {line_number} in {self.metadata_path}: {exc}",
                        file=sys.stderr,
                    )

    def _query_auto_agent(
        self,
        question: str,
        *,
        trace_id: Optional[str] = None,
        upload_files: Optional[List[dict]] = None,
    ) -> str:
        """Send the question to the auto agent endpoint and collect XML."""
        conversation_id = trace_id or str(uuid.uuid4())
        trace_id = trace_id or conversation_id

        message: dict = {"role": "user", "content": question}
        if upload_files:
            message["uploadFile"] = upload_files

        payload = {
            "messages": [message],
            "outputStyle": "gaia",
            "mode": "plans_executor",
            "conversation_id": conversation_id,
            "trace_id": trace_id,
        }
        with requests.post(self.api_url, json=payload, timeout=TIMEOUT_SECONDS, stream=True) as response:
            response.raise_for_status()
            content_type = response.headers.get("Content-Type", "").lower()

            if "text/event-stream" in content_type:
                return self._consume_sse_stream(response).strip()

            # Fallback to buffered handling for JSON or XML payloads.
            raw_text = (response.text or "").strip()
            if not raw_text:
                raise RuntimeError(
                    f"Empty response body (status {response.status_code}) for conversation {conversation_id}"
                )

            try:
                response_data = response.json()
            except ValueError as exc:
                if raw_text.startswith("<"):
                    return raw_text
                snippet = raw_text[:500]
                if len(raw_text) > 500:
                    snippet += "..."
                raise RuntimeError(
                    f"Non-JSON response (status {response.status_code}): {snippet or '<empty>'}"
                ) from exc

            result_messages = self._collect_result_messages(response_data)
            return "".join(result_messages).strip()

    def _consume_sse_stream(self, response: requests.Response) -> str:
        """Aggregate messageType=result payloads from an SSE response stream."""
        messages: List[str] = []
        data_buffer: List[str] = []

        for raw_line in response.iter_lines(decode_unicode=True):
            if raw_line is None:
                continue

            line = raw_line.rstrip("\r")
            if not line:
                if data_buffer:
                    data_payload = "\n".join(data_buffer).strip()
                    data_buffer.clear()
                    self._process_sse_payload(data_payload, messages)
                continue

            if line.startswith("data:"):
                data_buffer.append(line[5:].lstrip())
            # Ignore other fields such as 'event:' or comments.

        if data_buffer:
            data_payload = "\n".join(data_buffer).strip()
            self._process_sse_payload(data_payload, messages)

        return "".join(messages)

    def _process_sse_payload(self, data: str, messages: List[str]) -> None:
        """Handle a single SSE data block."""
        if not data or data == "[DONE]":
            return

        try:
            payload = json.loads(data)
        except ValueError:
            if data.startswith("<"):
                messages.append(data)
            else:
                print(f"[WARN] Unhandled SSE payload: {data[:80]!r}", file=sys.stderr)
            return

        segments = self._collect_result_messages(payload)
        if not segments and self._is_result_message(payload) and payload.get("finish"):
            snippet = json.dumps(payload, ensure_ascii=False)[:200]
            print(f"[WARN] Empty result payload at stream end: {snippet}", file=sys.stderr)
        messages.extend(segments)

    def _collect_result_messages(self, data: object) -> List[str]:
        """Recursively collect messageType=result payloads from a response."""
        if isinstance(data, dict):
            messages: List[str] = []

            if self._is_result_message(data):
                text = self._extract_message_text(data)
                if text is not None:
                    messages.append(text)

            for key in ("messages", "data", "response"):
                if key in data:
                    messages.extend(self._collect_result_messages(data[key]))
            return messages

        if isinstance(data, list):
            messages: List[str] = []
            for item in data:
                messages.extend(self._collect_result_messages(item))
            return messages

        return []

    @staticmethod
    def _is_result_message(message: object) -> bool:
        """Return True if the message dictionary indicates a result payload."""
        if not isinstance(message, dict):
            return False
        message_type = (
            message.get("messageType")
            or message.get("type")
            or message.get("message_type")
        )
        if isinstance(message_type, str):
            return message_type.lower() == "result"
        return False

    @staticmethod
    def _extract_message_text(message: dict) -> Optional[str]:
        """Extract text content from a response message structure."""

        def _stringify(value: object) -> Optional[str]:
            if isinstance(value, str):
                if value.strip() == "#":
                    return None
                return value
            if isinstance(value, list):
                parts = [part for part in (_stringify(item) for item in value) if part]
                if parts:
                    return "".join(parts)
            if isinstance(value, dict):
                for key in ("content", "text", "message", "body", "data", "result", "value"):
                    nested = _stringify(value.get(key))
                    if nested:
                        return nested
            return None

        for key in ("content", "text", "message", "body", "data", "result"):
            maybe_text = _stringify(message.get(key))
            if maybe_text:
                return maybe_text

        result_map = message.get("resultMap")
        if isinstance(result_map, dict):
            segments = [segment for segment in (_stringify(val) for val in result_map.values()) if segment]
            if segments:
                return "".join(segments)

        return None

    @staticmethod
    def _extract_final_answer(xml_blob: str) -> Optional[str]:
        """Parse an XML blob to retrieve the final_answer text."""
        if not xml_blob:
            return None

        xml_blob = xml_blob.strip()
        if not xml_blob:
            return None

        candidates = [xml_blob, f"<root>{xml_blob}</root>"]
        root: Optional[ET.Element] = None
        for candidate in candidates:
            try:
                root = ET.fromstring(candidate)
                break
            except ET.ParseError:
                root = None

        if root is None:
            return None

        final_answers = [(node.text or "") for node in root.iter("final_answer")]
        if not final_answers:
            return None
        return final_answers[-1].strip()

    @staticmethod
    def _compare_answers(expected: Optional[str], predicted: Optional[str]) -> int:
        """Return 1 if answers match exactly (after trimming), else 0."""
        if expected is None or predicted is None:
            return 0
        return int(expected.strip() == predicted.strip())

    def _upload_file(self, file_path: Path, trace_id: str) -> str:
        """Upload a local attachment and return its download URL."""
        with file_path.open("rb") as handle:
            response = requests.post(
                self.upload_url,
                data={
                    "request_id": trace_id,
                    "description": f"gaia eval upload: {file_path.name}",
                },
                files={"file": (file_path.name, handle)},
                timeout=TIMEOUT_SECONDS,
            )
        response.raise_for_status()

        payload = response.json()
        download_url = payload.get("download_url")
        if not download_url:
            snippet = json.dumps(payload, ensure_ascii=False)[:200]
            raise RuntimeError(f"upload response missing download_url: {snippet}")
        return download_url

    @staticmethod
    def _categorize_file(file_path: Path) -> str:
        """Return a coarse category (image/audio/text) for the given file based on extension."""
        ext = file_path.suffix.lower()
        if ext in {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"}:
            return "image"
        if ext in {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg"}:
            return "audio"
        return "text"

    def _write_results(self, lines: Iterable[str]) -> None:
        """Persist evaluation outcomes to result.txt."""
        with self._file_lock:
            with self.result_txt_path.open("w", encoding="utf-8") as fh:
                for line in lines:
                    fh.write(f"{line}\n")


def parse_file_types(value: str) -> Set[str]:
    """Parse a comma-separated list of attachment categories."""
    allowed = {"image", "audio", "text"}
    if value is None:
        return set(allowed)

    raw = value.strip()
    if not raw or raw.lower() in {"all", "*", "any"}:
        return set(allowed)

    parsed = {item.strip().lower() for item in raw.split(",") if item.strip()}
    invalid = parsed - allowed
    if invalid:
        raise argparse.ArgumentTypeError(f"invalid file types: {', '.join(sorted(invalid))}")
    return parsed


def load_task_ids_file(file_path: Path) -> Set[str]:
    """Read task IDs from a file (one per line)."""
    task_ids: Set[str] = set()
    with file_path.open("r", encoding="utf-8") as fh:
        for raw_line in fh:
            task_id = raw_line.strip()
            if not task_id or task_id.startswith("#"):
                continue
            task_ids.add(task_id)
    return task_ids


def parse_positive_int(value: str) -> int:
    """Ensure user supplied a positive integer (used for concurrency)."""
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("value must be a positive integer") from exc

    if parsed < 1:
        raise argparse.ArgumentTypeError("value must be a positive integer")
    return parsed


def parse_levels(value: str) -> Set[int]:
    """Parse a comma-separated list of integer levels."""
    raw = value.strip()
    if not raw:
        raise argparse.ArgumentTypeError("levels cannot be empty")

    levels: Set[int] = set()
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            level = int(part)
        except ValueError as exc:
            raise argparse.ArgumentTypeError("levels must be integers (comma-separated)") from exc
        if level < 0:
            raise argparse.ArgumentTypeError("levels must be positive integers")
        levels.add(level)

    if not levels:
        raise argparse.ArgumentTypeError("levels cannot be empty")
    return levels


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate GAIA dataset via auto agent API.")
    parser.add_argument(
        "--metadata",
        type=Path,
        default=DEFAULT_METADATA_PATH,
        help="Path to metadata.jsonl file (default: %(default)s)",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_RESULT_ROOT,
        help="Directory for XML responses and result summary (default: %(default)s)",
    )
    parser.add_argument(
        "--api-url",
        type=str,
        default=API_URL,
        help="autoagent API URL (default: %(default)s)",
    )
    parser.add_argument(
        "--upload-url",
        type=str,
        default=FILE_UPLOAD_URL,
        help="File upload endpoint URL (default: %(default)s)",
    )
    parser.add_argument(
        "--file-types",
        type=parse_file_types,
        default={"image", "audio", "text"},
        help="Comma-separated attachment categories to allow (choices: image,audio,text). "
        "Entries with disallowed attachment types will be marked as failed (default: all).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Process at most N tasks (handy for smoke testing).",
    )
    parser.add_argument(
        "--concurrency",
        type=parse_positive_int,
        default=1,
        help="Number of worker threads for GAIA evaluation (default: %(default)s).",
    )
    parser.add_argument(
        "--skip-existing",
        action="store_true",
        default=False,
        help="Reuse existing XML files instead of calling the API again.",
    )
    parser.add_argument(
        "--task-id",
        type=str,
        default=None,
        help="Optional task ID to process a specific task.",
    )
    parser.add_argument(
        "--task-id-file",
        type=Path,
        default=None,
        help="Optional file listing task IDs (one per line) to process.",
    )
    parser.add_argument(
        "--only-with-file",
        action="store_true",
        default=False,
        help="Process only metadata rows that include a file_name (skip others).",
    )
    parser.add_argument(
        "--levels",
        type=parse_levels,
        default=None,
        help="Optional comma-separated list of GAIA levels to process (e.g., 1,2,3). Defaults to all levels.",
    )
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> None:
    args = parse_args(argv)
    allowed_task_ids: Optional[Set[str]] = None
    if args.task_id_file is not None:
        try:
            allowed_task_ids = load_task_ids_file(args.task_id_file)
        except OSError as exc:
            print(f"[ERROR] Unable to read task ID file {args.task_id_file}: {exc}", file=sys.stderr)
            sys.exit(1)

    evaluator = GaiaEvaluator(
        metadata_path=args.metadata,
        output_dir=args.output_dir,
        skip_existing=args.skip_existing,
        allowed_file_types=args.file_types,
        allowed_task_ids=allowed_task_ids,
        allowed_levels=args.levels,
        api_url=args.api_url,
        upload_url=args.upload_url,
        only_with_file=args.only_with_file,
    )
    evaluator.run(limit=args.limit, specific_task_id=args.task_id, concurrency=args.concurrency)


if __name__ == "__main__":
    main()
