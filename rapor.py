"""nobetci rapor uretici.

Claude'un JSON analizini dogrular ve analiz girdisiyle birlestirip tek dosyalik HTML rapor uretir.
Yalnizca Python standart kutuphanesi kullanir (Bagimlilik Politikasi: SIFIR).

  python3 rapor.py --girdi girdi.json --analiz analiz.txt --html rapor/X.html --json rapor/X.json

Guvenlik: haber verisi ve Claude cikisi guvenilmeyen metindir. Butun metinler HTML'e kacislanarak
yazilir, yalnizca http/https linkler baglanti olur, sayfada JavaScript yoktur ve CSP ile de kapatilir.
"""
from __future__ import annotations

import argparse
import html
import json
import re
import sys
import urllib.parse
from datetime import datetime, timedelta, tzinfo
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

AYLAR = ["Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran", "Temmuz", "Ağustos",
         "Eylül", "Ekim", "Kasım", "Aralık"]
GUNLER = ["Pazartesi", "Salı", "Çarşamba", "Perşembe", "Cuma", "Cumartesi", "Pazar"]
EN_KISA_BOSLUK = timedelta(minutes=15)
# Metne sizan "(h19)" veya "(h22, h57)" gibi haber kimligi atiflari. Atif, maddenin haber listesinde durur.
KIMLIK_ATFI = re.compile(r"\s*\((?:h\d+)(?:\s*(?:,|ve)\s*h\d+)*\)")

MADDE_LISTELERI = ["marka_anilmalari", "riskler", "firsatlar"]

Aralik = Tuple[datetime, datetime]


class RaporHatasi(Exception):
    """Claude cikisi beklenen JSON semasina uymuyor."""


# --- Dogrulama ------------------------------------------------------------------

def _metni_temizle(deger: Any) -> Any:
    """Yazim kurali: uzun tire kullanilmaz. Parantez icindeki haber kimlikleri metinden atilir."""
    if isinstance(deger, str):
        deger = KIMLIK_ATFI.sub("", deger)
        return deger.replace(" \u2014 ", ", ").replace("\u2014", "-").replace(" \u2013 ", ", ").replace("\u2013", "-")
    if isinstance(deger, list):
        return [_metni_temizle(d) for d in deger]
    if isinstance(deger, dict):
        return {k: (v if k == "haberler" else _metni_temizle(v)) for k, v in deger.items()}
    return deger


def _metin(nesne: Dict[str, Any], alan: str, yer: str) -> str:
    deger = nesne.get(alan)
    if not isinstance(deger, str):
        raise RaporHatasi(f"{yer}.{alan} metin olmali")
    return deger.strip()


def _kimlikler(nesne: Dict[str, Any], yer: str, gecerli: Set[str], uyarilar: List[str]) -> List[str]:
    deger = nesne.get("haberler", [])
    if not isinstance(deger, list) or not all(isinstance(k, str) for k in deger):
        raise RaporHatasi(f"{yer}.haberler metin listesi olmali")
    bilinmeyen = [k for k in deger if k not in gecerli]
    if bilinmeyen:
        uyarilar.append(f"'{nesne.get('baslik', yer)}' maddesindeki bilinmeyen haber kimliği atıldı: "
                        + ", ".join(bilinmeyen))
    return [k for k in deger if k in gecerli]


