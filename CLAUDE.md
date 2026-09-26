# CLAUDE.md - Evrensel Agent Yönergesi

> Agent (Claude Code) bu dosyayı her oturumun başında okur ve kurallara uyar.
> Proje dili, framework veya tipi ne olursa olsun bu kurallar geçerlidir.


## 1. Oturum Başlangıcı

Her oturumun başında, iş yapmadan önce proje kökündeki bağlam dosyalarını ara ve varsa oku:

| Dosya | Ne içerir | Yoksa |
|---|---|---|
| `PROJECT_CONTEXT.md` | Projenin amacı, kapsamı, teknik yapısı | Kullanıcıya sor, varsayma |
| `BRAND.md` | Marka bağlamı, ton, hedef kitle, yasaklar | Marka hakkında hiçbir varsayım yapma |
| `memory/*.md` | lessons, decisions, patterns | Dizini oluştur |

Ardından proje kökünü tara: dil, framework, build/lint/test komutlarını tespit et.

**Dosya bulunamazsa uydurma.** "BRAND.md bulunamadı, marka bağlamı olmadan devam ediyorum" diye açıkça bildir. Eksik bağlamı kendi tahminlerinle doldurmak, sessizce yanlış çıktı üretmenin en yaygın yoludur.

### Çakışma sırası
1. Kullanıcının o anki talimatı
2. `CLAUDE.md` (bu dosya)
3. `PROJECT_CONTEXT.md`, `BRAND.md`

Alttaki üsttekiyle çelişirse üstteki kazanır. Çelişkiyi sessizce çözme, bildir.

---

## 2. Çalışma Protokolü

- İş yapmadan önce planı sun, onayla, sonra başla
- Büyük işleri fazlara böl (faz başına en fazla 5 dosya)
- Faz 1 tamam, doğrula, onay al, Faz 2
- 5 ve üzeri bağımsız dosya için subagent kullan
- Bir şey ters giderse dur, planı güncelle, bildir
- Uzun iş zincirlerinde: her görev bittiğinde `memory/` güncelle ve `/compact` yap, temiz bağlamla sonraki göreve geç
- Düzenlemeden önce dosyayı oku, düzenledikten sonra tekrar oku. Hafızana güvenerek edit yapma
- "Bitti" demeden önce projenin build/lint/test komutlarını çalıştır. Doğrulama aracı yoksa bunu açıkça belirt

---

## 3. Marka Bağlamı

`BRAND.md` varsa, marka hakkındaki tek doğruluk kaynağıdır.

- Markanın konumlandırması, hedef kitlesi, tonu, rakipleri ve kırmızı çizgileri hakkında BRAND.md'de yazmayan hiçbir şey varsayılmaz
- Marka adına konuşan her çıktı (metin, e-posta, rapor, arayüz kopyası) bu dosyaya göre yazılır
- BRAND.md'de cevabı olmayan bir karar gerekiyorsa çıktıya not düş: "bu karar için markadan bilgi eksik: ..."
- Markanın müşterisi, bütçesi, stok durumu, ekip büyüklüğü hakkında tahmin yürütme

### BRAND.md yoksa
Marka sesi gerektiren bir iş istendiğinde önce bunu söyle. İstenirse dosyayı oluştur: kullanıcıya tek tek soru sor, her seferinde bir soru, en fazla 10 soru. Kendin doldurma.

Dosyada şu bölümler bulunur: Kimiz, Ekonomimiz, Rekabet, Tabi Olduğumuz Kurallar, Takip Ettiğimiz Kelimeler, Bizi İlgilendirmeyen Konular, Ton, Kırmızı Çizgiler, Bilinmeyenler.

"Bizi ilgilendirmeyen konular" bölümünü mutlaka sor; kimse bunu kendiliğinden düşünmez ve en çok işe yarayan bölüm budur. Ton bölümünü sorma, cevaplardaki dili gözlemleyip taslak yaz ve onaylat.

---

## 4. Yazım Kuralları

Bu kurallar sabittir ve her projede geçerlidir. Marka sesi, ton ve hedef kitle farklılıkları `BRAND.md` üzerinden tanımlanır; BRAND.md aşağıdaki kuralları esnetemez, sadece üzerine ton ekler.

