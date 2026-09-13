"""Create portable, read-only fallback reports from a completed Lou run."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from lou.core.settings import get_settings
from lou.persistence import create_session_factory
from lou.reporting import EvidenceReportReader


def record(run_id: str, output_dir: Path) -> tuple[Path, Path, Path]:
    """Render the exact saved evidence in both presentation-friendly formats."""

    settings = get_settings()
    report = EvidenceReportReader(create_session_factory(settings), settings.artifact_root).read(
        run_id
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    json_file = output_dir / "evidence-report.json"
    markdown_file = output_dir / "evidence-report.md"
    json_file.write_text(report.render_json(), encoding="utf-8")
    markdown_file.write_text(report.render_markdown(), encoding="utf-8")
    manifest = output_dir / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "analysis_run_id": run_id,
                "files": {
                    path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                    for path in (json_file, markdown_file)
                },
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return json_file, markdown_file, manifest


def main() -> int:
    parser = argparse.ArgumentParser(description="Record a local Lou evidence fallback.")
    parser.add_argument("--run", required=True, help="Completed persisted analysis run ID.")
    parser.add_argument("--output-dir", type=Path, default=Path(".lou/demo-fallback"))
    arguments = parser.parse_args()
    files = record(arguments.run, arguments.output_dir)
    print("Recorded fallback: " + ", ".join(str(path) for path in files))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