def analiz_ayristir(metin: str, girdi: Dict[str, Any]) -> Tuple[Dict[str, Any], List[str]]:
    """Claude cikisini JSON olarak okur, semaya gore dogrular ve temizler.

    Sema disi cikis RaporHatasi verir. Var olmayan haber kimlikleri atilir ve uyari olarak doner."""
    bas, son = metin.find("{"), metin.rfind("}")
    if bas == -1 or son < bas:
        raise RaporHatasi("cikista JSON nesnesi bulunamadi")
    try:
        ham = json.loads(metin[bas:son + 1])
    except json.JSONDecodeError as hata:
        raise RaporHatasi(f"JSON okunamadi: {hata}") from hata
    if not isinstance(ham, dict):
        raise RaporHatasi("cikis bir JSON nesnesi olmali")
    ham = _metni_temizle(ham)

    gecerli = {g["id"] for g in girdi.get("haberler", [])}
    uyarilar: List[str] = []
    notlar = ham.get("veri_notlari", [])
    if not isinstance(notlar, list) or not all(isinstance(n, str) for n in notlar):
        raise RaporHatasi("veri_notlari metin listesi olmali")
    analiz: Dict[str, Any] = {"ozet": _metin(ham, "ozet", "kok"), "veri_notlari": notlar}

    for liste in MADDE_LISTELERI + ["icerik_fikirleri"]:
        ogeler = ham.get(liste)
        if not isinstance(ogeler, list) or not all(isinstance(o, dict) for o in ogeler):
            raise RaporHatasi(f"{liste} nesne listesi olmali")
        temiz: List[Dict[str, Any]] = []
        for sira, oge in enumerate(ogeler):
            yer = f"{liste}[{sira}]"
            madde: Dict[str, Any] = {"baslik": _metin(oge, "baslik", yer),
                                     "haberler": _kimlikler(oge, yer, gecerli, uyarilar)}
            if liste == "icerik_fikirleri":
                madde["fikir"] = _metin(oge, "fikir", yer)
                madde["kanal"] = _metin(oge, "kanal", yer)
            else:
                madde["neden_onemli"] = _metin(oge, "neden_onemli", yer)
                madde["oneri"] = _metin(oge, "oneri", yer)
                madde["dolayli"] = oge.get("dolayli") is True
            temiz.append(madde)
        analiz[liste] = temiz
    return analiz, uyarilar


# --- Kapsam ---------------------------------------------------------------------

def _zaman(deger: Optional[str]) -> Optional[datetime]:
    return datetime.fromisoformat(deger) if deger else None


def taranmayan_araliklar(girdi: Dict[str, Any]) -> List[Aralik]:
    """Pencere icinde hicbir kaynagin kapsamadigi zaman araliklarini dondurur.

    Bir kaynagin bir taramada kapsadigi aralik: en eski haberi ile tarama zamani arasi."""
    pencere_bas = datetime.fromisoformat(girdi["pencere_baslangic"])
    pencere_son = datetime.fromisoformat(girdi["olusturma_zamani"])
    kapsanan: List[Aralik] = []
    for tarama in girdi.get("kapsam", []):
        tarama_zamani = _zaman(tarama.get("tarama"))
        for kaynak in tarama.get("kaynaklar", []):
            en_eski = _zaman(kaynak.get("en_eski"))
            if kaynak.get("durum") == "okundu" and en_eski and tarama_zamani:
                kapsanan.append((max(en_eski, pencere_bas), min(tarama_zamani, pencere_son)))

    bosluklar: List[Aralik] = []
    imlec = pencere_bas
    for bas, son in sorted(kapsanan):
        if bas > imlec:
            bosluklar.append((imlec, bas))
        imlec = max(imlec, son)
    if pencere_son > imlec:
        bosluklar.append((imlec, pencere_son))
    return [(b, s) for b, s in bosluklar if s - b >= EN_KISA_BOSLUK]


# --- HTML -----------------------------------------------------------------------

def _k(metin: Any) -> str:
    return html.escape(str(metin), quote=True)


def _saat(deger: Optional[str], bolge: Optional[tzinfo]) -> str:
    zaman = _zaman(deger)
    if zaman is None:
        return "-"
    return zaman.astimezone(bolge).strftime("%d.%m %H:%M")


def _guvenli_link(link: str) -> Optional[str]:
    return link if urllib.parse.urlsplit(link).scheme in ("http", "https") else None


def _haber_kartlari(kimlikler: List[str], haberler: Dict[str, Dict[str, Any]], bolge: Optional[tzinfo]) -> str:
    if not kimlikler:
        return ""
    satirlar = []
    for kimlik in kimlikler:
        grup = haberler[kimlik]
        kaynaklar = []
        for kaynak in grup["kaynaklar"]:
            etiket = f'{_k(kaynak["kaynak"])} · {_k(_saat(kaynak.get("yayin_tarihi"), bolge))}'
            link = _guvenli_link(kaynak.get("link", ""))
            if link:
                kaynaklar.append(f'<a href="{_k(link)}" target="_blank" rel="noopener noreferrer">{etiket} ↗</a>')
            else:
                kaynaklar.append(f"<span>{etiket}</span>")
        satirlar.append(f'<li><span class="haber-baslik">{_k(grup["baslik"])}</span>'
                        f'<span class="kaynaklar">{"".join(kaynaklar)}</span></li>')
    return f'<ul class="haberler">{"".join(satirlar)}</ul>'


