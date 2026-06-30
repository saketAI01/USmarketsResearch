"""Called by start.bat to print API key status. Kept separate to avoid CMD quoting issues."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from config import key_status, get_keys
    keys   = get_keys()
    status = key_status()
    fmp_k  = keys.get("FMP_API_KEY", "")
    alp_k  = keys.get("ALPACA_KEY_ID", "")
    fmp_info = ("Active  (" + fmp_k[:8] + "...)") if status["fmp"]    else "Not set  (yFinance fallback active)"
    alp_info = ("Active  (" + alp_k[:8] + "...)") if status["alpaca"] else "Not set  (yFinance fallback active)"
    print("  FMP API:  " + fmp_info)
    print("  Alpaca:   " + alp_info)
    print("  yFinance: Always active (free, no key needed)")
except Exception as e:
    print("  Could not load key status: " + str(e))
