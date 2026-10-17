"""Shared by the provider connection checks: read a key from .env and build the one fictional sample prompt."""
from pathlib import Path

from build_prompt import ROOT, TEMPLATE_PATH, build_prompt, check, load_instructions, load_json

ENV_PATH = ROOT / ".env"
SAMPLE_PATH = ROOT / "data" / "sample-property.json"  # fictional only; real property data needs a separate decision


def read_key(name, env_path=ENV_PATH):
    """Return the value of name in the .env file, or "" if missing. Never print the result."""
    if not Path(env_path).exists():
        return ""
    for line in Path(env_path).read_text(encoding="utf-8-sig").splitlines():
        if line.startswith(f"{name}="):
            return line.split("=", 1)[1].strip()
    return ""


def sample_prompt():
    """The exact prompt both providers count: current template, fictional sample and instructions."""
    template = load_json(TEMPLATE_PATH)
    prop = load_json(SAMPLE_PATH)
    if check(template, prop) != ([], []):
        raise ValueError("הנכס הבדיוני אינו תקין לפי התבנית")
    return build_prompt(template, prop, load_instructions())
