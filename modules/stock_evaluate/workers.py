"""
QThread workers for async data operations.
"""
import traceback
import concurrent.futures
import requests
from PySide6.QtCore import QThread, Signal
from .config import load_api_keys, classify_market_cap
from .database import DatabaseManager
from .api_clients import FMPClient, AlpacaClient, WikipediaConstituents, YFinanceClient
from .engines import TechnicalEngine, ScoringEngine
from .logger import get_logger

log = get_logger("Workers")


class UniverseRefreshWorker(QThread):
    """Fetches S&P500 + NASDAQ constituents + batch quotes from FMP."""
    progress = Signal(str, int)   # message, percent
    finished = Signal(str)        # error or ""

    def __init__(self, db, keys):
        super().__init__()
        self.db = db
        self.keys = keys

    def run(self):
        try:
            fmp = FMPClient(self.keys["fmp"])

            # Step 1: Constituents from Wikipedia (free, no API key needed)
            self.progress.emit("Fetching S&P 500 from Wikipedia...", 5)
            sp = WikipediaConstituents.get_sp500()
            self.progress.emit("Fetching NASDAQ-100 from Wikipedia...", 15)
            nq = WikipediaConstituents.get_nasdaq100()

            # Build rows
            rows = {}
            for item in sp:
                sym = item.get("symbol", "")
                if not sym: continue
                rows[sym] = {
                    "symbol": sym,
                    "company_name": item.get("company_name", ""),
                    "sector": item.get("sector", ""),
                    "sub_sector": "",
                    "industry": item.get("industry", ""),
                    "exchange": item.get("exchange", "NYSE"),
                    "is_sp500": 1, "is_nasdaq": 0,
                }
            for item in nq:
                sym = item.get("symbol", "")
                if not sym: continue
                if sym in rows:
                    rows[sym]["is_nasdaq"] = 1
                else:
                    rows[sym] = {
                        "symbol": sym,
                        "company_name": item.get("company_name", ""),
                        "sector": item.get("sector", ""),
                        "sub_sector": "",
                        "industry": item.get("industry", ""),
                        "exchange": "NASDAQ",
                        "is_sp500": 0, "is_nasdaq": 1,
                    }

            self.progress.emit(f"Caching {len(rows)} symbols...", 25)
            self.db.upsert_constituents(list(rows.values()))

            # Step 2: Smart Refresh Strategy ("Fill the Tank")
            stale_symbols = self.db.get_stale_symbols(limit=60)
            all_symbols = list(rows.keys())
            
            # Phase A: Market-wide Quotes via Alpaca (Unlimited/Free)
            self.progress.emit(f"Updating prices for {len(all_symbols)} symbols...", 30)
            alpaca = AlpacaClient(self.keys.get("alpaca_key"), self.keys.get("alpaca_secret"))
            all_quotes = alpaca.get_batch_snapshots(all_symbols)
            
            # Phase B: Incremental Fundamentals via FMP (Rate Limited)
            if stale_symbols and self.keys.get("fmp"):
                self.progress.emit(f"Filing Tank: Fetching fundamentals for {len(stale_symbols)} symbols...", 50)
                fmp_details = fmp.get_batch_quotes(
                    stale_symbols, 
                    detailed_symbols=stale_symbols,
                    progress_cb=lambda c, t, s: self.progress.emit(f"Lot [{c}/{t}]: {s}...", 50 + int(c/t*40))
                )
                
                # Merge FMP details into Alpaca quotes
                detail_map = {q["symbol"]: q for q in fmp_details if q}
                for q in all_quotes:
                    sym = q["symbol"]
                    if sym in detail_map:
                        q.update(detail_map[sym])
                
                # Phase C: yfinance Fallback for remaining stale symbols
                remaining_stale = [s for s in stale_symbols if s not in detail_map]
                if remaining_stale:
                    self.progress.emit(f"Lot fallback: Fetching {len(remaining_stale)} symbols via yfinance...", 50)
                    for i, sym in enumerate(remaining_stale):
                        if self.isInterruptionRequested(): break
                        info = YFinanceClient.get_info(sym)
                        if info:
                            # Map yfinance info to FMP-style dict
                            detail_map[sym] = {
                                "symbol": sym,
                                "price": info.get("currentPrice") or info.get("regularMarketPrice"),
                                "pe": info.get("trailingPE"),
                                "pb": info.get("priceToBook"),
                                "roe": info.get("returnOnEquity"),
                                "de": info.get("debtToEquity"),
                                "rev_growth": info.get("revenueGrowth"),
                                "eps_growth": info.get("earningsGrowth"),
                                "div": info.get("dividendYield"),
                                "marketCap": info.get("marketCap"),
                                "eps": info.get("trailingEps"),
                                "avgVolume": info.get("averageVolume"),
                                "yearHigh": info.get("fiftyTwoWeekHigh"),
                                "yearLow": info.get("fiftyTwoWeekLow"),
                                "priceAvg50": info.get("fiftyDayAverage"),
                                "priceAvg200": info.get("twoHundredDayAverage"),
                            }
                            # Update existing quote in all_quotes
                            for q in all_quotes:
                                if q["symbol"] == sym:
                                    q.update(detail_map[sym])
                                    break
                        self.progress.emit(f"yFinance [{i+1}/{len(remaining_stale)}]: {sym}...", 50 + int((i+1)/len(remaining_stale)*40))
            
            self.progress.emit(f"Processing {len(all_quotes)} combined quotes...", 90)

            fund_rows = []
            const_updates = []
            for q in all_quotes:
                sym = q.get("symbol", "")
                mc = q.get("marketCap", 0) or 0
                fund_rows.append({
                    "symbol": sym,
                    "price": q.get("price"),
                    "change_pct": q.get("changePercentage"),
                    "pe_ratio": q.get("pe"),
                    "pb_ratio": q.get("pb"),
                    "debt_equity": q.get("de"),
                    "revenue_growth": q.get("rev_growth"),
                    "eps_growth": q.get("eps_growth"),
                    "roe": q.get("roe"),
                    "dividend_yield": q.get("div"),
                    "market_cap": mc,
                    "eps": q.get("eps"),
                    "avg_volume": q.get("avgVolume") or q.get("volume"),
                    "week52_high": q.get("yearHigh"),
                    "week52_low": q.get("yearLow"),
                    "price_avg50": q.get("priceAvg50"),
                    "price_avg200": q.get("priceAvg200"),
                })
                const_updates.append({
                    "symbol": sym,
                    "company_name": q.get("name", ""),
                    "exchange": q.get("exchange", ""),
                    "market_cap": mc,
                    "cap_segment": classify_market_cap(mc),
                    "sector": rows.get(sym, {}).get("sector", ""),
                })

            self.db.upsert_fundamentals(fund_rows)
            self.db.upsert_constituents(const_updates)

            self.progress.emit("Universe refresh complete!", 100)
            self.finished.emit("")
        except Exception as e:
            log.error(f"Universe Refresh Error: {e}")
            log.error(traceback.format_exc())
            self.finished.emit(str(e))


