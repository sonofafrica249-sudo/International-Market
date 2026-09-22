#!/usr/bin/env python3
"""
MD Khider International Market
لوحة متابعة أسعار الذهب والفضة والنفط مع إشارات توقع فنية
يعمل محلياً على حاسوبك ويمكن الوصول إليه من الهاتف على نفس الشبكة
"""

import json
import math
import os
from datetime import datetime, timedelta
from http.server import HTTPServer, SimpleHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
import threading
import time

try:
    import yfinance as yf
    import pandas as pd
    import numpy as np
except ImportError as e:
    print("يجب تثبيت المكتبات: pip install yfinance pandas numpy")
    raise e

# ====================== إعدادات ======================
# يدعم التشغيل المحلي + السيرفرات المجانية (Render / Railway / إلخ)
PORT = int(os.environ.get("PORT", 8080))
HOST = "0.0.0.0"

SYMBOLS = {
    "gold": {"ticker": "GC=F", "name_ar": "الذهب", "name_en": "Gold", "unit": "USD/oz", "icon": "🥇"},
    "silver": {"ticker": "SI=F", "name_ar": "الفضة", "name_en": "Silver", "unit": "USD/oz", "icon": "🥈"},
    "wti": {"ticker": "CL=F", "name_ar": "نفط WTI", "name_en": "WTI Crude", "unit": "USD/bbl", "icon": "🛢️"},
    "brent": {"ticker": "BZ=F", "name_ar": "نفط Brent", "name_en": "Brent Crude", "unit": "USD/bbl", "icon": "🛢️"},
}

# كاش بسيط لتقليل الطلبات
_cache = {}
_cache_ttl = 60  # ثانية


def get_cached(key, fetch_fn, ttl=_cache_ttl):
    now = time.time()
    if key in _cache and now - _cache[key]["ts"] < ttl:
        return _cache[key]["data"]
    data = fetch_fn()
    _cache[key] = {"data": data, "ts": now}
    return data


def calc_rsi(series, period=14):
    delta = series.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = -delta.where(delta < 0, 0.0)
    avg_gain = gain.ewm(alpha=1/period, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1/period, min_periods=period).mean()
    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    return rsi


def calc_signal(df):
    """إشارة توقع بسيطة مبنية على RSI + MA"""
    if len(df) < 50:
        return {"signal": "محايد", "score": 0, "confidence": 0, "details": "بيانات غير كافية"}

    close = df["Close"]
    rsi = calc_rsi(close).iloc[-1]
    ma20 = close.rolling(20).mean().iloc[-1]
    ma50 = close.rolling(50).mean().iloc[-1]
    last = close.iloc[-1]
    prev = close.iloc[-2] if len(close) > 1 else last

    score = 0
    details = []

    # RSI
    if rsi < 30:
        score += 25
        details.append(f"RSI={rsi:.1f} (تشبع بيعي → فرصة صعود)")
    elif rsi > 70:
        score -= 25
        details.append(f"RSI={rsi:.1f} (تشبع شرائي → احتمال هبوط)")
    else:
        details.append(f"RSI={rsi:.1f} (محايد)")

    # متوسطات
    if ma20 > ma50 and last > ma20:
        score += 20
        details.append("السعر فوق MA20 و MA20 فوق MA50 (اتجاه صاعد)")
    elif ma20 < ma50 and last < ma20:
        score -= 20
        details.append("السعر تحت MA20 و MA20 تحت MA50 (اتجاه هابط)")
    else:
        details.append("المتوسطات مختلطة")

    # زخم يومي
    change_pct = ((last - prev) / prev) * 100 if prev else 0
    if change_pct > 0.5:
        score += 10
        details.append(f"زخم يومي إيجابي ({change_pct:+.2f}%)")
    elif change_pct < -0.5:
        score -= 10
        details.append(f"زخم يومي سلبي ({change_pct:+.2f}%)")

    # تحديد الإشارة
    if score >= 25:
        signal = "ارتفاع محتمل"
        color = "bullish"
    elif score <= -25:
        signal = "انخفاض محتمل"
        color = "bearish"
    else:
        signal = "محايد / تقلب"
        color = "neutral"

    confidence = min(95, abs(score) * 2 + 30)

    return {
        "signal": signal,
        "score": score,
        "confidence": round(confidence),
        "color": color,
        "rsi": round(float(rsi), 1) if not math.isnan(rsi) else None,
        "ma20": round(float(ma20), 2) if not math.isnan(ma20) else None,
        "ma50": round(float(ma50), 2) if not math.isnan(ma50) else None,
        "details": details,
    }


