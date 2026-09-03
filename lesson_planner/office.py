"""Turning a .docx into a PDF, and a legacy .doc into a .docx.

Windows teachers get this through Microsoft Word, which they already have and
which renders the template exactly the way it will print. Word is driven from a
short PowerShell script in its own process, so no extra Python package is
needed and a COM call can never wedge a Flask worker thread.

LibreOffice is used only when it is already on the machine - it is what this
project is developed against on Linux. Nothing here installs it, and the
Windows installer never offers it.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path


class OfficeConversionError(RuntimeError):
    """Raised when no renderer is available, or the renderer failed."""


IS_WINDOWS = os.name == "nt"
CONVERSION_TIMEOUT = 180

# wdExportFormatPDF, and wdFormatDocumentDefault for a modern .docx.
_WORD_PDF_FORMAT = 17
_WORD_DOCX_FORMAT = 16

NO_RENDERER_MESSAGE = (
    "The Word preview needs Microsoft Word on this computer. Word is what draws the "
    "template exactly as it prints. The lesson plan itself still downloads without it."
)

# Word is closed again in the finally block whatever happens, so a failed preview
# cannot leave WINWORD.EXE running in the background.
_WORD_SCRIPT = """
param(
    [Parameter(Mandatory=$true)][string]$Source,
    [Parameter(Mandatory=$true)][string]$Target,
    [Parameter(Mandatory=$true)][int]$Format
)

$ErrorActionPreference = 'Stop'
$word = $null
$doc = $null
try {
    $word = New-Object -ComObject Word.Application
    $word.Visible = $false
    $word.DisplayAlerts = 0
    # FileName, ConfirmConversions, ReadOnly, AddToRecentFiles
    $doc = $word.Documents.Open($Source, $false, $true, $false)
    if ($Format -eq 17) {
        $doc.ExportAsFixedFormat($Target, 17)
    } else {
        $doc.SaveAs2($Target, $Format)
    }
} finally {
    if ($doc) {
        try { $doc.Close(0) } catch { }
        try { [void][Runtime.InteropServices.Marshal]::ReleaseComObject($doc) } catch { }
    }
    if ($word) {
        try { $word.Quit() } catch { }
        try { [void][Runtime.InteropServices.Marshal]::ReleaseComObject($word) } catch { }
    }
    [GC]::Collect()
    [GC]::WaitForPendingFinalizers()
}
"""


def _creation_flags() -> int:
    # Without this every preview refresh would flash a console window on Windows.
    return getattr(subprocess, "CREATE_NO_WINDOW", 0) if IS_WINDOWS else 0


def libreoffice_executable() -> str | None:
    for name in ("soffice", "libreoffice"):
        found = shutil.which(name)
        if found:
            return found
    if IS_WINDOWS:
        # Only ever used when the teacher already installed it themselves.
        for base in (os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)")):
            if not base:
                continue
            candidate = Path(base) / "LibreOffice" / "program" / "soffice.exe"
            if candidate.is_file():
                return str(candidate)
    return None


def word_available() -> bool:
    if not IS_WINDOWS:
        return False
    try:
        import winreg
    except ImportError:
        return False
    for root in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        try:
            with winreg.OpenKey(root, r"SOFTWARE\Classes\Word.Application"):
                return True
        except OSError:
            continue
    return False


def renderer_name() -> str:
    """Which renderer this computer will use, or "" when there is none."""
    # On Windows Word comes first: it is already there and it is the program the
    # finished plan is opened in, so its pagination is the one that matters.
    if word_available():
        return "word"
    if libreoffice_executable():
        return "libreoffice"
    return ""


def renderer_available() -> bool:
    return bool(renderer_name())


def _powershell() -> str | None:
    return shutil.which("powershell") or shutil.which("pwsh")


def _run_word(source: Path, target: Path, fmt: int) -> None:
    shell = _powershell()
    if not shell:
        raise OfficeConversionError("Windows PowerShell could not be found, so Word cannot be used.")
    with tempfile.TemporaryDirectory(prefix="lessonflow-word-") as folder:
        script = Path(folder) / "convert.ps1"
        script.write_text(_WORD_SCRIPT, encoding="utf-8")
        try:
            result = subprocess.run(
                [
                    shell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script),
                    "-Source", str(source.resolve()),
                    "-Target", str(target.resolve()),
                    "-Format", str(fmt),
                ],
                capture_output=True,
                text=True,
                timeout=CONVERSION_TIMEOUT,
                check=False,
                creationflags=_creation_flags(),
            )
        except subprocess.TimeoutExpired as exc:
            raise OfficeConversionError("Microsoft Word did not respond in time.") from exc
    if result.returncode != 0 or not target.is_file():
        detail = (result.stderr or result.stdout or "Word could not convert the document.").strip()
        raise OfficeConversionError(f"Microsoft Word could not convert the document: {detail}")


def _run_libreoffice(source: Path, output_dir: Path, target_format: str) -> None:
    executable = libreoffice_executable()
    if not executable:
        raise OfficeConversionError(NO_RENDERER_MESSAGE)
    with tempfile.TemporaryDirectory(prefix="lessonflow-office-") as profile:
        profile_uri = Path(profile).resolve().as_uri()
        try:
            result = subprocess.run(
                [
                    executable,
                    f"-env:UserInstallation={profile_uri}",
                    "--headless",
                    "--convert-to",
                    target_format,
                    "--outdir",
                    str(output_dir),
                    str(source),
                ],
                capture_output=True,
                text=True,
                timeout=CONVERSION_TIMEOUT,
                check=False,
                creationflags=_creation_flags(),
            )
        except subprocess.TimeoutExpired as exc:
            raise OfficeConversionError("The document renderer did not respond in time.") from exc
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "conversion failed").strip()
        raise OfficeConversionError(f"The document could not be converted: {detail}")


def convert_to_pdf(source: str | Path, output_dir: str | Path) -> Path:
    """Render one .docx to a PDF of the same stem inside output_dir."""
    document = Path(source)
    destination_dir = Path(output_dir)
    destination_dir.mkdir(parents=True, exist_ok=True)
    target = destination_dir / f"{document.stem}.pdf"
    if target.exists():
        target.unlink()

    renderer = renderer_name()
    if not renderer:
        raise OfficeConversionError(NO_RENDERER_MESSAGE)
    if renderer == "word":
        _run_word(document, target, _WORD_PDF_FORMAT)
    else:
        _run_libreoffice(document, destination_dir, "pdf")
    if not target.is_file():
        raise OfficeConversionError(f"No PDF was produced for {document.name}.")
    return target


def convert_doc_to_docx(source: str | Path, target: str | Path) -> Path:
    """Convert a legacy .doc into a .docx at exactly the target path."""
    document = Path(source)
    destination = Path(target)
    destination.parent.mkdir(parents=True, exist_ok=True)

    renderer = renderer_name()
    if not renderer:
        raise OfficeConversionError(
            "A legacy .doc file needs Microsoft Word on this computer. "
            "Open it in Word, save it as .docx, and upload that instead."
        )
    if renderer == "word":
        _run_word(document, destination, _WORD_DOCX_FORMAT)
    else:
        _run_libreoffice(document, destination.parent, "docx")
        produced = destination.parent / f"{document.stem}.docx"
        if produced.is_file() and produced != destination:
            produced.replace(destination)
    if not destination.is_file():
        raise OfficeConversionError("The legacy .doc file could not be converted.")
    return destination