def _madde_kartlari(maddeler: List[Dict[str, Any]], tur: str, haberler: Dict[str, Dict[str, Any]],
                    bolge: Optional[tzinfo]) -> str:
    if not maddeler:
        return '<p class="bos">Bu bölüm için bulgu yok.</p>'
    kartlar = []
    for madde in maddeler:
        rozet = '<span class="rozet">dolaylı</span>' if madde.get("dolayli") else ""
        if tur == "fikir":
            govde = (f'<p>{_k(madde["fikir"])}</p>'
                     f'<p class="alan"><span>Kanal</span>{_k(madde["kanal"])}</p>')
        else:
            govde = (f'<p class="alan"><span>Neden önemli</span>{_k(madde["neden_onemli"])}</p>'
                     f'<p class="alan"><span>Öneri</span>{_k(madde["oneri"])}</p>')
        kartlar.append(f'<article class="kart {tur}"><h3>{_k(madde["baslik"])}{rozet}</h3>{govde}'
                       f'{_haber_kartlari(madde["haberler"], haberler, bolge)}</article>')
    return "".join(kartlar)


def _kapsam_tablosu(girdi: Dict[str, Any], bolge: Optional[tzinfo]) -> str:
    satirlar = []
    for tarama in girdi.get("kapsam", []):
        for kaynak in tarama.get("kaynaklar", []):
            okundu = kaynak.get("durum") == "okundu"
            durum = ('<span class="durum ok">okundu</span>' if okundu
                     else f'<span class="durum hata">okunamadı</span><small>{_k(kaynak.get("hata") or "")}</small>')
            satirlar.append(
                f'<tr><td>{_k(_saat(tarama.get("tarama"), bolge))}</td><td>{_k(kaynak["ad"])}</td>'
                f'<td>{durum}</td><td class="sayi">{_k(kaynak.get("haber_sayisi", 0))}</td>'
                f'<td>{_k(_saat(kaynak.get("en_eski"), bolge))}</td>'
                f'<td>{_k(_saat(kaynak.get("en_yeni"), bolge))}</td></tr>')
    if not satirlar:
        return '<p class="bos">Pencere içinde tarama yok.</p>'
    return ('<div class="tablo-kutu"><table><thead><tr><th>Tarama</th><th>Kaynak</th><th>Durum</th>'
            '<th class="sayi">Haber</th><th>En eski</th><th>En yeni</th></tr></thead>'
            f'<tbody>{"".join(satirlar)}</tbody></table></div>')


def _bosluk_metni(bosluklar: List[Aralik], bolge: Optional[tzinfo]) -> str:
    if not bosluklar:
        return '<p>Pencerenin tamamı en az bir kaynak tarafından kapsandı.</p>'
    araliklar = ", ".join(
        f'{_k(b.astimezone(bolge).strftime("%d.%m %H:%M"))} - {_k(s.astimezone(bolge).strftime("%d.%m %H:%M"))}'
        for b, s in bosluklar)
    return (f'<p><strong>Hiçbir kaynağın kapsamadığı aralıklar:</strong> {araliklar}. '
            'RSS yalnızca son haberleri verdiği için bu aralıklardaki haberler rapora girmedi.</p>')


