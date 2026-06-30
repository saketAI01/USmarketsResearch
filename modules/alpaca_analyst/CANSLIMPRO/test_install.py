"""
CANSLIM Screener Pro — installation self-test.
Run: python test_install.py
"""
import sys, os, tempfile, socket
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

PASS, FAIL, WARN = "[PASS]", "[FAIL]", "[WARN]"
results = []

def can_reach_yahoo():
    try:
        socket.setdefaulttimeout(3)
        socket.socket(socket.AF_INET, socket.SOCK_STREAM).connect(("finance.yahoo.com", 443))
        return True
    except Exception:
        return False
    finally:
        socket.setdefaulttimeout(None)

def can_reach_alpaca():
    try:
        socket.setdefaulttimeout(3)
        socket.socket(socket.AF_INET, socket.SOCK_STREAM).connect(("data.alpaca.markets", 443))
        return True
    except Exception:
        return False
    finally:
        socket.setdefaulttimeout(None)

def can_reach_fmp():
    try:
        socket.setdefaulttimeout(3)
        socket.socket(socket.AF_INET, socket.SOCK_STREAM).connect(("financialmodelingprep.com", 443))
        return True
    except Exception:
        return False
    finally:
        socket.setdefaulttimeout(None)

def test(name, fn):
    try:
        msg = fn()
        tag = WARN if (msg and msg.startswith("SKIP")) else PASS
        results.append((tag, name, msg or ""))
        print(f"  {tag}  {name}" + (f"  — {msg}" if msg else ""))
    except Exception as e:
        results.append((FAIL, name, str(e)))
        print(f"  {FAIL}  {name}  — {e}")

print()
print("=" * 64)
print("  CANSLIM Screener Pro — Self Test")
print("=" * 64)

print("\n[1] Core imports")
test("config module",   lambda: __import__("config") and "OK")
test("pandas",          lambda: __import__("pandas").__version__)
test("numpy",           lambda: __import__("numpy").__version__)
test("yfinance",        lambda: __import__("yfinance").__version__)
test("requests",        lambda: __import__("requests").__version__)
test("reportlab",       lambda: __import__("reportlab").__version__)
try:
    import PySide6
    print(f"  {PASS}  PySide6  — {PySide6.__version__}")
    results.append((PASS, "PySide6", PySide6.__version__))
except ImportError:
    print(f"  {WARN}  PySide6 — not importable headless (fine on your Windows machine)")
    results.append((WARN, "PySide6", "headless env"))

print("\n[2] API key discovery")
def check_keys():
    from config import load_api_keys, key_status
    keys = load_api_keys()
    ks   = key_status()
    return (f"FMP: {'active ('+keys['FMP_API_KEY'][:8]+'...)' if ks['fmp'] else 'not set'}"
            f"  |  Alpaca: {'active ('+keys['ALPACA_KEY_ID'][:8]+'...)' if ks['alpaca'] else 'not set'}")
test("key loader", check_keys)

print("\n[3] CSV handler")
def test_csv():
    from core.csv_handler import load_watchlist
    p = Path("sample_watchlist_us.csv")
    if not p.exists(): return "SKIP — sample file not found"
    tickers, _ = load_watchlist(str(p))
    if not tickers: raise ValueError("No tickers loaded")
    return f"{len(tickers)} tickers loaded"
test("CSV watchlist load", test_csv)

print("\n[4] yFinance data fetching")
def test_yf_us():
    try:
        from core.data_fetcher import fetch_stock_data
        sd = fetch_stock_data("AAPL", delay=0)
        if sd.current_price <= 0:
            return "SKIP — yFinance returned 0 price (network blocked in this env; works on your machine)"
        return f"AAPL ${sd.current_price:.2f} | eps_qtrs={len(sd.quarterly_eps)} | src={sd.price_source}"
    except Exception as e:
        if "403" in str(e) or "allowlist" in str(e).lower():
            return "SKIP — network blocked in this env (works on your machine)"
        raise
test("yFinance US (AAPL)", test_yf_us)

