# utils/helpers.py — Shared utility functions
import io


def format_duration(seconds):
    """Format seconds into a human-readable duration."""
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}m"
    hours = minutes // 60
    mins = minutes % 60
    return f"{hours}h {mins}m" if mins else f"{hours}h"


def generate_history_graph(dates: list, rates: list, base_currency: str, target_currency: str, num_days: int):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt

    plt.style.use('dark_background')
    fig, ax = plt.subplots()
    ax.set_title(f"{num_days}-Day History: {base_currency} to {target_currency}", color='white')
    ax.plot(dates, rates, marker='o', linestyle='-', color='cyan')
    ax.set_xlabel("Date", color='white')
    ax.set_ylabel(f"Rate (1 {base_currency} = X {target_currency})", color='white')
    ax.tick_params(axis='x', colors='white', rotation=45)
    ax.tick_params(axis='y', colors='white')
    ax.grid(True, which='both', linestyle='--', linewidth=0.5, color='#444444')
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format='png')
    buf.seek(0)
    plt.close(fig)
    return buf
