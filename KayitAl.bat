@echo off
rem Hata ayiklama icin: meter'i acar ve oyun trafigini kayit.bin'e kaydeder.
rem Meter'i sag tik > Cikis ile kapatinca kayit tamamlanir.
call "%~dp0Baslat.bat" --dump kayit.bin