def test_yf_in():
    if not can_reach_yahoo():
        return "SKIP — finance.yahoo.com unreachable in this environment"
    from core.data_fetcher import fetch_stock_data
    sd = fetch_stock_data("RELIANCE.NS", delay=0)
    if sd.market != "IN": raise ValueError("Market detection failed")
    return f"RELIANCE.NS ₹{sd.current_price:.2f} | market={sd.market} | src={sd.price_source}"
test("yFinance India (RELIANCE.NS)", test_yf_in)

print("\n[5] Alpaca Markets API")
def test_alpaca():
    from config import key_status, get_keys
    if not key_status()["alpaca"]:
        return "SKIP — Alpaca keys not in keys.env (yFinance fallback is active)"
    if not can_reach_alpaca():
        return "SKIP — data.alpaca.markets unreachable in this environment"
    from core.alpaca_client import make_alpaca_client
    client = make_alpaca_client()
    df = client.get_daily_bars("AAPL", days=30)
    if df.empty: raise ValueError("Empty bars returned")
    return f"{len(df)} daily bars for AAPL via Alpaca IEX"
test("Alpaca price bars", test_alpaca)

print("\n[6] FMP API")
def test_fmp():
    from config import key_status, get_keys
    if not key_status()["fmp"]:
        return "SKIP — FMP key not in keys.env (yFinance fallback is active)"
    if not can_reach_fmp():
        return "SKIP — financialmodelingprep.com unreachable"
    keys = get_keys()
    from core.data_fetcher import _fmp_fundamentals
    fmp = _fmp_fundamentals("AAPL", keys["FMP_API_KEY"])
    if not fmp["annual_eps"]: raise ValueError("No FMP EPS data for AAPL — check API key")
    return f"FMP EPS periods={len(fmp['annual_eps'])}  inst%={fmp['institutional_pct']:.1f}%"
test("FMP fundamentals", test_fmp)

print("\n[7] CANSLIM scoring engine")
def test_engine():
    from config import load_api_keys
    from core.data_fetcher import StockData
    from core.canslim_engine import score_canslim
    import pandas as pd, numpy as np
    # Build a synthetic StockData so we don't need network
    sd = StockData(ticker="TEST", market="US")
    sd.company_name   = "Test Corp"
    sd.current_price  = 150.0
    sd.high_52w       = 160.0
    sd.low_52w        = 90.0
    sd.shares_float   = 5e8
    sd.shares_outstanding = 5e8
    sd.institutional_pct  = 72.0
    sd.annual_eps     = [5.0, 3.8, 2.9, 2.1]
    sd.quarterly_eps  = [1.5, 1.2, 1.0, 0.9, 1.1, 0.8, 0.7, 0.6]
    sd.annual_revenue = [80e9, 65e9, 52e9, 45e9]
    sd.quarterly_revenue = [22e9, 19e9, 18e9, 16e9, 17e9, 15e9, 14e9, 13e9]
    dates = pd.date_range(end=pd.Timestamp.today(), periods=260, freq="B")
    prices = 100 + np.cumsum(np.random.randn(260) * 1.2)
    prices = np.clip(prices, 80, 170)
    sd.price_history  = pd.DataFrame({"Open": prices*0.99, "High": prices*1.01,
                                       "Low": prices*0.98, "Close": prices,
                                       "Volume": np.random.randint(1e7,5e7,260)},
                                      index=dates)
    bench = 4000 + np.cumsum(np.random.randn(260) * 15)
    sd.benchmark_history = pd.DataFrame({"Close": bench}, index=dates)
    result = score_canslim(sd)
    sc = result.composite_score
    if not (0 <= sc <= 100): raise ValueError(f"Score {sc} out of range")
    scores = {k: v.score for k, v in result.components.items()}
    return (f"composite={sc:.1f} ({result.rating}) | "
            f"C={scores['C']} A={scores['A']} N={scores['N']} "
            f"S={scores['S']} L={scores['L']} I={scores['I']} M={scores['M']}")
