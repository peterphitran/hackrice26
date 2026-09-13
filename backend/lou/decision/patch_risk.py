"""Conservative risk flags derived from the exact patch bytes being evaluated."""

from pathlib import PurePosixPath


def patch_risk_flags(patch_content: bytes | None) -> tuple[str, ...]:
    if not patch_content:
        return ("patch_paths_unknown",)
    try:
        diff = patch_content.decode("utf-8")
    except UnicodeDecodeError:
        return ("patch_paths_unknown",)
    paths: list[str] = []
    for line in diff.splitlines():
        if line.startswith("+++ b/") or line.startswith("--- a/"):
            paths.append(line[6:])
    if not paths:
        return ("patch_paths_unknown",)
    flags: set[str] = set()
    for path in paths:
        parts = {part.lower() for part in PurePosixPath(path).parts}
        name = PurePosixPath(path).name.lower()
        stem = PurePosixPath(path).stem.lower()
        if (
            parts
            & {
                "auth",
                "authentication",
                "authorization",
                "security",
                "secrets",
                "payments",
                "billing",
                "permissions",
                "policies",
            }
            or stem
            in {
                "auth",
                "authentication",
                "authorization",
                "security",
                "secret",
                "secrets",
                "payment",
                "payments",
                "billing",
                "permission",
                "permissions",
                "policy",
            }
            or name in {".env", "credentials.json", "secrets.yaml", "secrets.yml"}
            or (".github" in parts and "workflows" in parts)
        ):
            flags.add("security_sensitive_file")
        if "migrations" in parts or name.endswith(".sql"):
            flags.add("schema_or_data_migration_file")
    return tuple(sorted(flags))
