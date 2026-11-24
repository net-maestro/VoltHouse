import io
import math
from datetime import datetime, timedelta
from typing import List
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

from app.config import settings
from app.database.models import OutageLog, TelemetryLog


def generate_house_charts_png(
    house_name: str,
    telemetry_logs: List[TelemetryLog],
    outage_logs: List[OutageLog],
) -> bytes:
    """
    Generate a 3-panel PNG dashboard for /charts:
    1) Battery SOC (%) over the last 24h + 20% critical elevator line
    2) House power consumption (kW) over the last 24h
    3) Bar chart of outage durations (hours) per day over the last 7 days
    """
    now = datetime.now()

    # Prepare 24h telemetry series (fallback to synthetic curve if DB just started)
    if len(telemetry_logs) >= 2:
        times = [t.recorded_at for t in telemetry_logs]
        socs = [t.soc for t in telemetry_logs]
        loads = [t.load_power_kw for t in telemetry_logs]
    else:
        times = [now - timedelta(minutes=15 * i) for i in range(96, -1, -1)]
        socs = [round(75.0 + 20.0 * math.sin(i / 12.0), 1) for i in range(len(times))]
        loads = [round(6.0 + 2.5 * abs(math.cos(i / 10.0)), 2) for i in range(len(times))]

    # Prepare 7-day outage duration buckets
    day_labels: List[str] = []
    day_hours: List[float] = []
    for d_offset in range(6, -1, -1):
        day_dt = (now - timedelta(days=d_offset)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        next_day_dt = day_dt + timedelta(days=1)
        day_labels.append(day_dt.strftime("%d.%m"))

        total_sec = 0.0
        for log in outage_logs:
            start = max(log.start_time, day_dt)
            end = min(log.end_time or now, next_day_dt)
            if end > start:
                total_sec += (end - start).total_seconds()
        day_hours.append(round(total_sec / 3600.0, 2))

    # Plot styling
    plt.rcParams.update(
        {
            "figure.facecolor": "#121826",
            "axes.facecolor": "#1E293B",
            "axes.edgecolor": "#334155",
            "axes.labelcolor": "#E2E8F0",
            "xtick.color": "#94A3B8",
            "ytick.color": "#94A3B8",
            "text.color": "#F8FAFC",
            "grid.color": "#334155",
            "grid.linestyle": "--",
            "grid.alpha": 0.6,
            "font.sans-serif": ["DejaVu Sans", "Arial", "sans-serif"],
        }
    )

    fig, (ax1, ax2, ax3) = plt.subplots(3, 1, figsize=(10, 12), dpi=130)
    fig.suptitle(
        f"Енергетична аналітика — {house_name} ({now.strftime('%d.%m.%Y %H:%M')})",
        fontsize=14,
        fontweight="bold",
        color="#F8FAFC",
        y=0.98,
    )

    # Panel 1: Battery SOC (%)
    ax1.plot(times, socs, color="#22C55E", linewidth=2.4, label="Заряд АКБ (SOC %)")
    ax1.fill_between(times, socs, 0, color="#22C55E", alpha=0.15)
    ax1.axhline(
        settings.ELEVATOR_CRITICAL_SOC,
        color="#EF4444",
        linestyle="--",
        linewidth=1.8,
        label=f"Критичний рівень ліфта ({int(settings.ELEVATOR_CRITICAL_SOC)}%)",
    )
    ax1.set_title("Динаміка заряду батарей за 24 години (%)", fontsize=11, pad=8)
    ax1.set_ylabel("Заряд (%)")
    ax1.set_ylim(0, 105)
    ax1.grid(True)
    ax1.legend(loc="lower left", facecolor="#0F172A", edgecolor="#334155")
    ax1.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))

    # Panel 2: Load Power (kW)
    ax2.plot(times, loads, color="#38BDF8", linewidth=2.2, label="Споживання будинку (кВт)")
    ax2.fill_between(times, loads, 0, color="#38BDF8", alpha=0.18)
    ax2.set_title("Потужність споживання будинку за 24 години (кВт)", fontsize=11, pad=8)
    ax2.set_ylabel("Потужність (кВт)")
    ax2.set_ylim(0, max(max(loads, default=10.0) * 1.25, 5.0))
    ax2.grid(True)
    ax2.legend(loc="upper right", facecolor="#0F172A", edgecolor="#334155")
    ax2.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))

    # Panel 3: Weekly Outage Durations (Bar Chart)
    bars = ax3.bar(day_labels, day_hours, color="#F59E0B", width=0.55, edgecolor="#FBBF24")
    ax3.set_title("Тривалість відключень по днях за тиждень (год)", fontsize=11, pad=8)
    ax3.set_ylabel("Годин без світла")
    ax3.set_ylim(0, max(max(day_hours, default=4.0) * 1.3, 4.0))
    ax3.grid(True, axis="y")

    for bar, val in zip(bars, day_hours):
        if val > 0:
            ax3.text(
                bar.get_x() + bar.get_width() / 2.0,
                bar.get_height() + 0.15,
                f"{val:g} год",
                ha="center",
                va="bottom",
                fontsize=9,
                color="#FDE68A",
                fontweight="bold",
            )

    plt.tight_layout(rect=[0, 0, 1, 0.96])

    buf = io.BytesIO()
    fig.savefig(buf, format="png")
    plt.close(fig)
    buf.seek(0)
    return buf.read()
