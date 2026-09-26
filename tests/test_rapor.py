"""rapor.py testleri: Claude cikisinin dogrulanmasi ve guvenli HTML uretimi."""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from typing import Any, Dict

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import rapor  # noqa: E402


def ornek_girdi() -> Dict[str, Any]:
    return {
        "olusturma_zamani": "2026-09-26T07:00:00+03:00",
        "pencere_saat": 48,
        "pencere_baslangic": "2026-09-24T07:00:00+03:00",
        "kapsam": [
            {"tarama": "2026-09-26T07:00:00+03:00", "kaynaklar": [
                {"ad": "Dünya", "durum": "okundu", "hata": None, "haber_sayisi": 25,
                 "en_eski": "2026-09-26T00:00:00+03:00", "en_yeni": "2026-09-26T06:50:00+03:00"},
                {"ad": "Ekonomim", "durum": "okunamadi", "hata": "HTTP Error 403: Forbidden",
                 "haber_sayisi": 0, "en_eski": None, "en_yeni": None},
            ]},
        ],
        "haber_sayisi": 2,
        "grup_sayisi": 2,
        "haberler": [
            {"id": "h1", "baslik": "Kahve fiyatları", "ozet": "", "ilk_yayin": "2026-09-26T01:45:00+03:00",
             "kaynaklar": [{"kaynak": "Dünya", "baslik": "Kahve fiyatları", "link": "https://dunya.com/k",
                            "yayin_tarihi": "2026-09-26T01:45:00+03:00"}]},
            {"id": "h2", "baslik": "Kötü link", "ozet": "", "ilk_yayin": None,
             "kaynaklar": [{"kaynak": "Dünya", "baslik": "Kötü link", "link": "javascript:alert(1)",
                            "yayin_tarihi": None}]},
        ],
    }


def ornek_analiz(**degisiklik: Any) -> Dict[str, Any]:
    analiz: Dict[str, Any] = {
        "ozet": "Kısa özet.",
        "veri_notlari": [],
        "marka_anilmalari": [],
        "riskler": [{"baslik": "Fiyat baskısı", "haberler": ["h1"], "neden_onemli": "Maliyet.",
                     "oneri": "İzleyin.", "dolayli": False}],
        "firsatlar": [],
        "icerik_fikirleri": [{"baslik": "Demleme", "fikir": "Filtre kahve anlatımı.",
                              "kanal": "sosyal medya", "haberler": ["h1"]}],
    }
    analiz.update(degisiklik)
    return analiz


class AnalizAyristirTesti(unittest.TestCase):
    def test_gecerli_json(self) -> None:
        analiz, uyarilar = rapor.analiz_ayristir(json.dumps(ornek_analiz()), ornek_girdi())
        self.assertEqual(analiz["riskler"][0]["haberler"], ["h1"])
        self.assertEqual(uyarilar, [])

    def test_kod_blogu_citi_kabul_edilir(self) -> None:
        metin = "```json\n" + json.dumps(ornek_analiz()) + "\n```\n"
        analiz, _ = rapor.analiz_ayristir(metin, ornek_girdi())
        self.assertEqual(analiz["ozet"], "Kısa özet.")

    def test_gecersiz_json_hata(self) -> None:
        with self.assertRaises(rapor.RaporHatasi):
            rapor.analiz_ayristir("# Nöbetçi Raporu\nmarkdown", ornek_girdi())

    def test_eksik_alan_hata(self) -> None:
        analiz = ornek_analiz()
        del analiz["riskler"]
        with self.assertRaises(rapor.RaporHatasi):
            rapor.analiz_ayristir(json.dumps(analiz), ornek_girdi())

    def test_yanlis_tip_hata(self) -> None:
        with self.assertRaises(rapor.RaporHatasi):
            rapor.analiz_ayristir(json.dumps(ornek_analiz(riskler="yok")), ornek_girdi())

    def test_bilinmeyen_haber_kimligi_atilir_ve_uyari_verilir(self) -> None:
        riskler = [{"baslik": "X", "haberler": ["h1", "h999"], "neden_onemli": "a", "oneri": "b"}]
        analiz, uyarilar = rapor.analiz_ayristir(json.dumps(ornek_analiz(riskler=riskler)), ornek_girdi())
        self.assertEqual(analiz["riskler"][0]["haberler"], ["h1"])
        self.assertFalse(analiz["riskler"][0]["dolayli"])
        self.assertEqual(len(uyarilar), 1)
        self.assertIn("h999", uyarilar[0])

    def test_uzun_tire_temizlenir(self) -> None:
        analiz, _ = rapor.analiz_ayristir(json.dumps(ornek_analiz(ozet="Kur arttı \u2014 maliyet de")), ornek_girdi())
        self.assertEqual(analiz["ozet"], "Kur arttı, maliyet de")

    def test_metindeki_haber_kimlikleri_atilir(self) -> None:
        ozet = "Fitch tahmini (h19) ve BİM kataloğu (h118). Tekrarlar (h22 ve h57) ile (h2, h143)."
        analiz, _ = rapor.analiz_ayristir(json.dumps(ornek_analiz(ozet=ozet)), ornek_girdi())
        self.assertEqual(analiz["ozet"], "Fitch tahmini ve BİM kataloğu. Tekrarlar ile.")
        self.assertEqual(analiz["riskler"][0]["haberler"], ["h1"])


