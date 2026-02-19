"""
REFURBED PROFIT BOT v3
======================
- Email-Parser: Final-Werte (inkl. negative)
- Chrome CDP Scraping: Commission, Discount, Refunds
- Retouren-Kosten: DE 5 EUR, Rest 9 EUR
- Netto-Gewinn Berechnung
- Zendesk Nachrichten-Scraping per Order-ID
- Discord Report
Usage:
  python bot_v3.py                  # Normal (neue Orders)
  python bot_v3.py --full-month     # Alle Orders (seit 01. des Monats)
  python bot_v3.py --returns        # Nur Retouren-Analyse
  python bot_v3.py --returns --zendesk  # Retouren + Zendesk Nachrichten
  python bot_v3.py --test-email     # Email-Test
"""
import json, re, time, os, sys, imaplib, email
sys.stdout.reconfigure(line_buffering=True)  # Flush nach jeder Zeile
from datetime import datetime, timedelta
from pathlib import Path
from playwright.sync_api import sync_playwright
import requests
from dotenv import load_dotenv
from collections import defaultdict
# ============================================
# KONFIGURATION
# ============================================
load_dotenv()
CONFIG = {
    "email_server": os.getenv("EMAIL_SERVER", "imap.handysparkauf.de"),
    "email_address": os.getenv("EMAIL_ADDRESS"),
    "email_password": os.getenv("EMAIL_PASSWORD"),
    "discord_webhook": os.getenv("DISCORD_WEBHOOK"),
    "zendesk_domain": os.getenv("ZENDESK_DOMAIN", "refurbed-merchant.zendesk.com"),
    "pauschal_dynamic": 20,
}
BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)
ORDERS_FILE = DATA_DIR / "orders.json"
RETURNS_FILE = DATA_DIR / "returns.json"
RETURN_COST = {"Deutschland": 5.0, "default": 9.0}
# ============================================
# EMAIL PARSER
# ============================================
def parse_sale_email(body):
    try:
        final_match = re.search(r'Final:\s*(-?[\d,\.]+)', body)
        if not final_match:
            return None
        final = float(final_match.group(1).replace(',', '.'))
        ek_match = re.search(r'Gesamt Einkauf:\s*([\d,\.]+)', body)
        ek = float(ek_match.group(1).replace(',', '.')) if ek_match else 0
        brutto_match = re.search(r'Gesamt Brutto:\s*([\d,\.]+)', body)
        brutto = float(brutto_match.group(1).replace(',', '.')) if brutto_match else 0
        order_match = re.search(r'merchant\.refurbed\.com/orders/details/(\d+)', body)
        order_id = order_match.group(1) if order_match else None
        if not order_id:
            return None
        lines = [l.strip() for l in body.strip().split('\n') if l.strip()]
        produkt, land, datum = "", "", ""
        for i, line in enumerate(lines):
            if re.match(r'\d{2}\.\d{2}\.\d{4}\s+\d{2}:\d{2}', line):
                datum = line.strip()
                if i + 1 < len(lines):
                    produkt = lines[i + 1]
            if re.match(r'Aut-\d+', line) and i + 1 < len(lines):
                land = lines[i + 1]
        return {
            "order_id": order_id, "final_email": final, "ek": ek,
            "brutto": brutto, "produkt": produkt, "land": land, "datum": datum,
        }
    except Exception as e:
        return None
def fetch_emails(since_days=10, since_date=None):
    print("[EMAIL] Verbinde mit Email-Server...")
    orders = []
    try:
        mail = imaplib.IMAP4_SSL(CONFIG["email_server"], 993)
        mail.login(CONFIG["email_address"], CONFIG["email_password"])
        mail.select("INBOX")
        if since_date:
            since = since_date
        else:
            since = (datetime.now() - timedelta(days=since_days)).strftime("%d-%b-%Y")
        _, ids = mail.search(None, f'(SINCE "{since}")')
        ids = ids[0].split()
        print(f"[EMAIL] {len(ids)} Emails gefunden")
        for mid in ids:
            try:
                _, data = mail.fetch(mid, "(RFC822)")
                msg = email.message_from_bytes(data[0][1])
                # Alle Emails parsen - kein Subject-Filter noetig
                # parse_sale_email filtert selbst nach Order-ID
                body = ""
                if msg.is_multipart():
                    for part in msg.walk():
                        if part.get_content_type() == "text/plain":
                            body = part.get_payload(decode=True).decode("utf-8", errors="ignore")
                            break
                else:
                    body = msg.get_payload(decode=True).decode("utf-8", errors="ignore")
                if body:
                    o = parse_sale_email(body)
                    if o:
                        orders.append(o)
            except:
                pass
        mail.logout()
        print(f"[EMAIL] {len(orders)} Orders geparsed")
    except Exception as e:
        print(f"[EMAIL] Fehler: {e}")
    return orders
