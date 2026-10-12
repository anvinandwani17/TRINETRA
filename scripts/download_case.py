"""Plan the pinned pair by default; explicit --download enables scene transfer."""
import argparse
import getpass
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from trinetra.core.cases import make_case
from trinetra.downloads.case_runner import plan_download, download_planned, read_json
from trinetra.downloads.sentinel1 import TokenProvider


def credentials():
    from trinetra.core.config import Settings
    settings = Settings(_env_file=ROOT / ".env")
    return settings.cdse_username.get_secret_value(), settings.cdse_password.get_secret_value()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--aoi", type=Path, required=True)
    parser.add_argument("--date", required=True)
    parser.add_argument("--data-dir", type=Path, default=ROOT / "data")
    parser.add_argument("--max-product-gib", type=float, default=3.0)
    parser.add_argument("--download", action="store_true")
    parser.add_argument("--transport", choices=("odata", "s3"), default="odata")
    parser.add_argument("--restart-partial", action="store_true")
    parser.add_argument("--two-factor", action="store_true")
    args = parser.parse_args()
    try:
        case = make_case(read_json(args.aoi), args.date)
        plan, paths = plan_download(case, args.data_dir, max_product_gib=args.max_product_gib)
        print("Case:", case.case_id)
        for role in ["pre", "post"]:
            p = plan["resolved_metadata"][role]
            print(role + ": " + p["Name"])
            print("  Catalogue size estimate: %.3f GiB" % (p["ContentLength"] / 1024 ** 3))
        print("Combined catalogue estimate: %.3f GiB" % (plan["catalogue_estimated_total_bytes"] / 1024 ** 3))
        print("Plan:", paths["run"] / "download_plan.json")
        if not args.download:
            print("PLAN ONLY: no scenes downloaded; no download credentials used.")
            print("Later, --download explicitly enables transfer of these two products.")
            return 0
        transfer = None
        tokens = None
        if args.transport == "s3":
            # Settings used by S3Transfer reads environment; load the repo's
            # private .env without overriding already configured environment.
            from dotenv import load_dotenv
            load_dotenv(ROOT / ".env", override=False)
            from trinetra.downloads.s3 import S3Transfer
            transfer = S3Transfer()
        else:
            tokens = TokenProvider(credentials, (lambda: getpass.getpass("Current CDSE 2FA code: ")) if args.two_factor else None)
        download_planned(plan, paths, tokens=tokens, transfer=transfer,
                         transport=args.transport, restart_partial=args.restart_partial)
        print("Two raw products verified. Manifest:", paths["run"] / "download_manifest.json")
        print("Preprocessing has NOT run.")
        return 0
    except (RuntimeError, ValueError, OSError, ImportError) as exc:
        print("Download stage stopped: " + str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
