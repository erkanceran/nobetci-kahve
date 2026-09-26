"""nobetci veri toplayici.

Yalnizca Python standart kutuphanesi kullanir (Bagimlilik Politikasi: SIFIR).

Komutlar:
  python3 toplayici.py topla    feeds.json'daki kaynaklari okur, veri/YYYY-MM-DD.json'a yazar,
                                7 gunden eski veri dosyalarini siler.
  python3 toplayici.py girdi    Son 48 saatin haberlerini tekrarlardan arindirip analiz
                                girdisi olarak stdout'a JSON yazar.
"""
from __future__ import annotations

import argparse
import difflib
import json
import os
import re
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set

KOK = Path(__file__).resolve().parent

USER_AGENT = "Mozilla/5.0 (compatible; nobetci/1.0; RSS okuyucu)"
ZAMAN_ASIMI_SN = 30
AZAMI_BOYUT = 5 * 1024 * 1024
YENIDEN_DENEME_BEKLEMESI_SN = 20
SAKLAMA_GUN = 7
OZET_AZAMI_KARAKTER = 600
VERI_DOSYA_DESENI = re.compile(r"^(\d{4}-\d{2}-\d{2})\.json$")

# Baslik benzerligi esikleri. 2026-09-26 tarihli gercek feed'lerdeki ciftlerle ayarlandi:
# yanlis birlestirme bilgi kaybettirmez (her kaynagin basligi ve linki korunur),
# ama esikler bilerek temkinli tutuldu.
BENZERLIK_KESIN = 0.85
BENZERLIK_YAPI = 0.70
ORTAK_KELIME_ORANI = 0.50

Haber = Dict[str, Any]
Okuyucu = Callable[[str], bytes]
Bekleyici = Callable[[float], None]


class FeedHatasi(Exception):
    """Bir feed indirilemedi veya RSS olarak ayristirilamadi."""


def log(mesaj: str) -> None:
    zaman = datetime.now().astimezone().isoformat(timespec="seconds")
    print(f"[{zaman}] {mesaj}", file=sys.stderr)


# --- Metin temizleme -------------------------------------------------------