# ============================================
# PORTAL SCRAPER - MIT TOTAL_REFUNDED
# ============================================
EXTRACT_JS = """() => {
    const tables = document.querySelectorAll('table');
    const result = {
        dynamic_commission: 0, discount: 0, total_commission: 0,
        total_charged: 0, total_paid: 0, total_refunded: 0
    };
    const p = s => parseFloat((s||'0').replace(',','.').replace(/ (EUR|SEK|DKK|NOK|CZK|PLN)/g,'')) || 0;
    for (const table of tables) {
        const text = table.textContent;
        const rows = table.querySelectorAll('tr');
        const d = {};
        rows.forEach(r => {
            const c = r.querySelectorAll('td');
            if (c.length >= 2) d[c[0].textContent.trim()] = c[c.length-1].textContent.trim();
        });
        // Payments-Tabelle
        if (text.includes('Total charged') && text.includes('Discount') && !text.includes('Base Commission')) {
            result.discount = p(d['Discount']);
            result.total_charged = p(d['Total charged']) || p(d['Total Charged']);
            result.total_paid = p(d['Total paid']) || p(d['Total Paid']);
            result.total_refunded = p(d['Total refunded']) || p(d['Total Refunded']) || 0;
        }
        // Commission-Tabelle
        if (text.includes('Dynamic Commission') && text.includes('Total Commission')) {
            result.dynamic_commission = p(d['Dynamic Commission']);
            result.total_commission = p(d['Total Commission']);
            if (result.discount === 0 && d['Discount']) result.discount = p(d['Discount']);
            if (result.total_refunded === 0) {
                result.total_refunded = p(d['Total Refunded']) || p(d['Total refunded']) || 0;
            }
        }
    }
    return (result.total_commission > 0 || result.discount > 0 || result.total_refunded > 0) ? result : null;
}"""
def scrape_all_orders(order_ids):
    print(f"\n[PORTAL] Scrape {len(order_ids)} Orders...")
    results = {}
    with sync_playwright() as pw:
        try:
            browser = pw.chromium.connect_over_cdp("http://127.0.0.1:9222")
            print("[PORTAL] Mit Chrome verbunden!")
            ctx = browser.contexts[0]
            page = ctx.new_page()
            for i, oid in enumerate(order_ids):
                try:
                    print(f"  [{i+1}/{len(order_ids)}] Order {oid}...", end=" ")
                    page.goto(f"https://merchant.refurbed.com/orders/details/{oid}",
                              wait_until="networkidle", timeout=15000)
                    if "login" in page.url.lower():
                        print("SESSION ABGELAUFEN!")
                        send_discord("Session abgelaufen!")
                        break
                    page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                    try:
                        page.wait_for_selector("text=Dynamic Commission", timeout=10000)
                        page.wait_for_timeout(1000)
                    except:
                        page.wait_for_timeout(5000)
                    data = page.evaluate(EXTRACT_JS)
                    if data:
                        results[oid] = data
                        ref = f" | REFUND:{data['total_refunded']}E" if data['total_refunded'] > 0 else ""
                        print(f"Dyn:{data['dynamic_commission']}E Disc:{data['discount']}E Comm:{data['total_commission']}E{ref}")
                    else:
                        print("Keine Daten")
                        results[oid] = {"dynamic_commission": 0, "discount": 0, "total_commission": 0, "total_refunded": 0}
                    time.sleep(1)
                except Exception as e:
                    print(f"Fehler: {e}")
                    results[oid] = {"dynamic_commission": 0, "discount": 0, "total_commission": 0, "total_refunded": 0}
            page.close()
            browser.close()
        except Exception as e:
            print(f"[PORTAL] Chrome-Fehler: {e}")
    return results