class ScreenerWorker(QThread):
    """Runs screening with fundamental + optional technical filters."""
    progress = Signal(str, int)
    result_ready = Signal(list)   # list of result dicts
    finished = Signal(str)

    def __init__(self, db, keys, filters):
        super().__init__()
        self.db = db
        self.keys = keys
        self.filters = filters

    def run(self):
        try:
            self.progress.emit("Applying fundamental filters...", 10)
            candidates = self.db.screen_fundamentals(self.filters)
            
            # Check for empty tank warning
            if not candidates:
                all_funds = self.db.get_fundamentals()
                missing = [f for f in all_funds if f.get("pe_ratio") is None]
                get_logger("Screener").warning(
                    f"Screening returned 0 results. Database has {len(all_funds)} stocks, "
                    f"but {len(missing)} are missing fundamental data (PE, Growth, etc.)."
                )
            
            self.progress.emit(f"{len(candidates)} passed fundamental filters", 30)

            # Technical computation and scoring
            if candidates and self.keys.get("alpaca_key"):
                syms = [c["symbol"] for c in candidates]
                if len(syms) > 200:
                    syms = syms[:200]
                    candidates = candidates[:200]

                self.progress.emit(f"Fetching OHLCV for {len(syms)} symbols...", 40)
                alpaca = AlpacaClient(self.keys["alpaca_key"], self.keys["alpaca_secret"])
                bars_dict = alpaca.get_bars(syms)

                self.progress.emit("Computing technical indicators...", 70)
                results = []
                for c in candidates:
                    sym = c["symbol"]
                    df = bars_dict.get(sym)
                    tech = {}
                    if df is not None and not df.empty:
                        tech = TechnicalEngine.compute(df)
                        c.update(tech)
                    
                    # Compute composite score
                    score_data = ScoringEngine.compute(c)
                    c["score"] = score_data["composite"]
                    c["score_details"] = score_data
                    
                    # Apply technical filters
                    if self.filters.get("tech_enabled", True):
                        if not TechnicalEngine.passes_technical_filters(tech, self.filters):
                            continue
                    
                    results.append(c)
                candidates = results
            else:
                # No technicals available — compute scores from fundamentals only
                for c in candidates:
                    score_data = ScoringEngine.compute(c)
                    c["score"] = score_data["composite"]
                    c["score_details"] = score_data

            candidates.sort(key=lambda x: x.get("score", 0), reverse=True)
            self.progress.emit(f"Screening complete: {len(candidates)} results", 100)
            self.result_ready.emit(candidates)
            self.finished.emit("")
        except Exception as e:
            traceback.print_exc()
            self.finished.emit(str(e))


