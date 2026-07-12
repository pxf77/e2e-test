"""Skill Package Loader - discovers and loads skill packages from src/e2e_agent/legacy/skills/."""
from __future__ import annotations

import os
import json
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

try:
    import yaml
except ModuleNotFoundError:  # pragma: no cover - exercised in minimal environments
    yaml = None


@dataclass
class SkillManifest:
    name: str
    version: str
    description: str
    entry_script: str | None = None
    knowledge_files: list[str] = field(default_factory=list)
    input_schema: str | None = None
    output_schema: str | None = None
    requires_node: bool = False


_DEFAULT_SKILL_PACKAGES_DIR = Path(__file__).resolve().parent


def _parse_scalar(value: str) -> object:
    stripped = value.strip()
    if stripped in {"null", "Null", "NULL"}:
        return None
    if stripped in {"true", "True", "TRUE"}:
        return True
    if stripped in {"false", "False", "FALSE"}:
        return False
    if len(stripped) >= 2 and stripped[0] == stripped[-1] and stripped[0] in {'"', "'"}:
        return stripped[1:-1]
    return stripped


def _simple_yaml_load(text: str) -> dict:
    """Parse the small subset of YAML used by Skill Package manifests."""
    data: dict[str, object] = {}
    current_list_key: str | None = None
    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("- "):
            if current_list_key is None:
                raise ValueError("List item found before list key in MANIFEST.yaml")
            data.setdefault(current_list_key, [])
            data[current_list_key].append(_parse_scalar(stripped[2:]))
            continue

        current_list_key = None
        if ":" not in line:
            raise ValueError(f"Unsupported MANIFEST.yaml line: {raw_line}")
        key, raw_value = line.split(":", 1)
        key = key.strip()
        value = raw_value.strip()
        if value == "":
            data[key] = []
            current_list_key = key
        else:
            data[key] = _parse_scalar(value)
    return data


def _load_yaml(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        if yaml is not None:
            loaded = yaml.safe_load(f)
        else:
            loaded = _simple_yaml_load(f.read())
    if not isinstance(loaded, dict):
        raise ValueError(f"MANIFEST.yaml must parse to an object: {path}")
    return loaded


class SkillPackageLoader:
    """Loads and introspects Skill Packages from the in-package skills directory.

    Each Skill Package must contain a MANIFEST.yaml at its root.
    """

    def __init__(self, base_dir: str | Path | None = None) -> None:
        self.base_dir = Path(base_dir) if base_dir else _DEFAULT_SKILL_PACKAGES_DIR

    def list_skills(self) -> list[str]:
        """Returns names of all discovered Skill Packages."""
        if not self.base_dir.exists():
            return []
        return sorted(
            d.name
            for d in self.base_dir.iterdir()
            if d.is_dir() and (d / "MANIFEST.yaml").exists()
        )

    def load_skill(self, skill_name: str) -> SkillManifest:
        """Loads and parses the MANIFEST.yaml for the given skill.

        Raises:
            FileNotFoundError: If the skill or its MANIFEST.yaml doesn't exist.
            ValueError: If the MANIFEST.yaml is missing required fields.
        """
        manifest_path = self.base_dir / skill_name / "MANIFEST.yaml"
        if not manifest_path.exists():
            raise FileNotFoundError(
                f"Skill '{skill_name}' not found. "
                f"Expected MANIFEST.yaml at: {manifest_path}"
            )

        data = _load_yaml(manifest_path)

        required = {"name", "version", "description"}
        missing = required - set(data.keys())
        if missing:
            raise ValueError(
                f"MANIFEST.yaml for '{skill_name}' is missing required fields: {missing}"
            )

        return SkillManifest(
            name=data["name"],
            version=data["version"],
            description=data["description"],
            entry_script=data.get("entry_script"),
            knowledge_files=data.get("knowledge_files", []),
            input_schema=data.get("input_schema"),
            output_schema=data.get("output_schema"),
            requires_node=data.get("requires_node", False),
        )

    def get_knowledge_path(self, skill_name: str, filename: str) -> Path:
        """Returns the absolute path to a knowledge file within a skill."""
        return self.base_dir / skill_name / "references" / "knowledge" / filename

    def get_skill_dir(self, skill_name: str) -> Path:
        """Returns the root directory of a skill package."""
        skill_dir = self.base_dir / skill_name
        if not skill_dir.exists():
            raise FileNotFoundError(f"Skill directory not found: {skill_dir}")
        return skill_dir

    def run_entry(self, skill_name: str, payload: dict, timeout_seconds: int = 120) -> dict:
        """Run the skill entry script with JSON stdin and parse JSON stdout."""
        manifest = self.load_skill(skill_name)
        if not manifest.entry_script:
            raise ValueError(f"Skill '{skill_name}' does not define entry_script")

        skill_dir = self.get_skill_dir(skill_name)
        script_path = skill_dir / manifest.entry_script
        if not script_path.exists():
            raise FileNotFoundError(f"Skill entry script not found: {script_path}")

        suffix = script_path.suffix.lower()
        if suffix == ".py":
            cmd = [sys.executable, str(script_path)]
        elif suffix in {".cjs", ".mjs", ".js"}:
            cmd = ["node", str(script_path)]
        else:
            raise ValueError(f"Unsupported skill entry script type: {script_path}")

        default_timeout = 300 if skill_name == "mpt-reg-exec" and timeout_seconds == 120 else timeout_seconds
        timeout_s = int(payload.get("skill_timeout_s") or default_timeout)
        try:
            result = subprocess.run(
                cmd,
                cwd=str(Path(__file__).resolve().parents[4]),
                input=json.dumps(payload, ensure_ascii=False),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env={
                    **os.environ,
                    "PYTHONIOENCODING": "utf-8",
                },
                timeout=timeout_s,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(
                f"Skill '{skill_name}' entry script timed out after {timeout_s}s"
            ) from exc
        if result.returncode != 0:
            stderr = (result.stderr or result.stdout).strip()
            raise RuntimeError(
                f"Skill '{skill_name}' entry script failed with code {result.returncode}: {stderr}"
            )
        try:
            parsed = json.loads(result.stdout or "{}")
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"Skill '{skill_name}' entry script returned invalid JSON"
            ) from exc
        if not isinstance(parsed, dict):
            raise ValueError(
                f"Skill '{skill_name}' entry script must return a JSON object"
            )
        return parsed

