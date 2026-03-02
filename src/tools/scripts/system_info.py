"""Cross-platform system info script used by the ``system_info`` tool.

Usage::

    python -m src.tools.scripts.system_info <category>

Where ``<category>`` is one of: ``cpu``, ``memory``, ``disk``, ``network``, ``os``, ``all``.
"""

from __future__ import annotations

import json
import os
import platform
import subprocess
import sys


def get_cpu() -> dict:
    info: dict = {}
    if sys.platform == "win32":
        info["model"] = platform.processor() or "unknown"
        info["cores_logical"] = os.cpu_count() or 0
        try:
            r = subprocess.run(
                ["wmic", "cpu", "get", "NumberOfCores", "/value"],
                capture_output=True,
                text=True,
            )
            for line in r.stdout.splitlines():
                if line.startswith("NumberOfCores="):
                    info["cores_physical"] = int(line.split("=")[1])
        except Exception:
            pass
    else:
        try:
            with open("/proc/cpuinfo") as f:
                cpuinfo = f.read()
            models = [ln.split(":")[1].strip() for ln in cpuinfo.splitlines() if ln.startswith("model name")]
            info["model"] = models[0] if models else "unknown"
            info["cores_logical"] = os.cpu_count() or 0
            with open("/proc/stat") as f:
                lines = [ln for ln in f.readlines() if ln.startswith("cpu") and not ln.startswith("cpu ")]
            info["cores_physical"] = len(lines)
        except Exception:
            pass
        try:
            with open("/proc/loadavg") as f:
                parts = f.read().split()
            info["load_avg_1m"] = float(parts[0])
            info["load_avg_5m"] = float(parts[1])
            info["load_avg_15m"] = float(parts[2])
        except Exception:
            pass
    return info


def get_memory() -> dict:
    info: dict = {}
    if sys.platform == "win32":
        try:
            r = subprocess.run(
                ["wmic", "os", "get", "TotalVisibleMemorySize,FreePhysicalMemory", "/value"],
                capture_output=True,
                text=True,
            )
            vals: dict[str, int] = {}
            for line in r.stdout.splitlines():
                if "=" in line:
                    k, v = line.strip().split("=", 1)
                    vals[k] = int(v)
            total = vals.get("TotalVisibleMemorySize", 0)
            free = vals.get("FreePhysicalMemory", 0)
            info["total_mb"] = round(total / 1024, 1)
            info["available_mb"] = round(free / 1024, 1)
            info["used_mb"] = round((total - free) / 1024, 1)
            info["usage_percent"] = round((total - free) / total * 100, 1) if total else 0
        except Exception:
            pass
    else:
        try:
            with open("/proc/meminfo") as f:
                lines = f.readlines()
            mem: dict[str, int] = {}
            for line in lines:
                parts = line.split(":")
                if len(parts) == 2:
                    key = parts[0].strip()
                    val = parts[1].strip().split()[0]
                    mem[key] = int(val)
            total = mem.get("MemTotal", 0)
            available = mem.get("MemAvailable", 0)
            swap_total = mem.get("SwapTotal", 0)
            swap_free = mem.get("SwapFree", 0)
            info["total_mb"] = round(total / 1024, 1)
            info["available_mb"] = round(available / 1024, 1)
            info["used_mb"] = round((total - available) / 1024, 1)
            info["usage_percent"] = round((total - available) / total * 100, 1) if total else 0
            info["swap_total_mb"] = round(swap_total / 1024, 1)
            info["swap_used_mb"] = round((swap_total - swap_free) / 1024, 1)
        except Exception:
            pass
    return info


