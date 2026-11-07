"""Release checks reject private state and incomplete executable packages."""

import importlib.util
import json
import stat
import warnings
import zipfile
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location("check_release", Path(__file__).parents[1] / "tools/check_release.py")
checks = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(checks)


@pytest.fixture
def candidate(tmp_path, monkeypatch):
    files = {"pyproject.toml": b'[project]\nversion="1.0"\n', "requirements.txt": b"",
             "__init__.py": b"", "minimax_h3_rewriter/api_writer.py": b"VALUE = 1\n",
             "web/js/api_writer_widgets.js": b"export const value = 1;\n",
             "minimax_h3_rewriter/api_profiles.example.json": b'{"version":1,"profiles":[]}',
             **{name: b"\x89PNG\r\n" + name.encode() for name in checks.SCREENSHOTS}}
    for name, value in files.items():
        path = tmp_path / "source" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(value)
    monkeypatch.setattr(checks, "REQUIRED", set(files))

    def inspect(*, kind="registry", extra=None, omit=(), replace=None, special=None):
        prefix = "pack/" if kind == "release" else ""
        archive = tmp_path / "candidate.zip"
        with warnings.catch_warnings(), zipfile.ZipFile(archive, "w") as bundle:
            warnings.simplefilter("ignore", UserWarning)
            for name, value in {**files, **(replace or {}), **(extra or {})}.items():
                if name not in omit:
                    bundle.writestr(prefix + name, value)
            if special is not None:
                bundle.writestr(special, b"target")
        return checks.inspect_archive(archive, kind=kind, source=tmp_path / "source")
    return inspect


@pytest.mark.parametrize("kind", ["registry", "release"])
def test_complete_candidate_has_runtime_files_and_hash(candidate, kind):
    report = candidate(kind=kind)
    assert report["runtime_files"] == 3
    assert len(report["sha256"]) == 64


@pytest.mark.parametrize("name", [
    "api_profiles.json", "api_secrets.enc", "api_secrets.enc.backup", ".env", ".env.local",
    "user/other.json", "minimax_h3_rewriter/_user/library.json", "downloads/model.gguf",
    "API_UNIVERSAL_WRITER_PLAN.md", "trace.har", "debug.log", "secret.pem", "uv.exe",
])
def test_private_state_is_rejected_in_both_formats(candidate, name):
    for kind in ("registry", "release"):
        with pytest.raises(checks.ArchiveError, match="Private"):
            candidate(kind=kind, extra={name: b"DUMMY_PRIVATE_VALUE"})


@pytest.mark.parametrize("name", ["../outside", "a/../../outside", "/absolute", "C:/absolute", "a\\other", "a/./b"])
def test_paths_are_not_extracted_or_accepted(candidate, name):
    member = zipfile.ZipInfo()
    member.filename = member.orig_filename = name
    with pytest.raises(checks.ArchiveError, match="Unsafe"):
        candidate(special=member)


def test_symlinks_and_case_aliases_are_rejected(candidate):
    link = zipfile.ZipInfo("link")
    link.create_system = 3
    link.external_attr = (stat.S_IFLNK | 0o777) << 16
    with pytest.raises(checks.ArchiveError, match="Links"):
        candidate(special=link)
    with pytest.raises(checks.ArchiveError, match="Duplicate"):
        candidate(extra={"__INIT__.py": b""})


def test_missing_or_stale_runtime_fails(candidate):
    with pytest.raises(checks.ArchiveError, match="Missing.*api_writer.py"):
        candidate(omit={"minimax_h3_rewriter/api_writer.py"})
    with pytest.raises(checks.ArchiveError, match="differs.*api_writer.py"):
        candidate(replace={"minimax_h3_rewriter/api_writer.py": b"VALUE = 0\n"})


def test_extra_local_runtime_must_be_packaged(candidate, tmp_path):
    (tmp_path / "source/minimax_h3_rewriter/new_adapter.py").write_text("VALUE = 2\n")
    with pytest.raises(checks.ArchiveError, match="Missing.*new_adapter.py"):
        candidate()


def test_registry_rejects_maintainer_files_but_release_keeps_them(candidate):
    with pytest.raises(checks.ArchiveError, match="Maintainer"):
        candidate(extra={"tools/maintain.py": b""})
    assert candidate(kind="release", extra={"tools/maintain.py": b""})["files"] == len(checks.REQUIRED) + 1


def test_example_profiles_cannot_embed_a_token(candidate):
    example = {"profiles": [{"secret": {"kind": "vault", "name": "test", "value": "DUMMY_PRIVATE_VALUE"}}]}
    with pytest.raises(checks.ArchiveError, match="Credential"):
        candidate(replace={"minimax_h3_rewriter/api_profiles.example.json": json.dumps(example).encode()})


@pytest.mark.parametrize("name", sorted(checks.SCREENSHOTS))
def test_documented_screenshots_cannot_be_missing(candidate, name):
    with pytest.raises(checks.ArchiveError, match="Missing files"):
        candidate(omit=[name])


@pytest.mark.parametrize("name", sorted(checks.SCREENSHOTS))
def test_outdated_screenshots_are_rejected(candidate, name):
    with pytest.raises(checks.ArchiveError, match="differs from checkout"):
        candidate(replace={name: b"old screenshot"})
