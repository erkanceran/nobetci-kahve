#!/bin/bash
# nobetci gunluk calistirma: feed'leri topla, analiz girdisini hazirla, Claude ile analiz et,
# rapor.py ile dogrulayip rapor/YYYY-MM-DD.html ve .json olarak yaz.
# launchd (com.nobetci.gunluk.plist) her sabah 07:00'de calistirir. Elle de calistirilabilir.
set -euo pipefail

KOK="$(cd "$(dirname "$0")" && pwd)"
CLAUDE="$HOME/.local/bin/claude"
if [[ -x /opt/homebrew/bin/python3 ]]; then
  PYTHON=/opt/homebrew/bin/python3
else
  PYTHON=/usr/bin/python3
fi

TARIH="$(date +%F)"
mkdir -p "$KOK/log" "$KOK/rapor"
exec >>"$KOK/log/$TARIH.log" 2>&1

log() { echo "[$(date '+%Y-%m-%dT%H:%M:%S%z')] $*"; }

GECICI="$(mktemp -d)"
trap 'rm -rf "$GECICI"' EXIT
trap 'log "HATA: satir $LINENO, cikis kodu $?"' ERR

log "basladi (python: $PYTHON)"

"$PYTHON" "$KOK/toplayici.py" topla

"$PYTHON" "$KOK/toplayici.py" girdi >"$GECICI/girdi.json"
{
  printf '<marka>\n'
  cat "$KOK/BRAND.md"
  printf '</marka>\n\n<haberler>\n'
  cat "$GECICI/girdi.json"
  printf '</haberler>\n'
} >"$GECICI/istem.txt"
log "analiz girdisi hazir ($(wc -c <"$GECICI/istem.txt" | tr -d ' ') bayt)"

# Harici agent kurali: bos calisma dizini, hicbir arac yok, tek tur, cikti once gecici alana.
mkdir "$GECICI/bos"
(
  cd "$GECICI/bos"
  "$CLAUDE" -p \
    --tools "" \
    --strict-mcp-config \
    --setting-sources "" \
    --disable-slash-commands \
    --no-session-persistence \
    --system-prompt "$(cat "$KOK/analiz_talimati.md")" \
    <"$GECICI/istem.txt" >"$GECICI/analiz.txt"
)

AD="$TARIH"
if [[ -e "$KOK/rapor/$AD.html" ]]; then
  AD="$TARIH-$(date +%H%M)"
fi

# rapor.py Claude cikisini dogrular; semaya uymazsa rapor yazilmaz, cikis log/'a kaydedilir.
if ! "$PYTHON" "$KOK/rapor.py" \
  --girdi "$GECICI/girdi.json" \
  --analiz "$GECICI/analiz.txt" \
  --html "$GECICI/rapor.html" \
  --json "$GECICI/rapor.json"; then
  cp "$GECICI/analiz.txt" "$KOK/log/$AD-gecersiz-analiz.txt"
  log "HATA: Claude cikisi dogrulanamadi, log/$AD-gecersiz-analiz.txt dosyasina kaydedildi"
  exit 1
fi

mv "$GECICI/rapor.json" "$KOK/rapor/$AD.json"
mv "$GECICI/rapor.html" "$KOK/rapor/$AD.html"
log "rapor yazildi: rapor/$AD.html"

# Loglar 30 gun saklanir.
find "$KOK/log" -type f \( -name '*.log' -o -name '*-gecersiz-analiz.txt' \) -mtime +30 -delete
log "bitti"
