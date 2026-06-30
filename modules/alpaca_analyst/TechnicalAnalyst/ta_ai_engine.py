#!/usr/bin/env python3
"""
ta_ai_engine.py — Technical Analyst Pro
AI insights via Google Gemini (new google-genai SDK) and Perplexity Sonar Pro.

Model preference / fallback chain (April 2026):
  1. gemini-3.1-pro-preview   (latest flagship, needs paid quota)
  2. gemini-2.5-pro           (stable 2.5 Pro, excellent quality)
  3. gemini-2.5-flash         (stable Flash — fast, near-Pro quality)
  4. gemini-2.5-flash-lite    (lightweight fallback)
  + Perplexity sonar-pro      (always run as independent second opinion)
  + Perplexity sonar-reasoning-pro  (reasoning fallback for Perplexity)

SDK note: uses the NEW `google-genai` package (import google.genai),
NOT the deprecated `google-generativeai` package.
Install: pip install google-genai
API version: v1alpha (required to access preview / latest models)
"""

from __future__ import annotations
import os, sys
from datetime import datetime
from typing import Optional, Callable

try:
    import requests
    REQUESTS_OK = True
except ImportError:
    REQUESTS_OK = False

# ── New Google Gen AI SDK ─────────────────────────────────────────────────
try:
    from google import genai as _google_genai
    from google.genai import types as _genai_types
    GENAI_OK = True
except ImportError:
    GENAI_OK = False

# ── PIL for image reading (vision mode) ──────────────────────────────────
try:
    from PIL import Image as PILImage
    PIL_OK = True
except ImportError:
    PIL_OK = False


# ═══════════════════════════════════════════════════════════════════════════
#  MODEL DEFINITIONS  (updated April 2026)
# ═══════════════════════════════════════════════════════════════════════════
# Try each Gemini model in order; skip on quota / rate-limit / 404 errors.
# Uses v1alpha API endpoint so preview models are accessible.
#
# Chain rationale:
#   gemini-3.1-pro-preview  — latest flagship (April 2026), best quality
#   gemini-2.5-pro          — stable 2.5 Pro, robust fallback
#   gemini-2.5-flash        — stable Flash, fast + near-Pro quality
#   gemini-2.5-flash-lite   — lightest model, almost always available
GEMINI_TEXT_CHAIN = [
    "gemini-3.1-pro-preview",   # Current top model (replaces 3-pro-preview, Apr 2026)
    "gemini-2.5-pro",           # Stable 2.5 Pro
    "gemini-2.5-flash",         # Stable Flash (fast, capable)
    "gemini-2.5-flash-lite",    # Lightweight fallback
]

GEMINI_VISION_CHAIN = [
    "gemini-3.1-pro-preview",
    "gemini-2.5-pro",
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
]

PERPLEXITY_URL            = "https://api.perplexity.ai/chat/completions"
PERPLEXITY_MODEL          = "sonar-pro"           # Primary: Sonar Pro (search-augmented)
PERPLEXITY_REASONING_MODEL = "sonar-reasoning-pro" # Fallback: chain-of-thought reasoning

# Errors that indicate we should skip to the next model (quota / rate / not found)
_SKIP_ERRORS = (
    "quota", "rate", "429", "resource_exhausted",
    "not found", "invalid", "404", "model_not_found",
    "permission", "billing",
)


def _is_skip_error(exc: Exception) -> bool:
    msg = str(exc).lower()
    return any(kw in msg for kw in _SKIP_ERRORS)