CSS = """
:root{--bg:#f7f4ef;--panel:#fff;--ink:#231e18;--muted:#6f655a;--line:#e6dfd4;--accent:#7a4a26;
--risk:#b3402e;--risk-bg:#fbeeeb;--firsat:#2c7a4b;--firsat-bg:#eaf5ee;--fikir:#2c5c88;--fikir-bg:#eaf1f8;
--marka:#7a4a26;--marka-bg:#f6eee6;--uyari:#8a5a00;--uyari-bg:#fdf4e1;--shadow:0 1px 2px rgba(35,30,24,.06),0 4px 16px rgba(35,30,24,.05)}
@media (prefers-color-scheme:dark){:root{--bg:#15120f;--panel:#1e1a16;--ink:#eee7dd;--muted:#a89d90;
--line:#332d26;--accent:#d49a6a;--risk:#f08a76;--risk-bg:#2d1a16;--firsat:#6fcf97;--firsat-bg:#15261c;
--fikir:#83b5e6;--fikir-bg:#15212d;--marka:#d49a6a;--marka-bg:#2a1f16;--uyari:#f0c060;--uyari-bg:#2a2210;--shadow:none}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"Helvetica Neue",Arial,sans-serif;-webkit-font-smoothing:antialiased}
main{max-width:880px;margin:0 auto;padding:40px 16px 64px}
header{margin-bottom:28px}
.ust{font-size:12px;letter-spacing:.12em;text-transform:uppercase;color:var(--accent);font-weight:600}
h1{font-size:32px;line-height:1.2;margin:6px 0 4px;letter-spacing:-.01em}
.tarih{color:var(--muted);margin:0}
.sayilar{display:flex;flex-wrap:wrap;gap:8px;margin-top:18px;padding:0;list-style:none}
.sayilar li{background:var(--panel);border:1px solid var(--line);border-radius:999px;padding:4px 12px;font-size:14px;color:var(--muted)}
.sayilar b{color:var(--ink);font-variant-numeric:tabular-nums}
section{margin-top:36px}
h2{font-size:20px;margin:0 0 14px;display:flex;align-items:center;gap:10px}
h2 .adet{font-size:13px;font-weight:600;color:var(--muted);background:var(--panel);border:1px solid var(--line);border-radius:999px;padding:1px 9px}
.ozet{background:var(--panel);border:1px solid var(--line);border-left:4px solid var(--accent);border-radius:10px;padding:18px 20px;font-size:17px;box-shadow:var(--shadow)}
.ozet p{margin:0}
.not{background:var(--uyari-bg);border:1px solid var(--line);border-radius:10px;padding:12px 16px;margin-top:14px;color:var(--ink)}
.not h3{margin:0 0 6px;font-size:14px;color:var(--uyari)}
.not ul{margin:0;padding-left:20px}
.kart{background:var(--panel);border:1px solid var(--line);border-left:4px solid var(--c);border-radius:10px;padding:16px 20px;margin-bottom:12px;box-shadow:var(--shadow)}
.kart.risk{--c:var(--risk);--cb:var(--risk-bg)}.kart.firsat{--c:var(--firsat);--cb:var(--firsat-bg)}
.kart.fikir{--c:var(--fikir);--cb:var(--fikir-bg)}.kart.marka{--c:var(--marka);--cb:var(--marka-bg)}
.kart h3{margin:0 0 8px;font-size:17px;line-height:1.35;display:flex;flex-wrap:wrap;align-items:center;gap:8px}
.kart p{margin:6px 0}
.alan span{display:block;font-size:12px;letter-spacing:.06em;text-transform:uppercase;color:var(--c);font-weight:600}
.rozet{font-size:11px;font-weight:600;letter-spacing:.04em;text-transform:uppercase;color:var(--c);background:var(--cb);border-radius:999px;padding:2px 8px}
.haberler{list-style:none;margin:12px 0 0;padding:12px 0 0;border-top:1px dashed var(--line)}
.haberler li{margin-bottom:8px;font-size:14px}
.haber-baslik{display:block;color:var(--ink)}
.kaynaklar{display:flex;flex-wrap:wrap;gap:4px 12px;font-size:13px;color:var(--muted)}
a{color:var(--c,var(--accent));text-decoration:none}a:hover{text-decoration:underline}
.bos{color:var(--muted);font-style:italic;margin:0}
.tablo-kutu{overflow-x:auto;background:var(--panel);border:1px solid var(--line);border-radius:10px}
table{width:100%;border-collapse:collapse;font-size:14px}
th,td{text-align:left;padding:9px 12px;border-bottom:1px solid var(--line);white-space:nowrap}
th{font-size:12px;letter-spacing:.06em;text-transform:uppercase;color:var(--muted);font-weight:600}
tr:last-child td{border-bottom:0}
.sayi{text-align:right;font-variant-numeric:tabular-nums}
.durum{font-weight:600}.durum.ok{color:var(--firsat)}.durum.hata{color:var(--risk)}
td small{display:block;color:var(--muted);white-space:normal}
.kapsam-not{color:var(--muted);font-size:14px}
footer{margin-top:48px;padding-top:16px;border-top:1px solid var(--line);color:var(--muted);font-size:13px}
@media (max-width:600px){h1{font-size:26px}main{padding-top:28px}.kart{padding:14px 16px}}
@media print{:root{--bg:#fff;--panel:#fff;--shadow:none}main{padding:0;max-width:none}.kart,.ozet{break-inside:avoid}
a::after{content:" (" attr(href) ")";font-size:11px;color:var(--muted);word-break:break-all}}
"""