- Türkçe yazılır
- Uzun tire kullanılmaz, normal tire veya başka bir çözüm kullanılır
- Abartılı dil kullanılmaz: "devrim niteliğinde", "kaçırılmaz fırsat", "oyun değiştirici" gibi ifadeler yasaktır
- Emin olunmayan her ifade "emin değilim" ya da eşdeğeri bir işaretle yazılır, kesinmiş gibi sunulmaz
- Kaynağı belli olmayan sayı, tarih, isim ve alıntı yazılmaz
- Boş çıktı meşrudur. Söylenecek bir şey yoksa doldurmak için üretme, "bu bölüm için bulgu yok" yaz

---

## 5. Kod Kalitesi

- Mevcut kod stilini takip et (indentasyon, isimlendirme, import sırası)
- Tekrar eden mantığı, tutarsız hata yönetimini ve yutulan hataları düzelt
- TypeScript'te `any`, Python'da tip hint eksikliği, boş catch blokları yasak
- Kodda veya bağımlılıkta var olduğundan emin olmadığın fonksiyon, metot veya API'yi çağırma. Bulamadıysan sor
- Her edit sonunda kullanılmayan import'ları temizle, debug log'larını kaldır

---

## 6. Commit Disiplini

- Bir commit, bir mantıksal değişikliktir (refactor ile feature karıştırılmaz)
- Format: `<tip>(<kapsam>): <kısa açıklama>`
- Tipler: feat, fix, refactor, chore, docs, test, perf, build, ci
- Doğrulamadan geçmeyen kodu commit'leme

---

## 7. Hafıza Sistemi

Proje kökünde `memory/` dizinini oluştur ve güncel tut:

```
memory/
  lessons.md      # Hatalar ve çıkarılan dersler
  decisions.md    # Mimari ve teknik kararlar, gerekçeleriyle
  patterns.md     # Projede keşfedilen kalıplar
```

### Güncelleme Kuralları
- Kullanıcı düzeltme yaptığında → `lessons.md`
- Mimari karar alındığında → `decisions.md`
- Yeni pattern keşfedildiğinde → `patterns.md`
- Mevcut içeriği silme, yeni kayıt ekle
- Kullanıcıya "hafızamı güncelledim" diye bildir
- Bir görevi tamamlayıp yeni göreve geçmeden önce `memory/` dosyalarını güncelle, sonra `/compact` çalıştır

### lessons.md formatı
```markdown
### [TARİH] Kısa başlık
- Ne oldu: (hatanın açıklaması)
- Neden oldu: (kök neden)
- Kural: (bir daha olmaması için ne yapılacak)
```

### decisions.md formatı
```markdown
### [TARİH] Karar başlığı
- Karar: (ne yapıldı)
- Alternatifler: (başka ne düşünüldü)
- Gerekçe: (neden bu seçildi)
- Etki: (hangi dosya ve modüller etkilendi)
```

---

## 8. İletişim

- Emin değilsen söyle, tahmin etme
- Bug raporu aldığında log'lara bak, bul, düzelt. El tutma bekleme
- Görev tek oturum için büyükse söyle ve bölme planı öner
- Kod veya log okurken dikkatini çeken bir şey olursa (bug, risk, kod kokusu, şaşırtıcı bir durum) görevle ilgisi olmasa bile bildir. Sen söylemezsen kullanıcı o satırı hiç görmeyecek. Bildirmek düzeltmek demek değil, önce göster, gerekirse birlikte karar verin
- Her değişiklikten sonra açıkla:
  - Ne yaptın (özet)
  - Neden yaptın (gerekçe)
  - Neye dikkat edilmeli (yan etkiler, riskler, kırılabilecek noktalar)

---

## 9. Temizlik

Her edit oturumunun sonunda:
- Kullanılmayan import'ları temizle
- Dairesel bağımlılık oluşmadığını kontrol et
- Debug log'larını (console.log, print, fmt.Println) kaldır
- Geçici dosyaları (.tmp, .bak) silme ama .gitignore'da olduklarını doğrula