def fetch_commodity(key):
    info = SYMBOLS[key]
    ticker = info["ticker"]

    def _fetch():
        t = yf.Ticker(ticker)
        hist = t.history(period="3mo", interval="1d")
        if hist.empty:
            return None

        last = float(hist["Close"].iloc[-1])
        prev_close = float(hist["Close"].iloc[-2]) if len(hist) > 1 else last
        change = last - prev_close
        change_pct = (change / prev_close) * 100 if prev_close else 0

        # بيانات إضافية
        high_52 = float(hist["High"].max())
        low_52 = float(hist["Low"].min())

        signal = calc_signal(hist)

        # آخر 30 يوم للرسم
        recent = hist.tail(30)
        chart = {
            "dates": [d.strftime("%Y-%m-%d") for d in recent.index],
            "closes": [round(float(x), 2) for x in recent["Close"].tolist()],
        }

        return {
            "key": key,
            "name_ar": info["name_ar"],
            "name_en": info["name_en"],
            "icon": info["icon"],
            "unit": info["unit"],
            "price": round(last, 2),
            "change": round(change, 2),
            "change_pct": round(change_pct, 2),
            "prev_close": round(prev_close, 2),
            "high_52w": round(high_52, 2),
            "low_52w": round(low_52, 2),
            "updated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "signal": signal,
            "chart": chart,
        }

    return get_cached(f"commodity_{key}", _fetch)


def fetch_all():
    result = {}
    for key in SYMBOLS:
        try:
            result[key] = fetch_commodity(key)
        except Exception as e:
            result[key] = {"error": str(e), "key": key, "name_ar": SYMBOLS[key]["name_ar"]}
    return result


