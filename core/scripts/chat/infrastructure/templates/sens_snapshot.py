"""Build sens block (time, hardware) — shared API for ModelWorker."""

from __future__ import annotations

import datetime
import subprocess

from infrastructure.locale.variables import var_get


def sens_hardware_suffix(locale: str = "en") -> str:
    """Extra SENS line: GPU (°C, core %, VRAM %), CPU (thermal_zone0)."""
    loc = str(locale or "en")
    bits = []
    try:
        r = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=temperature.gpu,utilization.gpu,memory.used,memory.total",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=1.5,
        )
        if r.returncode == 0 and (r.stdout or "").strip():
            line = (r.stdout.strip().splitlines() or [""])[0]
            sp = [x.strip() for x in line.split(",")]
            if len(sp) >= 4:
                try:
                    t_gpu, u_gpu = sp[0], sp[1]
                    mem_used, mem_total = float(sp[2]), float(sp[3])
                    mem_pct = int(round(100 * mem_used / mem_total)) if mem_total > 0 else None
                    if mem_pct is not None:
                        bits.append(
                            str(var_get("templates.gpu_line_full", loc) or "").format(
                                temp=t_gpu, util=u_gpu, mem=mem_pct
                            )
                        )
                    elif sp[0] and sp[1] is not None:
                        bits.append(str(var_get("templates.gpu_line_short", loc) or "").format(temp=sp[0], util=u_gpu))
                except ValueError:
                    if len(sp) >= 2 and sp[0] and sp[1] is not None:
                        bits.append(
                            str(var_get("templates.gpu_line_temp_util", loc) or "").format(temp=sp[0], util=sp[1])
                        )
            elif len(sp) >= 2 and sp[0] and sp[1] is not None:
                bits.append(str(var_get("templates.gpu_line_temp_util", loc) or "").format(temp=sp[0], util=sp[1]))
    except Exception:
        pass
    try:
        with open("/sys/class/thermal/thermal_zone0/temp") as f:
            millideg = int(f.read().strip())
            bits.append(f"CPU: {millideg / 1000.0:.0f}°C")
    except Exception:
        pass
    return (" · " + " · ".join(bits)) if bits else ""


def build_sens_status_line(*, perception_suffix: str | None = None, locale: str = "ru") -> str:
    loc = str(locale or "ru")
    now = datetime.datetime.now()
    time_str = now.strftime("%H:%M")
    date_str = now.strftime("%d %B %Y (%A)")
    hw_suffix = sens_hardware_suffix("en")
    line = str(var_get("templates.sens_time_line", loc) or "").format(time=time_str, date=date_str, hw=hw_suffix)
    extra = (perception_suffix or "").strip()
    if extra:
        line = f"{line} {extra}"
    return line
