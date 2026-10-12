# Hakari (秤) — Aion 2 DPS Meter

*Hakari* Japoncada "terazi" demek: tartar, ölçer.

Oyun içinde ne kadar hasar verdiğini gösteren küçük bir pencere. Oyunun ağ trafiğini
**sadece okur**: oyuna dokunmaz, belleğini okumaz, paket göndermez.

Varsayılan olarak Windows'un kendi ham soketini kullanır, yani **hiçbir sürücü yüklemez**.
WinDivert sürücüsü sadece `Baslat.bat --mode windivert` ile açıkça istenirse kullanılır.
Protokol bilgisi ve skill/boss adları [Aion2Flow](https://github.com/cloris-chan/Aion2Flow)'dan
(GPL-3.0) alındı. Adlar oyunun İngilizce istemcisinden.

## Exe ile kullanım (Python gerekmez)
1. `Hakari.exe`'yi istediğin bir klasöre koy ve çift tıkla, yönetici iznini onayla.
2. Windows "Bilinmeyen yayıncı" uyarısı verirse: **Ek bilgi → Yine de çalıştır**
   (exe dijital olarak imzalı değil).
3. İlk açılışta güvenlik duvarı kuralı eklensin mi diye sorar: **Evet** de. Yoksa Windows
   oyun paketlerini Hakari'ye ulaştırmaz. Kural sadece bu exe içindir; exe'yi başka klasöre
   taşırsan bir kez daha sorar.
4. Ayarlar (`ayarlar.json`), günlük (`hakari.log`) ve zindan kayıtları (`kayitlar\`) exe'nin
   yanına yazılır.

Bağlantı testi: exe'nin klasöründe bir terminal açıp `Hakari.exe --selftest`.
Exe'yi kendin derlemek için: `py -3 -m pip install -r requirements.txt pyinstaller pillow`,
sonra `build.bat` → `dist\Hakari.exe`.

## Kaynaktan kurulum (bir kez)
1. [Python](https://www.python.org/downloads/) 3.10 veya üstünü kur. Kurulumda "py launcher" seçili kalsın.
2. Bu klasörde bir terminal aç: `py -3 -m pip install -r requirements.txt`

## İlk kez: çalıştığını kontrol et
`Baslat.bat --selftest` çalıştır ve 10 saniye içinde tarayıcıda bir sayfa aç.
"BASARILI" yazıyorsa hazırsın. Windows bir güvenlik duvarı uyarısı gösterirse Python'a izin ver.

## Çalıştırma
1. Oyunu **pencereli / kenarlıksız tam ekran** moduna al (tam ekran modunda pencere görünmez).
2. `Baslat.bat`'a çift tıkla ve yönetici iznini onayla.
3. Karakterin, oyuna girişte veya teleport sonrasında otomatik tanınır. Meter'i oyundan
   sonra açtıysan ya bir kez teleport ol ya da listede kendi satırına sağ tıklayıp
   **Bu benim** de. Bu sadece
   ilk sefer gerekir: karakter adın `ayarlar.json`'a kaydedilir ve sonraki açılışlarda
   otomatik tanınırsın.

| Fare / tuş | İşlev |
|---|---|
| Sol tık + sürükle | Pencereyi taşı |
| Satıra tık (ya da büyük DPS rakamına) | O oyuncunun **skill dökümü**; üstteki ◀ çubuğu geri döner |
| Üstteki sekmeler | **Canlı** dövüş, son 3 **boss**, zindan **Toplam**ı |
| Sağ tık | Görünüm, verilen/alınan hasar, geçmiş dövüşler, boyut, saydamlık, arka plan rengi, kilit, sıfırla |
| Satıra sağ tık | "Bu benim" |
| Ctrl + fare tekerleği | Boyut (%80–%175) |
| Ctrl+Shift+H | Gizle / göster |
| Ctrl+Shift+L | Kilitle: pencere tıklamaları oyuna geçirir, sürüklenmez (🔒). Açmak için tekrar bas |
| Ctrl+Shift+C | Özeti panoya kopyala; oyun sohbetine kendin yapıştırırsın |

Kısayollar Windows'un normal kısayol sistemiyle alınır (klavye kancası yok). Hakari açıkken bu
tuş kombinasyonları başka programlarda çalışmaz; başka bir program önce aldıysa `hakari.log`'a yazılır.

**Dövüşler ve bosslar:** 15 saniye hasar gelmezse dövüş biter, sonraki vuruş yeni dövüş açar.
Bosslar oyun verisinden tanınır ve sekmede adıyla görünür. Boss faz geçişinde (dokunulmazlık)
hasar 90 saniyeye kadar kesilse de aynı boss'a dönülürse aynı dövüş devam eder.
Tüm dövüşler sağ tık → **Geçmiş dövüşler**'de.

**Zindan toplamı:** Hasar bosslar arasında silinmez, "Sıfırla (yeni zindan)" diyene kadar
birikir. Süre ve DPS sadece dövüşülen zamana göre hesaplanır; bosslar arası yürüyüş DPS'i düşürmez.

**Skill dökümü:** Skill adları, toplam hasar, krit oranı ve pay. DoT'lar ayrı satırda "(DoT)"
olarak görünür; altta en büyük vuruş yazar.

**Alınan hasar:** Sağ tık → Alınan hasar. Partide kimin ne kadar hasar yediğini gösterir.

**Grafik:** Büyük rakamın altında dövüş boyunca DPS'in (5 sn ortalama) seyri; zindan toplamında
sadece dövüşülen süreler arka arkaya eklenir.

**Kayıtlar ve rekorlar:** Her zindan `kayitlar\TARIH_SAAT.json` dosyasına yazılır (her dövüş
bitince güncellenir). Bir boss'ta önceki zindanlardaki en iyi DPS'in sağ üstte "Rekor" olarak
görünür; geçersen "▲ Yeni rekor!" yazar. Rekorlar karakter adına göre tutulur. Kayıtlarda
partidekilerin isimleri de olduğu için paylaşırken dikkat et.

Dövüş dışında pencere soluklaşır; fare üstüne gelince netleşir.
Arka plan rengi sağ tık → **Arka plan rengi**'nden seçilir: Koyu gri, Siyah, Antrasit, Lacivert,
Mor, Koyu yeşil, Bordo.
Seçimler, boyut, saydamlık, renk ve pencere konumu `ayarlar.json`'a kaydedilir.

**İsimler ve sınıflar:** Oyuncu isimleri, oyuncular görüş alanına girdiğinde ve parti/ittifak
listesi geldiğinde okunur. Sınıf (çubuk rengi) isim paketinden ya da kullanılan skill'lerden
anlaşılır; bu yüzden ismi henüz gelmemiş oyuncular da doğru renkte görünür.
**Parti** görünümü parti listesi geldikten sonra dolar; partiye meter açıkken katılmak ya da
bölge değiştirmek listeyi tazeler.

| Renk | Sınıf | Renk | Sınıf |
|---|---|---|---|
| Turuncu | Gladiator | Kırmızı | Sorcerer |
| Mavi | Templar | Turkuaz | Elementalist |
| Mor | Assassin | Altın | Cleric |
| Yeşil | Ranger | Açık yeşil | Chanter |
| Pembe | Brawler | Gri | Bilinmiyor |

## "Oyundan paket gelmiyor" uyarısı
Windows Güvenlik Duvarı ham sokete gelen paketleri engelliyor olabilir:
Windows Güvenlik → Güvenlik duvarı ve ağ koruması → Bir uygulamaya güvenlik duvarı üzerinden
izin ver → `python.exe` için Özel ağ kutusunu işaretle. Yine olmazsa son çare
`Baslat.bat --mode windivert` (sürücü kullanır).

## Bir şey yanlış görünüyorsa
`Baslat.bat --dump kayit.bin` ile çalıştırıp biraz savaş, sonra kapat.
`py main.py --replay kayit.bin` kaydı tekrar işleyip özet yazar (yönetici gerekmez).

## Güvenlik
- Exe'yi kendi klasörüne koy (ör. `C:\Oyunlar\Hakari\`), İndirilenler gibi kalabalık bir klasörde
  çalıştırma. Hakari yönetici izniyle çalışır; Windows bir programın yanındaki DLL'leri de
  yükleyebildiği için klasöre başka biri dosya koyamamalı.
- Hakari dışarıya hiçbir şey göndermez ve hiçbir portu dinlemez. Güvenlik duvarı kuralı sadece
  oyundan gelen paketlerin ham sokete ulaşması içindir.
- `kayit.bin` (--dump) ve `kayitlar\` başka oyuncuların isimlerini içerir; paylaşırken dikkat et.

## Bilinen eksikler
- Pet/çağrı hasarı sahibine eklenmez, ayrı satır olarak görünür.
- İsim paketi gelmemiş oyuncular `#id` ile gösterilir (sınıf rengi yine doğrudur).
- Oyun güncellemesi paket yapısını değiştirirse çözücünün güncellenmesi gerekir.

## Testler
`py -m unittest discover -s tests`