class DeepDiveWorker(QThread):
    """Fetch comprehensive data for a single stock."""
    progress = Signal(str)
    data_ready = Signal(dict)
    finished = Signal(str)  # error msg or ""

    def __init__(self, symbol, keys, db, period_days=365):
        super().__init__()
        self.symbol = symbol.upper()
        self.keys = keys
        self.db = db
        self.period_days = period_days

    def run(self):
        try:
            result = {"symbol": self.symbol}
            fmp = FMPClient(self.keys["fmp"])
            self.progress.emit("Fetching company profile...")
            profile = fmp.get_profile(self.symbol)
            result["profile"] = profile

            self.progress.emit("Fetching key metrics...")
            metrics = fmp.get_key_metrics_ttm(self.symbol)
            ratios = fmp.get_ratios_ttm(self.symbol)
            growth = fmp.get_financial_growth(self.symbol)
            result["metrics"] = metrics
            result["ratios"] = ratios
            result["growth"] = growth

            # Merge into fundamentals dict
            fund = {
                "symbol": self.symbol,
                "price": profile.get("price"),
                "change_pct": profile.get("changes"),
                "pe_ratio": ratios.get("peRatioTTM") or metrics.get("peRatioTTM"),
                "pb_ratio": ratios.get("priceToBookRatioTTM") or metrics.get("pbRatioTTM"),
                "ps_ratio": ratios.get("priceToSalesRatioTTM") or metrics.get("priceToSalesRatioTTM"),
                "peg_ratio": ratios.get("priceEarningsToGrowthRatioTTM"),
                "roe": metrics.get("roeTTM"),
                "debt_equity": metrics.get("debtToEquityTTM"),
                "revenue_growth": (growth.get("revenueGrowth") or 0) * 100 if growth.get("revenueGrowth") else None,
                "eps_growth": (growth.get("epsgrowth") or 0) * 100 if growth.get("epsgrowth") else None,
                "dividend_yield": metrics.get("dividendYieldPercentageTTM"),
                "market_cap": profile.get("mktCap"),
                "beta": profile.get("beta"),
                "week52_high": profile.get("range", "0-0").split("-")[-1] if profile.get("range") else None,
                "week52_low": profile.get("range", "0-0").split("-")[0] if profile.get("range") else None,
            }
            
            # DB Fallback if FMP is empty
            if not fund.get("price") or not fund.get("pe_ratio"):
                db_fund = self.db.get_fundamentals([self.symbol])
                if db_fund:
                    f = db_fund[0]
                    # Only fill if FMP was empty
                    for k in fund:
                        if fund[k] is None and f.get(k) is not None:
                            fund[k] = f[k]

            try:
                fund["week52_high"] = float(fund["week52_high"]) if fund["week52_high"] else None
                fund["week52_low"] = float(fund["week52_low"]) if fund["week52_low"] else None
            except: pass
            result["fundamentals"] = fund

            # OHLCV
            self.progress.emit("Fetching price history...")
            bars_df = None
            if self.keys.get("alpaca_key"):
                alpaca = AlpacaClient(self.keys["alpaca_key"], self.keys["alpaca_secret"])
                from datetime import datetime, timedelta
                start = (datetime.utcnow() - timedelta(days=self.period_days)).strftime("%Y-%m-%dT00:00:00Z")
                bars_dict = alpaca.get_bars([self.symbol], start=start)
                bars_df = bars_dict.get(self.symbol)

            if bars_df is not None and not bars_df.empty:
                self.db.upsert_bars(self.symbol, bars_df)
            else:
                bars_df = self.db.get_bars(self.symbol, self.period_days)

            result["bars"] = bars_df

            # Technicals
            self.progress.emit("Computing indicators...")
            if bars_df is not None and not bars_df.empty:
                tech = TechnicalEngine.compute(bars_df)
                result["technicals"] = tech
                score = ScoringEngine.compute(fund, tech)
                result["score"] = score
            else:
                result["technicals"] = {}
                result["score"] = ScoringEngine.compute(fund)

            self.progress.emit("Ready.")
            self.data_ready.emit(result)
            self.finished.emit("")
        except Exception as e:
            log.error(f"Deep Dive Error: {e}")
            self.finished.emit(str(e))