# ═══════════════════════════════════════════════════════════════════════════
#  GEMINI CLIENT WRAPPER
# ═══════════════════════════════════════════════════════════════════════════
class GeminiClient:
    """
    Thin wrapper around the new google.genai SDK.
    Tries each model in the chain; falls back gracefully on quota/errors.
    """

    def __init__(self, api_key: str):
        self._key     = api_key
        self._client  = None
        self._init()

    def _init(self):
        if not GENAI_OK or not self._key:
            return
        try:
            # v1alpha gives access to the latest preview models (gemini-3.1-pro-preview etc.)
            # v1beta is the SDK default but misses many current preview model IDs
            self._client = _google_genai.Client(
                api_key=self._key,
                http_options={"api_version": "v1alpha"},
            )
        except Exception as e:
            print(f"[GeminiClient] init error: {e}")
            self._client = None

    def generate_text(
        self,
        prompt: str,
        model_chain: list = None,
        progress_cb: Optional[Callable] = None,
    ) -> tuple[str, str]:
        """
        Generate text using the first working model in the chain.
        Returns (response_text, model_used) or ("", "") on total failure.
        """
        if self._client is None:
            return "", ""

        chain = model_chain or GEMINI_TEXT_CHAIN
        for model_id in chain:
            try:
                if progress_cb:
                    progress_cb(f"Querying {model_id}…")
                response = self._client.models.generate_content(
                    model=model_id,
                    contents=prompt,
                    config=_genai_types.GenerateContentConfig(
                        temperature=0.3,
                        max_output_tokens=1024,
                    ),
                )
                text = response.text or ""
                if text.strip():
                    return text, model_id
            except Exception as e:
                print(f"[Gemini] {model_id} failed: {e}")
                if not _is_skip_error(e):
                    # Non-retriable error (e.g. bad API key) — stop chain
                    return "", ""
                # Retriable (quota/rate/not-found) — try next model
                continue

        return "", ""

    def generate_with_image(
        self,
        image_path: str,
        prompt: str,
        model_chain: list = None,
        progress_cb: Optional[Callable] = None,
    ) -> tuple[str, str]:
        """
        Generate text from image + prompt. Returns (response_text, model_used).
        """
        if self._client is None or not os.path.exists(image_path):
            return "", ""

        # Load image bytes for the new SDK
        try:
            with open(image_path, "rb") as f:
                img_bytes = f.read()
            ext  = os.path.splitext(image_path)[1].lower().lstrip(".")
            mime = {
                "png": "image/png", "jpg": "image/jpeg",
                "jpeg": "image/jpeg", "webp": "image/webp",
                "gif": "image/gif",
            }.get(ext, "image/png")
        except Exception as e:
            print(f"[Gemini vision] Image read error: {e}")
            return "", ""

        chain = model_chain or GEMINI_VISION_CHAIN
        for model_id in chain:
            try:
                if progress_cb:
                    progress_cb(f"Sending chart to {model_id}…")

                image_part = _genai_types.Part.from_bytes(
                    data=img_bytes, mime_type=mime
                )
                response = self._client.models.generate_content(
                    model=model_id,
                    contents=[image_part, prompt],
                    config=_genai_types.GenerateContentConfig(
                        temperature=0.2,
                        max_output_tokens=2048,
                    ),
                )
                text = response.text or ""
                if text.strip():
                    return text, model_id
            except Exception as e:
                print(f"[Gemini vision] {model_id} failed: {e}")
                if not _is_skip_error(e):
                    return "", ""
                continue

        return "", ""

    @property
    def available(self) -> bool:
        return self._client is not None


