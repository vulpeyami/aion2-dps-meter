"""Windows Guvenlik Duvari kurali.

Ham sokete gelen paketler de guvenlik duvarindan gecer: "Genel" ag profilinde izni
olmayan bir programa hicbir paket ulasmaz, uyari da gosterilmez. Hakari.exe icin
yalnizca o exe'yi kapsayan bir "gelen trafige izin ver" kurali eklenir. Hakari hicbir
portu dinlemedigi icin bu kural baska bir kapi acmaz.
"""

from __future__ import annotations

import hashlib
import subprocess

CREATE_NO_WINDOW = 0x08000000


def rule_name(program: str) -> str:
    # Exe tasinirsa yeni yolu icin ayri kural gerekir; ad yola gore benzersiz.
    digest = hashlib.sha1(program.lower().encode("utf-8")).hexdigest()[:8]
    return f"Hakari DPS meter {digest}"


def _netsh(command: str) -> int:
    # Komut tek bir metin olarak verilir: netsh kendi key="deger" ayristirmasini yapar,
    # boylece bosluk iceren yollar dogru tirnaklanir.
    result = subprocess.run(f"netsh advfirewall firewall {command}", capture_output=True,
                            creationflags=CREATE_NO_WINDOW)
    return result.returncode


def has_rule(program: str) -> bool:
    return _netsh(f'show rule name="{rule_name(program)}"') == 0


def add_rule(program: str) -> bool:
    return _netsh(f'add rule name="{rule_name(program)}" dir=in action=allow '
                  f'program="{program}" enable=yes profile=any') == 0
