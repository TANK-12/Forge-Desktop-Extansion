"""An alarm's or timer's notification on Windows: a toast through Windows
PowerShell 5.1, which ships with Windows 10 and 11 (Relay task 130).
The text reaches PowerShell as an environment variable, never as code."""

from __future__ import annotations

import os
import shutil
import subprocess

SCRIPT = (
    "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications,"
    " ContentType = WindowsRuntime] > $null;"
    "$x = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent("
    "[Windows.UI.Notifications.ToastTemplateType]::ToastText02);"
    "$t = $x.GetElementsByTagName('text');"
    "$t.Item(0).AppendChild($x.CreateTextNode('Forge')) > $null;"
    "$t.Item(1).AppendChild($x.CreateTextNode($env:FORGE_NOTE)) > $null;"
    "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("
    "'{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\\WindowsPowerShell\\v1.0\\powershell.exe')"
    ".Show([Windows.UI.Notifications.ToastNotification]::new($x))"
)
NO_WINDOW = 0x08000000  # CREATE_NO_WINDOW: no console flashes up


def available() -> str | None:
    return None if shutil.which("powershell") else "Windows PowerShell is missing"


def args() -> list[str]:
    return [
        "powershell",
        "-NoProfile",
        "-NonInteractive",
        "-WindowStyle",
        "Hidden",
        "-Command",
        SCRIPT,
    ]


def notify(text: str, popen=subprocess.Popen) -> None:
    env = {**os.environ, "FORGE_NOTE": text[:200]}
    try:
        popen(
            args(),
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=NO_WINDOW,
        )
    except OSError:
        pass
