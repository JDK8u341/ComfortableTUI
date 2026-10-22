import json
from types import SimpleNamespace

import pytest

from comfortable_tui import register_glass_shader


def package(tmp_path, **overrides):
    (tmp_path/"effect.hlsl").write_text("float4 main():SV_Target{return 1;}", encoding="utf-8")
    spec = dict(version=1, name="test.package", source="effect.hlsl", parameters=[])
    spec.update(overrides)
    path = tmp_path/"effect.json"
    path.write_text(json.dumps(spec), encoding="utf-8")
    return path


def test_manifest_is_a_single_cold_registration(monkeypatch, tmp_path):
    calls = []
    module = SimpleNamespace(glass_protocol_version=7, register_shader=lambda *args: calls.append(args))
    monkeypatch.setitem(__import__("sys").modules, "comfortable_tui._native_glass", module)
    monkeypatch.setattr("comfortable_tui._native_glass", module, raising=False)
    path = package(tmp_path, parameters=[dict(name="amount", minimum=0, maximum=1, default=.5)])
    assert register_glass_shader(path) == "test.package"
    assert len(calls) == 1 and calls[0][0] == "test.package" and calls[0][3][0]["default"] == .5


@pytest.mark.parametrize("changes", [dict(version=True), dict(entry="main;bad"), dict(parameters=[{}]),
                                     dict(sampling_radius=float("nan")), dict(unknown=True)])
def test_rejects_invalid_manifest_before_native_code(tmp_path, changes):
    with pytest.raises(ValueError):
        register_glass_shader(package(tmp_path, **changes))


def test_rejects_package_escape_duplicate_keys_and_oversize(tmp_path):
    outside = tmp_path/"outside.hlsl"
    outside.write_text("shader", encoding="utf-8")
    inner = tmp_path/"package"
    inner.mkdir()
    with pytest.raises(ValueError, match="escapes"):
        register_glass_shader(package(inner, source="../outside.hlsl"))
    path = inner/"effect.json"
    path.write_text('{"version":1,"version":1}', encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate"):
        register_glass_shader(path)
    path = package(inner)
    (inner/"effect.hlsl").write_bytes(b"x"*(128*1024+1))
    with pytest.raises(ValueError, match="size limit"):
        register_glass_shader(path)