def html_uret(girdi: Dict[str, Any], analiz: Dict[str, Any], uyarilar: List[str]) -> str:
    olusturma = datetime.fromisoformat(girdi["olusturma_zamani"])
    bolge = olusturma.tzinfo
    haberler = {g["id"]: g for g in girdi.get("haberler", [])}
    kaynak_sayisi = len({k["ad"] for t in girdi.get("kapsam", []) for k in t.get("kaynaklar", [])})
    tarih = f"{olusturma.day} {AYLAR[olusturma.month - 1]} {olusturma.year}, {GUNLER[olusturma.weekday()]}"

    bolumler = [
        ("Doğrudan Marka Anılmaları", "marka_anilmalari", "marka"),
        ("Riskler", "riskler", "risk"),
        ("Fırsatlar", "firsatlar", "firsat"),
        ("İçerik Fikirleri", "icerik_fikirleri", "fikir"),
    ]
    sayilar = "".join(f"<li><b>{_k(sayi)}</b> {_k(ad)}</li>" for sayi, ad in [
        (kaynak_sayisi, "kaynak"), (girdi.get("haber_sayisi", 0), "haber"),
        (girdi.get("grup_sayisi", 0), "ayrı haber"), (len(analiz["riskler"]), "risk"),
        (len(analiz["firsatlar"]), "fırsat"), (len(analiz["icerik_fikirleri"]), "içerik fikri")])

    notlar = ""
    if analiz["veri_notlari"] or uyarilar:
        maddeler = "".join(f"<li>{_k(n)}</li>" for n in analiz["veri_notlari"] + uyarilar)
        notlar = f'<div class="not"><h3>Veri notları</h3><ul>{maddeler}</ul></div>'

    govde = "".join(
        f'<section><h2>{_k(baslik)}<span class="adet">{len(analiz[alan])}</span></h2>'
        f'{_madde_kartlari(analiz[alan], tur, haberler, bolge)}</section>'
        for baslik, alan, tur in bolumler)

    return f"""<!doctype html>
<html lang="tr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'">
<meta name="referrer" content="no-referrer">
<title>Nöbetçi Raporu {_k(olusturma.strftime("%d.%m.%Y"))}</title>
<style>{CSS}</style>
</head>
<body>
<main>
<header>
<div class="ust">Kuytu Kahve · Haber Nöbetçisi</div>
<h1>Nöbetçi Raporu</h1>
<p class="tarih">{_k(tarih)} · {_k(olusturma.strftime("%d.%m.%Y %H:%M"))}</p>
<ul class="sayilar">{sayilar}</ul>
</header>
<section><h2>Özet</h2><div class="ozet"><p>{_k(analiz["ozet"])}</p></div>{notlar}</section>
{govde}
<section><h2>Kapsam</h2>
{_kapsam_tablosu(girdi, bolge)}
<div class="kapsam-not">{_bosluk_metni(taranmayan_araliklar(girdi), bolge)}
<p>Pencere: {_k(_saat(girdi["pencere_baslangic"], bolge))} - {_k(olusturma.strftime("%d.%m %H:%M"))} ({_k(girdi.get("pencere_saat", 48))} saat).</p></div>
</section>
<footer>Bu rapor feeds.json'daki RSS kaynaklarından otomatik toplandı ve Claude ile analiz edildi. Bağlantılar, kaynak adları ve saatler analizden değil doğrudan haber verisinden alınır.</footer>
</main>
</body>
</html>
"""


# --- Komut satiri ---------------------------------------------------------------

def main(argv: Optional[List[str]] = None) -> int:
    ayristirici = argparse.ArgumentParser(description="nobetci HTML rapor uretici")
    ayristirici.add_argument("--girdi", required=True, type=Path, help="toplayici.py girdi ciktisi")
    ayristirici.add_argument("--analiz", required=True, type=Path, help="Claude ciktisi")
    ayristirici.add_argument("--html", required=True, type=Path, help="yazilacak HTML rapor")
    ayristirici.add_argument("--json", required=True, type=Path, help="yazilacak dogrulanmis analiz")
    args = ayristirici.parse_args(argv)

    girdi = json.loads(args.girdi.read_text(encoding="utf-8"))
    try:
        analiz, uyarilar = analiz_ayristir(args.analiz.read_text(encoding="utf-8"), girdi)
    except RaporHatasi as hata:
        print(f"rapor.py: Claude cikisi gecersiz: {hata}", file=sys.stderr)
        return 1
    for uyari in uyarilar:
        print(f"rapor.py: uyari: {uyari}", file=sys.stderr)

    args.html.write_text(html_uret(girdi, analiz, uyarilar), encoding="utf-8")
    args.json.write_text(json.dumps({**analiz, "uyarilar": uyarilar}, ensure_ascii=False, indent=2) + "\n",
                         encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
