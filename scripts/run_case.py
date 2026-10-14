"""Retired prototype CLI; never generate synthetic flood evidence."""
import sys


def main():
    print(
        "scripts/run_case.py is retired: its prototype fabricated missing hazard "
        "polygons and did not verify source dates. Use scripts/run_integrated_case.py "
        "--aoi <polygon.geojson> --date <YYYY-MM-DD> --method <method> for verified "
        "saved outputs, or scripts/run_general_pipeline.py for a new baseline case.",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
