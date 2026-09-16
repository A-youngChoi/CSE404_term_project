"""Pre-commit security check for staged changes.

1. Runs `gitleaks protect --staged --redact` when gitleaks is installed.
2. Always runs a local pattern scan of the staged diff:
   * forbidden file types/locations (env files, databases, logs, keys, model weights, data/exports),
   * secret-like strings (API keys, tokens, private keys, passwords, cookies),
   * absolute home-directory paths,
   * e-mail addresses and oversized files.
Findings print file, line and rule name only - never the matched value.

    python scripts/security_check.py            # scan staged changes (exit 1 on findings)
    python scripts/security_check.py --all      # scan every tracked file
"""

from __future__ import annotations

import argparse
import fnmatch
import re
import shutil
import subprocess
import sys

ALLOW_MARKER = "security-check: allow"
MAX_FILE_BYTES = 1_000_000

FORBIDDEN_PATHS = [
    ".env", ".env.*", "*.db", "*.db-*", "*.sqlite", "*.sqlite3", "*.log", "*.pem", "*.key", "*.p12", "id_rsa*",
    "*.gguf", "*.safetensors", "*.bin", "*.pt", "*.onnx", "*.npy",
    "data/*", "*/data/*", "exports/*", "*/exports/*", "uploads/*", "*/uploads/*", "models/*",
    "node_modules/*", "*/node_modules/*", ".venv/*", "*/.venv/*", "dist/*", "*/dist/*", "coverage/*", "*/coverage/*",
    ".DS_Store", "*/.DS_Store",
]
ALLOWED_PATHS = [".env.example", "*/.env.example"]

_BEGIN = "-----BEGIN"
SECRET_PATTERNS = {
    "private_key": re.compile(_BEGIN + r" (?:RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY"),
    "aws_access_key": re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
    "github_token": re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,})\b"),
    "openai_style_key": re.compile(r"\bsk-(?:proj-|ant-)?[A-Za-z0-9]{20,}\b"),
    "slack_token": re.compile(r"\bxox[abposr]-[A-Za-z0-9-]{10,}\b"),
    "google_api_key": re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"),
    "bearer_token": re.compile(r"(?i)authorization:\s*bearer\s+[A-Za-z0-9._~+/-]{20,}"),
    "password_assignment": re.compile(r"(?i)\b(?:password|passwd|secret|api_?key|access_?token)\b\s*[:=]\s*['\"][^'\"\s]{6,}['\"]"),
    "cookie_header": re.compile(r"(?i)^\s*(?:set-)?cookie:\s*\S+=\S+"),
    "url_credentials": re.compile(r"https?://[^/\s:@]+:[^/\s@]+@"),
}
PATH_PATTERNS = {
    "absolute_home_path": re.compile(r"(?:/Users/|/home/)[A-Za-z0-9._-]+/|[A-Za-z]:\\\\Users\\\\"),
}
EMAIL_PATTERN = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
EMAIL_ALLOWED = ("@example.com", "@example.org", "@users.noreply.github.com", "@anthropic.com")


def git(*args: str) -> str:
    return subprocess.run(["git", *args], capture_output=True, text=True, check=True).stdout


def path_findings(paths: list[str]) -> list[str]:
    out = []
    for p in paths:
        name = p.rsplit("/", 1)[-1]
        if any(fnmatch.fnmatch(p, a) or fnmatch.fnmatch(name, a) for a in ALLOWED_PATHS):
            continue
        if name == ".gitkeep":
            continue
        for pattern in FORBIDDEN_PATHS:
            if fnmatch.fnmatch(p, pattern) or fnmatch.fnmatch(name, pattern):
                out.append(f"{p}: forbidden_path ({pattern})")
                break
    return out


def scan_lines(path: str, lines: list[tuple[int, str]]) -> list[str]:
    out = []
    for lineno, line in lines:
        if ALLOW_MARKER in line:
            continue
        for rule, rx in {**SECRET_PATTERNS, **PATH_PATTERNS}.items():
            if rx.search(line):
                out.append(f"{path}:{lineno}: {rule}")
        for m in EMAIL_PATTERN.finditer(line):
            if not m.group(0).lower().endswith(EMAIL_ALLOWED):
                out.append(f"{path}:{lineno}: email_address")
    return out


def staged_added_lines() -> dict[str, list[tuple[int, str]]]:
    diff = git("diff", "--cached", "--unified=0", "--no-color", "--diff-filter=ACMR")
    files: dict[str, list[tuple[int, str]]] = {}
    current = None
    lineno = 0
    for raw in diff.splitlines():
        if raw.startswith("+++ "):
            current = raw[6:] if raw.startswith("+++ b/") else None
            if current:
                files.setdefault(current, [])
        elif raw.startswith("@@"):
            m = re.search(r"\+(\d+)", raw)
            lineno = int(m.group(1)) if m else 0
        elif raw.startswith("+") and current is not None:
            files[current].append((lineno, raw[1:]))
            lineno += 1
    return files


def run_gitleaks(all_files: bool) -> tuple[bool, str]:
    exe = shutil.which("gitleaks")
    if not exe:
        return True, "gitleaks not installed - local pattern scan only"
    cmd = [exe, "detect", "--no-banner", "--redact"] if all_files else [exe, "protect", "--staged", "--no-banner", "--redact"]
    result = subprocess.run(cmd, capture_output=True, text=True)
    return result.returncode == 0, "gitleaks: " + ("no leaks found" if result.returncode == 0 else "LEAKS DETECTED")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true", help="scan all tracked files instead of the staged diff")
    args = ap.parse_args()
    top = git("rev-parse", "--show-toplevel").strip()
    import os

    os.chdir(top)
    findings: list[str] = []
    if args.all:
        paths = git("ls-files").splitlines()
        contents = {}
        for p in paths:
            try:
                with open(p, encoding="utf-8") as fh:
                    contents[p] = list(enumerate(fh.read().splitlines(), 1))
            except (UnicodeDecodeError, FileNotFoundError, IsADirectoryError):
                contents[p] = []
    else:
        paths = [p for p in git("diff", "--cached", "--name-only", "--diff-filter=ACMR").splitlines() if p]
        contents = staged_added_lines()
    findings += path_findings(paths)
    for p in paths:
        try:
            if os.path.getsize(p) > MAX_FILE_BYTES:
                findings.append(f"{p}: oversized_file")
        except OSError:
            pass
        findings += scan_lines(p, contents.get(p, []))

    ok, gitleaks_msg = run_gitleaks(args.all)
    print(gitleaks_msg)
    print(f"scanned {len(paths)} file(s) ({'all tracked' if args.all else 'staged'})")
    if findings or not ok:
        print("SECURITY CHECK FAILED:")
        for f in findings:
            print("  -", f)
        return 1
    print("security check passed: no secrets, credentials, personal paths, or data files detected")
    return 0


if __name__ == "__main__":
    sys.exit(main())