# ============================================
# RETOUREN
# ============================================
def get_return_cost(land):
    return RETURN_COST.get(land, RETURN_COST["default"])
def normalize_product(p):
    p = p.replace('\x00', '').strip()
    m = re.search(r'(iPhone\s*\d+\s*(?:Pro\s*Max|Pro|Plus|mini)?)\s*[\|,]?\s*(\d+\s*(?:GB|TB))', p, re.IGNORECASE)
    if m:
        return f"{m.group(1).strip()} {m.group(2).strip().upper().replace(' ', '')}"
    return p[:50]
def analyze_returns(orders):
    returns = []
    for o in orders:
        if o.get("total_refunded", 0) > 0:
            ship = get_return_cost(o.get("land", ""))
            returns.append({
                "order_id": o["order_id"],
                "produkt": o.get("produkt", ""),
                "model": normalize_product(o.get("produkt", "")),
                "land": o.get("land", ""),
                "real_profit": o.get("real_profit", 0),
                "total_refunded": o["total_refunded"],
                "return_shipping": ship,
                "total_loss": round(o.get("real_profit", 0) + ship, 2),
            })
    lost_profit = sum(r["real_profit"] for r in returns)
    total_shipping = sum(r["return_shipping"] for r in returns)
    brutto = sum(o.get("real_profit", 0) for o in orders)
    netto = brutto - lost_profit - total_shipping
    return {
        "count": len(returns),
        "returns": returns,
        "lost_profit": round(lost_profit, 2),
        "total_shipping": round(total_shipping, 2),
        "total_loss": round(lost_profit + total_shipping, 2),
        "brutto_profit": round(brutto, 2),
        "netto_profit": round(netto, 2),
    }
# ============================================
# ZENDESK NACHRICHTEN SCRAPER
# ============================================
def scrape_zendesk_messages(order_id, page):
    messages = []
    try:
        url = f"https://refurbed-merchant.zendesk.com/agent/search/1?copy&type=ticket&q={order_id}"
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
        time.sleep(6)
        # Finde Ticket-Link und navigiere direkt dahin
        ticket_href = page.evaluate("""() => {
            const links = document.querySelectorAll('a[href*="/agent/tickets/"]');
            for (const l of links) {
                if (l.href && l.href.includes('/agent/tickets/')) return l.href;
            }
            return null;
        }""")
        if not ticket_href:
            return messages
        page.goto(ticket_href, wait_until="domcontentloaded", timeout=20000)
        time.sleep(5)
        msg_data = page.evaluate("""() => {
            const msgs = [];
            const seen = new Set();
            const comments = document.querySelectorAll('[data-test-id*="comment"]');
            comments.forEach(c => {
                const msgEl = c.querySelector('[data-test-id*="message"]');
                if (msgEl && msgEl.textContent.trim().length > 10) {
                    const txt = msgEl.textContent.trim().substring(0, 1000);
                    if (!seen.has(txt)) {
                        seen.add(txt);
                        msgs.push({ text: txt });
                    }
                }
            });
            return msgs;
        }""")
        if msg_data:
            messages = msg_data
    except Exception as e:
        pass
    return messages
