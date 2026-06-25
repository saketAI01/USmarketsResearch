"""
SQLite database manager for caching universe, fundamentals, bars, watchlist.
"""
import sqlite3
from datetime import datetime
import pandas as pd
from .config import DB_PATH, TTL_CONSTITUENTS, TTL_FUNDAMENTALS, TTL_BARS


class DatabaseManager:
    def __init__(self, db_path=None):
        self.db_path = str(db_path or DB_PATH)
        self._init_tables()

    def _conn(self):
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _init_tables(self):
        with self._conn() as c:
            c.executescript("""
                CREATE TABLE IF NOT EXISTS constituents (
                    symbol TEXT PRIMARY KEY, company_name TEXT, sector TEXT,
                    industry TEXT, sub_sector TEXT, exchange TEXT,
                    market_cap REAL DEFAULT 0, cap_segment TEXT,
                    country TEXT DEFAULT 'US', is_sp500 INTEGER DEFAULT 0,
                    is_nasdaq INTEGER DEFAULT 0, last_updated TEXT);
                CREATE TABLE IF NOT EXISTS fundamentals (
                    symbol TEXT PRIMARY KEY, price REAL, change_pct REAL,
                    pe_ratio REAL, pb_ratio REAL, ps_ratio REAL, peg_ratio REAL,
                    roe REAL, debt_equity REAL, revenue_growth REAL,
                    eps_growth REAL, eps REAL, dividend_yield REAL,
                    market_cap REAL, beta REAL, week52_high REAL,
                    week52_low REAL, avg_volume REAL, price_avg50 REAL,
                    price_avg200 REAL, last_updated TEXT);
                CREATE TABLE IF NOT EXISTS daily_bars (
                    symbol TEXT, date TEXT, open REAL, high REAL, low REAL,
                    close REAL, volume REAL, PRIMARY KEY (symbol, date));
                CREATE INDEX IF NOT EXISTS idx_bars_sym ON daily_bars(symbol);
                
                CREATE TABLE IF NOT EXISTS watchlists (
                    name TEXT PRIMARY KEY, is_preset INTEGER DEFAULT 0,
                    created_at TEXT);
                CREATE TABLE IF NOT EXISTS watchlist_items (
                    watchlist_name TEXT, symbol TEXT, added_date TEXT, 
                    entry_price REAL, target_price REAL, stop_loss REAL, 
                    notes TEXT DEFAULT '', 
                    PRIMARY KEY (watchlist_name, symbol));
                
                INSERT OR IGNORE INTO watchlists (name, is_preset, created_at)
                VALUES ('My First List', 0, datetime('now'));
            """)



    # --- constituents ---
    def upsert_constituents(self, rows):
        with self._conn() as c:
            for r in rows:
                c.execute("""INSERT INTO constituents
                    (symbol,company_name,sector,industry,sub_sector,exchange,
                     market_cap,cap_segment,country,is_sp500,is_nasdaq,last_updated)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(symbol) DO UPDATE SET
                    company_name=excluded.company_name,
                    sector=excluded.sector,
                    industry=COALESCE(excluded.industry,industry),
                    sub_sector=COALESCE(excluded.sub_sector,sub_sector),
                    exchange=COALESCE(excluded.exchange,exchange),
                    market_cap=COALESCE(excluded.market_cap,market_cap),
                    cap_segment=COALESCE(excluded.cap_segment,cap_segment),
                    is_sp500=MAX(is_sp500,excluded.is_sp500),
                    is_nasdaq=MAX(is_nasdaq,excluded.is_nasdaq),
                    last_updated=excluded.last_updated""",
                (r.get("symbol"),r.get("company_name"),r.get("sector"),
                 r.get("industry"),r.get("sub_sector"),r.get("exchange"),
                 r.get("market_cap",0),r.get("cap_segment"),r.get("country","US"),
                 r.get("is_sp500",0),r.get("is_nasdaq",0),
                 datetime.utcnow().isoformat()))

    def get_all_constituents(self):
        with self._conn() as c:
            return [dict(r) for r in c.execute(
                "SELECT * FROM constituents ORDER BY market_cap DESC").fetchall()]

    def get_symbols(self):
        with self._conn() as c:
            return [r["symbol"] for r in c.execute("SELECT symbol FROM constituents").fetchall()]

    def get_sectors(self):
        with self._conn() as c:
            return [r["sector"] for r in c.execute(
                "SELECT DISTINCT sector FROM constituents WHERE sector IS NOT NULL ORDER BY sector").fetchall()]

    def get_industries(self, sectors=None):
        with self._conn() as c:
            if sectors:
                ph = ",".join("?"*len(sectors))
                rows = c.execute(f"SELECT DISTINCT industry FROM constituents WHERE sector IN ({ph}) AND industry IS NOT NULL ORDER BY industry", sectors).fetchall()
            else:
                rows = c.execute("SELECT DISTINCT industry FROM constituents WHERE industry IS NOT NULL ORDER BY industry").fetchall()
        return [r["industry"] for r in rows]

    def constituents_fresh(self):
        with self._conn() as c:
            row = c.execute("SELECT MIN(last_updated) as oldest FROM constituents").fetchone()
        if not row or not row["oldest"]: return False
        return (datetime.utcnow()-datetime.fromisoformat(row["oldest"])).total_seconds() < TTL_CONSTITUENTS*3600

    # --- fundamentals ---
    def get_stale_symbols(self, limit=100):
        """Identify symbols missing fundamentals or older than TTL."""
        with self._conn() as c:
            # First priority: missing data
            missing = c.execute("""
                SELECT c.symbol FROM constituents c
                LEFT JOIN fundamentals f ON c.symbol = f.symbol
                WHERE f.pe_ratio IS NULL OR f.last_updated IS NULL
                LIMIT ?""", (limit,)).fetchall()
            
            if len(missing) < limit:
                # Second priority: old data
                stale_limit = limit - len(missing)
                old = c.execute("""
                    SELECT symbol FROM fundamentals 
                    WHERE last_updated < datetime('now', '-7 days')
                    ORDER BY last_updated ASC
                    LIMIT ?""", (stale_limit,)).fetchall()
                return [r["symbol"] for r in missing] + [r["symbol"] for r in old]
            
            return [r["symbol"] for r in missing]

    def get_fundamentals_coverage(self):
        """Returns (count, total) of symbols with valid fundamentals."""
        with self._conn() as c:
            total = c.execute("SELECT COUNT(*) FROM constituents").fetchone()[0]
            count = c.execute("SELECT COUNT(*) FROM fundamentals WHERE pe_ratio IS NOT NULL").fetchone()[0]
            return count, total

    def upsert_fundamentals(self, rows):
        with self._conn() as c:
            for r in rows:
                c.execute("""INSERT INTO fundamentals
                    (symbol,price,change_pct,pe_ratio,pb_ratio,ps_ratio,peg_ratio,
                     roe,debt_equity,revenue_growth,eps_growth,eps,dividend_yield,
                     market_cap,beta,week52_high,week52_low,avg_volume,
                     price_avg50,price_avg200,last_updated)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(symbol) DO UPDATE SET
                    price=excluded.price,change_pct=excluded.change_pct,
                    pe_ratio=excluded.pe_ratio,pb_ratio=excluded.pb_ratio,
                    ps_ratio=excluded.ps_ratio,peg_ratio=excluded.peg_ratio,
                    roe=excluded.roe,debt_equity=excluded.debt_equity,
                    revenue_growth=excluded.revenue_growth,
                    eps_growth=excluded.eps_growth,eps=excluded.eps,
                    dividend_yield=excluded.dividend_yield,
                    market_cap=excluded.market_cap,beta=excluded.beta,
                    week52_high=excluded.week52_high,week52_low=excluded.week52_low,
                    avg_volume=excluded.avg_volume,price_avg50=excluded.price_avg50,
                    price_avg200=excluded.price_avg200,last_updated=excluded.last_updated""",
                (r.get("symbol"),r.get("price"),r.get("change_pct"),
                 r.get("pe_ratio"),r.get("pb_ratio"),r.get("ps_ratio"),
                 r.get("peg_ratio"),r.get("roe"),r.get("debt_equity"),
                 r.get("revenue_growth"),r.get("eps_growth"),r.get("eps"),
                 r.get("dividend_yield"),r.get("market_cap"),r.get("beta"),
                 r.get("week52_high"),r.get("week52_low"),r.get("avg_volume"),
                 r.get("price_avg50"),r.get("price_avg200"),
                 datetime.utcnow().isoformat()))

    def get_fundamentals(self, symbols=None):
        with self._conn() as c:
            if symbols:
                ph = ",".join("?"*len(symbols))
                rows = c.execute(f"SELECT * FROM fundamentals WHERE symbol IN ({ph})", symbols).fetchall()
            else:
                rows = c.execute("SELECT * FROM fundamentals").fetchall()
        return [dict(r) for r in rows]

    def fundamentals_fresh(self):
        with self._conn() as c:
            row = c.execute("SELECT MIN(last_updated) as oldest FROM fundamentals").fetchone()
            f_count = c.execute("SELECT COUNT(*) as cnt FROM fundamentals").fetchone()["cnt"]
            c_count = c.execute("SELECT COUNT(*) as cnt FROM constituents").fetchone()["cnt"]
        if not row or not row["oldest"]: return False
        # If we have significantly fewer fundamentals than constituents, it's not fresh
        if c_count > 0 and f_count < (c_count * 0.8): return False
        
        return (datetime.utcnow()-datetime.fromisoformat(row["oldest"])).total_seconds() < TTL_FUNDAMENTALS*3600

    # --- daily bars ---
    def upsert_bars(self, symbol, df):
        with self._conn() as c:
            for _, row in df.iterrows():
                c.execute("INSERT OR REPLACE INTO daily_bars (symbol,date,open,high,low,close,volume) VALUES (?,?,?,?,?,?,?)",
                    (symbol,str(row["date"]),row["open"],row["high"],row["low"],row["close"],row["volume"]))

    def get_bars(self, symbol, days=365):
        with self._conn() as c:
            rows = c.execute("SELECT * FROM daily_bars WHERE symbol=? ORDER BY date DESC LIMIT ?", (symbol,days)).fetchall()
        if not rows: return pd.DataFrame()
        df = pd.DataFrame([dict(r) for r in rows])
        df["date"] = pd.to_datetime(df["date"])
        return df.sort_values("date").reset_index(drop=True)

    def bars_fresh(self, symbol):
        with self._conn() as c:
            row = c.execute("SELECT MAX(date) as latest FROM daily_bars WHERE symbol=?", (symbol,)).fetchone()
        if not row or not row["latest"]: return False
        return (datetime.utcnow()-datetime.fromisoformat(str(row["latest"]))).total_seconds() < TTL_BARS*3600

    # --- watchlists ---
    # --- watchlists ---
    def _sync_with_central(self):
        try:
            import nuvama_watchlist_db as wl_db
        except ImportError:
            return
        
        try:
            # 1. Fetch central watchlists
            central_wls = wl_db.get_watchlists()
            central_names = [w["name"] for w in central_wls if w["name"].startswith("US_")]
            
            with self._conn() as c:
                # 2. Add/update central watchlists in local DB
                for w in central_wls:
                    name = w["name"]
                    if not name.startswith("US_"):
                        continue
                    c.execute("INSERT OR IGNORE INTO watchlists (name, is_preset, created_at) VALUES (?, 0, ?)",
                              (name, datetime.utcnow().isoformat()))
                    
                    # Fetch items from central and sync
                    central_items = wl_db.get_items(w["id"])
                    c.execute("DELETE FROM watchlist_items WHERE watchlist_name=?", (name,))
                    for item in central_items:
                        c.execute("""
                            INSERT OR IGNORE INTO watchlist_items 
                            (watchlist_name, symbol, added_date, target_price, stop_loss)
                            VALUES (?, ?, ?, ?, ?)
                        """, (name, item["symbol"], datetime.utcnow().isoformat(), item["target"], item["sl"]))
                
                # 3. Clean up any local non-preset watchlists starting with US_ that no longer exist centrally
                local_wls = c.execute("SELECT name FROM watchlists WHERE is_preset=0").fetchall()
                for lw in local_wls:
                    lname = lw["name"]
                    if lname.startswith("US_") and lname not in central_names:
                        c.execute("DELETE FROM watchlists WHERE name=?", (lname,))
                        c.execute("DELETE FROM watchlist_items WHERE watchlist_name=?", (lname,))
        except Exception as e:
            print(f"Error in US watchlist sync: {e}")

    def create_watchlist(self, name):
        if not name.startswith("US_"):
            name = "US_" + name
        try:
            import nuvama_watchlist_db as wl_db
            wl_db.create_watchlist(name)
        except ImportError:
            pass
        with self._conn() as c:
            c.execute("INSERT OR IGNORE INTO watchlists (name, is_preset, created_at) VALUES (?, 0, ?)",
                      (name, datetime.utcnow().isoformat()))

    def delete_watchlist(self, name):
        try:
            import nuvama_watchlist_db as wl_db
            if name.startswith("US_"):
                wid = wl_db.get_watchlist_id_by_name(name)
                if wid:
                    wl_db.delete_watchlist(wid)
        except ImportError:
            pass
        with self._conn() as c:
            c.execute("DELETE FROM watchlists WHERE name=? AND is_preset=0", (name,))
            c.execute("DELETE FROM watchlist_items WHERE watchlist_name=?", (name,))

    def rename_watchlist(self, old_name, new_name):
        if not new_name.startswith("US_"):
            new_name = "US_" + new_name
        try:
            import nuvama_watchlist_db as wl_db
            if old_name.startswith("US_"):
                wid = wl_db.get_watchlist_id_by_name(old_name)
                if wid:
                    wl_db.rename_watchlist(wid, new_name)
        except ImportError:
            pass
        with self._conn() as c:
            c.execute("UPDATE watchlists SET name=? WHERE name=? AND is_preset=0", (new_name, old_name))
            c.execute("UPDATE watchlist_items SET watchlist_name=? WHERE watchlist_name=?", (new_name, old_name))

    def get_watchlists(self):
        self._sync_with_central()
        with self._conn() as c:
            return [dict(r) for r in c.execute("SELECT * FROM watchlists WHERE name LIKE 'US_%' ORDER BY name ASC").fetchall()]

    def add_to_watchlist(self, watchlist_name, symbol, entry_price=None, target=None, stop=None, notes=""):
        try:
            import nuvama_watchlist_db as wl_db
            if watchlist_name.startswith("US_"):
                wid = wl_db.get_watchlist_id_by_name(watchlist_name)
                if wid:
                    wl_db.add_item(wid, symbol, target or 0, stop or 0)
        except ImportError:
            pass
        with self._conn() as c:
            c.execute("""INSERT INTO watchlist_items 
                (watchlist_name, symbol, added_date, entry_price, target_price, stop_loss, notes)
                VALUES (?,?,?,?,?,?,?) ON CONFLICT(watchlist_name, symbol) DO UPDATE SET
                entry_price=COALESCE(excluded.entry_price, entry_price),
                target_price=COALESCE(excluded.target_price, target_price),
                stop_loss=COALESCE(excluded.stop_loss, stop_loss),
                notes=COALESCE(excluded.notes, notes)""",
                (watchlist_name, symbol, datetime.utcnow().isoformat(), entry_price, target, stop, notes))

    def remove_from_watchlist(self, watchlist_name, symbol):
        try:
            import nuvama_watchlist_db as wl_db
            if watchlist_name.startswith("US_"):
                wid = wl_db.get_watchlist_id_by_name(watchlist_name)
                if wid:
                    wl_db.remove_item(wid, symbol)
        except ImportError:
            pass
        with self._conn() as c:
            c.execute("DELETE FROM watchlist_items WHERE watchlist_name=? AND symbol=?", (watchlist_name, symbol))

    def get_watchlist_items(self, watchlist_name):
        with self._conn() as c:
            return [dict(r) for r in c.execute(
                "SELECT * FROM watchlist_items WHERE watchlist_name=? ORDER BY added_date DESC", 
                (watchlist_name,)).fetchall()]

    def get_preset_symbols(self, preset_type, value):
        """Dynamic lookup for preset watchlists."""
        with self._conn() as c:
            if preset_type == "Index":
                if value == "S&P 500": return [r["symbol"] for r in c.execute("SELECT symbol FROM constituents WHERE is_sp500=1").fetchall()]
                if value == "NASDAQ-100": return [r["symbol"] for r in c.execute("SELECT symbol FROM constituents WHERE is_nasdaq=1").fetchall()]
            elif preset_type == "Sector":
                return [r["symbol"] for r in c.execute("SELECT symbol FROM constituents WHERE sector=?", (value,)).fetchall()]
            elif preset_type == "Cap Segment":
                return [r["symbol"] for r in c.execute("SELECT symbol FROM constituents WHERE cap_segment=?", (value,)).fetchall()]
        return []

    def update_watchlist_item_field(self, watchlist_name, symbol, field, value):
        allowed = {"entry_price", "target_price", "stop_loss", "notes"}
        if field not in allowed: return
        try:
            import nuvama_watchlist_db as wl_db
            if watchlist_name.startswith("US_") and field in {"target_price", "stop_loss"}:
                wid = wl_db.get_watchlist_id_by_name(watchlist_name)
                if wid:
                    items = wl_db.get_items(wid)
                    item = next((i for i in items if i["symbol"].upper() == symbol.upper()), None)
                    t_val = value if field == "target_price" else (item["target"] if item else 0)
                    s_val = value if field == "stop_loss" else (item["sl"] if item else 0)
                    wl_db.update_item_params(wid, symbol, t_val or 0, s_val or 0)
        except ImportError:
            pass
        with self._conn() as c:
            c.execute(f"UPDATE watchlist_items SET {field}=? WHERE watchlist_name=? AND symbol=?", 
                      (value, watchlist_name, symbol))


    # --- screening query ---
    def screen_fundamentals(self, filters):
        query = """SELECT c.symbol,c.company_name,c.sector,c.industry,c.cap_segment,
            c.is_sp500,c.is_nasdaq,f.price,f.change_pct,f.pe_ratio,f.pb_ratio,
            f.ps_ratio,f.peg_ratio,f.roe,f.debt_equity,f.revenue_growth,
            f.eps_growth,f.eps,f.dividend_yield,f.market_cap,f.beta,
            f.week52_high,f.week52_low,f.avg_volume,f.price_avg50,f.price_avg200
            FROM constituents c LEFT JOIN fundamentals f ON c.symbol=f.symbol
            WHERE f.price IS NOT NULL"""
        params = []
        syms = filters.get("specific_symbols")
        if syms:
            query += f" AND c.symbol IN ({','.join('?'*len(syms))})"
            params.extend(syms)
        
        uni = filters.get("universe","Combined")
        if uni == "S&P 500": query += " AND c.is_sp500=1"
        elif uni == "NASDAQ-100": query += " AND c.is_nasdaq=1"
        segs = filters.get("cap_segments")
        if segs:
            query += f" AND c.cap_segment IN ({','.join('?'*len(segs))})"
            params.extend(segs)
        secs = filters.get("sectors")
        if secs:
            query += f" AND c.sector IN ({','.join('?'*len(secs))})"
            params.extend(secs)
        inds = filters.get("industries")
        if inds:
            query += f" AND c.industry IN ({','.join('?'*len(inds))})"
            params.extend(inds)
        rng = {"pe_min":("f.pe_ratio",">="),"pe_max":("f.pe_ratio","<="),
               "pb_max":("f.pb_ratio","<="),"peg_max":("f.peg_ratio","<="),
               "roe_min":("f.roe",">="),"debt_equity_max":("f.debt_equity","<="),
               "rev_growth_min":("f.revenue_growth",">="),
               "eps_growth_min":("f.eps_growth",">="),
               "div_yield_min":("f.dividend_yield",">="),
               "div_yield_max":("f.dividend_yield","<="),
               "market_cap_min":("f.market_cap",">="),
               "market_cap_max":("f.market_cap","<="),
               "beta_max":("f.beta","<="),
               "price_min":("f.price",">="),
               "volume_min":("f.avg_volume",">="),
               "change_pct_min":("f.change_pct",">="),
               "change_pct_max":("f.change_pct","<=")}
        for key,(col,op) in rng.items():
            val = filters.get(key)
            if val is not None:
                query += f" AND {col} {op} ?"
                params.append(val)
        sma200 = filters.get("price_vs_sma200")
        if sma200 == "Above": query += " AND f.price>f.price_avg200 AND f.price_avg200>0"
        elif sma200 == "Below": query += " AND f.price<f.price_avg200 AND f.price_avg200>0"
        sma50 = filters.get("price_vs_sma50")
        if sma50 == "Above": query += " AND f.price>f.price_avg50 AND f.price_avg50>0"
        elif sma50 == "Below": query += " AND f.price<f.price_avg50 AND f.price_avg50>0"
        query += " ORDER BY f.market_cap DESC"
        with self._conn() as c:
            return [dict(r) for r in c.execute(query, params).fetchall()]
