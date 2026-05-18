from pathlib import Path
from io import StringIO, BytesIO
import csv
import json
import uuid

from flask import Flask, render_template, request, send_file, flash, redirect, url_for

from parser import rows_for_sorting_matrix
from routing_config import load_routing_config
from matcher import apply_sorting_matrix, summarize_bins


BASE_DIR = Path(__file__).resolve().parent
UPLOAD_FOLDER = BASE_DIR / "uploads"
UPLOAD_FOLDER.mkdir(exist_ok=True)

EXPORT_STATE_FOLDER = BASE_DIR / "export_state"
EXPORT_STATE_FOLDER.mkdir(exist_ok=True)

ROUTING_CONFIG_PATH = BASE_DIR / "rstCCT.csv"

app = Flask(
    __name__,
    template_folder=str(BASE_DIR / "templates"),
)
app.secret_key = "dev-secret-key"

routing_rules = load_routing_config(ROUTING_CONFIG_PATH)

# This is now the single source of truth for what gets exported
# and should mirror the table shown on screen.
DISPLAY_COLUMNS = [
    ("transaction_date", "Date"),
    ("transaction_time", "Time"),
    ("expected_bin", "Bin"),
    ("item_barcode_ab", "Barcode"),
    ("title_aj", "Title"),
    ("aq_display", "AQ"),
    ("config_collection_code", "CSV Code"),
    ("config_description", "Description"),
    ("screen_message_af", "AF"),
    ("source_location_cr", "CR"),
    ("routing_target_ct_or_cl", "CT Raw"),
    ("ct_destination_location", "CT Location"),
    ("ct_patron_barcode", "CT Patron"),
    ("route_status", "Status"),
    ("notes", "Notes"),
]