def categorize_return(messages):
    if not messages:
        return "Unbekannt"
    text = " ".join(m.get("text", "") for m in messages).lower()
    cats = [
        ("Display/Bildschirm", ["display", "bildschirm", "screen", "kratzer am display", "pixel", "ghost touch", "touch reagiert", "burn-in", "fleck", "streifen"]),
        ("Akku/Batterie", ["akku", "batterie", "battery", "laden", "akkuhealth", "schnell leer", "haelt nicht", "kapazit"]),
        ("Kamera", ["kamera", "camera", "foto", "linse", "verschwommen", "wackelt", "autofokus"]),
        ("Gehaeuse/Optik", ["gehaeuse", "delle", "beule", "rahmen", "rueckseite", "kratzer", "zustand schlechter"]),
        ("Software", ["software", "haengt", "absturz", "neustart", "langsam", "icloud", "sperre", "aktivierung"]),
        ("Lautsprecher/Mikro", ["lautsprecher", "speaker", "mikrofon", "ton", "sound", "leise"]),
        ("Will nicht mehr", ["will nicht", "nicht mehr", "anders entschieden", "doch nicht", "widerruf", "no longer", "changed mind"]),
        ("Falsche Ware", ["falsch", "wrong", "anderes produkt", "andere farbe", "falsches modell"]),
        ("Face ID", ["face id", "faceid", "gesichtserkennung"]),
        ("WLAN/Netz", ["wlan", "wifi", "netz", "signal", "empfang", "sim"]),
        ("Knopfe/Tasten", ["taste", "button", "power button", "lautstaerke"]),
    ]
    for cat, keywords in cats:
        for kw in keywords:
            if kw in text:
                return cat
    return "Sonstiges"
def run_zendesk_analysis(returns):
    if not returns:
        return {}
    print(f"\n[ZENDESK] Scrape Nachrichten fuer {len(returns)} Retouren...")
    results = {}
    with sync_playwright() as pw:
        try:
            browser = pw.chromium.connect_over_cdp("http://127.0.0.1:9222")
            ctx = browser.contexts[0]
            page = ctx.new_page()
            for i, ret in enumerate(returns):
                oid = ret["order_id"]
                print(f"  [{i+1}/{len(returns)}] Zendesk Order {oid}...", end=" ")
                msgs = scrape_zendesk_messages(oid, page)
                if msgs:
                    results[oid] = msgs
                    reason = categorize_return(msgs)
                    ret["return_reason"] = reason
                    ret["zendesk_messages"] = [m["text"][:200] for m in msgs]
                    print(f"{len(msgs)} Nachrichten -> {reason}")
                else:
                    ret["return_reason"] = "Kein Ticket"
                    print("Kein Ticket gefunden")
                time.sleep(1)
            page.close()
            browser.close()
        except Exception as e:
            print(f"[ZENDESK] Fehler: {e}")
    return results
# ============================================
# DISCORD
# ============================================
def send_discord(msg):
    if not CONFIG.get("discord_webhook"):
        return
    try:
        if len(msg) > 1900:
            msg = msg[:1900] + "\n..."
        requests.post(CONFIG["discord_webhook"], json={"content": msg}, timeout=10)
    except:
        pass
# ============================================
# HAUPTABLAUF
# ============================================
def run_report(full_month=False):
    print("=" * 60)
    print(f"REFURBED PROFIT BOT v3 - {datetime.now().strftime('%d.%m.%Y %H:%M')}")
    print("=" * 60)
    existing = []
    existing_ids = set()
    if ORDERS_FILE.exists():
        try:
            existing = json.loads(ORDERS_FILE.read_text(encoding="latin-1"))
            existing_ids = {o["order_id"] for o in existing}
            print(f"[DATEN] {len(existing)} bestehende Orders")
        except:
            pass
    if full_month:
        orders = fetch_emails(since_date="01-Feb-2026")
    else:
        orders = fetch_emails(since_days=10)
    new_orders = [o for o in orders if o["order_id"] not in existing_ids]
    if not new_orders:
        print("Keine NEUEN Orders gefunden.")
        return
    print(f"\n{len(new_orders)} neue Orders")
    portal = scrape_all_orders([o["order_id"] for o in new_orders])
    pauschal = CONFIG["pauschal_dynamic"]
    results = []
    for o in new_orders:
        pd = portal.get(o["order_id"], {})
        dyn = pd.get("dynamic_commission", 0)
        disc = pd.get("discount", 0)
        ref = pd.get("total_refunded", 0)
        diff = pauschal - dyn
        profit = o["final_email"] + diff + disc
        results.append({
            **o,
            "dynamic_real": dyn,
            "dynamic_diff": round(diff, 2),
            "discount": disc,
            "total_commission": pd.get("total_commission", 0),
            "total_refunded": ref,
            "is_returned": ref > 0,
            "return_shipping": get_return_cost(o.get("land", "")) if ref > 0 else 0,
            "real_profit": round(profit, 2),
        })
    all_orders = existing + results
    all_orders.sort(key=lambda x: x.get("real_profit", 0), reverse=True)
    ORDERS_FILE.write_text(json.dumps(all_orders, indent=2, ensure_ascii=False), encoding="latin-1")
    print(f"[DATEN] {len(all_orders)} Orders gespeichert")
    total_real = sum(r["real_profit"] for r in results)
    refunded = [r for r in results if r.get("is_returned")]
    report = f"**PROFIT BOT v3 | {datetime.now().strftime('%d.%m.%Y %H:%M')}**\n"
    report += f"{len(results)} Orders | GEWINN: **{total_real:.2f} EUR**\n"
    if refunded:
        report += f"Retouren: {len(refunded)} erkannt!\n"
    send_discord(report)
    print(f"\nECHTER GEWINN: {total_real:.2f} EUR")
