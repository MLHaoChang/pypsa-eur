"""Schema-derived API-contract inputs, enhanced by captured offline samples."""
import inspect
import json
from pathlib import Path

from jsonschema import Draft202012Validator

from harness.catalogue import TOOLS
from services.chat_tools import DISPATCHERS


def minimal_value(schema, key=""):
    if "const" in schema:
        return schema["const"]
    if "enum" in schema:
        return schema["enum"][0]
    if "default" in schema:
        value = schema["default"]
        if not list(Draft202012Validator(schema).iter_errors(value)):
            return value
    for union in ("anyOf", "oneOf"):
        if union in schema:
            choices = [s for s in schema[union] if s.get("type") != "null"]
            return minimal_value((choices or schema[union])[0], key)
    kind = schema.get("type", "object" if "properties" in schema else "string")
    if isinstance(kind, list):
        kind = next((k for k in kind if k != "null"), "null")
    if kind == "object":
        return {k: minimal_value(schema.get("properties", {}).get(k, {}), k)
                for k in schema.get("required", [])}
    if kind == "array":
        return [minimal_value(schema.get("items", {}), key) for _ in range(schema.get("minItems", 0))]
    if kind == "boolean":
        return False
    if kind == "null":
        return None
    if kind in ("number", "integer"):
        return max(1, schema.get("minimum", 1), schema.get("exclusiveMinimum", 0) + 1)
    values = {"component_class": "Bus", "name": "B1", "new_name": "B3",
              "start": "2026-01-01T00:00:00", "end": "2026-01-01T02:00:00",
              "workflow_id": "build-network", "step": "components",
              "csv_content": "snapshot,value\n2026-01-01,1\n"}
    value = values.get(key, "eA==" if "b64" in key else "fixture")
    return value.ljust(schema.get("minLength", 0), "x")[:schema.get("maxLength", len(value))]


def contract_arguments(tool, capture_path="/tmp/tool-handler-coverage.json"):
    schema = tool["input_schema"]
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema)
    candidates = []
    path = Path(capture_path)
    if path.is_file():
        candidates = json.loads(path.read_text()).get("tools", {}).get(tool["name"], {}).get("samples", [])
    for args in candidates + [minimal_value(schema)]:
        if list(validator.iter_errors(args)):
            continue
        try:
            inspect.signature(DISPATCHERS[tool["name"]]).bind(**args)
        except TypeError:
            continue
        return args
    raise ValueError(f"No schema- and signature-compatible arguments for {tool['name']}")


def canonical_arguments(handler, args):
    bound = inspect.signature(handler).bind(**args)
    bound.apply_defaults()
    return bound.arguments


TOOL_BY_NAME = {tool["name"]: tool for tool in TOOLS}