class _MetinCikarici(HTMLParser):
    ATLANAN = {"script", "style"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parcalar: List[str] = []
        self._atla = 0

    def handle_starttag(self, tag: str, attrs: List[Any]) -> None:
        if tag in self.ATLANAN:
            self._atla += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in self.ATLANAN and self._atla:
            self._atla -= 1

    def handle_data(self, data: str) -> None:
        if not self._atla:
            self.parcalar.append(data)


def html_temizle(metin: Optional[str]) -> str:
    """HTML etiketlerini, gorselleri ve script/style iceriklerini atar, duz metin dondurur."""
    if not metin:
        return ""
    cikarici = _MetinCikarici()
    cikarici.feed(metin)
    cikarici.close()
    return re.sub(r"\s+", " ", " ".join(cikarici.parcalar)).strip()


def tarih_ayristir(metin: Optional[str]) -> Optional[datetime]:
    """RSS pubDate (RFC 822) degerini saat dilimli datetime'a cevirir."""
    if not metin:
        return None
    try:
        sonuc = parsedate_to_datetime(metin.strip())
    except (TypeError, ValueError, IndexError):
        return None
    if sonuc is None or sonuc.tzinfo is None:
        return None
    return sonuc


def link_normalize(link: str) -> str:
    """Karsilastirma icin linki sadelestirir: utm_* parametreleri, parca ve sondaki / atilir."""
    parca = urllib.parse.urlsplit(link.strip())
    sorgu = [
        (k, v)
        for k, v in urllib.parse.parse_qsl(parca.query, keep_blank_values=True)
        if not k.lower().startswith("utm_")
    ]
    yol = parca.path.rstrip("/") or "/"
    return urllib.parse.urlunsplit(
        (parca.scheme.lower(), parca.netloc.lower(), yol, urllib.parse.urlencode(sorgu), "")
    )


# --- RSS okuma --------------------------------------------------------------

def rss_ayristir(icerik: bytes, kaynak: str, simdi: datetime) -> List[Haber]:
    """RSS 2.0 icerigini haber listesine cevirir. Basligi olmayan ogeler atlanir."""
    # RSS'in DTD'ye ihtiyaci yok. DOCTYPE/ENTITY iceren icerik reddedilir; boylece
    # defusedxml olmadan XXE ve "billion laughs" saldirilari engellenir.
    if re.search(rb"<!\s*(DOCTYPE|ENTITY)", icerik[:AZAMI_BOYUT], re.IGNORECASE):
        raise FeedHatasi("DOCTYPE/ENTITY iceren XML kabul edilmiyor")
    try:
        kok = ET.fromstring(icerik)
    except ET.ParseError as hata:
        raise FeedHatasi(f"XML ayristirilamadi: {hata}") from hata
    if kok.tag != "rss" or kok.find("channel") is None:
        raise FeedHatasi(f"RSS 2.0 degil (kok etiket: {kok.tag})")

    haberler: List[Haber] = []
    for oge in kok.iterfind("./channel/item"):
        baslik = html_temizle(oge.findtext("title"))
        link = (oge.findtext("link") or "").strip()
        if not link:
            guid = (oge.findtext("guid") or "").strip()
            if guid.startswith(("http://", "https://")):
                link = guid
        if not baslik or not link:
            continue
        yayin = tarih_ayristir(oge.findtext("pubDate"))
        ozet = html_temizle(oge.findtext("description"))[:OZET_AZAMI_KARAKTER]
        haberler.append(
            {
                "kaynak": kaynak,
                "baslik": baslik,
                "link": link_normalize(link),
                "yayin_tarihi": yayin.isoformat() if yayin else None,
                "ozet": ozet,
                "toplanma_zamani": simdi.isoformat(timespec="seconds"),
            }
        )
    return haberler


def http_oku(url: str) -> bytes:
    istek = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(istek, timeout=ZAMAN_ASIMI_SN) as yanit:
        icerik = yanit.read(AZAMI_BOYUT + 1)
    if len(icerik) > AZAMI_BOYUT:
        raise FeedHatasi(f"yanit {AZAMI_BOYUT} bayttan buyuk")
    return icerik


def feed_oku(
    feed: Dict[str, str],
    simdi: datetime,
    okuyucu: Okuyucu = http_oku,
    bekle: Bekleyici = time.sleep,
) -> List[Haber]:
    """Feed'i okur. Hata olursa (bot korumasi dahil) 20 sn bekleyip 1 kez daha dener."""
    son_hata: Optional[Exception] = None
    for deneme in range(2):
        if deneme:
            log(f"{feed['ad']}: {son_hata}; {YENIDEN_DENEME_BEKLEMESI_SN} sn sonra yeniden deneniyor")
            bekle(YENIDEN_DENEME_BEKLEMESI_SN)
        try:
            return rss_ayristir(okuyucu(feed["url"]), feed["ad"], simdi)
        except (urllib.error.URLError, OSError, FeedHatasi) as hata:
            son_hata = hata
    raise FeedHatasi(f"2 denemede okunamadi: {son_hata}")


# --- Dosya islemleri ----------------------------------------------------------

def feedleri_yukle(kok: Path) -> List[Dict[str, str]]:
    veri = json.loads((kok / "feeds.json").read_text(encoding="utf-8"))
    feedler = veri["feeds"]
    for feed in feedler:
        if not str(feed.get("url", "")).startswith(("http://", "https://")) or not feed.get("ad"):
            raise ValueError(f"feeds.json'da gecersiz kayit: {feed}")
    return feedler


def json_yaz(yol: Path, veri: Any) -> None:
    """Yarim kalmis dosya olusmasin diye once gecici dosyaya yazar, sonra yerine tasir."""
    yol.parent.mkdir(parents=True, exist_ok=True)
    fd, gecici = tempfile.mkstemp(dir=yol.parent, prefix=".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as dosya:
            json.dump(veri, dosya, ensure_ascii=False, indent=2)
        os.replace(gecici, yol)
    except BaseException:
        os.unlink(gecici)
        raise


def eski_dosyalari_sil(veri_dizini: Path, bugun: date, gun: int = SAKLAMA_GUN) -> List[str]:
    """Adi YYYY-MM-DD.json olan ve `gun` gunden eski dosyalari siler. Diger dosyalara dokunmaz."""
    silinenler: List[str] = []
    if not veri_dizini.is_dir():
        return silinenler
    for yol in sorted(veri_dizini.iterdir()):
        eslesme = VERI_DOSYA_DESENI.match(yol.name)
        if not eslesme or not yol.is_file():
            continue
        try:
            dosya_tarihi = date.fromisoformat(eslesme.group(1))
        except ValueError:
            continue
        if (bugun - dosya_tarihi).days > gun:
            yol.unlink()
            silinenler.append(yol.name)
    return silinenler


def topla(
    kok: Path,
    simdi: datetime,
    okuyucu: Okuyucu = http_oku,
    bekle: Bekleyici = time.sleep,
) -> Path:
    """Tum feed'leri okur, gunun dosyasina ekler (ayni gun tekrar calisirsa birlestirir)."""
    veri_dizini = kok / "veri"
    hedef = veri_dizini / f"{simdi.date().isoformat()}.json"

    mevcut: List[Haber] = []
    if hedef.exists():
        mevcut = json.loads(hedef.read_text(encoding="utf-8")).get("haberler", [])
    gorulen = {h["link"] for h in mevcut}

    kaynaklar: List[Dict[str, Any]] = []
    for feed in feedleri_yukle(kok):
        try:
            haberler = feed_oku(feed, simdi, okuyucu=okuyucu, bekle=bekle)
        except FeedHatasi as hata:
            log(f"{feed['ad']}: okunamadi ({hata})")
            kaynaklar.append({"ad": feed["ad"], "url": feed["url"], "durum": "okunamadi",
                              "haber_sayisi": 0, "hata": str(hata)})
            continue
        yeni = [h for h in haberler if h["link"] not in gorulen]
        gorulen.update(h["link"] for h in yeni)
        mevcut.extend(yeni)
        log(f"{feed['ad']}: {len(haberler)} haber okundu, {len(yeni)} yeni")
        kaynaklar.append({"ad": feed["ad"], "url": feed["url"], "durum": "okundu",
                          "haber_sayisi": len(haberler), "hata": None})

    json_yaz(hedef, {
        "tarih": simdi.date().isoformat(),
        "toplama_zamani": simdi.isoformat(timespec="seconds"),
        "kaynaklar": kaynaklar,
        "haberler": mevcut,
    })
    silinenler = eski_dosyalari_sil(veri_dizini, simdi.date())
    if silinenler:
        log(f"eski veri silindi: {', '.join(silinenler)}")
    return hedef


# --- Tekrar tespiti ve analiz girdisi -----------------------------------------

def _kelimeler(baslik: str) -> List[str]:
    # Turkce buyuk I/İ harfleri str.lower() ile dogru kuculmez, once elle cevrilir.
    kucuk = baslik.replace("I", "ı").replace("İ", "i").lower()
    return re.findall(r"\w+", kucuk)


def ayni_haber_mi(baslik_a: str, baslik_b: str) -> bool:
    """Iki basligin ayni haberi anlatip anlatmadigini yaklasik olarak belirler."""
    a, b = _kelimeler(baslik_a), _kelimeler(baslik_b)
    if not a or not b:
        return False
    kume_a, kume_b = set(a), set(b)
    ortak_oran = len(kume_a & kume_b) / len(kume_a | kume_b)
    if ortak_oran == 0:
        return False
    benzerlik = difflib.SequenceMatcher(None, " ".join(a), " ".join(b)).ratio()
    return benzerlik >= BENZERLIK_KESIN or (
        benzerlik >= BENZERLIK_YAPI and ortak_oran >= ORTAK_KELIME_ORANI
    )


def _haber_zamani(haber: Haber) -> Optional[datetime]:
    deger = haber.get("yayin_tarihi") or haber.get("toplanma_zamani")
    if not deger:
        return None
    try:
        return datetime.fromisoformat(deger)
    except ValueError:
        return None


def haberleri_grupla(haberler: List[Haber]) -> List[Dict[str, Any]]:
    """Ayni linkli ve ayni haberi anlatan (benzer baslikli) ogeleri tek grupta birlestirir."""
    gruplar: List[Dict[str, Any]] = []
    link_grubu: Dict[str, Dict[str, Any]] = {}

    for haber in haberler:
        grup = link_grubu.get(haber["link"])
        if grup is None:
            grup = next(
                (g for g in gruplar if any(ayni_haber_mi(haber["baslik"], k["baslik"]) for k in g["kaynaklar"])),
                None,
            )
        if grup is None:
            grup = {"baslik": haber["baslik"], "ozet": "", "ilk_yayin": None, "kaynaklar": []}
            gruplar.append(grup)

        if haber["link"] not in link_grubu:
            grup["kaynaklar"].append({"kaynak": haber["kaynak"], "baslik": haber["baslik"],
                                      "link": haber["link"], "yayin_tarihi": haber["yayin_tarihi"]})
            link_grubu[haber["link"]] = grup
        if len(haber.get("ozet") or "") > len(grup["ozet"]):
            grup["ozet"] = haber["ozet"]
        yayin = haber.get("yayin_tarihi")
        if yayin and (grup["ilk_yayin"] is None
                      or datetime.fromisoformat(yayin) < datetime.fromisoformat(grup["ilk_yayin"])):
            grup["ilk_yayin"] = yayin
    # Claude rapor maddelerinde haberlere bu kimlikle atif yapar; link ve kaynak bilgisini
    # rapor.py bu kimlikten doldurur, boylece Claude link yazmaz ve uyduramaz.
    return [{"id": f"h{sira}", **grup} for sira, grup in enumerate(gruplar, start=1)]


def tarama_kapsami(icerik: Dict[str, Any]) -> Dict[str, Any]:
    """Bir veri dosyasindaki her kaynagin durumunu ve haberlerinin gercekte kapsadigi
    yayin araligini dondurur. RSS yalnizca son N haberi verdigi icin bu aralik
    raporda acikca gosterilir."""
    zamanlar: Dict[str, List[datetime]] = {}
    for haber in icerik.get("haberler", []):
        yayin = haber.get("yayin_tarihi")
        zamanlar.setdefault(haber["kaynak"], [])
        if yayin:
            zamanlar[haber["kaynak"]].append(datetime.fromisoformat(yayin))

    kaynaklar: List[Dict[str, Any]] = []
    for kaynak in icerik.get("kaynaklar", []):
        liste = zamanlar.get(kaynak["ad"], [])
        kaynaklar.append({
            "ad": kaynak["ad"],
            "durum": kaynak.get("durum"),
            "hata": kaynak.get("hata"),
            "haber_sayisi": sum(1 for h in icerik.get("haberler", []) if h["kaynak"] == kaynak["ad"]),
            "en_eski": min(liste).isoformat() if liste else None,
            "en_yeni": max(liste).isoformat() if liste else None,
        })
    return {"tarama": icerik.get("toplama_zamani"), "kaynaklar": kaynaklar}


def analiz_girdisi(
    veri_dizini: Path,
    simdi: datetime,
    saat: int = 48,
    etkin_kaynaklar: Optional[Set[str]] = None,
) -> Dict[str, Any]:
    """Son `saat` saatin haberlerini tekrarlardan arindirip analiz girdisi olarak dondurur.

    `etkin_kaynaklar` verilirse yalnizca bu kaynaklarin haberleri ve kapsam bilgisi kullanilir;
    boylece feeds.json'dan cikarilan bir kaynagin eski verisi rapora girmez."""
    def etkin(ad: str) -> bool:
        return etkin_kaynaklar is None or ad in etkin_kaynaklar

    esik = simdi - timedelta(hours=saat)
    ust_sinir = simdi + timedelta(hours=1)
    dosyalar = sorted(
        p for p in veri_dizini.glob("*.json") if VERI_DOSYA_DESENI.match(p.name)
    ) if veri_dizini.is_dir() else []

    haberler: List[Haber] = []
    kapsam: List[Dict[str, Any]] = []
    for yol in dosyalar:
        icerik = json.loads(yol.read_text(encoding="utf-8"))
        tarama = icerik.get("toplama_zamani")
        if tarama and esik <= datetime.fromisoformat(tarama) <= ust_sinir:
            tarama_bilgisi = tarama_kapsami(icerik)
            tarama_bilgisi["kaynaklar"] = [k for k in tarama_bilgisi["kaynaklar"] if etkin(k["ad"])]
            kapsam.append(tarama_bilgisi)
        for haber in icerik.get("haberler", []):
            if not etkin(haber["kaynak"]):
                continue
            zaman = _haber_zamani(haber)
            if zaman is not None and esik <= zaman <= ust_sinir:
                haberler.append(haber)

    haberler.sort(key=lambda h: _haber_zamani(h) or esik)
    gruplar = haberleri_grupla(haberler)
    return {
        "olusturma_zamani": simdi.isoformat(timespec="seconds"),
        "pencere_saat": saat,
        "pencere_baslangic": esik.isoformat(timespec="seconds"),
        "kapsam": kapsam,
        "haber_sayisi": len(haberler),
        "grup_sayisi": len(gruplar),
        "haberler": gruplar,
    }


def girdi_json(girdi: Dict[str, Any]) -> str:
    """Analiz girdisini JSON metnine cevirir. '<' karakteri \\u003c olarak kodlanir; boylece
    haber metni, Claude'a verilen <haberler> etiketinin sinirini kiramaz."""
    return json.dumps(girdi, ensure_ascii=False, indent=1).replace("<", "\\u003c")


# --- Komut satiri -------------------------------------------------------------

def main(argv: Optional[List[str]] = None) -> int:
    ayristirici = argparse.ArgumentParser(description="nobetci veri toplayici")
    alt = ayristirici.add_subparsers(dest="komut", required=True)
    alt.add_parser("topla", help="feed'leri oku ve gunun veri dosyasina yaz")
    girdi = alt.add_parser("girdi", help="analiz girdisini stdout'a yaz")
    girdi.add_argument("--saat", type=int, default=48)
    args = ayristirici.parse_args(argv)

    simdi = datetime.now().astimezone()
    if args.komut == "topla":
        hedef = topla(KOK, simdi)
        log(f"yazildi: {hedef.relative_to(KOK)}")
    else:
        etkin_kaynaklar = {feed["ad"] for feed in feedleri_yukle(KOK)}
        girdi = analiz_girdisi(KOK / "veri", simdi, args.saat, etkin_kaynaklar)
        sys.stdout.write(girdi_json(girdi) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
