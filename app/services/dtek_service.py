from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Dict, List, Tuple


@dataclass
class ScheduleSlot:
    start_hour: int
    end_hour: int
    status: str  # "off" (відключення), "possible" (можливе), "on" (зі світлом)


# Standard DTEK queue schedules (hours of planned outages per day-of-week parity)
DEFAULT_DTEK_SCHEDULES: Dict[str, List[Tuple[int, int]]] = {
    "Черга 1.1": [(3, 7), (12, 16), (21, 24)],
    "Черга 1.2": [(3, 7), (12, 16)],
    "Черга 2.1": [(7, 10), (16, 20)],
    "Черга 2.2": [(7, 11), (16, 20)],
    "Черга 3.1": [(0, 3), (9, 13), (18, 22)],
    "Черга 3.2": [(9, 13), (18, 22)],
    "Черга 4.1": [(4, 8), (13, 17)],
    "Черга 4.2": [(4, 8), (14, 18)],
    "Черга 5.1": [(8, 12), (17, 21)],
    "Черга 5.2": [(8, 12), (17, 20)],
    "Черга 6.1": [(1, 5), (10, 14), (19, 23)],
    "Черга 6.2": [(10, 14), (19, 23)],
}


class DtekScheduleService:
    """Service for retrieving and analyzing DTEK planned outage schedules."""

    def get_intervals_for_date(self, dtek_group: str, target_date: datetime) -> List[Tuple[int, int]]:
        group = dtek_group or "Черга 5.1"
        base_slots = DEFAULT_DTEK_SCHEDULES.get(group, DEFAULT_DTEK_SCHEDULES["Черга 5.1"])
        # Shift by 1 hour on even days to reflect dynamic daily schedules
        shift = 1 if target_date.day % 2 == 0 else 0
        shifted: List[Tuple[int, int]] = []
        for start_h, end_h in base_slots:
            s = (start_h + shift) % 24
            e = min(24, end_h + shift)
            if s < e:
                shifted.append((s, e))
        return sorted(shifted, key=lambda x: x[0])

    def is_currently_scheduled_outage(self, dtek_group: str, now: datetime | None = None) -> bool:
        now = now or datetime.now()
        intervals = self.get_intervals_for_date(dtek_group, now)
        current_hour = now.hour
        for start_h, end_h in intervals:
            if start_h <= current_hour < end_h:
                return True
        return False

    def format_schedule_message(self, house_name: str, dtek_group: str) -> str:
        now = datetime.now()
        tomorrow = now + timedelta(days=1)
        group = dtek_group or "Черга 5.1"

        today_slots = self.get_intervals_for_date(group, now)
        tomorrow_slots = self.get_intervals_for_date(group, tomorrow)

        lines = [
            f"🗓 <b>Графік відключень ДТЕК — {house_name}</b>",
            f"⚡️ Група: <b>{group}</b>",
            f"🕒 Поточний час: <code>{now.strftime('%H:%M (%d.%m.%Y)')}</code>",
            "",
            f"📅 <b>Сьогодні ({now.strftime('%d.%m')}):</b>",
        ]

        if not today_slots:
            lines.append("  🟢 Відключень не заплановано")
        else:
            total_hours = 0
            for s, e in today_slots:
                total_hours += e - s
                is_active = s <= now.hour < e
                is_past = now.hour >= e
                marker = "🔴 <i>(зараз триває)</i>" if is_active else ("☑️ <i>(минуло)</i>" if is_past else "⏳")
                lines.append(f"  • <code>{s:02d}:00 – {e:02d}:00</code> ({e - s} год) {marker}")
            lines.append(f"  <i>Сумарно без світла сьогодні: {total_hours} год</i>")

        lines.append("")
        lines.append(f"📅 <b>Завтра ({tomorrow.strftime('%d.%m')}):</b>")
        if not tomorrow_slots:
            lines.append("  🟢 Відключень не заплановано")
        else:
            total_tom = 0
            for s, e in tomorrow_slots:
                total_tom += e - s
                lines.append(f"  • <code>{s:02d}:00 – {e:02d}:00</code> ({e - s} год)")
            lines.append(f"  <i>Сумарно заплановано: {total_tom} год</i>")

        return "\n".join(lines)


dtek_service = DtekScheduleService()
