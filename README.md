# Aion2 DPS Meter

Oyun içinde ne kadar hasar verdiğini gösteren küçük bir pencere. Oyunun ağ trafiğini
**sadece okur**: oyuna dokunmaz, belleğini okumaz, paket göndermez.

Varsayılan olarak Windows'un kendi ham soketini kullanır, yani **hiçbir sürücü yüklemez**.
WinDivert sürücüsü sadece `Baslat.bat --mode windivert` ile açıkça istenirse kullanılır.
Protokol bilgisi [Aion2Flow](https://github.com/cloris-chan/Aion2Flow)'dan (GPL-3.0) alındı.

## Kurulum (bir kez)
1. [Python](https://www.python.org/downloads/) 3.10 veya üstünü kur. Kurulumda "py launcher" seçili kalsın.
2. Bu klasörde bir terminal aç: `py -3 -m pip install -r requirements.txt`

## İlk kez: çalıştığını kontrol et
`Baslat.bat --selftest` çalıştır ve 10 saniye içinde tarayıcıda bir sayfa aç.
"BASARILI" yazıyorsa hazırsın. Windows bir güvenlik duvarı uyarısı gösterirse Python'a izin ver.

## Çalıştırma
1. Oyunu **pencereli / kenarlıksız tam ekran** moduna al (tam ekran modunda pencere görünmez).
2. `Baslat.bat`'a çift tıkla ve yönetici iznini onayla.
3. Karakterin, oyuna girişte veya teleport sonrasında otomatik tanınır. Meter'i oyundan
   sonra açtıysan ya bir kez teleport ol ya da listede kendi satırına çift tıkla.

| Fare | İşlev |
|---|---|
| Sol tık + sürükle | Pencereyi taşı |
| Sağ tık | Sıfırla / diğer oyuncuları gizle / çıkış |
| Satıra çift tık | "Bu benim" |

15 saniye hasar gelmezse sayaç durur. Sonraki vuruşta yeni bir karşılaşma başlar.

## "Oyundan paket gelmiyor" uyarısı
Windows Güvenlik Duvarı ham sokete gelen paketleri engelliyor olabilir:
Windows Güvenlik → Güvenlik duvarı ve ağ koruması → Bir uygulamaya güvenlik duvarı üzerinden
izin ver → `python.exe` için Özel ağ kutusunu işaretle. Yine olmazsa son çare
`Baslat.bat --mode windivert` (sürücü kullanır).

## Bir şey yanlış görünüyorsa
`Baslat.bat --dump kayit.bin` ile çalıştırıp biraz savaş, sonra kapat.
`py main.py --replay kayit.bin` kaydı tekrar işleyip özet yazar (yönetici gerekmez).

## Bilinen eksikler
- Pet/çağrı hasarı sahibine eklenmez, ayrı satır olarak görünür.
- Diğer oyuncular isim yerine `#id` ile gösterilir.
- Oyun güncellemesi paket yapısını değiştirirse çözücünün güncellenmesi gerekir.

## Testler
`py -m unittest discover -s tests`
