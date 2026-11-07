"""Local administration; never accept key values as command-line arguments.

Use the same OS account as ComfyUI. --user-directory is explicit so this tool
cannot accidentally configure a different installation or the package itself.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import pathlib
import sys
import warnings

if not __package__:
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from minimax_h3_rewriter import api_config as config
from minimax_h3_rewriter.api_contract import ContractError
from minimax_h3_rewriter.api_client import ApiError
from minimax_h3_rewriter.api_profiles import ProfileSet, example_profiles
from minimax_h3_rewriter.api_secrets import SecretError, SecretResolver, encrypted_store, storage_kind


def _hidden_key():
    if not sys.stdin.isatty():
        raise SecretError("terminal")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", getpass.GetPassWarning)
            first = getpass.getpass("API key (hidden): ")
            second = getpass.getpass("Repeat API key (hidden): ")
    except getpass.GetPassWarning:
        raise SecretError("terminal") from None
    if first != second:
        raise ConfigMismatch()
    return first


class ConfigMismatch(RuntimeError):
    def __init__(self):
        super().__init__("The two API key entries differ; nothing was saved.")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--user-directory", required=True, help="Existing ComfyUI user directory, including a custom --user-directory.")
    commands = parser.add_subparsers(dest="command", required=True)
    initializing = commands.add_parser("init", help="Write non-secret templates; never overwrite an existing file.")
    initializing.add_argument("--include-local-runtimes", action="store_true", help="Also include conservative Ollama/vLLM templates.")
    commands.add_parser("list", help="Show IDs/models and credential status, never key values.")
    probing = commands.add_parser("probe", help="Read this profile's model list; no generation or profile edits.")
    probing.add_argument("profile_id")
    probing.add_argument("--health", action="store_true", help="Check /health before /models, for a server such as vLLM.")
    importing = commands.add_parser("import", help="Validate and save an administrator's non-secret profiles JSON.")
    importing.add_argument("file")
    for command in ("key-set", "key-delete", "key-migrate"):
        sub = commands.add_parser(command)
        sub.add_argument("profile_id")
    args = parser.parse_args(argv)
    failure = None
    try:
        root = config.user_root(args.user_directory)
        if args.command == "init":
            if config.read_bytes(root, config.PROFILES) is not None:
                raise config.ConfigError("api_profiles.json already exists; it was not changed.")
            config.save_profiles(example_profiles(include_local_runtimes=args.include_local_runtimes), args.user_directory)
            print("Created non-secret templates. Edit api_profiles.json for your server/model before using a key.")
        elif args.command == "import":
            try:
                with pathlib.Path(args.file).open("rb") as handle:
                    body = handle.read(config.MAX_FILE + 1)
            except OSError:
                raise config.ConfigError("Cannot read the supplied profiles document.") from None
            if len(body) > config.MAX_FILE:
                raise config.ConfigError("The profiles document is too large.")
            profiles = ProfileSet.from_dict(config.parse_json(body))
            config.save_profiles(profiles, args.user_directory)
            print("Saved validated non-secret profiles. Use key-migrate after changing allowed models on the same recipient; changed recipients require key-set.")
        else:
            profiles = config.load_profiles(args.user_directory)
            if args.command == "list":
                for profile in profiles.profiles:
                    status = "anonymous"
                    if profile.secret.kind != "none":
                        try:
                            SecretResolver(args.user_directory)(profile)
                            status = "available (" + profile.secret.kind + ")"
                        except (SecretError, config.ConfigError) as error:
                            status = "unavailable: " + str(error)
                    print(profile.id + " | " + profile.model + " | " + status)
            elif args.command == "probe":
                from minimax_h3_rewriter.api_models import probe_profile

                result = asyncio.run(probe_profile(profiles.resolve(args.profile_id), health=args.health,
                                                   authorize=SecretResolver(args.user_directory)))
                print(json.dumps(result, ensure_ascii=True, indent=2))
            else:
                profile = profiles.resolve(args.profile_id)
                if profile.secret.kind != "vault":
                    raise SecretError("source")
                store = encrypted_store(args.user_directory)
                if args.command == "key-set":
                    store.put(profile, _hidden_key())
                    backend = "current-user Windows DPAPI" if storage_kind() == "windows_dpapi" else "the configured server AES-GCM vault key"
                    print("Saved with " + backend + ". The key is bound to this profile and recipient.")
                elif args.command == "key-migrate":
                    store.migrate(profile)
                    print("Updated the encrypted key's allowed models for the same profile and recipient.")
                else:
                    removed = store.delete(profile)
                    print("Deleted the saved key." if removed else "No saved key for this profile.")
    except (config.ConfigError, ContractError, SecretError, ConfigMismatch, ApiError) as error:
        failure = str(error)
    except (ValueError, TypeError, RecursionError, OverflowError):
        failure = "Invalid profiles document; nothing was saved."
    except (EOFError, KeyboardInterrupt):
        failure = "Probe cancelled." if args.command == "probe" else "Key entry cancelled; nothing was saved."
    if failure:
        print(failure, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