# ═══════════════════════════════════════════════════════════════════════════
#  AI INSIGHTS ENGINE
# ═══════════════════════════════════════════════════════════════════════════
class AIInsightsEngine:
    """
    Orchestrates AI commentary with full model fallback:
      Gemini 2.5 Pro  →  Gemini 2.5 Flash  →  Perplexity Sonar Pro  →  Gemini 2.0 Flash
    """

    def __init__(self, credentials: dict = None):
        self.creds  = credentials or {}
        self._gemini = GeminiClient(self.creds.get("gemini_key", ""))

    # ── Image mode (Gemini vision) ────────────────────────────────────────
    def analyze_image(
        self,
        image_path: str,
        progress_cb: Optional[Callable] = None,
    ) -> dict:
        """Analyze a chart image using Gemini Vision."""
        if not self._gemini.available:
            return self._unavailable(
                "Gemini API key not configured or google-genai not installed.\n"
                "Install: pip install google-genai"
            )

        if progress_cb:
            progress_cb("Preparing chart for Gemini Vision analysis…")

        text, model_used = self._gemini.generate_with_image(
            image_path,
            self._vision_prompt(),
            progress_cb=progress_cb,
        )

        if not text:
            return self._unavailable(
                "All Gemini vision models failed or returned empty responses. "
                "Check API key and quota."
            )

        if progress_cb:
            progress_cb(f"Vision analysis complete ({model_used})")

        return {
            "source":    "gemini_vision",
            "model":     model_used,
            "raw":       text,
            "combined":  f"### Gemini Vision Analysis  _(model: {model_used})_\n\n{text}",
            "timestamp": datetime.now().isoformat(),
        }

    # ── Data mode (text → Gemini + Perplexity) ───────────────────────────
    def generate_insights(
        self,
        analysis: dict,
        use_perplexity: bool = True,
        progress_cb: Optional[Callable] = None,
    ) -> dict:
        """Generate AI narrative from computed TA analysis dict."""
        result = {
            "gemini":     None,
            "perplexity": None,
            "combined":   "",
            "models_used": [],
            "timestamp":  datetime.now().isoformat(),
        }

        summary = self._analysis_to_text(analysis)

        # ── 1. Gemini (try full chain) ────────────────────────────────────
        if self._gemini.available:
            if progress_cb:
                progress_cb("Requesting Gemini AI commentary (Pro → Flash chain)…")

            text, model_used = self._gemini.generate_text(
                self._text_prompt(summary),
                progress_cb=progress_cb,
            )
            if text:
                result["gemini"]    = text
                result["models_used"].append(model_used)
                if progress_cb:
                    progress_cb(f"Gemini response received ({model_used})")
            else:
                result["gemini"] = None
                if progress_cb:
                    progress_cb("Gemini chain exhausted — trying Perplexity…")

        # ── 2. Perplexity (independent second opinion) ────────────────────
        pplx_key = self.creds.get("perplexity_key", "")
        if use_perplexity and pplx_key and REQUESTS_OK:
            # Always try Perplexity regardless of whether Gemini succeeded
            if progress_cb:
                progress_cb("Querying Perplexity Sonar Pro…")
            pplx = self._query_perplexity(summary, pplx_key)
            if pplx and not pplx.startswith("["):
                result["perplexity"] = pplx
                result["models_used"].append("perplexity/sonar-pro")
            else:
                result["perplexity"] = pplx  # keep error text for debugging

        # ── 3. Fallback: Gemini 2.0 Flash if everything above failed ─────
        if not result["gemini"] and not result.get("perplexity"):
            if self._gemini.available:
                if progress_cb:
                    progress_cb("Falling back to Gemini 2.0 Flash…")
                text, model_used = self._gemini.generate_text(
                    self._text_prompt(summary),
                    model_chain=["gemini-2.0-flash"],
                    progress_cb=progress_cb,
                )
                if text:
                    result["gemini"]    = text
                    result["models_used"].append(model_used)

        # ── Build combined output ─────────────────────────────────────────
        parts = []
        if result["gemini"] and not result["gemini"].startswith("["):
            model_tag = result["models_used"][0] if result["models_used"] else "Gemini"
            parts.append(
                f"### AI Commentary  _(model: {model_tag})_\n\n{result['gemini']}"
            )
        if result["perplexity"] and not result["perplexity"].startswith("["):
            parts.append(
                f"### Perplexity Sonar Pro — Second Opinion\n\n{result['perplexity']}"
            )

        if parts:
            result["combined"] = "\n\n---\n\n".join(parts)
        else:
            errors = []
            if not self._gemini.available:
                errors.append("Gemini: API key not configured or google-genai not installed")
            if not pplx_key:
                errors.append("Perplexity: API key not configured")
            if (result.get("gemini") or "").startswith("["):
                errors.append(f"Gemini error: {result['gemini']}")
            if (result.get("perplexity") or "").startswith("["):
                errors.append(f"Perplexity error: {result['perplexity']}")
            result["combined"] = (
                "**AI Insights could not be generated.**\n\n"
                + "\n".join(f"- {e}" for e in errors)
                + "\n\nCheck Settings > API Keys and ensure google-genai is installed."
            )

        return result

    # ── Perplexity ────────────────────────────────────────────────────────
    def _query_perplexity(self, summary: str, key: str) -> str:
        """
        Try sonar-pro first; fall back to sonar-reasoning-pro if the primary
        model is unavailable or credit-limited.
        """
        system_msg = (
            "You are a professional technical analyst. "
            "Provide concise, objective commentary on the chart data presented. "
            "Focus on actionable insights, key risk levels, and probability-weighted "
            "outcomes. Be precise and professional. "
            "Do NOT reference news, earnings, or fundamentals."
        )
        user_msg = self._perplexity_prompt(summary)
        headers  = {
            "Authorization": f"Bearer {key}",
            "Content-Type":  "application/json",
        }

        for model in (PERPLEXITY_MODEL, PERPLEXITY_REASONING_MODEL):
            try:
                resp = requests.post(
                    PERPLEXITY_URL,
                    headers=headers,
                    json={
                        "model": model,
                        "messages": [
                            {"role": "system", "content": system_msg},
                            {"role": "user",   "content": user_msg},
                        ],
                        "max_tokens": 600,
                        "temperature": 0.3,
                    },
                    timeout=45,
                )
                if resp.status_code == 200:
                    return resp.json()["choices"][0]["message"]["content"]
                # 402 = payment/credit required → try next model
                # 429 = rate limited → try next model
                err_txt = resp.text[:200]
                print(f"[Perplexity] {model} HTTP {resp.status_code}: {err_txt}")
                if resp.status_code not in (402, 429, 404):
                    # Unexpected error — don't bother retrying with other model
                    return f"[Perplexity HTTP {resp.status_code}: {err_txt}]"
                # otherwise fall through to next model
            except Exception as e:
                print(f"[Perplexity] {model} error: {e}")
                return f"[Perplexity error: {e}]"

        return "[Perplexity] All models exhausted (quota/credit limit reached)"

    # ── Prompt builders ───────────────────────────────────────────────────
    def _vision_prompt(self) -> str:
        return (
            "Analyze this financial price chart using ONLY pure technical analysis — "
            "no news, fundamentals, or sentiment.\n\n"
            "Provide a structured report:\n\n"
            "1. **TREND**: Direction (uptrend/downtrend/sideways), strength (strong/moderate/weak), "
            "structural evidence (HH/HL or LH/LL pattern).\n\n"
            "2. **SUPPORT & RESISTANCE**: The 3 most significant visible levels, "
            "with rationale for each (touch count, confluence, significance).\n\n"
            "3. **MOVING AVERAGES**: Any visible MAs — their alignment, slope, "
            "and whether they act as dynamic support or resistance.\n\n"
            "4. **VOLUME**: Volume trend and whether it confirms or diverges from price action.\n\n"
            "5. **CHART PATTERNS**: Any candlestick formations, chart patterns, or noteworthy "
            "price action (flags, wedges, head & shoulders, double tops/bottoms, etc.).\n\n"
            "6. **PROBABILISTIC SCENARIOS** (probabilities must sum to 100%):\n"
            "   - Bull Case (XX%): trigger condition, price target, invalidation level\n"
            "   - Base Case (XX%): most likely near-term action\n"
            "   - Bear Case (XX%): trigger condition, price target, invalidation level\n\n"
            "7. **KEY INSIGHT**: One concise sentence — the single most important "
            "technical observation from this chart.\n\n"
            "Use professional, objective language. Quantify everything possible."
        )

    def _text_prompt(self, summary: str) -> str:
        return (
            "You are a senior technical analyst. Based on the following computed technical "
            "analysis data, write a professional commentary of 280-380 words.\n\n"
            "TECHNICAL ANALYSIS DATA:\n"
            f"{summary}\n\n"
            "Your commentary must:\n"
            "1. Synthesise the most significant signals into a coherent directional narrative\n"
            "2. Explain what the current chart structure implies for near-term price action\n"
            "3. Name the single most critical price level to watch (and why)\n"
            "4. Acknowledge any conflicting signals or structural ambiguity\n"
            "5. Close with a concise risk management observation\n\n"
            "Style: third-person, professional, flowing paragraphs (no bullet points), "
            "precise price levels where relevant. Base the analysis ONLY on the data provided."
        )

    def _perplexity_prompt(self, summary: str) -> str:
        return (
            "Provide a second-opinion technical analysis on the following data "
            "(150-200 words, professional tone, no bullet points).\n\n"
            f"DATA:\n{summary}\n\n"
            "Focus on: (1) the dominant technical bias, "
            "(2) the one level that would definitively change the outlook, "
            "(3) one risk factor the primary analysis may be underweighting."
        )

    # ── Analysis → text ───────────────────────────────────────────────────
    def _analysis_to_text(self, analysis: dict) -> str:
        sym   = analysis.get("symbol", "Unknown")
        price = analysis.get("current_price", 0)
        date  = analysis.get("analysis_date", "")
        td    = analysis.get("trend", {})
        ma    = analysis.get("moving_averages", {})
        vd    = analysis.get("volume", {})
        sup   = analysis.get("support_levels", [])
        res   = analysis.get("resistance_levels", [])
        pats  = analysis.get("patterns", [])
        scens = analysis.get("scenarios", [])
        obs   = analysis.get("key_observations", [])

        lines = [
            f"Symbol: {sym}  |  Date: {date}  |  Price: ${price:,.2f}",
            f"Performance: 1-bar {analysis.get('chg_1bar',0):+.1f}%  |  "
            f"4-bar {analysis.get('chg_4bar',0):+.1f}%  |  "
            f"52-bar {analysis.get('chg_52bar',0):+.1f}%",
            "",
            f"TREND: {td.get('direction','?')} / {td.get('strength','?')}  |  RSI {td.get('rsi',50):.0f}",
            f"  Structure: {td.get('hh',0)} HH / {td.get('hl',0)} HL / "
            f"{td.get('lh',0)} LH / {td.get('ll',0)} LL  |  "
            f"Duration ~{td.get('duration_bars','?')} bars",
            f"  {td.get('description','')}",
        ]
        if td.get("exhaustion_signals"):
            lines.append(f"  Exhaustion signals: {', '.join(td['exhaustion_signals'])}")

        lines += ["", f"MOVING AVERAGES: alignment = {ma.get('alignment','?')}"]
        for p in [20, 50, 200]:
            m = ma.get(f"ma{p}")
            if m:
                lines.append(
                    f"  MA{p}: ${m['value']:,.2f}  |  {m['price_relation']}  |  "
                    f"slope {m['slope']}  |  {m['distance_pct']:+.1f}% from price"
                )
        if ma.get("golden_cross"): lines.append("  *** GOLDEN CROSS recently detected ***")
        if ma.get("death_cross"):  lines.append("  *** DEATH CROSS recently detected ***")

        lines += ["", "SUPPORT LEVELS:"]
        for s in sup[:4]:
            lines.append(
                f"  ${s['price']:,.2f}  ({s['strength']}, {s['touches']} touches, "
                f"{s['distance_pct']:.1f}% away)"
            )

        lines += ["", "RESISTANCE LEVELS:"]
        for r in res[:4]:
            lines.append(
                f"  ${r['price']:,.2f}  ({r['strength']}, {r['touches']} touches, "
                f"{r['distance_pct']:.1f}% away)"
            )

        lines += [
            "",
            f"VOLUME: {vd.get('trend','?')} ({vd.get('trend_pct',0):+.1f}%)  |  "
            f"confirmation: {vd.get('confirmation','?').replace('_',' ')}",
        ]
        if vd.get("spike"):
            lines.append(f"  Volume spike: {vd.get('spike_ratio',1):.1f}x 20-bar average")

        if pats:
            lines += ["", "IDENTIFIED PATTERNS:"]
            for p in pats:
                lines.append(f"  {p['name']} ({p['direction']}, {p['confidence']}) — {p['description']}")

        lines += ["", "PROBABILISTIC SCENARIOS:"]
        for sc in scens:
            lines.append(f"  [{sc['probability']}%]  {sc['name']}")
            lines.append(f"    {sc['description']}")
            tgts = ", ".join(f"${t:,.2f}" for t in sc.get("target_levels", []))
            lines.append(
                f"    Targets: {tgts}  |  Invalidation: ${sc.get('invalidation_level',0):,.2f}"
            )

        lines += ["", "KEY OBSERVATIONS:"]
        for o in obs:
            lines.append(f"  * {o}")

        return "\n".join(lines)

    # ── Utility ───────────────────────────────────────────────────────────
    def _unavailable(self, reason: str) -> dict:
        return {
            "source":    "none",
            "model":     "",
            "raw":       "",
            "combined":  f"**AI insights unavailable:** {reason}",
            "timestamp": datetime.now().isoformat(),
        }

    def is_configured(self) -> dict:
        return {
            "gemini":     self._gemini.available,
            "perplexity": bool(self.creds.get("perplexity_key")),
            "genai_sdk":  GENAI_OK,
        }