def run_returns_only():
    if not ORDERS_FILE.exists():
        print("Keine orders.json! Erst Bot normal laufen lassen.")
        return
    orders = json.loads(ORDERS_FILE.read_text(encoding="latin-1"))
    print(f"[DATEN] {len(orders)} Orders geladen")
    ret = analyze_returns(orders)
    print(f"\n{'='*60}")
    print(f"RETOUREN-ANALYSE")
    print(f"{'='*60}")
    print(f"  Retouren:           {ret['count']}")
    print(f"  Entgangener Gewinn: {ret['lost_profit']:,.2f} EUR")
    print(f"  Retour-Versand:     {ret['total_shipping']:,.2f} EUR")
    print(f"  TOTAL VERLUST:      {ret['total_loss']:,.2f} EUR")
    print(f"  Brutto-Gewinn:      {ret['brutto_profit']:,.2f} EUR")
    print(f"  NETTO-GEWINN:       {ret['netto_profit']:,.2f} EUR")
    model_rets = defaultdict(list)
    for r in ret["returns"]:
        model_rets[r["model"]].append(r)
    print(f"\n[NACH MODELL]")
    for model, rets in sorted(model_rets.items(), key=lambda x: len(x[1]), reverse=True):
        total = len([o for o in orders if normalize_product(o.get("produkt", "")) == model])
        rate = len(rets) / total * 100 if total > 0 else 0
        loss = sum(r["total_loss"] for r in rets)
        print(f"  {model:<35} {len(rets):>2}/{total:<3} ({rate:4.0f}%) | -{loss:>8,.2f} EUR")
    if "--zendesk" in sys.argv:
        run_zendesk_analysis(ret["returns"])
        reasons = defaultdict(int)
        for r in ret["returns"]:
            reasons[r.get("return_reason", "Unbekannt")] += 1
        print(f"\n[RETOUREN-GRUENDE]")
        for reason, cnt in sorted(reasons.items(), key=lambda x: x[1], reverse=True):
            print(f"  {reason:<25} {cnt:>3}x")
    RETURNS_FILE.write_text(json.dumps(ret, indent=2, ensure_ascii=False, default=str))
    print(f"\n[DATEN] Retouren in {RETURNS_FILE} gespeichert")
    report = f"**RETOUREN-REPORT | {datetime.now().strftime('%d.%m.%Y')}**\n"
    report += f"{ret['count']} Retouren von {len(orders)} Orders\n"
    report += f"Brutto: **{ret['brutto_profit']:,.2f} EUR**\n"
    report += f"Verlust: **-{ret['total_loss']:,.2f} EUR**\n"
    report += f"**NETTO: {ret['netto_profit']:,.2f} EUR**"
    send_discord(report)
if __name__ == "__main__":
    if "--test-email" in sys.argv:
        for o in fetch_emails(3)[:5]:
            print(f"  {o['order_id']}: {o['final_email']:.2f} | {o['produkt'][:40]}")
    elif "--returns" in sys.argv:
        run_returns_only()
    elif "--full-month" in sys.argv:
        run_report(full_month=True)
    else:
        run_report()