def allowed_file(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in {"txt", "log"}


def get_latest_uploaded_file():
    uploads = sorted(
        [p for p in UPLOAD_FOLDER.iterdir() if p.is_file() and allowed_file(p.name)],
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return uploads[0] if uploads else None


def resolve_source_file(source_file_name: str | None):
    if source_file_name:
        candidate = UPLOAD_FOLDER / source_file_name
        if candidate.exists() and candidate.is_file():
            return candidate
    return get_latest_uploaded_file()


def load_rows_from_file(path: Path):
    rows = rows_for_sorting_matrix(path)
    rows = apply_sorting_matrix(rows, routing_rules)

    # Add a convenience field for CT raw display/export
    for row in rows:
        row["routing_target_ct_or_cl"] = row.get("routing_target_ct") or row.get("routing_target_cl") or ""

    rows = sorted(
        rows,
        key=lambda r: (
            r.get("expected_bin", 0),
            r.get("transaction_date") or "",
            r.get("transaction_time") or "",
            r.get("aq_display") or "",
            r.get("item_barcode_ab") or "",
        )
    )
    return rows


def apply_filters(rows, filters):
    filtered = rows

    filter_date = (filters.get("filter_date") or "").strip()
    filter_time_start = (filters.get("filter_time_start") or "").strip()
    filter_time_end = (filters.get("filter_time_end") or "").strip()
    filter_bin = (filters.get("filter_bin") or "").strip()
    filter_location = (filters.get("filter_location") or "").strip().lower()

    if filter_date:
        filtered = [r for r in filtered if (r.get("transaction_date") or "") == filter_date]

    if filter_time_start:
        filtered = [r for r in filtered if (r.get("transaction_time") or "") >= filter_time_start]

    if filter_time_end:
        filtered = [r for r in filtered if (r.get("transaction_time") or "") <= filter_time_end]

    if filter_bin != "":
        try:
            filter_bin_int = int(filter_bin)
            filtered = [r for r in filtered if r.get("expected_bin") == filter_bin_int]
        except ValueError:
            pass

    if filter_location:
        filtered = [
            r for r in filtered
            if filter_location in (r.get("aq_display") or "").strip().lower()
        ]

    return filtered


def get_unique_locations(rows):
    values = sorted({
        (r.get("aq_display") or "").strip()
        for r in rows
        if (r.get("aq_display") or "").strip()
    })
    return values


def write_export_state(rows, source_file_name):
    export_id = uuid.uuid4().hex
    export_path = EXPORT_STATE_FOLDER / f"{export_id}.json"

    payload = {
        "source_file": source_file_name,
        "row_count": len(rows),
        "rows": rows,
    }

    export_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return export_id


def read_export_state(export_id):
    if not export_id:
        return None

    export_path = EXPORT_STATE_FOLDER / f"{export_id}.json"
    if not export_path.exists():
        return None

    return json.loads(export_path.read_text(encoding="utf-8"))


def rows_for_export(rows):
    """
    Convert internal row dicts into the exact column set shown on screen.
    """
    exported_rows = []

    for row in rows:
        exported_row = {}
        for key, header in DISPLAY_COLUMNS:
            value = row.get(key, "")
            exported_row[header] = value if value is not None else ""
        exported_rows.append(exported_row)

    return exported_rows


def get_field_reference_data():
    return [
        {
            "code": "AB",
            "label": "Item Barcode",
            "definition": "The item barcode scanned during check-in.",
            "why_it_matters": "Used to identify the exact item transaction.",
        },
        {
            "code": "AQ",
            "label": "Home Location / Permanent Location",
            "definition": "The item's permanent or home location code from the ILS.",
            "why_it_matters": "This is the main value used to match the item to rstCCT.csv and assign the expected bin.",
        },
        {
            "code": "AJ",
            "label": "Title",
            "definition": "The title associated with the item in the SIP response.",
            "why_it_matters": "Helps staff verify the item without needing to rely only on the barcode.",
        },
        {
            "code": "AF",
            "label": "Route Message / Screen Message",
            "definition": "A human-readable message returned in the SIP response.",
            "why_it_matters": "Provides context such as transit, holdshelf, or error conditions.",
        },
        {
            "code": "CR",
            "label": "Source Location",
            "definition": "The source or originating location included in the SIP response.",
            "why_it_matters": "Useful when reviewing why an item may have routed a certain way.",
        },
        {
            "code": "CT",
            "label": "Destination Location + Patron Barcode",
            "definition": "A compound value where the portion before the colon is the destination location code and the portion after the colon is the patron barcode.",
            "why_it_matters": "Provides routing context for holds, transits, and other special workflows. It is not the assigned AMH bin.",
        },
        {
            "code": "09",
            "label": "Check-in Request",
            "definition": "The SIP2 message sent to request a check-in.",
            "why_it_matters": "Represents the incoming check-in transaction before the response is returned.",
        },
        {
            "code": "10",
            "label": "Check-in Response",
            "definition": "The SIP2 response message returned after the check-in request.",
            "why_it_matters": "Contains most of the fields displayed in the app, including AQ, AJ, AF, CR, and CT.",
        },
    ]


@app.route("/", methods=["GET", "POST"])
def index():
    if request.method == "POST":
        file = request.files.get("logfile")

        if not file or not file.filename:
            flash("Please choose a log file to upload.")
            return redirect(url_for("index"))

        if not allowed_file(file.filename):
            flash("Please upload a .txt or .log file.")
            return redirect(url_for("index"))

        saved_path = UPLOAD_FOLDER / file.filename
        file.save(saved_path)

        return redirect(url_for("index", source_file=file.filename))

    filters = {
        "source_file": request.args.get("source_file", ""),
        "filter_date": request.args.get("filter_date", ""),
        "filter_time_start": request.args.get("filter_time_start", ""),
        "filter_time_end": request.args.get("filter_time_end", ""),
        "filter_bin": request.args.get("filter_bin", ""),
        "filter_location": request.args.get("filter_location", ""),
    }

    context = {
        "rows": [],
        "summary": None,
        "filename": None,
        "all_locations": [],
        "export_id": "",
        "filters": filters,
    }

    source_path = resolve_source_file(filters["source_file"])

    if source_path:
        try:
            all_rows = load_rows_from_file(source_path)
            filtered_rows = apply_filters(all_rows, filters)
            summary = summarize_bins(filtered_rows)
            export_id = write_export_state(filtered_rows, source_path.name)

            context["rows"] = filtered_rows
            context["summary"] = summary
            context["filename"] = source_path.name
            context["filters"]["source_file"] = source_path.name
            context["all_locations"] = get_unique_locations(all_rows)
            context["export_id"] = export_id

        except Exception as exc:
            flash(f"Error parsing file: {exc}")

    return render_template("index.html", **context)


@app.route("/fields")
def fields_reference():
    source_file = request.args.get("source_file", "")
    source_path = resolve_source_file(source_file)

    sample_row = None

    try:
        if source_path:
            sample_rows = load_rows_from_file(source_path)
            if sample_rows:
                sample_row = sample_rows[0]
    except Exception:
        sample_row = None

    return render_template(
        "fields.html",
        field_definitions=get_field_reference_data(),
        sample_row=sample_row,
    )


@app.route("/export-csv", methods=["GET"])
def export_csv():
    export_id = request.args.get("export_id", "")
    payload = read_export_state(export_id)

    if not payload or "rows" not in payload:
        flash("No filtered rows are available to export. Please load and filter a log file first.")
        return redirect(url_for("index"))

    rows = payload["rows"]
    export_rows = rows_for_export(rows)

    output = StringIO()

    if export_rows:
        writer = csv.DictWriter(output, fieldnames=list(export_rows[0].keys()))
        writer.writeheader()
        writer.writerows(export_rows)
    else:
        writer = csv.writer(output)
        writer.writerow(["No rows found"])

    byte_stream = BytesIO(output.getvalue().encode("utf-8"))

    source_name = payload.get("source_file", "amh_log")
    safe_name = Path(source_name).stem

    return send_file(
        byte_stream,
        mimetype="text/csv",
        as_attachment=True,
        download_name=f"{safe_name}_filtered_view.csv",
    )


if __name__ == "__main__":
    app.run(debug=True)