class BoslukTesti(unittest.TestCase):
    def test_kapsanmayan_araliklar(self) -> None:
        bosluklar = rapor.taranmayan_araliklar(ornek_girdi())
        self.assertEqual([(b.isoformat(), s.isoformat()) for b, s in bosluklar],
                         [("2026-09-24T07:00:00+03:00", "2026-09-26T00:00:00+03:00")])

    def test_birlesen_araliklar(self) -> None:
        girdi = ornek_girdi()
        girdi["kapsam"].insert(0, {"tarama": "2026-09-25T07:00:00+03:00", "kaynaklar": [
            {"ad": "Dünya", "durum": "okundu", "hata": None, "haber_sayisi": 25,
             "en_eski": "2026-09-24T07:00:00+03:00", "en_yeni": "2026-09-25T06:00:00+03:00"}]})
        bosluklar = rapor.taranmayan_araliklar(girdi)
        self.assertEqual([(b.isoformat(), s.isoformat()) for b, s in bosluklar],
                         [("2026-09-25T07:00:00+03:00", "2026-09-26T00:00:00+03:00")])


class HtmlTesti(unittest.TestCase):
    def _html(self, **degisiklik: Any) -> str:
        analiz, uyarilar = rapor.analiz_ayristir(json.dumps(ornek_analiz(**degisiklik)), ornek_girdi())
        return rapor.html_uret(ornek_girdi(), analiz, uyarilar)

    def test_temel_yapi(self) -> None:
        html = self._html()
        self.assertTrue(html.startswith("<!doctype html>"))
        self.assertIn("Nöbetçi Raporu", html)
        self.assertIn("26.09.2026", html)
        self.assertIn('href="https://dunya.com/k"', html)
        self.assertIn("Fiyat baskısı", html)
        self.assertIn("HTTP Error 403: Forbidden", html)
        self.assertIn("Content-Security-Policy", html)

    def test_metin_kacislanir(self) -> None:
        html = self._html(ozet="<script>alert(1)</script>", riskler=[
            {"baslik": '<img src=x onerror="a()">', "haberler": ["h1"], "neden_onemli": "a", "oneri": "b"}])
        self.assertNotIn("<script>alert", html)
        self.assertNotIn("<img src=x", html)
        self.assertIn("&lt;script&gt;", html)

    def test_http_disi_link_baglanti_olmaz(self) -> None:
        html = self._html(riskler=[{"baslik": "X", "haberler": ["h2"], "neden_onemli": "a", "oneri": "b"}])
        self.assertNotIn("javascript:", html)

    def test_bos_bolum_bulgu_yok_yazar(self) -> None:
        html = self._html()
        self.assertIn("Bu bölüm için bulgu yok.", html)

    def test_uyarilar_gosterilir(self) -> None:
        riskler = [{"baslik": "X", "haberler": ["h404"], "neden_onemli": "a", "oneri": "b"}]
        html = self._html(riskler=riskler)
        self.assertIn("h404", html)


if __name__ == "__main__":
    unittest.main()