def get_disk() -> list[dict]:
    disks: list[dict] = []
    if sys.platform == "win32":
        try:
            r = subprocess.run(
                ["wmic", "logicaldisk", "get", "DeviceID,FileSystem,Size,FreeSpace", "/value"],
                capture_output=True,
                text=True,
            )
            # Parse multi-record wmic output (records separated by blank lines)
            current: dict[str, str] = {}
            for line in r.stdout.splitlines():
                line = line.strip()
                if "=" in line:
                    k, v = line.split("=", 1)
                    current[k] = v
                elif not line and current:
                    size = int(current.get("Size") or "0")
                    free = int(current.get("FreeSpace") or "0")
                    if size > 0:
                        disks.append(
                            {
                                "device": current.get("DeviceID", ""),
                                "filesystem": current.get("FileSystem", ""),
                                "total_gb": round(size / 1_073_741_824, 2),
                                "used_gb": round((size - free) / 1_073_741_824, 2),
                                "available_gb": round(free / 1_073_741_824, 2),
                                "usage_percent": f"{round((size - free) / size * 100)}%",
                                "mount": current.get("DeviceID", ""),
                            }
                        )
                    current = {}
        except Exception:
            pass
    else:
        try:
            r = subprocess.run(
                ["df", "-B1", "--output=source,fstype,size,used,avail,pcent,target"],
                capture_output=True,
                text=True,
            )
            lines = r.stdout.strip().splitlines()[1:]
            for line in lines:
                parts = line.split()
                if len(parts) >= 7 and not parts[0].startswith("tmpfs") and not parts[0].startswith("devtmpfs"):
                    disks.append(
                        {
                            "device": parts[0],
                            "filesystem": parts[1],
                            "total_gb": round(int(parts[2]) / 1_073_741_824, 2),
                            "used_gb": round(int(parts[3]) / 1_073_741_824, 2),
                            "available_gb": round(int(parts[4]) / 1_073_741_824, 2),
                            "usage_percent": parts[5],
                            "mount": parts[6],
                        }
                    )
        except Exception:
            pass
    return disks


def get_network() -> list[dict]:
    interfaces: list[dict] = []
    if sys.platform == "win32":
        try:
            r = subprocess.run(
                [
                    "powershell",
                    "-NoProfile",
                    "-Command",
                    "Get-NetIPAddress"
                    " | Select-Object InterfaceAlias,AddressFamily,IPAddress,PrefixLength"
                    " | ConvertTo-Json",
                ],
                capture_output=True,
                text=True,
            )
            data = json.loads(r.stdout)
            if isinstance(data, dict):
                data = [data]
            by_iface: dict[str, list[dict]] = {}
            for entry in data:
                alias = entry.get("InterfaceAlias", "")
                family = "inet6" if entry.get("AddressFamily") == 23 else "inet"
                by_iface.setdefault(alias, []).append(
                    {
                        "family": family,
                        "address": entry.get("IPAddress", ""),
                        "prefix": entry.get("PrefixLength"),
                    }
                )
            for name, addrs in by_iface.items():
                interfaces.append({"name": name, "state": "up", "mac": None, "addresses": addrs})
        except Exception:
            pass
    else:
        try:
            r = subprocess.run(["ip", "-j", "addr", "show"], capture_output=True, text=True)
            data = json.loads(r.stdout)
            for iface in data:
                addrs = []
                for a in iface.get("addr_info", []):
                    addrs.append({"family": a.get("family"), "address": a.get("local"), "prefix": a.get("prefixlen")})
                interfaces.append(
                    {
                        "name": iface.get("ifname"),
                        "state": iface.get("operstate", "").lower(),
                        "mac": iface.get("address"),
                        "addresses": addrs,
                    }
                )
        except Exception:
            pass
    return interfaces


def get_os() -> dict:
    info: dict = {}
    info["system"] = platform.system()
    info["kernel"] = platform.release()
    info["architecture"] = platform.machine()
    info["hostname"] = platform.node()

    if sys.platform == "win32":
        info["distro"] = platform.version()
    else:
        try:
            with open("/etc/os-release") as f:
                for line in f:
                    if line.startswith("PRETTY_NAME="):
                        info["distro"] = line.split("=", 1)[1].strip().strip('"')
                        break
        except Exception:
            pass
        try:
            with open("/proc/uptime") as f:
                up = float(f.read().split()[0])
            days = int(up // 86400)
            hours = int((up % 86400) // 3600)
            mins = int((up % 3600) // 60)
            info["uptime"] = f"{days}d {hours}h {mins}m"
        except Exception:
            pass
    return info


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python -m src.tools.scripts.system_info <category>", file=sys.stderr)
        sys.exit(1)

    category = sys.argv[1]
    result: dict = {}

    if category in ("cpu", "all"):
        result["cpu"] = get_cpu()
    if category in ("memory", "all"):
        result["memory"] = get_memory()
    if category in ("disk", "all"):
        result["disk"] = get_disk()
    if category in ("network", "all"):
        result["network"] = get_network()
    if category in ("os", "all"):
        result["os"] = get_os()

    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