import yfinance as yf

class DashboardDataWorker(QThread):
    """Fetch dashboard overview data."""
    progress = Signal(str)
    data_ready = Signal(dict)
    finished = Signal(str)

    def __init__(self, keys, db=None):
        super().__init__()
        self.keys = keys
        self.db = db

    def run(self):
        try:
            fmp = FMPClient(self.keys["fmp"])
            data = {}

            self.progress.emit("Fetching market indices...")
            indices = fmp.get_index_quotes()
            
            # Fallback for missing indices using yfinance
            missing = [k for k, v in {"^GSPC":"S&P", "^IXIC":"NAS", "^DJI":"DOW", "^VIX":"VIX"}.items() if k not in indices]
            if missing:
                self.progress.emit("Index fallback (yf)...")
                for sym in missing:
                    try:
                        t = yf.Ticker(sym)
                        h = t.history(period="2d")
                        if not h.empty:
                            close = h["Close"].iloc[-1]
                            prev = h["Close"].iloc[-2] if len(h)>1 else close
                            chg_pct = ((close/prev)-1)*100
                            indices[sym] = {"price": close, "changePercentage": chg_pct}
                    except: continue
            
            data["indices"] = indices

            # Movers from FMP (Live)
            self.progress.emit("Fetching market movers...")
            data["gainers"] = fmp.get_movers("gainers")
            data["losers"] = fmp.get_movers("losers")

            # Fallback to DB if FMP movers are empty
            if not data["gainers"] and self.db:
                self.progress.emit("Mover fallback (DB)...")
                fund = self.db.get_fundamentals()
                valid = [f for f in fund if f.get("change_pct") is not None and f.get("price")]
                sorted_up = sorted(valid, key=lambda x: x.get("change_pct", 0), reverse=True)
                data["gainers"] = [{"symbol": f["symbol"], "price": f["price"], "changesPercentage": f["change_pct"], "volume": f.get("avg_volume",0)} for f in sorted_up[:10]]
            
            if not data["losers"] and self.db:
                fund = self.db.get_fundamentals()
                valid = [f for f in fund if f.get("change_pct") is not None and f.get("price")]
                sorted_dn = sorted(valid, key=lambda x: x.get("change_pct", 0))
                data["losers"] = [{"symbol": f["symbol"], "price": f["price"], "changesPercentage": f["change_pct"], "volume": f.get("avg_volume",0)} for f in sorted_dn[:10]]

            # Sector performance
            self.progress.emit("Computing sector data...")
            if self.db:
                fund = self.db.get_fundamentals()
                constituents = self.db.get_all_constituents()
                const_map = {c["symbol"]: c for c in constituents}
                sector_data = {}
                for f in fund:

                    sym = f["symbol"]
                    sec = const_map.get(sym, {}).get("sector", "")
                    chg = f.get("change_pct") or 0
                    if sec:
                        if sec not in sector_data:
                            sector_data[sec] = []
                        sector_data[sec].append(chg)
                sectors = []
                for sec, changes in sorted(sector_data.items()):
                    avg = sum(changes) / len(changes) if changes else 0
                    sectors.append({"sector": sec, "changesPercentage": f"{avg:.2f}%"})
                data["sectors"] = sectors
            else:
                data["sectors"] = []
                if "gainers" not in data: data["gainers"] = []
                if "losers" not in data: data["losers"] = []

            self.progress.emit("Ready.")
            self.data_ready.emit(data)
            self.finished.emit("")
        except Exception as e:
            log.error(f"Dashboard Data Error: {e}")
            self.finished.emit(str(e))



