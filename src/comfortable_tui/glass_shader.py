"""Explicit trusted HLSL packages. Parsing/compilation happens only at registration."""
from __future__ import annotations

import json
import math
from pathlib import Path
import re


def _read(path, maximum):
    with path.open("rb") as stream:
        value = stream.read(maximum+1)
    if len(value) > maximum:
        raise ValueError("Glass shader package file exceeds size limit")
    return value.decode("utf-8")


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate shader manifest key")
        result[key] = value
    return result


def register_glass_shader(manifest):
    """Register a local, application-trusted v1 HLSL package; returns its type name.

    This executes GPU code with the application's privileges, not a sandbox.
    No scanning, network fetching, includes, or per-frame Python interpretation.
    """
    path = Path(manifest).resolve(strict=True)
    spec = json.loads(_read(path, 32*1024), object_pairs_hook=_object)
    if not isinstance(spec, dict) or set(spec)-{"version", "name", "source", "entry", "parameters", "sampling_radius"}:
        raise ValueError("invalid Glass shader manifest fields")
    if type(spec.get("version")) is not int or spec["version"] != 1:
        raise ValueError("unsupported Glass shader package version")
    name, source, entry = spec.get("name"), spec.get("source"), spec.get("entry", "main")
    if not isinstance(name, str) or not 1 <= len(name.encode("utf-8")) <= 128:
        raise ValueError("shader needs a type name of at most 128 UTF-8 bytes")
    if not isinstance(source, str) or not source or len(source) > 256:
        raise ValueError("shader source must be a local package path")
    shader = (path.parent/source).resolve(strict=True)
    if not shader.is_relative_to(path.parent):
        raise ValueError("shader source escapes package directory")
    if not isinstance(entry, str) or re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]{0,63}", entry) is None:
        raise ValueError("invalid shader entry point")
    radius = spec.get("sampling_radius", 0.)
    if type(radius) not in (int, float) or not math.isfinite(radius) or not 0 <= radius <= 128:
        raise ValueError("sampling_radius must be in 0..128")
    parameters = spec.get("parameters", [])
    if not isinstance(parameters, list) or len(parameters) > 4:
        raise ValueError("HLSL v1 supports at most four float parameters")
    schema, names = [], set()
    for parameter in parameters:
        if not isinstance(parameter, dict) or set(parameter) != {"name", "minimum", "maximum", "default"}:
            raise ValueError("invalid shader parameter schema")
        key = parameter["name"]
        if not isinstance(key, str) or not key or len(key.encode("utf-8")) > 64 or key in names:
            raise ValueError("invalid or duplicate shader parameter name")
        names.add(key)
        low, high, default = (parameter[k] for k in ("minimum", "maximum", "default"))
        if any(type(v) not in (int, float) or not math.isfinite(v) for v in (low, high, default)) or not low <= default <= high:
            raise ValueError("invalid shader parameter range")
        schema.append(dict(name=key, minimum=float(low), maximum=float(high), default=float(default)))
    code = _read(shader, 128*1024)
    from . import _native_glass
    if getattr(_native_glass, "glass_protocol_version", None) != 7:
        raise RuntimeError("Rebuild Glass runtime for control protocol 7")
    _native_glass.register_shader(name, code, entry, schema, float(radius))
    return name
