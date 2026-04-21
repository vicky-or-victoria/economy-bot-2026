import io
import math
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.gridspec import GridSpec
import numpy as np
from datetime import datetime


# ── Palette ───────────────────────────────────────────────────────────────────
BG       = "#0d0d14"
PANEL    = "#13131f"
GRID     = "#1e1e30"
TEXT     = "#c8c8e0"
SUBTEXT  = "#6b6b90"
ACCENT   = "#00d4aa"
COLORS   = ["#00d4aa", "#e63946", "#f4a261", "#a8dadc", "#e9c46a",
            "#264653", "#2a9d8f", "#e76f51", "#8ecae6", "#ffb703"]


def _sparkline_color(prices: list[float]) -> str:
    if len(prices) < 2:
        return ACCENT
    return "#2a9d8f" if prices[-1] >= prices[0] else "#e63946"


def generate_market_overview(stocks_data: list[dict]) -> io.BytesIO:
    """
    stocks_data: list of {ticker, name, history: [(price, ts), ...]}
    Returns a BytesIO PNG.
    """
    n = len(stocks_data)
    if n == 0:
        return _empty_chart("No stocks listed yet.")

    cols = min(3, n)
    rows = math.ceil(n / cols)
    fig_w = cols * 4 + 1
    fig_h = rows * 2.8 + 1.2

    fig = plt.figure(figsize=(fig_w, fig_h), facecolor=BG)
    fig.suptitle("LIVE MARKET", fontsize=13, color=TEXT, fontweight="bold",
                 fontfamily="monospace", y=0.98)

    for i, stock in enumerate(stocks_data):
        ax = fig.add_subplot(rows, cols, i + 1)
        ax.set_facecolor(PANEL)
        for spine in ax.spines.values():
            spine.set_visible(False)
        ax.tick_params(colors=SUBTEXT, labelsize=7)
        ax.yaxis.tick_right()
        ax.set_xticks([])

        history = stock.get("history", [])
        prices = [float(p) for p, _ in history][::-1]

        if len(prices) >= 2:
            color = _sparkline_color(prices)
            xs = list(range(len(prices)))
            ax.plot(xs, prices, color=color, linewidth=1.5, solid_capstyle="round")
            ax.fill_between(xs, prices, alpha=0.12, color=color)
        elif len(prices) == 1:
            ax.axhline(prices[0], color=ACCENT, linewidth=1)

        current = prices[-1] if prices else stock.get("current_price", 0)
        change_pct = 0.0
        if len(prices) >= 2:
            change_pct = (prices[-1] - prices[0]) / max(prices[0], 0.0001) * 100
        chg_color = "#2a9d8f" if change_pct >= 0 else "#e63946"
        sign = "+" if change_pct >= 0 else ""

        ax.set_title(
            f"{stock['ticker']}  {sign}{change_pct:.1f}%",
            fontsize=8, color=chg_color, fontfamily="monospace", pad=4
        )
        ax.text(0.02, 0.88, f"{current:.2f}", transform=ax.transAxes,
                fontsize=9, color=TEXT, fontfamily="monospace", fontweight="bold")
        ax.grid(axis="y", color=GRID, linewidth=0.5)

    fig.tight_layout(rect=[0, 0, 1, 0.96])
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=130, bbox_inches="tight", facecolor=BG)
    plt.close(fig)
    buf.seek(0)
    return buf


def generate_business_chart(ticker: str, name: str, history: list[tuple]) -> io.BytesIO:
    """
    history: list of (price, timestamp) tuples, newest first
    Returns a BytesIO PNG.
    """
    fig, ax = plt.subplots(figsize=(6, 2.4), facecolor=BG)
    ax.set_facecolor(PANEL)
    for spine in ax.spines.values():
        spine.set_visible(False)
    ax.tick_params(colors=SUBTEXT, labelsize=7)
    ax.yaxis.tick_right()
    ax.set_xticks([])

    prices = [float(p) for p, _ in history][::-1]

    if len(prices) >= 2:
        color = _sparkline_color(prices)
        xs = list(range(len(prices)))
        ax.plot(xs, prices, color=color, linewidth=2, solid_capstyle="round")
        ax.fill_between(xs, prices, alpha=0.15, color=color)
        change_pct = (prices[-1] - prices[0]) / max(prices[0], 0.0001) * 100
        sign = "+" if change_pct >= 0 else ""
        chg_color = "#2a9d8f" if change_pct >= 0 else "#e63946"
        ax.set_title(
            f"{ticker} — {name}   {sign}{change_pct:.1f}%",
            fontsize=9, color=chg_color, fontfamily="monospace", pad=6
        )
    else:
        ax.set_title(f"{ticker} — {name}", fontsize=9, color=TEXT,
                     fontfamily="monospace", pad=6)

    current = prices[-1] if prices else 0
    ax.text(0.01, 0.85, f"{current:.4f}", transform=ax.transAxes,
            fontsize=10, color=TEXT, fontfamily="monospace", fontweight="bold")
    ax.grid(axis="y", color=GRID, linewidth=0.5)

    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=130, bbox_inches="tight", facecolor=BG)
    plt.close(fig)
    buf.seek(0)
    return buf


def _empty_chart(message: str) -> io.BytesIO:
    fig, ax = plt.subplots(figsize=(6, 2), facecolor=BG)
    ax.set_facecolor(BG)
    ax.axis("off")
    ax.text(0.5, 0.5, message, transform=ax.transAxes,
            ha="center", va="center", color=SUBTEXT, fontsize=10)
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=100, bbox_inches="tight", facecolor=BG)
    plt.close(fig)
    buf.seek(0)
    return buf