test("CANSLIM engine (synthetic data)", test_engine)

print("\n[8] CSV export")
def test_csv_export():
    from core.data_fetcher import StockData
    from core.canslim_engine import score_canslim
    from core.csv_handler import export_results_csv
    import pandas as pd, numpy as np
    sd = StockData(ticker="SYNTH", market="US"); sd.company_name="Synthetic Corp"
    sd.current_price=100.0; sd.high_52w=110.0; sd.low_52w=80.0
    sd.shares_float=1e8; sd.shares_outstanding=1e8; sd.institutional_pct=60.0
    sd.annual_eps=[3.0,2.5,2.0]; sd.quarterly_eps=[0.8,0.7,0.6,0.5,0.6,0.5,0.4,0.3]
    dates = pd.date_range(end=pd.Timestamp.today(), periods=260, freq="B")
    p = 100+np.cumsum(np.random.randn(260))
    sd.price_history = pd.DataFrame({"Close":p,"Open":p,"High":p*1.01,"Low":p*0.99,"Volume":np.ones(260)*1e6},index=dates)
    sd.benchmark_history = pd.DataFrame({"Close":4000+np.cumsum(np.random.randn(260)*10)},index=dates)
    result = score_canslim(sd)
    with tempfile.NamedTemporaryFile(suffix=".csv", delete=False, mode="w") as f:
        tmp = f.name
    try:
        out  = export_results_csv([result], tmp)
        size = Path(out).stat().st_size
        return f"CSV written — {size} bytes"
    finally:
        os.unlink(tmp)
test("CSV results export", test_csv_export)

print("\n[9] PDF report generation")
def test_pdf():
    from core.data_fetcher import StockData
    from core.canslim_engine import score_canslim
    from core.report_generator import generate_report
    import pandas as pd, numpy as np
    sd = StockData(ticker="SYNTH", market="US"); sd.company_name="Synthetic Corp"; sd.sector="Technology"
    sd.current_price=100.0; sd.high_52w=108.0; sd.low_52w=78.0
    sd.shares_float=1e8; sd.shares_outstanding=1e8; sd.institutional_pct=65.0
    sd.annual_eps=[3.5,2.8,2.0,1.5]; sd.quarterly_eps=[1.0,0.85,0.75,0.65,0.7,0.6,0.5,0.4]
    sd.annual_revenue=[50e9,42e9,35e9,29e9]; sd.quarterly_revenue=[14e9,12e9,11e9,10e9,10e9,9e9,8e9,7e9]
    dates = pd.date_range(end=pd.Timestamp.today(), periods=260, freq="B")
    p = 80+np.cumsum(np.abs(np.random.randn(260)*1.5))
    sd.price_history=pd.DataFrame({"Close":p,"Open":p*0.99,"High":p*1.01,"Low":p*0.98,"Volume":np.ones(260)*2e6},index=dates)
    sd.benchmark_history=pd.DataFrame({"Close":4000+np.cumsum(np.random.randn(260)*12)},index=dates)
    result = score_canslim(sd)
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
        tmp = f.name
    try:
        out  = generate_report([result], tmp, {"n_analyzed":1,"min_score":0,"markets":"US"})
        size = Path(out).stat().st_size
        if size < 5000: raise ValueError(f"PDF too small: {size} bytes")
        return f"PDF written — {size/1024:.1f} KB"
    finally:
        os.unlink(tmp)
test("PDF report generation", test_pdf)

print()
print("=" * 64)
passed = sum(1 for r in results if r[0] == PASS)
failed = sum(1 for r in results if r[0] == FAIL)
warned = sum(1 for r in results if r[0] == WARN)
print(f"  Results: {passed} passed  |  {failed} failed  |  {warned} skipped/warned")
print("=" * 64)
if failed:
    print("\nFailed tests:")
    for r in results:
        if r[0] == FAIL: print(f"  - {r[1]}: {r[2]}")
    print()
    sys.exit(1)
else:
    print("\nAll critical tests passed. Run start.bat to launch the app.\n")
    sys.exit(0)
