# Aion2 DPS Meter

Oyun içinde ne kadar hasar verdiğini gösteren küçük bir pencere. Oyunun ağ trafiğini
WinDivert ile **sadece okur**: oyuna dokunmaz, belleğini okumaz, paket göndermez.
Protokol bilgisi [Aion2Flow](https://github.com/cloris-chan/Aion2Flow)'dan (GPL-3.0) alındı.

## Kurulum (bir kez)
1. [Python](https://www.python.org/downloads/) 3.10 veya üstünü kur. Kurulumda "py launcher" seçili kalsın.
2. Bu klasörde bir terminal aç: `py -3 -m pip install -r requirements.txt`

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

## Bir şey yanlış görünüyorsa
`Baslat.bat --dump kayit.bin` ile çalıştırıp biraz savaş, sonra kapat.
`py main.py --replay kayit.bin` kaydı tekrar işleyip özet yazar (yönetici gerekmez).

## Bilinen eksikler
- Pet/çağrı hasarı sahibine eklenmez, ayrı satır olarak görünür.
- Diğer oyuncular isim yerine `#id` ile gösterilir.
- Oyun güncellemesi paket yapısını değiştirirse çözücünün güncellenmesi gerekir.

## Testler
`py -m unittest discover -s tests`
