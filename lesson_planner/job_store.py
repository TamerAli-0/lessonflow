from __future__ import annotations

import json
import shutil
import uuid
from pathlib import Path
from typing import Any


class JobStore:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def create(self) -> str:
        job_id = uuid.uuid4().hex
        self.path(job_id).mkdir(parents=True)
        return job_id

    def path(self, job_id: str) -> Path:
        safe_id = "".join(character for character in job_id if character.isalnum())
        if not safe_id or safe_id != job_id:
            raise ValueError("Invalid job identifier.")
        return self.root / safe_id

    def save_upload(self, job_id: str, source, filename: str) -> Path:
        suffix = Path(filename).suffix.lower()
        destination = self.path(job_id) / f"module{suffix}"
        source.save(destination)
        return destination

    def save_json(self, job_id: str, name: str, payload: Any) -> Path:
        destination = self.path(job_id) / f"{name}.json"
        destination.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return destination

    def load_json(self, job_id: str, name: str) -> Any:
        return json.loads((self.path(job_id) / f"{name}.json").read_text(encoding="utf-8"))

    def save_template(self, job_id: str, source, filename: str) -> Path:
        destination = self.path(job_id) / "template.docx"
        source.save(destination)
        return destination

    def clean(self, job_id: str) -> None:
        directory = self.path(job_id)
        if directory.exists():
            shutil.rmtree(directory)

