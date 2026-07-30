"""thermal_monitor.py — reads live CPU/GPU temperatures via `sensors`/`nvidia-smi`.

WHY not wired to auto-pause a simulation yet: doing that (per the spec's "if
GPU thermals spike during a Qiskit simulation, throttle or pause it") needs a
notion of "which process is the simulation," which nothing in this codebase
tracks yet. This module only ships the reading + threshold-check primitives;
auto-pause is a roadmap item once job tracking exists.
"""
import json
import subprocess
from dataclasses import dataclass
from typing import Optional


class ThermalReadError(Exception):
    """Raised when a sensor source can't be read or parsed."""


@dataclass(frozen=True)
class ThermalReading:
    cpu_c: float
    gpu_c: Optional[float]


class ThermalMonitor:
    """Reads current CPU/GPU temperatures from local sensor tooling."""

    def check(self) -> ThermalReading:
        return ThermalReading(cpu_c=self._read_cpu_temp(), gpu_c=self._read_gpu_temp())

    def is_over_threshold(self, reading: ThermalReading, cpu_max: float, gpu_max: float) -> bool:
        if reading.cpu_c >= cpu_max:
            return True
        return reading.gpu_c is not None and reading.gpu_c >= gpu_max

    def _read_cpu_temp(self) -> float:
        completed = subprocess.run(["sensors", "-j"], capture_output=True, text=True)
        if completed.returncode != 0:
            raise ThermalReadError(f"sensors failed: {completed.stderr.strip()}")
        data = json.loads(completed.stdout)
        return self._extract_package_temp(data)

    @staticmethod
    def _extract_package_temp(data: dict) -> float:
        for chip, readings in data.items():
            if not chip.startswith("coretemp"):
                continue
            for label, values in readings.items():
                if label.startswith("Package id"):
                    return next(v for k, v in values.items() if k.endswith("_input"))
        raise ThermalReadError("no coretemp Package id reading found in sensors output")

    def _read_gpu_temp(self) -> Optional[float]:
        completed = subprocess.run(
            ["nvidia-smi", "--query-gpu=temperature.gpu", "--format=csv,noheader"],
            capture_output=True,
            text=True,
        )
        if completed.returncode != 0:
            return None
        return float(completed.stdout.strip())


if __name__ == "__main__":
    monitor = ThermalMonitor()
    reading = monitor.check()
    print(f"CPU: {reading.cpu_c}°C, GPU: {reading.gpu_c}°C")
    print(f"Over threshold (85/85)? {monitor.is_over_threshold(reading, 85, 85)}")
