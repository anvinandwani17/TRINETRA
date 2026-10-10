"""Functional environment checks; no network, datasets, training, or credentials.

Runs all checks even when one fails and exits nonzero unless all pass. Tiny
temporary raster/vector fixtures are software tests, never satellite results.
"""

import argparse
import importlib
import importlib.metadata
import json
import platform
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def check_python():
    require(sys.version_info[:2] == (3, 12), "Use Python 3.12 for Anvi's environment")
    require(struct.calcsize("P") == 8, "Use a 64-bit Python interpreter")
    require(sys.prefix != sys.base_prefix, "Run this smoke test inside a virtual environment")
    return {"version": platform.python_version(), "platform": platform.system(),
            "architecture": platform.machine()}


def check_imports():
    packages = {"fastapi": "fastapi", "uvicorn": "uvicorn",
                "pydantic_settings": "pydantic-settings", "pytest": "pytest",
                "httpx": "httpx", "numpy": "numpy", "pandas": "pandas",
                "rasterio": "rasterio", "geopandas": "geopandas",
                "shapely": "shapely", "pyproj": "pyproj", "pyogrio": "pyogrio"}
    versions, errors = {}, []
    for module, distribution in packages.items():
        try:
            importlib.import_module(module)
            versions[distribution] = importlib.metadata.version(distribution)
        except Exception as exc:
            errors.append(f"{module}: {type(exc).__name__}: {exc}")
    try:
        importlib.import_module("trinetra")
    except Exception as exc:
        errors.append(f"trinetra: {type(exc).__name__}: {exc}")
    require(not errors, "; ".join(errors))
    return versions


def check_torch():
    import torch
    import numpy as np

    # Tensor math, autograd, and NumPy interop; no model training.
    x = torch.tensor([1.0, 2.0, 3.0], requires_grad=True)
    value = (x * x).sum()
    require(value.item() == 14.0, "Incorrect PyTorch tensor result")
    value.backward()
    np.testing.assert_allclose(x.grad.numpy(), [2.0, 4.0, 6.0])
    t = torch.from_numpy(np.array([4.0, 5.0], dtype=np.float32))
    require(t.sum().item() == 9.0, "PyTorch/NumPy interoperability failed")
    require(torch.version.cuda is None, "Expected CPU-only PyTorch; install requirements-ml-cpu.txt in a clean venv")
    return {"version": torch.__version__, "device": "cpu", "cuda_available": torch.cuda.is_available()}


def check_geospatial():
    import geopandas as gpd
    import numpy as np
    import pyogrio
    import pyproj
    import rasterio
    from rasterio.io import MemoryFile
    from rasterio.transform import from_origin
    from shapely.geometry import Point, box

    require(box(0, 0, 2, 2).intersection(box(1, 1, 3, 3)).area == 1,
            "Shapely intersection failed")
    x, y = pyproj.Transformer.from_crs(4326, 32643, always_xy=True).transform(75, 0)
    require(abs(x - 500000) < 0.01 and abs(y) < 0.01, "PROJ transformation failed")
    frame = gpd.GeoDataFrame({"id": [1]}, geometry=[Point(75, 0)], crs="EPSG:4326")
    projected = frame.to_crs(32643)
    require(abs(projected.geometry.iloc[0].x - 500000) < 0.01, "GeoPandas reprojection failed")
    with tempfile.TemporaryDirectory(prefix="trinetra-smoke-") as folder:
        path = Path(folder) / "fixture.geojson"
        frame.to_file(path, driver="GeoJSON", engine="pyogrio")
        restored = gpd.read_file(path, engine="pyogrio")
        require(len(restored) == 1 and restored.geometry.iloc[0].equals(Point(75, 0)),
                "GeoPandas/pyogrio vector roundtrip failed")
    data = np.array([[1, 2], [3, 4]], dtype=np.uint8)
    with MemoryFile() as mem:
        with mem.open(driver="GTiff", height=2, width=2, count=1, dtype="uint8",
                      crs="EPSG:32643", transform=from_origin(500000, 20, 10, 10)) as ds:
            ds.write(data, 1)
        with mem.open() as ds:
            np.testing.assert_array_equal(ds.read(1), data)
            require(ds.crs.to_epsg() == 32643, "Raster CRS roundtrip failed")
    return {"rasterio": rasterio.__version__, "gdal_rasterio": rasterio.__gdal_version__,
            "gdal_pyogrio": pyogrio.__gdal_version_string__,
            "proj": pyproj.proj_version_str, "geopandas": gpd.__version__}


def check_fastapi():
    from fastapi.testclient import TestClient
    from trinetra import __version__
    from trinetra.api.main import create_app
    from trinetra.core.config import Settings

    with TestClient(create_app(Settings(_env_file=None, app_name="TRINETRA"))) as client:
        response = client.get("/api/v1/health")
        require(response.status_code == 200, "Health route did not return HTTP 200")
        require(response.json() == {"status": "ok", "service": "TRINETRA", "version": __version__},
                "Health response contract failed")
        require(set(client.get("/openapi.json").json()["paths"]) == {"/api/v1/health"},
                "Unexpected API contract")
    return {"health_http_status": 200, "transport": "in-process TestClient"}


def run_command(command, cwd):
    result = subprocess.run(command, cwd=cwd, capture_output=True, text=True,
                            timeout=180, encoding="utf-8", errors="replace")
    require(result.returncode == 0, (result.stdout + result.stderr)[-5000:])
    return result.stdout.strip()


def check_frontend():
    node = shutil.which("node")
    npm = shutil.which("npm.cmd" if sys.platform == "win32" else "npm")
    require(node is not None and npm is not None, "Install Node.js 22.12+ with npm and reopen the terminal")
    web = ROOT / "web"
    require((web / "node_modules").is_dir(), "Run npm install in web/ first")
    js = """
import React from 'react';
import { renderToString } from 'react-dom/server';
import { version as viteVersion } from 'vite';
import reactPlugin from '@vitejs/plugin-react';
const [major, minor] = process.versions.node.split('.').map(Number);
if (major < 22 || (major === 22 && minor < 12)) throw Error('Use Node.js 22.12+');
if (renderToString(React.createElement('h1', null, 'TRINETRA')) !== '<h1>TRINETRA</h1>')
  throw Error('React rendering failed');
if (!reactPlugin()) throw Error('React Vite plugin failed');
console.log(JSON.stringify({node:process.version,react:React.version,vite:viteVersion}));
"""
    versions = json.loads(run_command([node, "--input-type=module", "-e", js], web))
    run_command([npm, "run", "build"], web)
    require((web / "dist" / "index.html").is_file(), "Vite did not produce dist/index.html")
    return {**versions, "react_render": "passed", "production_build": "passed"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, help="Optional JSON result path; no credentials recorded")
    args = parser.parse_args()
    checks = []
    for name, function in [("Python", check_python), ("Required imports", check_imports),
                           ("PyTorch", check_torch), ("Geospatial", check_geospatial),
                           ("FastAPI", check_fastapi), ("Frontend", check_frontend)]:
        try:
            detail = function()
            checks.append({"name": name, "status": "PASS", "detail": detail})
            print(f"PASS: {name}", flush=True)
        except Exception as exc:
            checks.append({"name": name, "status": "FAIL", "error": f"{type(exc).__name__}: {exc}"})
            print(f"FAIL: {name}: {exc}", flush=True)
    passed = all(c["status"] == "PASS" for c in checks)
    report = {"generated_at": datetime.now(timezone.utc).isoformat(),
              "all_passed": passed, "checks": checks}
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print("All environment checks passed." if passed else "Environment NOT ready. Fix failed checks and rerun.")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
