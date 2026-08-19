"""Seletores nativos: caminhos existem apenas dentro do processo local."""

from __future__ import annotations

import platform
import subprocess
from pathlib import Path


class SelectionCancelled(Exception):
    """O usuário fechou o seletor sem escolher um arquivo."""


def select_local_file() -> Path:
    """Abre um diálogo nativo sem receber caminho pela fronteira MCP."""

    system = platform.system()
    if system == "Darwin":
        script = (
            'POSIX path of (choose file with prompt "Selecione um documento para '
            'desidentificação local" of type {"public.image", "com.adobe.pdf", '
            '"public.plain-text"})'
        )
        completed = subprocess.run(  # noqa: S603
            ["/usr/bin/osascript", "-e", script],
            check=False,
            capture_output=True,
            text=True,
            timeout=1800,
        )
    elif system == "Windows":
        script = (
            "Add-Type -AssemblyName System.Windows.Forms; "
            "$d=New-Object System.Windows.Forms.OpenFileDialog; "
            "$d.Filter='Documentos|*.pdf;*.png;*.jpg;*.jpeg;*.webp;*.tif;*.tiff;"
            "*.heic;*.heif;*.txt'; "
            "if($d.ShowDialog() -eq 'OK'){[Console]::Out.Write($d.FileName)}else{exit 2}"
        )
        completed = subprocess.run(  # noqa: S603
            [
                r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                script,
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=1800,
        )
    else:
        raise RuntimeError("sistema operacional não suportado")

    if completed.returncode != 0 or not completed.stdout.strip():
        raise SelectionCancelled
    candidate = Path(completed.stdout.strip())
    if candidate.is_symlink() or not candidate.is_file():
        raise RuntimeError("seleção inválida")
    return candidate.resolve(strict=True)


def select_local_directory(*, prompt: str) -> Path:
    """Abre um diálogo nativo de pasta; o caminho não atravessa o MCP como argumento."""

    system = platform.system()
    safe_prompt = prompt.replace('"', "")
    try:
        if system == "Darwin":
            script = f'''
tell application "Finder"
    activate
    set theFolder to choose folder with prompt "{safe_prompt}"
    return POSIX path of theFolder
end tell
'''
            completed = subprocess.run(  # noqa: S603
                ["/usr/bin/osascript"],
                input=script,
                check=False,
                capture_output=True,
                text=True,
                timeout=1800,
            )
        elif system == "Windows":
            script = (
                "Add-Type -AssemblyName System.Windows.Forms; "
                "$d=New-Object System.Windows.Forms.FolderBrowserDialog; "
                f"$d.Description='{safe_prompt}'; "
                "if($d.ShowDialog() -eq 'OK'){[Console]::Out.Write($d.SelectedPath)}else{exit 2}"
            )
            completed = subprocess.run(  # noqa: S603
                [
                    r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe",
                    "-NoProfile",
                    "-NonInteractive",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-Command",
                    script,
                ],
                check=False,
                capture_output=True,
                text=True,
                timeout=1800,
            )
        else:
            raise RuntimeError("sistema operacional não suportado")
    except subprocess.TimeoutExpired as exc:
        raise TimeoutError("seleção de pasta expirou") from exc

    if completed.returncode != 0 or not completed.stdout.strip():
        raise SelectionCancelled
    candidate = Path(completed.stdout.strip())
    if candidate.is_symlink() or not candidate.is_dir():
        raise RuntimeError("seleção inválida")
    return candidate.resolve(strict=True)


__all__ = ["SelectionCancelled", "select_local_directory", "select_local_file"]