# ====================== الواجهة HTML ======================
HTML_PAGE = r'''<!DOCTYPE html>
<html lang="ar" dir="rtl">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0" />
  <title>MD Khider International Market</title>
  <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
  <style>
    :root {
      --bg: #0b0f19;
      --card: #141b2d;
      --card-hover: #1a2338;
      --border: #1e2a44;
      --gold: #f5c542;
      --silver: #c0c7d4;
      --oil: #ff8c42;
      --green: #22c55e;
      --red: #ef4444;
      --text: #e8eef7;
      --muted: #8b9bb8;
      --accent: #3b82f6;
    }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      font-family: "Segoe UI", Tahoma, "Noto Sans Arabic", sans-serif;
      background: var(--bg);
      color: var(--text);
      min-height: 100vh;
      line-height: 1.5;
    }
    .header {
      background: linear-gradient(135deg, #0f172a 0%, #1e293b 50%, #0f172a 100%);
      border-bottom: 1px solid var(--border);
      padding: 1.2rem 1.5rem;
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      justify-content: space-between;
      gap: 1rem;
      position: sticky;
      top: 0;
      z-index: 100;
      backdrop-filter: blur(12px);
    }
    .logo {
      display: flex;
      align-items: center;
      gap: 0.75rem;
    }
    .logo-icon {
      width: 48px; height: 48px;
      background: linear-gradient(135deg, #f5c542, #d4a017);
      border-radius: 12px;
      display: flex; align-items: center; justify-content: center;
      font-size: 1.5rem;
      box-shadow: 0 4px 15px rgba(245,197,66,0.3);
    }
    .logo h1 {
      font-size: 1.25rem;
      font-weight: 700;
      letter-spacing: -0.02em;
    }
    .logo span {
      font-size: 0.75rem;
      color: var(--muted);
      display: block;
    }
    .header-actions {
      display: flex;
      gap: 0.75rem;
      align-items: center;
    }
    .btn {
      background: var(--card);
      border: 1px solid var(--border);
      color: var(--text);
      padding: 0.5rem 1rem;
      border-radius: 8px;
      cursor: pointer;
      font-size: 0.875rem;
      transition: all 0.2s;
      display: flex; align-items: center; gap: 0.4rem;
    }
    .btn:hover { background: var(--card-hover); border-color: var(--accent); }
    .btn-primary {
      background: linear-gradient(135deg, #2563eb, #1d4ed8);
      border: none;
    }
    .btn-primary:hover { filter: brightness(1.1); }
    .status {
      font-size: 0.8rem;
      color: var(--muted);
      display: flex; align-items: center; gap: 0.4rem;
    }
    .dot {
      width: 8px; height: 8px;
      border-radius: 50%;
      background: var(--green);
      animation: pulse 2s infinite;
    }
    @keyframes pulse {
      0%, 100% { opacity: 1; }
      50% { opacity: 0.4; }
    }
    .container {
      max-width: 1400px;
      margin: 0 auto;
      padding: 1.5rem;
    }
    .grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
      gap: 1.25rem;
      margin-bottom: 2rem;
    }
    .card {
      background: var(--card);
      border: 1px solid var(--border);
      border-radius: 16px;
      padding: 1.25rem;
      transition: transform 0.2s, box-shadow 0.2s;
      position: relative;
      overflow: hidden;
    }
    .card:hover {
      transform: translateY(-2px);
      box-shadow: 0 12px 30px rgba(0,0,0,0.35);
    }
    .card::before {
      content: "";
      position: absolute;
      top: 0; left: 0; right: 0;
      height: 3px;
      background: var(--accent);
    }
    .card.gold::before { background: linear-gradient(90deg, #f5c542, #d4a017); }
    .card.silver::before { background: linear-gradient(90deg, #c0c7d4, #8a94a6); }
    .card.wti::before, .card.brent::before { background: linear-gradient(90deg, #ff8c42, #e85d04); }
    .card-header {
      display: flex;
      justify-content: space-between;
      align-items: flex-start;
      margin-bottom: 0.75rem;
    }
    .card-title {
      font-size: 0.95rem;
      color: var(--muted);
      display: flex; align-items: center; gap: 0.4rem;
    }
    .card-price {
      font-size: 1.85rem;
      font-weight: 700;
      letter-spacing: -0.03em;
      margin: 0.25rem 0;
    }
    .change {
      font-size: 0.9rem;
      font-weight: 600;
      display: inline-flex;
      align-items: center;
      gap: 0.25rem;
      padding: 0.2rem 0.55rem;
      border-radius: 6px;
    }
    .change.up { background: rgba(34,197,94,0.15); color: var(--green); }
    .change.down { background: rgba(239,68,68,0.15); color: var(--red); }
    .meta {
      display: flex;
      flex-wrap: wrap;
      gap: 0.75rem;
      margin-top: 0.85rem;
      font-size: 0.78rem;
      color: var(--muted);
    }
    .meta span { display: flex; align-items: center; gap: 0.25rem; }
    .signal-box {
      margin-top: 1rem;
      padding: 0.75rem;
      border-radius: 10px;
      background: rgba(0,0,0,0.25);
      border: 1px solid var(--border);
    }
    .signal-box.bullish { border-color: rgba(34,197,94,0.4); background: rgba(34,197,94,0.08); }
    .signal-box.bearish { border-color: rgba(239,68,68,0.4); background: rgba(239,68,68,0.08); }
    .signal-box.neutral { border-color: rgba(139,155,184,0.3); }
    .signal-label {
      font-size: 0.85rem;
      font-weight: 600;
      margin-bottom: 0.35rem;
    }
    .signal-details {
      font-size: 0.75rem;
      color: var(--muted);
      list-style: none;
    }
    .signal-details li { margin: 0.15rem 0; }
    .confidence {
      font-size: 0.75rem;
      margin-top: 0.4rem;
      color: var(--muted);
    }
    .section-title {
      font-size: 1.15rem;
      font-weight: 600;
      margin: 1.5rem 0 1rem;
      display: flex; align-items: center; gap: 0.5rem;
    }
    .charts-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
      gap: 1.25rem;
    }
    .chart-card {
      background: var(--card);
      border: 1px solid var(--border);
      border-radius: 16px;
      padding: 1rem;
    }
    .chart-card h3 {
      font-size: 0.95rem;
      margin-bottom: 0.75rem;
      color: var(--muted);
    }
    .disclaimer {
      margin-top: 2.5rem;
      padding: 1rem 1.25rem;
      background: rgba(239,68,68,0.08);
      border: 1px solid rgba(239,68,68,0.25);
      border-radius: 12px;
      font-size: 0.8rem;
      color: var(--muted);
      text-align: center;
    }
    .loading {
      text-align: center;
      padding: 3rem;
      color: var(--muted);
    }
    .spinner {
      width: 40px; height: 40px;
      border: 3px solid var(--border);
      border-top-color: var(--gold);
      border-radius: 50%;
      animation: spin 0.8s linear infinite;
      margin: 0 auto 1rem;
    }
    @keyframes spin { to { transform: rotate(360deg); } }
    footer {
      text-align: center;
      padding: 1.5rem;
      color: var(--muted);
      font-size: 0.8rem;
      border-top: 1px solid var(--border);
      margin-top: 2rem;
    }
    @media (max-width: 600px) {
      .logo h1 { font-size: 1rem; }
      .card-price { font-size: 1.5rem; }
      .container { padding: 1rem; }
    }
  </style>
</head>
<body>
  <header class="header">
    <div class="logo">
      <div class="logo-icon">MK</div>
      <div>
        <h1>MD Khider International Market</h1>
        <span>متابعة أسعار الذهب • الفضة • النفط</span>
      </div>
    </div>
    <div class="header-actions">
      <div class="status" id="status">
        <span class="dot"></span>
        <span id="lastUpdate">جاري التحميل...</span>
      </div>
      <button class="btn btn-primary" onclick="loadData(true)">
        🔄 تحديث
      </button>
    </div>
  </header>

  <main class="container">
    <div id="loading" class="loading">
      <div class="spinner"></div>
      <p>جاري جلب أحدث الأسعار...</p>
    </div>

    <div id="content" style="display:none;">
      <div class="grid" id="cards"></div>

      <h2 class="section-title">📊 الرسوم البيانية (آخر 30 يوم)</h2>
      <div class="charts-grid" id="charts"></div>
    </div>

    <div class="disclaimer">
      ⚠️ تنويه: الإشارات المعروضة مبنية على تحليل فني بسيط (RSI + المتوسطات المتحركة) وليست نصيحة استثمارية.
      الأسواق المالية عالية المخاطر. استخدم المعلومات على مسؤوليتك الخاصة.
      البيانات مستمدة من Yahoo Finance وقد تكون متأخرة بضع دقائق.
    </div>
  </main>

  <footer>
    MD Khider International Market &copy; 2026 — للاستخدام الشخصي
  </footer>

  <script>
    const charts = {};

    function formatNum(n, digits=2) {
      if (n == null || isNaN(n)) return "—";
      return Number(n).toLocaleString("en-US", {minimumFractionDigits: digits, maximumFractionDigits: digits});
    }

    function renderCards(data) {
      const container = document.getElementById("cards");
      container.innerHTML = "";
      const order = ["gold", "silver", "wti", "brent"];
      order.forEach(key => {
        const d = data[key];
        if (!d || d.error) {
          container.innerHTML += `<div class="card"><p>خطأ في ${key}</p></div>`;
          return;
        }
        const up = d.change >= 0;
        const sig = d.signal || {};
        const sigClass = sig.color || "neutral";
        container.innerHTML += `
          <div class="card ${key}">
            <div class="card-header">
              <div class="card-title">${d.icon} ${d.name_ar} <small style="opacity:0.6">(${d.name_en})</small></div>
            </div>
            <div class="card-price">${formatNum(d.price)}</div>
            <div>
              <span class="change ${up ? 'up' : 'down'}">
                ${up ? '▲' : '▼'} ${formatNum(Math.abs(d.change))} (${formatNum(d.change_pct)}%)
              </span>
            </div>
            <div class="meta">
              <span>الوحدة: ${d.unit}</span>
              <span>أعلى 3 أشهر: ${formatNum(d.high_52w)}</span>
              <span>أدنى 3 أشهر: ${formatNum(d.low_52w)}</span>
            </div>
            <div class="signal-box ${sigClass}">
              <div class="signal-label">
                ${sigClass === 'bullish' ? '📈' : sigClass === 'bearish' ? '📉' : '➖'} 
                التوقع: <strong>${sig.signal || '—'}</strong>
              </div>
              <ul class="signal-details">
                ${(sig.details || []).map(x => `<li>• ${x}</li>`).join('')}
              </ul>
              <div class="confidence">مستوى الثقة التقريبي: ${sig.confidence || 0}%</div>
            </div>
          </div>
        `;
      });
    }

    function renderCharts(data) {
      const container = document.getElementById("charts");
      container.innerHTML = "";
      const order = ["gold", "silver", "wti", "brent"];
      const colors = {
        gold: "#f5c542",
        silver: "#c0c7d4",
        wti: "#ff8c42",
        brent: "#e85d04"
      };
      order.forEach(key => {
        const d = data[key];
        if (!d || !d.chart) return;
        const id = `chart-${key}`;
        container.innerHTML += `
          <div class="chart-card">
            <h3>${d.icon} ${d.name_ar}</h3>
            <canvas id="${id}" height="180"></canvas>
          </div>
        `;
        setTimeout(() => {
          const ctx = document.getElementById(id);
          if (charts[key]) charts[key].destroy();
          charts[key] = new Chart(ctx, {
            type: 'line',
            data: {
              labels: d.chart.dates,
              datasets: [{
                label: d.name_ar,
                data: d.chart.closes,
                borderColor: colors[key],
                backgroundColor: colors[key] + "22",
                fill: true,
                tension: 0.3,
                pointRadius: 0,
                borderWidth: 2
              }]
            },
            options: {
              responsive: true,
              plugins: { legend: { display: false } },
              scales: {
                x: { ticks: { color: "#8b9bb8", maxTicksLimit: 6 }, grid: { color: "#1e2a44" } },
                y: { ticks: { color: "#8b9bb8" }, grid: { color: "#1e2a44" } }
              }
            }
          });
        }, 50);
      });
    }

    async function loadData(force=false) {
      try {
        document.getElementById("loading").style.display = force ? "block" : "none";
        const res = await fetch("/api/prices" + (force ? "?t=" + Date.now() : ""));
        const data = await res.json();
        renderCards(data);
        renderCharts(data);
        document.getElementById("loading").style.display = "none";
        document.getElementById("content").style.display = "block";
        const now = new Date().toLocaleString("ar-EG");
        document.getElementById("lastUpdate").textContent = "آخر تحديث: " + now;
      } catch (e) {
        document.getElementById("loading").innerHTML = `<p style="color:#ef4444">خطأ في الاتصال: ${e.message}</p>`;
      }
    }

    loadData();
    // تحديث تلقائي كل دقيقتين
    setInterval(() => loadData(), 120000);
  </script>
</body>
</html>
'''


