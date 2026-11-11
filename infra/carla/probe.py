#!/usr/bin/env python3
"""Inspect a prospective CARLA host without installing or downloading anything."""
import argparse
import ctypes as c
from ctypes.util import find_library
from datetime import datetime, timezone
import glob
import json
import os
from pathlib import Path
import platform
import shutil
import struct
import subprocess
import urllib.request


def command(args):
    executable = shutil.which(args[0])
    if not executable:
        return {"available": False}
    try:
        result = subprocess.run([executable, *args[1:]], capture_output=True, text=True, timeout=15)
        return {"available": True, "exit_code": result.returncode,
                "stdout": result.stdout[:12000], "stderr": result.stderr[:4000]}
    except Exception as exc:
        return {"available": True, "error": f"{type(exc).__name__}: {exc}"}


def vulkan_devices():
    class Application(c.Structure):
        _fields_ = [("sType", c.c_uint32), ("pNext", c.c_void_p), ("name", c.c_char_p), ("version", c.c_uint32),
                    ("engine", c.c_char_p), ("engine_version", c.c_uint32), ("api", c.c_uint32)]
    class Info(c.Structure):
        _fields_ = [("sType", c.c_uint32), ("pNext", c.c_void_p), ("flags", c.c_uint32),
                    ("app", c.POINTER(Application)), ("layers", c.c_uint32), ("layer_names", c.c_void_p),
                    ("extensions", c.c_uint32), ("extension_names", c.c_void_p)]
    library = find_library("vulkan")
    if not library:
        return {"loader": None, "error": "Vulkan loader missing", "devices": []}
    v = c.CDLL(library)
    v.vkCreateInstance.argtypes = [c.POINTER(Info), c.c_void_p, c.POINTER(c.c_void_p)]
    v.vkCreateInstance.restype = c.c_int32
    v.vkEnumeratePhysicalDevices.argtypes = [c.c_void_p, c.POINTER(c.c_uint32), c.c_void_p]
    v.vkEnumeratePhysicalDevices.restype = c.c_int32
    v.vkGetPhysicalDeviceProperties.argtypes = [c.c_void_p, c.c_void_p]
    v.vkDestroyInstance.argtypes = [c.c_void_p, c.c_void_p]
    app = Application(0, None, b"CARLA host preflight", 0, b"preflight", 0, 1 << 22)
    info = Info(1, None, 0, c.pointer(app), 0, None, 0, None)
    instance = c.c_void_p()
    status = v.vkCreateInstance(c.byref(info), None, c.byref(instance))
    result = {"loader": library, "create_instance_result": status, "devices": []}
    if status != 0:
        return result
    try:
        count = c.c_uint32()
        v.vkEnumeratePhysicalDevices(instance, c.byref(count), None)
        devices = (c.c_void_p * count.value)()
        v.vkEnumeratePhysicalDevices(instance, c.byref(count), devices)
        for device in devices:
            properties = c.create_string_buffer(4096)
            v.vkGetPhysicalDeviceProperties(device, properties)
            api, driver, vendor, model, kind = struct.unpack_from("5I", properties.raw)
            name = properties.raw[20:276].split(b"\0", 1)[0].decode(errors="replace")
            result["devices"].append({"name": name, "vendor_id": hex(vendor), "device_id": hex(model),
                                      "device_type": kind, "api_version": f"{api >> 22}.{(api >> 12) & 1023}.{api & 4095}"})
    finally:
        v.vkDestroyInstance(instance, None)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-dir", type=Path, default=Path.cwd())
    parser.add_argument("--output", type=Path)
    parser.add_argument("--require-gpu", action="store_true", help="Fail unless Vulkan enumerates a real integrated/discrete GPU")
    parser.add_argument("--api-url", help="Optionally check the SSH-forwarded API, normally http://127.0.0.1:18000")
    args = parser.parse_args()
    work = args.work_dir.resolve()
    existing = work
    while not existing.exists():
        existing = existing.parent
    result = {"observed_at": datetime.now(timezone.utc).isoformat(), "platform": platform.platform(),
              "python": platform.python_version(), "work_dir": str(work), "uid": os.getuid(),
              "free_disk_gib": round(shutil.disk_usage(existing).free / 1024 ** 3, 2),
              "ram_gib": round(os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 1024 ** 3, 2),
              "nvidia_smi": command(["nvidia-smi", "--query-gpu=name,driver_version,memory.total", "--format=csv,noheader"]),
              "vulkan_icd_files": glob.glob("/usr/share/vulkan/icd.d/*.json") + glob.glob("/etc/vulkan/icd.d/*.json"),
              "graphics_loader": {name: find_library(name) for name in ("vulkan", "GL", "EGL")}}
    try:
        result["vulkan"] = vulkan_devices()
    except Exception as exc:
        result["vulkan"] = {"error": f"{type(exc).__name__}: {exc}", "devices": []}
    result["hardware_vulkan"] = any(device["device_type"] in (1, 2) for device in result["vulkan"]["devices"])
    if args.api_url:
        try:
            with urllib.request.urlopen(args.api_url.rstrip("/") + "/health", timeout=5) as response:
                result["api_health"] = {"status": response.status, "body": json.load(response)}
        except Exception as exc:
            result["api_health"] = {"error": f"{type(exc).__name__}: {exc}"}
    encoded = json.dumps(result, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded)
    print(encoded, end="")
    return 3 if args.require_gpu and not result["hardware_vulkan"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