class AIAnalysisWorker(QThread):
    """Run AI analysis using Gemini cascade or Perplexity."""
    progress = Signal(str)
    finished = Signal(str, str)  # result_text, error

    def __init__(self, symbol, analysis_type, context_data, keys):
        super().__init__()
        self.symbol = symbol
        self.analysis_type = analysis_type
        self.context = context_data
        self.keys = keys

    def run(self):
        try:
            prompt = self._build_prompt()

            # Gemini cascade: 2.5 Flash first (fast/cheap), fallback to others
            result = None
            gemini_key = self.keys.get("gemini")
            if gemini_key and self.analysis_type != "News & Sentiment":
                models = ["gemini-3.1-flash-lite", "gemini-1.5-pro"]
                for model_name in models:
                    self.progress.emit(f"Trying {model_name}...")
                    result = self._call_gemini(gemini_key, model_name, prompt)
                    if result:
                        break

            # Perplexity for news/sentiment or as fallback
            if not result:
                pplx_key = self.keys.get("perplexity")
                if pplx_key:
                    self.progress.emit("Querying Perplexity Sonar Pro...")
                    result = self._call_perplexity(pplx_key, prompt)

            if result:
                self.finished.emit(result, "")
            else:
                self.finished.emit("", "All AI providers failed. Check API keys.")
        except Exception as e:
            traceback.print_exc()
            self.finished.emit("", str(e))

    def _build_prompt(self):
        ctx = self.context or {}
        price = ctx.get("price", "N/A")
        pe = ctx.get("pe_ratio", "N/A")
        mc = ctx.get("market_cap", "N/A")
        sector = ctx.get("sector", "N/A")
        rsi = ctx.get("rsi_14", "N/A")
        
        from datetime import datetime
        now_str = datetime.now().strftime("%B %d, %Y")

        base = f"""Stock: {self.symbol}
Analysis Date: {now_str} (Use this as the current reference date)
Current Market Price: ${price} (This is the verified live price as of {now_str})
P/E Ratio: {pe} | Market Cap: ${mc}
Sector: {sector} | RSI(14): {rsi}"""

        if self.analysis_type == "Comprehensive Analysis":
            return f"""You are a senior equity research analyst. Provide a comprehensive analysis of {self.symbol}.

{base}

Cover: 1) Business overview 2) Financial health 3) Valuation assessment
4) Technical outlook 5) Key risks 6) Investment thesis (bull vs bear case)
7) Fair value estimate with rationale. Use data-driven analysis."""

        elif self.analysis_type == "News & Sentiment":
            return f"""Provide the latest news, developments, and market sentiment for {self.symbol} stock.
{base}
Include: recent earnings, analyst upgrades/downgrades, sector trends, and any catalysts.
Provide source citations."""

        elif self.analysis_type == "Investment Thesis":
            return f"""Build a structured investment thesis for {self.symbol}.
{base}
Format: BULL CASE (3 key points), BEAR CASE (3 key points), CATALYST TIMELINE,
RISK/REWARD ASSESSMENT, POSITION SIZING RECOMMENDATION."""

        elif self.analysis_type == "Peer Comparison":
            return f"""Compare {self.symbol} against its top 5 sector peers.
{base}
Create a comparative analysis table covering: valuation multiples, growth rates,
profitability margins, and relative strength. Identify the best-positioned stock."""

        return f"Analyze {self.symbol} stock. {base}"

    def _call_gemini(self, api_key, model, prompt):
        try:
            from google import genai
            client = genai.Client(api_key=api_key)
            response = client.models.generate_content(
                model=model,
                contents=prompt
            )
            return response.text if response else None
        except Exception as e:
            print(f"[Gemini/{model}] Error: {e}")
            return None

    def _call_perplexity(self, api_key, prompt):
        try:
            r = requests.post(
                "https://api.perplexity.ai/chat/completions",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={
                    "model": "sonar-pro",
                    "messages": [
                        {"role": "system", "content": "You are a professional equity research analyst. Be thorough and data-driven."},
                        {"role": "user", "content": prompt}
                    ]
                }, timeout=60)
            r.raise_for_status()
            data = r.json()
            text = data["choices"][0]["message"]["content"]
            citations = data.get("citations", [])
            if citations:
                text += "\n\n---\n**Sources:**\n"
                for i, url in enumerate(citations, 1):
                    text += f"{i}. {url}\n"
            return text
        except Exception as e:
            print(f"[Perplexity] Error: {e}")
            return None
