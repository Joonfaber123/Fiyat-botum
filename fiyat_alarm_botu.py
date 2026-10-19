#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import time
import logging
from datetime import datetime
import requests

# SENİN TELEGRAM BİLGİLERİN
TELEGRAM_BOT_TOKEN = "8807046980:AAFej_lNLoQJD5WxwGkzgxCxYkBWp7pvkB4"
TELEGRAM_CHAT_ID = "5393404201"

KONTROL_ARALIGI_SN = 15  # 15 saniyede bir kontrol

ALARMLAR = []

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("alarm-bot")

HTTP_TIMEOUT = 10
session = requests.Session()
session.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
son_update_id = 0


def kaynak_belirle(sembol):
    s = sembol.upper()
    if "=F" in s or s.startswith("^") or "=X" in s:
        return "yahoo"
    # Genel Forex çiftleri
    forex_listesi = ["USDJPY", "EURUSD", "GBPUSD", "USDCHF", "AUDUSD", "USDCAD", "NZDUSD"]
    if s in forex_listesi:
        return "yahoo_forex"
    return "binance"


def binance_fiyat(sembol):
    r = session.get(
        "https://api.binance.com/api/v3/ticker/price",
        params={"symbol": sembol.upper()},
        timeout=HTTP_TIMEOUT,
    )
    r.raise_for_status()
    return float(r.json()["price"])


def yahoo_fiyat(sembol):
    sym = sembol.upper()
    if not sym.endswith("=X") and not sym.endswith("=F") and not sym.startswith("^"):
        sym += "=X"
    r = session.get(
        f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}",
        params={"interval": "1m", "range": "1d"},
        timeout=HTTP_TIMEOUT,
    )
    r.raise_for_status()
    data = r.json()
    meta = data["chart"]["result"][0]["meta"]
    return float(meta["regularMarketPrice"])


def fiyat_al(sembol):
    kaynak = kaynak_belirle(sembol)
    if kaynak == "binance":
        try:
            return binance_fiyat(sembol)
        except Exception:
            # Binance'te bulamazsa Yahoo'da dene
            return yahoo_fiyat(sembol)
    else:
        return yahoo_fiyat(sembol)


def telegram_gonder(mesaj):
    try:
        r = session.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": mesaj},
            timeout=HTTP_TIMEOUT,
        )
        return r.status_code == 200
    except Exception as e:
        log.error("Telegram gonderilemedi: %s", e)
        return False


def telegram_komutlarini_kontrol_et():
    global son_update_id
    try:
        r = session.get(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates",
            params={"offset": son_update_id + 1, "timeout": 1},
            timeout=HTTP_TIMEOUT,
        )
        if r.status_code != 200:
            return
        
        for update in r.json().get("result", []):
            son_update_id = update["update_id"]
            mesaj_obj = update.get("message", {})
            chat_id = str(mesaj_obj.get("chat", {}).get("id", ""))
            metin = mesaj_obj.get("text", "").strip()

            if chat_id != str(TELEGRAM_CHAT_ID) or not metin:
                continue

            parcalar = metin.split()
            komut = parcalar[0].lower()

            if komut in ["/start", "/yardim"]:
                telegram_gonder(
                    "🤖 Komutlar:\n\n"
                    "➕ Alarm Ekle:\n/ekle SEMBOL FIYAT YON\n(Örn: /ekle BTCUSDT 95000 yukari)\n(Örn: /ekle USDJPY 154.50 asagi)\n(Örn: /ekle NQ=F 21500 yukari)\n\n"
                    "📋 Aktif Alarmlar:\n/liste\n\n"
                    "💰 Anlık Fiyat Sor:\n/fiyat USDJPY\n\n"
                    "🗑️ Tümünü Sil:\n/temizle"
                )
            elif komut == "/liste":
                aktifler = [a for a in ALARMLAR if a.get("aktif")]
                if not aktifler:
                    telegram_gonder("📭 Şu an aktif alarm yok.")
                else:
                    liste_metni = "📋 Aktif Alarmlar:\n"
                    for i, a in enumerate(aktifler, 1):
                        liste_metni += f"{i}. {a['sembol']} -> {a['hedef']} ({a['yon']})\n"
                    telegram_gonder(liste_metni)

            elif komut == "/temizle":
                for a in ALARMLAR:
                    a["aktif"] = False
                telegram_gonder("🗑️ Tüm aktif alarmlar silindi.")

            elif komut == "/fiyat" and len(parcalar) >= 2:
                sembol = parcalar[1].upper()
                try:
                    f = fiyat_al(sembol)
                    telegram_gonder(f"💰 {sembol} Güncel Fiyat: {f}")
                except Exception as e:
                    telegram_gonder(f"❌ {sembol} fiyatı alınamadı.")

            elif komut == "/ekle":
                if len(parcalar) < 4:
                    telegram_gonder("⚠️ Eksik format! Örnek:\n/ekle USDJPY 154.50 asagi")
                    continue
                sembol = parcalar[1].upper()
                try:
                    hedef = float(parcalar[2].replace(",", "."))
                except ValueError:
                    telegram_gonder("⚠️ Hedef fiyat sayı olmalı!")
                    continue
                yon = parcalar[3].lower()
                if yon not in ["yukari", "asagi"]:
                    telegram_gonder("⚠️ Yön 'yukari' veya 'asagi' olmalı!")
                    continue

                ALARMLAR.append({"sembol": sembol, "hedef": hedef, "yon": yon, "aktif": True})
                telegram_gonder(f"✅ Alarm kuruldu!\n{sembol} -> {hedef} ({yon})")

    except Exception as e:
        log.warning("Komut kontrol hatasi: %s", e)


def tur_kontrol():
    aktifler = [a for a in ALARMLAR if a.get("aktif")]
    if not aktifler:
        return

    fiyatlar = {}
    for alarm in aktifler:
        s = alarm["sembol"]
        if s not in fiyatlar:
            try:
                fiyatlar[s] = fiyat_al(s)
            except Exception as e:
                log.warning("%s fiyati alinamadi: %s", s, e)
                fiyatlar[s] = None

    for alarm in aktifler:
        fiyat = fiyatlar.get(alarm["sembol"])
        if fiyat is None:
            continue

        log.info("%s = %s (hedef %s %s)", alarm["sembol"], fiyat, alarm["hedef"], alarm["yon"])

        tetiklendi = (alarm["yon"] == "yukari" and fiyat >= alarm["hedef"]) or \
                     (alarm["yon"] == "asagi" and fiyat <= alarm["hedef"])

        if tetiklendi:
            yon_yazi = "YUKARI Kırılım 📈" if alarm["yon"] == "yukari" else "AŞAĞI Kırılım 📉"
            mesaj = (
                f"🚨 ALARM: {alarm['sembol']}\n"
                f"{yon_yazi}\n"
                f"Hedef: {alarm['hedef']}\n"
                f"Güncel Fiyat: {fiyat}\n"
                f"Saat: {datetime.now().strftime('%H:%M:%S')}"
            )
            if telegram_gonder(mesaj):
                alarm["aktif"] = False


def main():
    log.info("Bot basladi.")
    telegram_gonder("✅ Bot güncellendi! Artık USDJPY, EURUSD gibi pariteleri direkt yazabilirsin.")

    while True:
        telegram_komutlarini_kontrol_et()
        tur_kontrol()
        time.sleep(KONTROL_ARALIGI_SN)


if __name__ == "__main__":
    main()