class MarketHandler(SimpleHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path in ("/", "/index.html"):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(HTML_PAGE.encode("utf-8"))
            return

        if path == "/api/prices":
            try:
                data = fetch_all()
                body = json.dumps(data, ensure_ascii=False, default=str).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Cache-Control", "no-cache")
                self.end_headers()
                self.wfile.write(body)
            except Exception as e:
                err = json.dumps({"error": str(e)}).encode("utf-8")
                self.send_response(500)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(err)
            return

        self.send_error(404, "Not Found")

    def log_message(self, format, *args):
        # تقليل الضوضاء في السجل
        print(f"[{datetime.now().strftime('%H:%M:%S')}] {args[0]}")


def get_local_ip():
    import socket
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def main():
    print("=" * 60)
    print("  MD Khider International Market")
    print("  لوحة متابعة أسعار الذهب والفضة والنفط")
    print("=" * 60)
    print(f"\n  ✅ الخادم يعمل على المنفذ {PORT}")
    print(f"  🌐 HOST = {HOST}")
    if os.environ.get("PORT"):
        print("  🚀 تم اكتشاف بيئة سيرفر (Render / Railway / ...)")
    else:
        local_ip = get_local_ip()
        print(f"\n  📱 من الهاتف (نفس الشبكة):  http://{local_ip}:{PORT}")
        print(f"  💻 من الحاسوب:             http://127.0.0.1:{PORT}")
    print("\n  اضغط Ctrl+C للإيقاف\n")
    print("-" * 60)

    server = HTTPServer((HOST, PORT), MarketHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nتم إيقاف الخادم.")
        server.server_close()


if __name__ == "__main__":
    main()
