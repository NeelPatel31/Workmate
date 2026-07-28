import yaml
from pathlib import Path
from typing import Optional

from ..container_handlers.constants import DEFAULT_SKILLS_DIR, VIRTUAL_WORKSPACE
from ..utils import logger


SKILL_MANIFEST = "SKILL.md"


def get_skill_path(skill_name: str) -> str:
    """Return the agent-facing virtual path for a skill."""
    return f"{VIRTUAL_WORKSPACE}/skills/{skill_name}"


def _parse_frontmatter(skill_md_path: Path) -> Optional[dict]:
    try:
        text = skill_md_path.read_text(encoding="utf-8")
    except OSError as exc:
        logger.warning("Could not read %s: %s", skill_md_path, exc)
        return None

    text = text.strip()
    if not text.startswith("---"):
        logger.warning("No frontmatter found in %s", skill_md_path)
        return None

    end_idx = text.find("---", 3)
    if end_idx == -1:
        logger.warning("Unclosed frontmatter in %s", skill_md_path)
        return None

    yaml_block = text[3:end_idx]
    try:
        data = yaml.safe_load(yaml_block)
    except yaml.YAMLError as exc:
        logger.warning("Invalid YAML in %s: %s", skill_md_path, exc)
        return None

    if not isinstance(data, dict):
        logger.warning("Frontmatter is not a mapping in %s", skill_md_path)
        return None

    return data


def _escape_xml(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


def _skill_to_xml(frontmatter: dict, container_path: str) -> Optional[str]:
    name = frontmatter.get("name")
    description = frontmatter.get("description")

    if not name or not description:
        logger.warning(
            "Skill missing required field(s) – name: %s, description: %s",
            name,
            bool(description),
        )
        return None

    lines = [
        "<skill>",
        f"  <name>{_escape_xml(str(name))}</name>",
        f"  <description>{_escape_xml(str(description))}</description>",
        f"  <path>{_escape_xml(container_path)}</path>",
    ]

    license_val = frontmatter.get("license")
    if license_val:
        lines.append(f"  <license>{_escape_xml(str(license_val))}</license>")

    compatibility = frontmatter.get("compatibility")
    if compatibility:
        lines.append(
            f"  <compatibility>{_escape_xml(str(compatibility))}</compatibility>"
        )

    allowed_tools = frontmatter.get("allowed-tools")
    if allowed_tools:
        lines.append(
            f"  <allowed-tools>{_escape_xml(str(allowed_tools))}</allowed-tools>"
        )

    metadata = frontmatter.get("metadata")
    if isinstance(metadata, dict) and metadata:
        lines.append("  <metadata>")
        for key, value in metadata.items():
            safe_key = _escape_xml(str(key))
            safe_val = _escape_xml(str(value))
            lines.append(f"    <{safe_key}>{safe_val}</{safe_key}>")
        lines.append("  </metadata>")

    lines.append("</skill>")
    return "\n".join(lines)


def resolve_skills_dir() -> Path | None:
    local_dir = DEFAULT_SKILLS_DIR

    if local_dir.exists() and local_dir.is_dir():
        logger.info("Using local skills dir: %s", local_dir)
        return local_dir

    logger.warning("No valid skills directory found at %s", local_dir)
    return None


def get_skills_xml() -> str:
    skills_dir = resolve_skills_dir()
    logger.info(f"Skills dir: {skills_dir}")

    if not skills_dir or not skills_dir.is_dir():
        logger.warning("Skills directory does not exist: %s", skills_dir)
        return ""

    xml_blocks: list[str] = []

    for child in sorted(skills_dir.iterdir()):
        if not child.is_dir():
            continue

        manifest = child / SKILL_MANIFEST
        if not manifest.is_file():
            logger.debug("No %s in %s – skipping", SKILL_MANIFEST, child.name)
            continue

        frontmatter = _parse_frontmatter(manifest)
        if frontmatter is None:
            continue

        container_path = get_skill_path(child.name)
        xml = _skill_to_xml(frontmatter, container_path)
        if xml is not None:
            xml_blocks.append(xml)

    logger.info(f"No. of skills: {len(xml_blocks)}")
    return "\n".join(xml_blocks)
