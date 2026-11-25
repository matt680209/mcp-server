from __future__ import annotations

import asyncio
import ipaddress
import shutil
import socket
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import unquote, urlsplit


class SourceError(ValueError):
    pass


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, new_url):
        return None


async def store_upload(upload, destination: Path) -> str:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("wb") as target:
        while chunk := await upload.read(1024 * 1024):
            target.write(chunk)
    return destination.name


async def acquire_source(
    source_kind: str,
    source_value: str,
    destination_dir: Path,
    allowed_hosts: list[str],
    shared_roots: list[str],
) -> tuple[Path, str]:
    destination_dir.mkdir(parents=True, exist_ok=True)
    if source_kind == "shared":
        return await asyncio.to_thread(_copy_shared, source_value, destination_dir, shared_roots)
    if source_kind == "url":
        return await asyncio.to_thread(_download_url, source_value, destination_dir, allowed_hosts)
    raise SourceError("Choose a file upload, URL, or shared-folder file.")


def _copy_shared(source_value: str, destination_dir: Path, shared_roots: list[str]) -> tuple[Path, str]:
    source = Path(source_value).resolve(strict=True)
    if not source.is_file():
        raise SourceError("The shared-folder source must be a file.")
    resolved_roots = [Path(root).resolve() for root in shared_roots]
    if not any(_is_relative_to(source, root) for root in resolved_roots):
        raise SourceError("This shared-folder location is not allowed by the administrator.")
    destination = destination_dir / _safe_name(source.name)
    shutil.copyfile(source, destination)
    return destination, source.name


def _download_url(url: str, destination_dir: Path, allowed_hosts: list[str]) -> tuple[Path, str]:
    parsed = urlsplit(url.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise SourceError("Use an HTTP(S) URL without embedded credentials.")
    host = parsed.hostname.rstrip(".").lower()
    if host not in {item.rstrip(".").lower() for item in allowed_hosts}:
        raise SourceError("This URL host is not allowed by the administrator.")
    try:
        addresses = {entry[4][0] for entry in socket.getaddrinfo(host, parsed.port or (443 if parsed.scheme == "https" else 80))}
    except OSError as exc:
        raise SourceError("The URL host could not be resolved.") from exc
    if not addresses or any(not _is_public_address(address) for address in addresses):
        raise SourceError("URLs resolving to private or local network addresses are not allowed.")

    filename = _safe_name(Path(unquote(parsed.path)).name or "download")
    destination = destination_dir / filename
    request = urllib.request.Request(url, headers={"User-Agent": "MCP-Project/1.0"})
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        with opener.open(request, timeout=30) as response, destination.open("wb") as target:
            while chunk := response.read(1024 * 1024):
                target.write(chunk)
    except (OSError, urllib.error.URLError, urllib.error.HTTPError) as exc:
        destination.unlink(missing_ok=True)
        raise SourceError(f"Could not retrieve the specified URL: {exc}") from exc
    return destination, filename


def _is_public_address(value: str) -> bool:
    address = ipaddress.ip_address(value.split("%", 1)[0])
    return address.is_global


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _safe_name(name: str) -> str:
    cleaned = Path(name.replace("\\", "/")).name
    cleaned = "".join(character for character in cleaned if character.isprintable() and character not in '<>:"/\\|?*')
    return cleaned[:180] or "input-file"
